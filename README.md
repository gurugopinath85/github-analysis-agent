# GitHub Repository Analysis Agent

An AI agent that performs a full health analysis of any GitHub repository. Give it a URL; it autonomously calls the GitHub API, investigates the repo, and produces a structured markdown report.

**Built with:**
- [LangGraph](https://github.com/langchain-ai/langgraph) — ReAct reasoning loop (agent ↔ tools)
- [Claude claude-sonnet-4-6](https://anthropic.com) — reasoning model via `langchain-anthropic`
- [GitHub MCP Server](https://github.com/modelcontextprotocol/servers) — all GitHub API calls via MCP
- [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters) — translates MCP tools into LangChain tools

---

## Prerequisites

| Requirement | Check |
|-------------|-------|
| Python 3.11+ | `python3 --version` |
| Node.js 18+ & npx | `npx --version` |
| uv package manager | `uv --version` — install: `brew install uv` |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |
| GitHub Personal Access Token | [github.com/settings/tokens](https://github.com/settings/tokens) — needs `repo` read scope |

---

## Setup

```bash
# 1. Enter the project directory
cd github-repo-agent

# 2. Install Python dependencies
uv sync

# 3. Add your credentials
cp .env.example .env
# Open .env and replace the placeholder values:
#   ANTHROPIC_API_KEY=sk-ant-api03-...
#   GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

---

## Usage

**Step 1 — Test the MCP connection (do this first):**
```bash
uv run python mcp_client.py
```
Expected output: a numbered list of ~28 GitHub tools. If you see this, the connection works.

**Step 2 — Run a full analysis:**
```bash
uv run python main.py https://github.com/owner/repo
```

The report is printed to the terminal and saved as `{owner}_{repo}_report.md` in the project folder.

**Examples:**
```bash
uv run python main.py https://github.com/anthropics/anthropic-quickstarts
uv run python main.py https://github.com/langchain-ai/langgraph
uv run python main.py https://github.com/facebook/react
```

---

## Report Sections

Every report contains these seven sections:

1. **Executive Summary** — 2–3 sentence health overview
2. **Repository Health Score** — Score /10 with justification
3. **Open Pull Requests** — Table with age and severity tag
4. **Issue Tracker Analysis** — Bug backlog and unassigned issue summary
5. **Branch Hygiene** — Stale branch detection
6. **Code Health Files** — README / CI / CONTRIBUTING presence check
7. **Recommendations** — 3–5 specific, named action items

Findings are tagged **[Critical]**, **[Warning]**, or **[Info]**.

---

## Architecture

```
main.py          CLI entry point — asyncio.run(run_analysis(url))
mcp_client.py    Spawns GitHub MCP server via npx, returns LangChain tools
agent.py         LangGraph StateGraph: agent node ↔ tools node (ReAct loop)
```

**How the ReAct loop works:**
1. `agent` node: LLM reads all messages, decides whether to call a tool or write the final report
2. `tools` node: executes the chosen MCP tool call (e.g. `get_repository`, `list_pull_requests`)
3. Tool result is appended to the message history and we loop back to step 1
4. When the LLM stops calling tools, the `extract_report` node captures the final text

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `npx: command not found` | Install Node.js from [nodejs.org](https://nodejs.org) |
| `GITHUB_PERSONAL_ACCESS_TOKEN is not set` | Check `.env` file exists with your token |
| `AuthenticationError` | Verify your Anthropic API key in `.env` |
| First run slow (~10–15s for Step 1) | npm is downloading the GitHub MCP server package — normal |
| Analysis takes > 3 minutes | Normal for large repos with many PRs/issues/branches |
