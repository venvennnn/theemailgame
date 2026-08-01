#!/usr/bin/env python3
"""Submit your agent code for prize verification.

    python scripts/submit_code.py --name <your-agent-name> --server https://play.theemailgame.com

Zips the files that make up your agent (my_agent.py by default; add more with
--files) and posts them to the competition server, authenticated with your
issued key (OPENAI_API_KEY). You get back a sha256 receipt with a server-side
timestamp. Resubmit as often as you like - the LAST submission before the
deadline (competition end + 1 hour) is the one that counts. Required for
prize winners; their submitted code is checked against how their agent
actually played.
"""
import argparse
import base64
import io
import json
import os
import sys
import zipfile

import requests


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="your agent name (from your handout)")
    ap.add_argument("--server", default=os.environ.get("INBOX_ARENA_SERVER",
                                                       "https://play.theemailgame.com"))
    ap.add_argument("--files", nargs="*", default=["my_agent.py"],
                    help="files to include (default: my_agent.py)")
    args = ap.parse_args()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in args.files:
            if not os.path.isfile(f):
                print(f"missing file: {f}")
                return 1
            z.write(f, os.path.basename(f))
    raw = buf.getvalue()

    payload = {
        "agent_id": args.name,
        "entry_key": os.environ.get("OPENAI_API_KEY", ""),
        "filename": f"{args.name}_code.zip",
        "content_b64": base64.b64encode(raw).decode(),
    }
    # requests, not urllib: it is the HTTP stack every agent already runs on,
    # with a CA bundle that works where a stale system store does not.
    r = requests.post(args.server.rstrip("/") + "/submit_code", json=payload, timeout=30)
    if r.status_code != 200:
        print(f"REJECTED ({r.status_code}): {r.text[:300]}")
        return 1
    res = r.json()
    print("SUBMITTED.")
    print(f"  receipt sha256: {res['sha256']}")
    print(f"  server time:    {res['received_utc']} UTC")
    print("Keep this receipt. Resubmitting later replaces it (last one counts).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
