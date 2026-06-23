"""
agent6.py — Top-level agent loop for EAGV3 Week 6.

Entry point:
    python agent6.py "your query here"

The loop:
    1. ensure_gateway  – confirm the MCP stdio server is available.
    2. mcp_session     – open a fresh MCP ClientSession per run.
    3. perception      – turn the query + memory hits into ranked Goals.
    4. decision        – pick the next tool-call or inline answer for a Goal.
    5. action          – execute the tool via MCP and persist any artifact.
    6. Repeat until all goals are done or MAX_ITERATIONS reached.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import json

import action
import decision
import memory
import perception
from artifact_store import artifacts
from schemas import Goal, HistoryEntry

# ── constants ─────────────────────────────────────────────────────────────────
MAX_ITERATIONS = 10
_SERVER_PATH = Path(__file__).parent / "mcp_server.py"

# ── logging helpers ───────────────────────────────────────────────────────────
_PAD = 16  # width of the label column


def _label(name: str) -> str:
    """Left-aligned label padded to _PAD characters."""
    tag = f"[{name}]"
    return tag.ljust(_PAD)


def _indent() -> str:
    return " " * _PAD


def _log_perception(goals: list[Goal]) -> None:
    prefix = _label("perception")
    for i, g in enumerate(goals):
        state = "[done]" if g.done else "[open]"
        line = f"{state} {g.text}"
        print(f"{prefix if i == 0 else _indent()}{line}")
        if g.attach_artifact_id:
            print(f"{_indent()}  attach={g.attach_artifact_id}")

# ── gateway / MCP session ─────────────────────────────────────────────────────
_gateway_proc: subprocess.Popen | None = None


def ensure_gateway() -> None:
    """Verify the MCP server script exists and is runnable.

    Each call to `run()` spawns its own fresh stdio process inside
    `mcp_session()`, so this function is mainly a pre-flight check.
    """
    global _gateway_proc
    if not _SERVER_PATH.exists():
        raise FileNotFoundError(f"MCP server not found: {_SERVER_PATH}")
    # If we previously spawned a persistent process, clean up any zombie.
    if _gateway_proc is not None and _gateway_proc.poll() is not None:
        _gateway_proc = None


@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    """Async context manager: spawn the MCP server as a stdio subprocess."""
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(_SERVER_PATH)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session: ClientSession) -> list:
    """Return the raw MCP tool descriptors from the server."""
    resp = await session.list_tools()
    return resp.tools


def mcp_tools_for_decision(mcp_tools: list) -> list[dict]:
    """Convert MCP tool descriptors into the dict schema decision.next_step expects."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema if hasattr(t, "inputSchema") else {},
        }
        for t in mcp_tools
    ]


# ── final answer synthesis ────────────────────────────────────────────────────

_ERROR_PREFIXES = ("[Decision error]", "[ERROR]", "[Error", "[SKIP]")


def _is_error_answer(text: str | None) -> bool:
    return bool(text) and text.startswith(_ERROR_PREFIXES)


def final_answer_from(history: list[HistoryEntry]) -> str:
    """Combine all successful answers from history (one per goal), in order."""
    # Pick the last successful answer per goal_id, preserving goal order.
    per_goal: dict[str, str] = {}
    goal_order: list[str] = []
    for h in history:
        if h.kind != "answer" or not h.text or _is_error_answer(h.text):
            continue
        if h.goal_id not in per_goal:
            goal_order.append(h.goal_id)
        per_goal[h.goal_id] = h.text  # last successful answer wins

    if per_goal:
        if len(per_goal) == 1:
            return next(iter(per_goal.values()))
        return "\n\n".join(per_goal[gid] for gid in goal_order)

    # Fallback: last action descriptor
    for h in reversed(history):
        if h.kind == "action":
            return h.result_descriptor or "Task completed."
    return "No answer produced."


# ── main agent loop ───────────────────────────────────────────────────────────

