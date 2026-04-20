"""
agent.py — LangGraph StateGraph for the GitHub repository analysis agent.

THE REACT LOOP (plain English):
  ReAct stands for "Reason + Act". The agent alternates between:
    1. Reasoning: the LLM reads the full message history and decides what to do next
    2. Acting:    it calls a GitHub MCP tool to fetch real data
  This repeats until the LLM decides it has enough information to write the report.

GRAPH STRUCTURE:
  START
    │
    ▼
  [agent]  ◄──────────────────────┐
    │                             │
    │  tools_condition()          │
    ├──► "tools"  ──► [tools] ────┘   (loop: keep calling tools)
    │
    └──► "__end__"  ──► [extract_report] ──► END   (done: capture report text)

STATE (the shared whiteboard):
  Every node reads from and writes to AgentState. The `add_messages` reducer
  on the `messages` field means writes APPEND instead of replacing — this is
  how the agent remembers every tool result it has seen so far.
"""

from typing import Annotated

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

import json
import asyncio
from langchain_core.messages import ToolMessage, HumanMessage

async def run_parallel_analysis(tools: list, owner: str, repo: str) -> str:
    """
    Run all GitHub data fetches in parallel instead of sequentially.
    Returns a single combined context string for the LLM to synthesize.
    
    This replaces the multi-round ReAct loop with one parallel fetch round
    followed by one synthesis call — much faster for large repos.
    """
    # Build a lookup dict so we can call tools by name
    tool_map = {tool.name: tool for tool in tools}

    async def call_tool(name: str, **kwargs) -> str:
        """Call one MCP tool and return trimmed result."""
        try:
            tool = tool_map.get(name)
            if not tool:
                return f"{name}: tool not available"
            result = await tool.ainvoke(kwargs)
            return _trim_tool_response(str(result))
        except Exception as e:
            return f"{name}: {str(e)[:200]}"

    # Fire all fetches simultaneously
    results = await asyncio.gather(
        call_tool("get_repository", owner=owner, repo=repo),
        call_tool("list_pull_requests", owner=owner, repo=repo,
                  state="open", per_page=10),
        call_tool("list_issues", owner=owner, repo=repo,
                  state="open", per_page=10),
        call_tool("list_branches", owner=owner, repo=repo, per_page=20),
        call_tool("get_file_contents", owner=owner, repo=repo,
                  path="README.md"),
        call_tool("get_file_contents", owner=owner, repo=repo,
                  path="CONTRIBUTING.md"),
        call_tool("get_file_contents", owner=owner, repo=repo,
                  path=".github/workflows"),
    )

    labels = [
        "REPOSITORY INFO",
        "OPEN PULL REQUESTS (latest 10)",
        "OPEN ISSUES (latest 10)",
        "BRANCHES (latest 20)",
        "README.md",
        "CONTRIBUTING.md",
        "CI/CD WORKFLOWS",
    ]

    # Combine into one context block for the LLM
    context = "\n\n".join(
        f"=== {label} ===\n{result}"
        for label, result in zip(labels, results)
    )
    return context

def _trim_tool_response(content: str, max_chars: int = 8000) -> str:
    """
    Trim a tool response to prevent context overflow.
    
    GitHub API responses are verbose JSON. We try to parse and summarize,
    falling back to simple truncation if parsing fails.
    """
    if len(content) <= max_chars:
        return content
    
    # Try to parse as JSON and keep only the most useful fields
    try:
        data = json.loads(content)
        
        # If it's a list (e.g. list of PRs, issues, branches), summarize each item
        if isinstance(data, list):
            trimmed = []
            for item in data[:30]:  # cap at 30 items max
                if isinstance(item, dict):
                    # Keep only the fields the agent actually needs
                    summary = {k: v for k, v in item.items() if k in {
                        "number", "title", "state", "user", "author",
                        "created_at", "updated_at", "merged_at", "closed_at",
                        "assignee", "assignees", "labels", "name", "commit",
                        "protected", "body"
                    }}
                    # Trim body text — often huge
                    if "body" in summary and summary["body"]:
                        summary["body"] = str(summary["body"])[:200]
                    # Flatten nested user objects down to just the login name
                    if "user" in summary and isinstance(summary["user"], dict):
                        summary["user"] = summary["user"].get("login", "unknown")
                    trimmed.append(summary)
            return json.dumps(trimmed, indent=None)
        
        # If it's a dict (e.g. get_repository), keep the most useful top-level keys
        if isinstance(data, dict):
            useful_keys = {
                "name", "full_name", "description", "stargazers_count", "forks_count",
                "open_issues_count", "default_branch", "pushed_at", "language",
                "topics", "visibility", "size", "watchers_count"
            }
            trimmed = {k: v for k, v in data.items() if k in useful_keys}
            return json.dumps(trimmed, indent=None)
            
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fallback: hard truncate with a note
    return content[:max_chars] + f"\n[... trimmed — original was {len(content)} chars]"


