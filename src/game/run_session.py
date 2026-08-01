"""Run a single game session as its own process.

Spawned by the email server (one process per concurrent game). Because each game
runs in a separate process, the orchestration's module-level state (round
history in runtime, the scoring dedup set) is naturally isolated per game, so
concurrent games cannot corrupt each other. The process talks to the shared
email server over HTTP and exits when its one game is done.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.game.service import start_session


def main():
    ap = argparse.ArgumentParser(description="Run one game session in its own process")
    ap.add_argument("--game-id", required=True, help="server-assigned session id (e.g. arena_...)")
    ap.add_argument("--agents", required=True, help="comma-separated agent ids in this game")
    ap.add_argument("--server", default="http://127.0.0.1:8000", help="email server URL")
    args = ap.parse_args()

    agent_ids = [a.strip() for a in args.agents.split(",") if a.strip()]
    print(f"[run_session] game {args.game_id} with agents {agent_ids}")
    start_session(agent_ids, session_id=args.game_id)


if __name__ == "__main__":
    main()
