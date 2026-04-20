"""
mcp_client.py — Connects to the GitHub MCP server and returns LangChain-compatible tools.

HOW IT WORKS (plain English):
  The GitHub MCP server is an npm package that wraps the entire GitHub REST API.
  Instead of calling GitHub directly, our agent calls this local server, and the
  server handles the GitHub API calls on our behalf.

  We launch the server as a *child process* using `npx`. Our Python process and
  the npm process talk to each other through stdin/stdout pipes — this is called
  "stdio transport". No ports, no network sockets needed.

  `MultiServerMCPClient` handles all of that plumbing. You give it a config dict
  that says "here's the command to run and the environment it needs", and it
  translates the server's tool list into standard LangChain tools that LangGraph
  can call natively.

IMPORTANT API NOTE (langchain-mcp-adapters >= 0.1.0):
  Do NOT use `async with MultiServerMCPClient(...) as client:` — it raises
  NotImplementedError. The correct pattern is:
    client = MultiServerMCPClient({...})
    tools = await client.get_tools()
"""

import asyncio
import os

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()


def _build_client() -> MultiServerMCPClient:
    """
    Create a configured MultiServerMCPClient pointed at the GitHub MCP server.

    The GitHub MCP server reads GITHUB_PERSONAL_ACCESS_TOKEN from its own
    environment, so we must forward it from our .env into the child process.
    """
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise EnvironmentError(
            "GITHUB_PERSONAL_ACCESS_TOKEN is not set.\n"
            "Check that your .env file exists and contains the token."
        )

    return MultiServerMCPClient(
        connections={
            "github": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "GITHUB_PERSONAL_ACCESS_TOKEN": token,
                    # Forward PATH so the child process can resolve npx and node
                    "PATH": os.environ.get("PATH", ""),
                },
            }
        }
    )


async def get_github_tools() -> list:
    """
    Return all GitHub MCP tools as LangChain BaseTool objects.

    Each tool (e.g. get_repository, list_pull_requests, get_file_contents) is
    a fully typed LangChain tool. When called, it spawns a fresh npx process,
    executes one GitHub API operation, and returns the result as a string.

    The graph calls this once at startup to get the tool list, then passes
    those tools to build_graph() so the LLM knows what it can call.
    """
    client = _build_client()
    tools = await client.get_tools()
    return tools


# ── Test block ────────────────────────────────────────────────────────────────
# Run with:  uv run python mcp_client.py
# This proves the MCP connection works before you build anything on top of it.
if __name__ == "__main__":
    async def _main() -> None:
        print("Connecting to GitHub MCP server via npx...")
        print("(First run downloads @modelcontextprotocol/server-github — may take ~10s)\n")
        try:
            tools = await get_github_tools()
            print(f"Success! Found {len(tools)} tools:\n")
            for i, tool in enumerate(tools, 1):
                # Truncate long descriptions for readability
                desc = tool.description[:80].replace("\n", " ")
                print(f"  {i:2d}. {tool.name}")
                print(f"       {desc}...")
        except EnvironmentError as e:
            print(f"[Config Error] {e}")
        except Exception as e:
            print(f"[Error] {e}")
            raise

    asyncio.run(_main())
