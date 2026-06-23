#!/usr/bin/env bash
# demo.sh — Run a predefined agent query by number.
# Usage:  ./demo.sh 1        — single query, memory cleared first
#         ./demo.sh 2        — single query, memory cleared first
#         ./demo.sh 3        — Run 1: remember mom's birthday + create reminders (clears memory)
#         ./demo.sh 4        — Run 2: recall mom's birthday (keeps memory from run 3)
#         ./demo.sh 3+4      — Run both 3 then 4 in sequence (memory preserved between them)

set -e
cd "$(dirname "$0")"

Q1="Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."
Q2="Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."
Q3="My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder for two weeks before and on the day."
Q4="When is mom's birthday?"
Q5="Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on."

_run_query() {
  local num="$1"
  local query="$2"
  local clear_mem="${3:-yes}"   # "yes" = clear memory before running

  if [[ "$clear_mem" == "yes" ]]; then
    echo '[]' > state/memory.json
    echo "(memory cleared)"
  else
    echo "(memory preserved from previous run)"
  fi
  echo ""
  echo "=== Query ${num} ==="
  echo "$query"
  echo ""
  uv run python agent6.py "$query"
}

case "${1}" in
  1)   _run_query 1 "$Q1" yes ;;
  2)   _run_query 2 "$Q2" yes ;;
  3)   _run_query 3 "$Q3" yes ;;
  4)   _run_query 4 "$Q4" no  ;;   # intentionally keeps memory so run 3's fact is visible
  5)   _run_query 5 "$Q5" yes ;;
  3+4)
    _run_query 3 "$Q3" yes
    echo ""
    echo "──────────────────────────────────────────"
    echo ""
    _run_query 4 "$Q4" no
    ;;
  *)
    echo "Usage: $0 <query>"
    echo "  1   — Claude Shannon biography + contributions  (memory cleared)"
    echo "  2   — Tokyo weekend activities + Saturday weather  (memory cleared)"
    echo "  3   — Remember mom's birthday + create reminders  (memory cleared)"
    echo "  4   — Recall mom's birthday  (keeps memory — run 3 first)"
    echo "  3+4 — Run 3 then 4 back-to-back (memory flows through)"
    echo "  5   — Search asyncio best practices, fetch top 3, synthesise  (memory cleared)"
    exit 1
    ;;
esac
