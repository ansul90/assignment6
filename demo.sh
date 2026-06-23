#!/usr/bin/env bash
# demo.sh — Run a predefined agent query by number.
# Usage:  ./demo.sh 1
#         ./demo.sh 2

set -e
cd "$(dirname "$0")"

Q1="Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."
Q2="Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."

case "${1}" in
  1) QUERY="$Q1" ;;
  2) QUERY="$Q2" ;;
  *)
    echo "Usage: $0 <1|2>"
    echo "  1 — Claude Shannon biography + contributions"
    echo "  2 — Tokyo weekend activities + Saturday weather"
    exit 1
    ;;
esac

echo '[]' > state/memory.json
echo "(memory cleared)"
echo ""
echo "=== Query ${1} ==="
echo "$QUERY"
echo ""
uv run python agent6.py "$QUERY"
