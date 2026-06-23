# EAGV3 Week 6 — Cognitive Agent with Memory

A multi-layer cognitive agent that decomposes natural-language queries into sequential goals, uses durable memory across runs, calls MCP tools to take actions, and synthesises a final answer — all driven through a local LLM gateway.

---

## Architecture

```
User Query
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  agent6.py  (orchestration loop, max 10 iterations)          │
│                                                              │
│  memory.remember()  ──► state/memory.json                    │
│                                                              │
│  ┌──────────┐   ┌────────────┐   ┌──────────┐   ┌────────┐  │
│  │ Memory   │──►│ Perception │──►│ Decision │──►│ Action │  │
│  │ memory.py│   │perception.py   │decision.py   │action.py  │
│  └──────────┘   └─────┬──────┘   └────┬─────┘   └────────┘  │
│                        │              │                      │
│                        └──────┬───────┘                      │
│                               ▼                              │
│                        LLM Gateway V3                        │
│                       (llm_gatewayV3/)                       │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
MCP Server  (mcp_server.py — stdio, sandboxed tools)
```

### Cognitive Layers

| Module | Role |
|---|---|
| `memory.py` | Keyword-ranked durable store (`state/memory.json`). Persists facts, preferences, and tool outcomes across runs. |
| `perception.py` | Decomposes the query into sequential `Goal` objects via LLM; marks goals done by inspecting history; attaches relevant artifacts to the next open goal. |
| `decision.py` | Given the current goal + memory hits + artifacts + history, calls the LLM to decide: invoke one MCP tool OR return an inline answer. |
| `action.py` | Executes the MCP tool call; spills responses > 4 KB into the in-process `ArtifactStore` and returns a short descriptor. |
| `artifact_store.py` | In-memory byte-blob store (keyed `art:<id>`); shared between `action.py` and `agent6.py` without circular imports. |

### Supporting Infrastructure

| Component | Description |
|---|---|
| `llm_gatewayV3/` | Local HTTP gateway that routes to Cerebras / NVIDIA / GitHub / OpenRouter / Gemini / Groq / Ollama. Handles caching, routing, and provider fallback. |
| `mcp_server.py` | FastMCP stdio server exposing 9 tools (see below). Sandboxes all file operations under `./sandbox/`. |
| `schemas.py` | Pydantic v2 contracts for all inter-module data (`MemoryItem`, `Goal`, `Observation`, `HistoryEntry`, `ToolCall`, `DecisionOutput`). |

---

## MCP Tools

| Tool | Description |
|---|---|
| `web_search` | Tavily (primary) / DuckDuckGo (fallback), hard-capped at 5 results |
| `fetch_url` | Fetches clean markdown from any URL via crawl4ai (headless Chromium) |
| `get_time` | Current time in any IANA timezone |
| `currency_convert` | Currency conversion via frankfurter.dev |
| `read_file` | Read a UTF-8 file from `sandbox/` |
| `list_dir` | List a directory inside `sandbox/` |
| `create_file` | Create a new file in `sandbox/` (auto-creates parent directories) |
| `update_file` | Overwrite an existing file in `sandbox/` |
| `edit_file` | Find-and-replace inside a `sandbox/` file |

---

## Setup

### Prerequisites

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) package manager

### Install

```bash
cd assignment6
uv sync
```

### Configure API keys

**`llm_gatewayV3/.env`** — LLM provider keys (at least one required):

```env
GEMINI_API_KEY=...
NVIDIA_API_KEY=...
GROQ_API_KEY=...
CEREBRAS_API_KEY=...
OPEN_ROUTER_API_KEY=...
GITHUB_ACCESS_TOKEN=...

# Provider routing order (first healthy provider wins)
LLM_ORDER=cerebras,nvidia,github,openrouter,gemini,groq,ollama
```

**`.env`** — MCP server keys:

```env
TAVILY_API_KEY=...    # optional; DuckDuckGo is used as fallback
```

---

## Running

### 1. Start the LLM gateway (keep this running in a separate terminal)

```bash
uv run python -m llm_gatewayV3.main
```

### 2. Run a query

```bash
uv run python agent6.py "your query here"
```

### 3. Use the demo script

```bash
./demo.sh <n>
```

| Query | Description | Memory |
|---|---|---|
| `1` | Claude Shannon biography + key contributions | cleared |
| `2` | Tokyo weekend activities + Saturday weather | cleared |
| `3` | Remember mom's birthday (15 May 2026) + create reminder file | cleared |
| `4` | Recall mom's birthday from memory + sandbox file | **preserved** (run `3` first) |
| `3+4` | Run 3 then 4 back-to-back (memory flows through) | — |
| `5` | Search asyncio best practices, fetch top 3, synthesise | cleared |

---

## Console Output Format

Each run prints a structured trace:

```
[memory.remember]  classified "..." as fact
                   keywords: [...]

─── iter 1 ───
[memory.read]   N hits
                fact: "..."
[perception]    [open] Goal text
                [done] Another goal
[decision]      TOOL_CALL: tool_name({"arg": "val"})
[action]        → result or [artifact art:xxxx, N bytes]

─── iter 2 ───
...

[done] all N goals satisfied

FINAL: ...
```

---

## Project Structure

```
assignment6/
├── agent6.py              # Orchestration loop
├── memory.py              # Durable keyword-ranked memory store
├── perception.py          # Goal decomposition + artifact attachment
├── decision.py            # LLM-driven tool/answer selection
├── action.py              # MCP tool dispatch + artifact spilling
├── artifact_store.py      # In-process byte-blob store
├── mcp_server.py          # FastMCP stdio server (9 tools)
├── schemas.py             # Pydantic v2 contracts
├── demo.sh                # Predefined demo queries
├── clean.py               # Resets state/memory.json and sandbox/
├── pyproject.toml         # Dependencies (uv)
├── .env                   # TAVILY_API_KEY
├── state/
│   └── memory.json        # Durable memory store (JSON array)
├── sandbox/               # MCP file tool working directory
└── llm_gatewayV3/         # Local LLM gateway
    ├── main.py            # FastAPI server (default port 8101)
    ├── router.py          # Provider selection logic
    ├── providers.py       # Gemini / NVIDIA / Groq / Cerebras / etc.
    ├── client.py          # LLM() client used by perception + decision
    ├── cache.py           # Response caching
    ├── db.py              # SQLite-backed request log
    └── .env               # API keys + model overrides
```

---

## Resetting State

```bash
uv run python clean.py        # clears state/memory.json and sandbox/
# or manually:
echo '[]' > state/memory.json
rm -rf sandbox/*
```