async def run(query: str) -> str:
    ensure_gateway()
    run_id = uuid.uuid4().hex[:8]
    history: list[HistoryEntry] = []
    prior_goals: list[Goal] = []
    seen_calls: set[str] = set()

    # Durable memory: classify the user's query so facts/preferences
    # in it survive into future runs.
    mem_item = memory.remember(query, source="user_query", run_id=run_id)
    kw_preview = json.dumps(mem_item.keywords[:8])
    print(f"{_label('memory.remember')} classified \"{mem_item.descriptor[:100]}\" as {mem_item.kind}")
    print(f"{_indent()}keywords: {kw_preview}")

    async with mcp_session() as session:
        mcp_tools = await load_tools(session)
        tools = mcp_tools_for_decision(mcp_tools)

        for it in range(1, MAX_ITERATIONS + 1):
            print(f"\n─── iter {it} ───")

            # ── memory ────────────────────────────────────────────────────────
            hits = memory.read(query, history)
            print(f"{_label('memory.read')}{len(hits)} hit{'s' if len(hits) != 1 else ''}")
            for h in hits:
                if h.kind in ("fact", "preference"):
                    text_val = h.value.get("text") or h.descriptor
                    print(f"{_indent()}{h.kind}: \"{text_val[:120]}\"")

            # ── perception ────────────────────────────────────────────────────
            obs = perception.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            _log_perception(obs.goals)

            if obs.all_done:
                print(f"\n[done] all {len(obs.goals)} goal{'s' if len(obs.goals) != 1 else ''} satisfied")
                break

            goal = obs.next_unfinished()

            # ── artifact attachment ───────────────────────────────────────────
            attached = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                data = artifacts.get_bytes(goal.attach_artifact_id)
                attached.append((goal.attach_artifact_id, data))
                print(f"{_label('attach')}{goal.attach_artifact_id} ({len(data):,} bytes)")

            # ── decision ──────────────────────────────────────────────────────
            out = decision.next_step(goal, hits, attached, history, tools)

            if out.answer is not None:
                preview = out.answer[:120].replace("\n", " ")
                is_error = out.answer.startswith(("[Decision error]", "[ERROR]", "[Error"))
                tag = "ERROR" if is_error else "ANSWER"
                print(f"{_label('decision')}{tag}: {preview}{'…' if len(out.answer) > 120 else ''}")
                history.append(HistoryEntry(
                    iter=it, kind="answer",
                    goal_id=goal.id, text=out.answer,
                ))
                if is_error:
                    hint = "(gateway unreachable — is llm_gatewayV3 running on :8101?)" \
                        if "Connection refused" in out.answer else "(will retry next iteration)"
                    print(f"{_indent()}{hint}")
                continue

            # ── action ────────────────────────────────────────────────────────
            try:
                args_str = json.dumps(out.tool_call.arguments)
            except Exception:
                args_str = str(out.tool_call.arguments)

            call_key = f"{out.tool_call.name}:{json.dumps(out.tool_call.arguments, sort_keys=True)}"
            if call_key in seen_calls:
                print(f"{_label('decision')}SKIP: {out.tool_call.name}({args_str}) already called — use artifact")
                history.append(HistoryEntry(
                    iter=it, kind="answer",
                    goal_id=goal.id,
                    text=(
                        f"[SKIP] {out.tool_call.name} with arguments {args_str} was already executed this run. "
                        "The result is stored as an artifact. Use the attached artifact to answer instead of calling the tool again."
                    ),
                ))
                continue
            seen_calls.add(call_key)

            print(f"{_label('decision')}TOOL_CALL: {out.tool_call.name}({args_str})")

            result_text, art_id = await action.execute(session, out.tool_call)
            memory.record_outcome(
                tool_call=out.tool_call,
                result_text=result_text,
                artifact_id=art_id,
                run_id=run_id,
                goal_id=goal.id,
            )
            action_preview = result_text[:120].replace("\n", " ")
            print(f"{_label('action')}→ {action_preview}{'…' if len(result_text) > 120 else ''}")

            history.append(HistoryEntry(
                iter=it, kind="action",
                goal_id=goal.id,
                tool=out.tool_call.name,
                arguments=out.tool_call.arguments,
                result_descriptor=result_text[:300],
                artifact_id=art_id,
            ))

    return final_answer_from(history)


if __name__ == "__main__":
    _query = " ".join(sys.argv[1:]) or "What is the current time in Kolkata?"
    answer = asyncio.run(run(_query))
    print(f"\nFINAL: {answer}")
