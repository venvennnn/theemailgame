#!/usr/bin/env python3
"""
Run a custom The Email Game agent from a user-supplied Python file.

Usage:
    python scripts/run_custom_agent.py agent_id "Display Name" --module path/to/my_agent.py
    python scripts/run_custom_agent.py agent_id "Display Name" --module path/to/my_agent.py --server https://...

The module must define a class named CustomAgent that subclasses BaseAgent.
"""

import sys
import os
import argparse
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.base_agent import BaseAgent


def load_custom_agent_class(module_path: str):
    """Dynamically load a CustomAgent class from a user-supplied file."""
    path = Path(module_path).resolve()

    if not path.exists():
        print(f"❌ Module file not found: {path}")
        sys.exit(1)

    if path.suffix != ".py":
        print(f"❌ Module must be a .py file, got: {path.suffix}")
        sys.exit(1)

    # Load the module
    spec = importlib.util.spec_from_file_location("custom_agent_module", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"❌ Failed to import {path.name}: {e}")
        sys.exit(1)

    # Find the CustomAgent class
    if not hasattr(mod, "CustomAgent"):
        print(f"❌ {path.name} must define a class named 'CustomAgent'")
        sys.exit(1)

    cls = mod.CustomAgent

    # Validate it subclasses BaseAgent
    if not (isinstance(cls, type) and issubclass(cls, BaseAgent)):
        print(f"❌ CustomAgent must subclass BaseAgent")
        sys.exit(1)

    print(f"✅ Loaded CustomAgent from {path.name}")
    return cls


def main():
    parser = argparse.ArgumentParser(description="Run a custom The Email Game agent")
    parser.add_argument("agent_id", help="Your agent name: the public identity shown on the leaderboard and used in-game")
    parser.add_argument("username", nargs="?", default=None,
                        help="Optional local display label (defaults to your agent name; not shown publicly)")
    parser.add_argument("--module", required=True, help="Path to your custom agent .py file")
    parser.add_argument("--server", default=os.environ.get("INBOX_ARENA_SERVER", "http://localhost:8000"), help="Email server URL")
    parser.add_argument("--prompt", default=None, help="Path to a system prompt file (optional - your agent may ignore this)")
    parser.add_argument("--model", default=None, help="OpenAI model to use")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature 0.0-2.0 (default: 1.0)")
    parser.add_argument("--dev", action="store_true", help="Enable development mode")
    args = parser.parse_args()

    agent_class = load_custom_agent_class(args.module)

    print(f"Starting custom agent {args.agent_id}")

    agent = agent_class(
        args.agent_id,
        args.username,
        email_server_url=args.server,
        dev_mode=args.dev,
        prompt_file=args.prompt,
        model=args.model,
        temperature=args.temperature,
    )

    try:
        agent.run_sync()
    except KeyboardInterrupt:
        print("\nShutting down agent...")
        agent.stop()
        import asyncio
        try:
            asyncio.run(agent.disconnect_gracefully())
        except Exception:
            pass


if __name__ == "__main__":
    main()
