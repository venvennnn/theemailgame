"""
Play-test your agent locally - one command, runs once, never enters the ladder.

    python scripts/playtest.py my_agent.py                       # vs 3 base LLM opponents
    python scripts/playtest.py my_agent.py --opponent rival.py   # vs your own custom opponent(s)
    python scripts/playtest.py my_agent.py --model gpt-4.1       # realistic-model check

Starts a local server, your agent, and 3 opponents, plays ONE game, prints the
final scores, and shuts everything down. Opponents default to the base LLM
agent; supply --opponent <module.py> (repeatable, up to 3) to test against your
own agents instead - any slots you don't fill are base LLM agents.

This always runs a single game and exits - it can NEVER requeue. Requeue (the
live ladder) only happens on the host's competition server.

Cost: all agents make LLM calls on your API key. Default model is gpt-4.1-mini
to keep iteration cheap; use --model gpt-4.1 for a realistic check.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NUM_OPPONENTS = 3


def main():
    ap = argparse.ArgumentParser(description="Play-test your agent locally (single game, no requeue)")
    ap.add_argument("agent_file", help="path to your agent module, e.g. my_agent.py")
    ap.add_argument("--name", default="myagent", help="your agent's id (default: myagent)")
    ap.add_argument("--opponent", action="append", default=[], metavar="MODULE.py",
                    help="custom agent module to use as an opponent (repeatable, up to 3); "
                         "unfilled slots use the base LLM agent")
    ap.add_argument("--model", default="gpt-4.1-mini",
                    help="model for all agents this run (default: gpt-4.1-mini; use gpt-4.1 for a realistic check)")
    ap.add_argument("--requeue", "--loop", action="store_true", dest="requeue",
                    help="keep playing back-to-back games (local requeue) instead of one game; "
                         "watch your agent play repeatedly. Ctrl+C to stop. Local board, no login.")
    ap.add_argument("--reset", action="store_true",
                    help="clear your local leaderboard / match history / logs before running (start fresh)")
    ap.add_argument("--show-opponents", action="store_true",
                    help="also print opponents' logs in the terminal (default: show only your agent)")
    args = ap.parse_args()

    if args.reset:
        import shutil
        n = 0
        for sub in ("session_results", "agent_logs"):
            d = PROJECT_ROOT / sub
            if d.exists():
                for f in list(d.iterdir()):
                    try:
                        shutil.rmtree(f) if f.is_dir() else f.unlink(); n += 1
                    except Exception:
                        pass
        for f in PROJECT_ROOT.glob("current_game_*.json"):
            try:
                f.unlink(); n += 1
            except Exception:
                pass
        print(f"🧹 Reset: cleared {n} local item(s) (local leaderboard/history/logs).")

    def _exists(p):
        return (PROJECT_ROOT / p).exists() or Path(p).exists()

    if not _exists(args.agent_file):
        print(f"❌ Agent file not found: {args.agent_file}")
        return 1
    if len(args.opponent) > NUM_OPPONENTS:
        print(f"❌ At most {NUM_OPPONENTS} opponents (a game has {NUM_OPPONENTS + 1} agents).")
        return 1
    for opp in args.opponent:
        if not _exists(opp):
            print(f"❌ Opponent file not found: {opp}")
            return 1

    env = {**os.environ, "OPENAI_MODEL": args.model}

    specs = [f"custom:{args.name}:{args.agent_file}"]
    for i in range(NUM_OPPONENTS):
        if i < len(args.opponent):
            specs.append(f"custom:opponent{i+1}:{args.opponent[i]}")
        else:
            specs.append(f"base:opponent{i+1}")

    cmd = [sys.executable, "scripts/arena_cli.py", "session"]
    if args.requeue:
        cmd.append("--loop")
    if not args.show_opponents:
        cmd.append("--quiet-opponents")   # terminal shows only your agent by default
    for s in specs:
        cmd += ["--agent", s]

    opp_desc = ", ".join(args.opponent) if args.opponent else "3 base LLM opponents"
    print(f"Play-testing '{args.name}' ({args.agent_file}) vs {opp_desc} on {args.model}...")
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
