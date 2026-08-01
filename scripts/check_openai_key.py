#!/usr/bin/env python3
"""Verify your LLM access before you build or compete.

Reads OPENAI_API_KEY (and optional OPENAI_BASE_URL / OPENAI_MODEL) from the
environment or a local .env, makes one tiny chat call, and prints a clear
pass/fail. Use this to confirm a host-issued (budget-capped) key + gateway URL
work, or that your own key is set up, before launching an agent.

Usage:
    python scripts/check_openai_key.py [--model gpt-4.1]
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _mask(secret: str) -> str:
    """Show only enough of a key to identify it; never print it in full."""
    if not secret:
        return "(unset)"
    return f"{secret[:6]}…{secret[-4:]}" if len(secret) > 12 else "set"


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Check OpenAI/gateway access")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1"),
                    help="model to test (default: $OPENAI_MODEL or gpt-4.1)")
    args = ap.parse_args()

    key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL") or None

    print("Checking LLM access:")
    print(f"  key:      {_mask(key)}")
    print(f"  base_url: {base_url or 'https://api.openai.com (default)'}")
    print(f"  model:    {args.model}")

    if not key:
        print("\n[FAIL] OPENAI_API_KEY is not set. Put it in .env or your shell env.")
        return 1

    try:
        import openai
    except ImportError:
        print("\n[FAIL] openai package not installed. Run: pip install -r requirements.txt")
        return 1

    try:
        client = openai.OpenAI(api_key=key, base_url=base_url, timeout=30)
        resp = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=5,
        )
        reply = (resp.choices[0].message.content or "").strip()
        print(f"\n[PASS] Model responded: {reply!r}. You're ready to go.")
        return 0
    except Exception as e:
        msg = str(e)
        print(f"\n[FAIL] Test call failed: {msg}")
        low = msg.lower()
        if "auth" in low or "api key" in low or "401" in low:
            print("  -> The key looks invalid. Re-copy the key you were given.")
        elif "model" in low and ("not" in low or "exist" in low or "access" in low):
            print(f"  -> '{args.model}' isn't available on this key. Try --model gpt-4.1-mini, "
                  "or ask the host which models are allowed.")
        elif "budget" in low or "quota" in low or "exceeded" in low or "429" in low:
            print("  -> Budget or rate limit hit. If this is a host-issued key, your "
                  "budget may be spent; ask the organizers.")
        elif base_url:
            print("  -> Could not reach the gateway. Check OPENAI_BASE_URL with the host.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
