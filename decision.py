"""
decision.py — Decision module for EAGV3 Week 6.

Responsibilities:
1. Receive Goal, memory hits, attached artifacts, history, and available tools.
2. Build system prompt and user message context.
3. Call LLM gateway with native tool-use, following LLM_ORDER for provider selection.
4. Return DecisionOutput (typed Pydantic contract from schemas.py).

All inputs and outputs use typed Pydantic contracts from schemas.py.
"""
from __future__ import annotations

from typing import Any

from llm_gatewayV3.client import LLM
from schemas import DecisionOutput, Goal, HistoryEntry, MemoryItem, ToolCall

# ── LLM Client singleton ──────────────────────────────────────────────────────
_llm = LLM()


# ── helpers ───────────────────────────────────────────────────────────────────

def _remap_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tools (with 'parameters') to the 'input_schema' key the gateway expects."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {})
        }
        for t in mcp_tools
    ]


def _build_system() -> str:
    return (
        "You are the Decision module of an intelligent agent. Your job is to decide the next step "
        "to satisfy the current goal.\n"
        "You are provided with:\n"
        "1. The current Goal to satisfy.\n"
        "2. Relevant Memory Hits (past queries, tool outcomes, and facts).\n"
        "3. Attached Artifacts (raw content previews from prior tool executions).\n"
        "4. Recent Run History (actions and answers in the current session).\n\n"
        "You have access to a set of MCP tools. Decide whether to call one tool or return an inline answer.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "- If you need information from the web, a file, a URL, or a calculation, call the appropriate tool.\n"
        "- You can only call EXACTLY ONE tool at a time.\n"
        "- If you already have all the information needed to answer the goal directly, return an inline answer — do NOT call a tool.\n"
        "- ATTACHED ARTIFACTS contain the actual fetched content already embedded in this prompt. "
        "Read from them directly and return an inline answer. NEVER call fetch_url, read_file, or any "
        "other tool just to re-read content that is already present in ATTACHED ARTIFACTS.\n"
        "- NEVER pass artifact handles (art:xxxx) as arguments to any tool. They are not file paths or URLs.\n"
        "- Always prefer accuracy and completeness.\n\n"
        "REMINDER / CALENDAR TASKS:\n"
        "- There is NO calendar integration. When asked to 'create a reminder', 'set a reminder', or "
        "'add a calendar event', ALWAYS use the create_file tool to write a plain-text .txt file.\n"
        "- Use the path format: <descriptive_name>.txt  (file goes directly in the sandbox root, no subdirectory)\n"
        "- The file content must contain ONLY the key fact and the reminder dates — keep it brief.\n"
        "  Example content for a birthday reminder:\n"
        "    Mom's birthday: 15 May 2026\n"
        "    Reminder (2 weeks before): 1 May 2026\n"
        "- Do NOT apologise for lacking a calendar tool. Just write the file.\n\n"
        "RECALL / LOOKUP TASKS:\n"
        "- When the goal is to answer a question about a previously stored fact (e.g. 'When is X?', "
        "'What date is Y?'), prefer reading from the sandbox file over relying on memory alone.\n"
        "- Step 1: call list_dir({\"path\": \".\"}) to see what files exist in the sandbox.\n"
        "- Step 2: if a relevant file is listed (e.g. a .txt file whose name matches the topic), "
        "call read_file({\"path\": \"<filename>\"}) to retrieve its content.\n"
        "- Step 3: answer from the file content.\n"
        "- Only skip to an inline answer if no relevant file is found after listing the directory."
    )


def _build_user_message(
    goal: Goal,
    hits: list[MemoryItem],
    attached: list[tuple[str, bytes]],
    history: list[HistoryEntry],
) -> str:
    # 1. Goal
    prompt = f"CURRENT GOAL:\n{goal.text}\n\n"

    # 2. Memory Hits (top 5)
    prompt += "RELEVANT MEMORIES:\n"
    if hits:
        for i, entry in enumerate(hits[:5]):
            preview = (
                entry.value.get("result_preview") or
                entry.value.get("text") or ""
            )[:300]
            prompt += f"{i+1}. [{entry.kind}] {entry.descriptor}\n"
            if preview:
                prompt += "".join(f"   {line}\n" for line in preview.splitlines())
    else:
        prompt += "None.\n"
    prompt += "\n"

    # 3. Attached artifacts (decode bytes, skip nav preamble, show 10000 chars of body)
    # Wikipedia and similar pages have 2–3 KB of navigation menus before the article
    # body. Skipping the first 2000 chars and taking the next 10000 ensures the LLM
    # sees actual article content rather than sidebar/menu boilerplate.
    ARTIFACT_SKIP = 2000
    ARTIFACT_WINDOW = 15000
    prompt += "ATTACHED ARTIFACTS:\n"
    if attached:
        for art_id, data in attached:
            try:
                content = data.decode("utf-8", errors="replace")
            except Exception:
                content = f"<Binary data: {len(data)} bytes>"
            if len(content) > ARTIFACT_SKIP + ARTIFACT_WINDOW:
                trimmed = content[ARTIFACT_SKIP:ARTIFACT_SKIP + ARTIFACT_WINDOW]
                trimmed += "\n... [TRUNCATED] ..."
            else:
                trimmed = content
            prompt += f"[ARTIFACT {art_id}]\n{trimmed}\n\n"
    else:
        prompt += "None.\n\n"

    # 4. Recent history (last 8 entries)
    prompt += "RECENT SESSION HISTORY:\n"
    if history:
        for h in history[-8:]:
            if h.kind == "answer":
                prompt += f"Iter {h.iter} [Answer] (Goal {h.goal_id}): {h.text}\n"
            elif h.kind == "action":
                prompt += (
                    f"Iter {h.iter} [Action] (Goal {h.goal_id}): tool='{h.tool}' "
                    f"args={h.arguments}\n"
                    f"  Result: {h.result_descriptor}\n"
                )
    else:
        prompt += "No history yet.\n"

    prompt += "\nDecide: call a tool OR provide an inline answer."
    return prompt


# ── public API ────────────────────────────────────────────────────────────────

def next_step(
    goal: Goal,
    hits: list[MemoryItem],
    attached: list[tuple[str, bytes]],
    history: list[HistoryEntry],
    mcp_tools: list[dict],
) -> DecisionOutput:
    """Determine the next step to satisfy the goal: call a tool or return an inline answer."""
    gateway_tools = _remap_tools(mcp_tools)

    try:
        resp = _llm.chat(
            prompt=_build_user_message(goal, hits, attached, history),
            system=_build_system(),
            tools=gateway_tools,
            tool_choice="auto",
            temperature=0.0,
        )

        tool_calls = resp.get("tool_calls", [])
        if tool_calls:
            tc = tool_calls[0]
            # Gateway returns tool_calls as plain dicts (JSON-deserialised)
            if isinstance(tc, dict):
                tc_name = tc["name"]
                tc_args = tc.get("arguments", {})
            else:
                tc_name = tc.name
                tc_args = tc.arguments
            return DecisionOutput(
                answer=None,
                tool_call=ToolCall(name=tc_name, arguments=tc_args),
            )

        return DecisionOutput(
            answer=resp.get("text") or "No answer produced.",
            tool_call=None,
        )

    except Exception as exc:
        return DecisionOutput(
            answer=f"[Decision error] {exc}",
            tool_call=None,
        )