# ── State ──────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    The shared memory that flows between every node in the graph.

    messages:  Full conversation history. The `add_messages` reducer ensures
               new messages are appended, not overwritten. Without this, the
               agent would forget its tool results after each step.

    repo_url:  The GitHub URL passed in at startup (e.g. "https://github.com/owner/repo").

    report:    The final markdown report string. Populated only when the agent
               finishes its analysis and the extract_report node runs.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    repo_url: str
    report: str | None


# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert GitHub repository analyst. Your job is to produce a \
thorough, structured health report for any repository given its URL.

## Analysis Playbook (follow in this order)

NOTE:: When calling any tool:: request a maximum of 10 items per call (use per_page=10)..

1. **Repository Overview**
   Call `get_repository` with the owner and repo name extracted from the URL.
   Record: stars, forks, open issue count, default branch, last push date, primary language.

2. **Pull Request Audit**
    For each PR, note its title, author, and how many days
   it has been open (calculate from today: 2026-04-18).
   - Open > 30 days → [Warning]
   - Open > 60 days → [Critical]
   - Open ≤ 30 days → [Info]

3. **Issue Tracker Analysis**
   List open issues. Focus on:
   - Issues labeled "bug" unresolved > 14 days → [Critical]
   - Issues with no assignee → [Warning]
   - Report total open count and a summary of common themes.

4. **Branch Hygiene**
   List all branches. Flag any branch with no recent activity (last commit > 90 days ago)
   as stale → [Warning]. Report total branch count.

5. **Code Health Files**
   Use `get_file_contents` to check for each of these paths 1 at a time:
   - `README.md`
   - `.github/workflows` (try listing directory contents)
   - `CONTRIBUTING.md`
   For each: report Present / Missing / Minimal (present but < 100 words).
   if an error occurs, treat it as Missing and continue to next file. do not retry.

6. **Synthesize the Report**
   After gathering all data above, write a complete markdown report with EXACTLY
   these seven sections in this order:

   ## Executive Summary
   2–3 sentences summarizing the repository's overall health and most urgent issues.

   ## Repository Health Score
   **Score: X/10** — one sentence justifying the score.
   Deduct 1 point each for: stale PRs (>30d), unresolved bug backlog, no CI/CD,
   missing README, missing CONTRIBUTING.md, >10 unassigned issues, >5 stale branches.

   ## Open Pull Requests
   | PR # | Title | Author | Age (days) | Status |
   |------|-------|--------|------------|--------|

   ## Issue Tracker Analysis
   Narrative summary of bugs, unassigned issues, and backlog health.

   ## Branch Hygiene
   - Total branches: N
   - Stale branches (>90 days): list them, or "None found"

   ## Code Health Files
   | File | Status | Notes |
   |------|--------|-------|

   ## Recommendations
   Numbered list of 3–5 specific, actionable items. Be concrete — name the PR number,
   issue number, or branch name. Bad: "review open PRs". Good: "Assign a reviewer to
   PR #42 (open 47 days, author: alice)".

## Severity Tags
- [Critical] = requires immediate attention
- [Warning]  = should be addressed soon
- [Info]     = no action required

Start by extracting the owner and repo name from the repository URL in the first message,
then follow the playbook steps in order.
"""


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph(tools: list, owner: str, repo: str):
    """
    Fast two-node graph:
      START → fetch_all (parallel) → synthesize → extract_report → END
    
    No loop needed — we know exactly what data we want upfront.
    """
    llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0)

    async def fetch_all_node(state: AgentState) -> dict:
        """Fetch all GitHub data in parallel — runs in ~5-10 seconds."""
        print("  Fetching all data in parallel...", flush=True)
        context = await run_parallel_analysis(tools, owner, repo)
        # Store as a human message so the LLM sees it as input context
        return {"messages": [HumanMessage(content=context)]}

    def synthesize_node(state: AgentState) -> dict:
        """
        Single LLM call to synthesize all fetched data into the report.
        No tool calls here — just reasoning over the pre-fetched context.
        """
        print("  Synthesizing report...", flush=True)
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = llm.invoke(messages)
        return {"messages": [response]}

    def extract_report(state: AgentState) -> dict:
        last = state["messages"][-1]
        if isinstance(last, AIMessage):
            return {"report": last.content}
        return {"report": "Error: could not extract report."}

    graph = StateGraph(AgentState)
    graph.add_node("fetch_all", fetch_all_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("extract_report", extract_report)

    graph.add_edge(START, "fetch_all")
    graph.add_edge("fetch_all", "synthesize")
    graph.add_edge("synthesize", "extract_report")
    graph.add_edge("extract_report", END)

    return graph.compile()
