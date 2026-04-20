"""
main.py — CLI entry point for the GitHub repository analysis agent.

USAGE:
  uv run python main.py https://github.com/owner/repo

WHAT THIS FILE DOES:
  1. Parses the repository URL from the command line
  2. Loads GitHub MCP tools (connects to the MCP server)
  3. Builds the LangGraph and runs the full analysis
  4. Prints the markdown report to the terminal
  5. Saves the report as {owner}_{repo}_report.md in the current directory

WHY EVERYTHING IS ASYNC:
  Both get_github_tools() and graph.ainvoke() are async functions — they use
  `await` internally. Python requires all `await` calls to run inside an event
  loop. `asyncio.run()` at the bottom of this file creates that event loop and
  runs our async code to completion before the script exits.
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from agent import build_graph
from mcp_client import get_github_tools

load_dotenv()


def parse_repo_url(url: str) -> tuple[str, str]:
    """
    Extract (owner, repo_name) from a GitHub URL.

    Accepts:
      "https://github.com/facebook/react"     → ("facebook", "react")
      "https://github.com/owner/repo.git"     → ("owner", "repo")

    Raises ValueError if the URL is malformed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(
            f"URL must start with https:// — got: {url!r}\n"
            "Example: https://github.com/owner/repo"
        )
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            f"Could not extract owner/repo from: {url!r}\n"
            "Expected format: https://github.com/owner/repo"
        )
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def save_report(report: str, owner: str, repo: str) -> str:
    """Write the report to {owner}_{repo}_report.md and return the file path."""
    filename = f"{owner}_{repo}_report.md"
    filepath = os.path.join(os.getcwd(), filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)
    return filepath


async def run_analysis(repo_url: str) -> None:
    """
    Orchestrate the full analysis pipeline:
      load tools → build graph → run agent → print + save report.
    """
    print(f"\nAnalyzing: {repo_url}")
    print("─" * 60)

    print("Step 1/3  Loading GitHub MCP tools...", flush=True)
    tools = await get_github_tools()
    print(f"          {len(tools)} tools loaded.\n")

    print("Step 2/3  Building LangGraph...", flush=True)
    owner, repo_name = parse_repo_url(repo_url)
    graph = build_graph(tools, owner, repo_name)
    print("          Graph compiled.\n")

    print("Step 3/3  Running analysis (1–3 minutes, multiple API calls)...", flush=True)
    print("          Watch for tool calls below:\n")

    # Initial state: the agent's first message contains the repo URL.
    # The agent_node reads state["messages"] and the system prompt tells it
    # to extract owner/repo from this first message.
    initial_state = {
        "messages": [
            HumanMessage(content=f"Please analyze this GitHub repository: {repo_url}")
        ],
        "repo_url": repo_url,
        "report": None,
    }

    # ainvoke() runs the full graph to completion (all tool calls + final answer)
    # and returns the final state dict. We must use ainvoke (not invoke) because
    # the MCP tool calls are async coroutines.
    final_state = await graph.ainvoke(initial_state)

    report = final_state.get("report")
    if not report:
        print("[Error] The agent did not produce a report.")
        print("Last few messages:")
        for msg in final_state.get("messages", [])[-3:]:
            print(f"  {type(msg).__name__}: {str(msg.content)[:200]}")
        sys.exit(1)

    # Print to terminal
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60 + "\n")

    # Save to file
    owner, repo_name = parse_repo_url(repo_url)
    filepath = save_report(report, owner, repo_name)
    print(f"Report saved → {filepath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a GitHub repository and generate a health report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  uv run python main.py https://github.com/facebook/react
  uv run python main.py https://github.com/langchain-ai/langgraph
        """,
    )
    parser.add_argument(
        "repo_url",
        help="Full GitHub repository URL (https://github.com/owner/repo)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_analysis(args.repo_url))
    except KeyboardInterrupt:
        print("\n[Interrupted]")
        sys.exit(0)
    except ValueError as e:
        print(f"[Input Error] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
