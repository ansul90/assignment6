"""
clean.py — Wipe the state/ directory between assignment attempts.

Usage:
    python clean.py
"""
import shutil
from pathlib import Path

STATE = Path(__file__).parent / "state"

if STATE.exists():
    shutil.rmtree(STATE)
    print(f"Removed {STATE}")
else:
    print(f"{STATE} does not exist — nothing to clean.")
