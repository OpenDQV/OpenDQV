#!/usr/bin/env python3
"""
Grok consultation helper for Claude Code sessions.

Usage:
    python scripts/grok_consult.py "Your question here"
    python scripts/grok_consult.py "Your question" --model grok-3

Requires XAI_API_KEY environment variable.
The 'ClaudeCodeAccess' key from ~/Documents/github-info.txt should be
set as XAI_API_KEY in ~/.bashrc — never hardcoded here.

Called by Claude Code during roundtable sessions when Grok's perspective
is needed as a domain expert / strategic reviewer.
"""

import json
import os
import sys
import urllib.request
import urllib.error

DEFAULT_MODEL = "grok-3"
API_URL = "https://api.x.ai/v1/chat/completions"

SYSTEM_PROMPT = """You are Grok, participating as a domain expert and strategic reviewer
in OpenDQV development roundtables. OpenDQV is an open-source, contract-driven
data quality validation platform — the write-time enforcement layer (Layer 1) that
blocks bad data before it enters any system.

Key context:
- OpenDQV is MIT-licensed, 43 production contracts, v1.2.3
- Layer 1 only: validate individual records at write time via API
- The real moat: governance (maker-checker, hash-chained audit trail, lifecycle)
- Positioning: "The bouncer at the door. Nothing else."
- Founder: Sunny Sharma, 20yr domain expert, British-Indian, solo maintainer
- Your previous contributions: positioning strategy, Postgres doc review (9.8/10),
  "useful not sticky" philosophy, compute cost angle, migration unblocking story

Be direct, specific, and constructively critical. Short answers preferred unless
depth is warranted. You are a trusted advisor, not a yes-machine."""


def consult(question: str, model: str = DEFAULT_MODEL) -> str:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        return "ERROR: XAI_API_KEY not set. Add it to ~/.bashrc"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.7,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "OpenDQV-GrokConsult/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return f"ERROR: HTTP {e.code} — {body}"
    except Exception as exc:
        return f"ERROR: {exc}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/grok_consult.py 'Your question here'")
        sys.exit(1)

    model = DEFAULT_MODEL
    args = sys.argv[1:]
    if "--model" in args:
        idx = args.index("--model")
        model = args[idx + 1]
        args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]

    question = " ".join(args)
    print(consult(question, model))
