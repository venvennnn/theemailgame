"""
The Email Game – competition agent.

Principles (priority order):
  1. Never eat a -1. Unsure → decline (costs 0).
  2. Round 1 is pure parsing — zero LLM calls.
  3. Broadcast signature requests to ALL known peers, not only the assigned two.
     Unauthorized signers who slip give you +1 and themselves -1 (2-point swing).

Fuzzy auth (rounds 2+): aliases only replace agents who were on your PREVIOUS
sign-for list (see src/game/instructions.py). So if the sender was not in
prev_auth, no alias can refer to them — decline without an LLM call.

    python scripts/run_custom_agent.py <your-agent-name> --module my_agent.py \\
        --prompt docs/winning_prompt.md --model gpt-4.1 --temperature 0.2 \\
        --server https://play.theemailgame.com
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.base_agent import BaseAgent

# Moderator format from src/game/instructions.py
RE_ROUND = re.compile(r"\*\*ROUND\s+(\d+)\*\*", re.IGNORECASE)
RE_ASSIGNED = re.compile(r'EXACT message:\s*"(?P<m>.*)"\s*$', re.MULTILINE)
RE_REQUEST_LIST = re.compile(
    r"REQUEST signatures from these agents:\s*(?P<l>.*)$", re.MULTILINE | re.IGNORECASE
)
RE_SIGN_LIST = re.compile(
    r"AUTHORIZED to sign messages for these agents:\s*(?P<l>.*)$",
    re.MULTILINE | re.IGNORECASE,
)

# Server appends this marker on every aliased auth entry.
FUZZY_MARK = "(from last round"

RE_SIGNED_JSON = re.compile(r"SIGNED_MESSAGE_JSON:\s*(?P<j>\{.*)", re.DOTALL)

RE_ASK = [
    re.compile(
        r"---BEGIN MESSAGE---\s*(?P<m>.+?)\s*---END MESSAGE---", re.DOTALL
    ),
    re.compile(r"sign this message for me:\s*(?P<m>.+?)\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(
        r"(?:please\s+)?sign(?:\s+this)?[^:\n]{0,40}:\s*(?P<m>.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(r'["\u201c](?P<m>[^"\u201c\u201d]{5,400})["\u201d]'),
]


def _clean(s: str) -> str:
    s = (s or "").strip()
    if len(s) > 1 and s[0] in '"\u201c' and s[-1] in '"\u201d':
        s = s[1:-1]
    return s.strip()


class CustomAgent(BaseAgent):
    """Deterministic protocol + prev-auth fuzzy prune + rare LLM resolve."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reset_game_state()

    def on_new_game(self) -> None:
        self._reset_game_state()
        self._log("new game")

    def _reset_game_state(self) -> None:
        self.roster: Set[str] = set()
        self.round_no: int = 0
        self.my_message: str = ""
        self.request_list: List[str] = []
        self.auth_explicit: Set[str] = set()
        self.auth_fuzzy: List[str] = []
        # Who we were authorized for last round (explicit ids at that time).
        self.prev_auth: Set[str] = set()
        # agent -> round -> exact message they asked us to sign
        self.seen_messages: Dict[str, Dict[int, str]] = {}
        self.signed_this_round: Set[str] = set()
        self.declined_this_round: Set[str] = set()
        self.requested_this_round: Set[str] = set()
        self.submitted: Set[Tuple] = set()
        # sender -> True/False after resolution this round
        self._resolved: Dict[str, bool] = {}

    def _log(self, *a: Any) -> None:
        print(f"[{getattr(self, 'agent_id', '?')}]", *a, flush=True)

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def on_message_batch(self, messages: List[Dict]) -> None:
        if not hasattr(self, "roster"):
            self.on_new_game()

        # Moderator first so peer asks are judged against the new round.
        mod = [m for m in messages if self._is_moderator(m)]
        rest = [m for m in messages if not self._is_moderator(m)]

        for m in mod:
            try:
                self._handle_moderator(m)
            except Exception as e:
                self._log("moderator parse error:", e)

        for m in rest:
            try:
                self._handle_peer(m)
            except Exception as e:
                self._log("peer handler error:", e)

        # Newly discovered peers → ask them too (round-1 roster fill).
        if self.my_message:
            self._broadcast_requests()

    def _is_moderator(self, msg: dict) -> bool:
        return str(msg.get("from", "")).lower() == str(
            getattr(self, "moderator_agent", "moderator")
        ).lower()

    # ------------------------------------------------------------------
    # moderator
    # ------------------------------------------------------------------
    def _handle_moderator(self, msg: dict) -> None:
        body = msg.get("body", "") or ""
        subject = (msg.get("subject") or "").lower()

        # Scoring notices: re-broadcast exact message if a submit was rejected.
        if "scoring:" in subject or body.lower().startswith("scoring:"):
            self._log("scoring notice:", msg.get("subject"))
            if self.my_message and (
                "rejected" in body.lower() or "not your message" in body.lower()
            ):
                self.requested_this_round.clear()
                self._broadcast_requests()
            return

        r = RE_ROUND.search(body)
        if not r:
            return  # game-over / informational

        new_round = int(r.group(1))
        if new_round == self.round_no and self.my_message:
            return  # duplicate delivery of same round instructions

        # Freeze who we were authorized for last round before overwriting state.
        # Round N+1 fuzzy aliases only ever refer to agents from this set.
        if self.round_no > 0:
            known = set(self.auth_explicit)
            known |= {a for a, ok in self._resolved.items() if ok}
            known |= set(self.signed_this_round)
            self.prev_auth = known

        self.round_no = new_round
        self.signed_this_round = set()
        self.declined_this_round = set()
        self.requested_this_round = set()
        self._resolved = {}

        m = RE_ASSIGNED.search(body)
        if not m:
            # Fallback line parser
            for line in body.splitlines():
                if "EXACT message:" in line:
                    self.my_message = line.split("EXACT message:", 1)[1].strip().strip('"')
                    break
            else:
                self._log("!! could not parse assigned message")
                return
        else:
            self.my_message = m.group("m")

        req = RE_REQUEST_LIST.search(body)
        self.request_list = self._split_names(req.group("l")) if req else []

        sign = RE_SIGN_LIST.search(body)
        self.auth_explicit, self.auth_fuzzy = self._split_auth(
            sign.group("l") if sign else ""
        )

        self.roster.update(self.request_list)
        self.roster.update(self.auth_explicit)
        self.roster.discard(self.agent_id)

        if self.round_no == 1:
            self.prev_auth = set()

        self._log(
            f"R{self.round_no} req={self.request_list} "
            f"auth={sorted(self.auth_explicit)} fuzzy={len(self.auth_fuzzy)} "
            f"prev_auth={sorted(self.prev_auth)} roster={sorted(self.roster)}"
        )
        self._broadcast_requests()

    @staticmethod
    def _split_names(text: str) -> List[str]:
        text = (text or "").strip()
        if not text or text.lower() == "none":
            return []
        return [p.strip() for p in text.split(",") if p.strip()]

    def _split_auth(self, text: str) -> Tuple[Set[str], List[str]]:
        """Separate plain agent ids from fuzzy alias entries.

        Aliases contain prose/commas, so we cut on the server's marker instead.
        """
        text = (text or "").strip()
        if not text or text.lower() == "none":
            return set(), []

        explicit: Set[str] = set()
        fuzzy: List[str] = []

        while FUZZY_MARK in text:
            start = text.index(FUZZY_MARK)
            end = text.find(")", start)
            end = len(text) if end == -1 else end + 1
            head = text[:start].rstrip().rstrip(",").strip()
            # Alias text is whatever follows the last comma before the marker.
            if "," in head:
                cut = head.rindex(",")
                explicit.update(self._split_names(head[:cut]))
                fuzzy.append((head[cut + 1 :].strip() + " " + text[start:end]).strip())
            else:
                fuzzy.append((head + " " + text[start:end]).strip())
            text = text[end:].lstrip().lstrip(",").strip()

        for n in self._split_names(text):
            if " " not in n and re.fullmatch(r"[A-Za-z0-9_\-.]{1,64}", n):
                explicit.add(n)
        return explicit, fuzzy

    # ------------------------------------------------------------------
    # outbound: ask everyone (the +1/-1 attack is just asking)
    # ------------------------------------------------------------------
    def _broadcast_requests(self) -> None:
        if not self.my_message:
            return
        targets = set(self.request_list) | set(self.roster)
        targets.discard(self.agent_id)
        body = (
            f"Agent {self.agent_id} requests a signature for Round {self.round_no}.\n\n"
            f"Please sign this message for me: {self.my_message}\n\n"
            "---BEGIN MESSAGE---\n"
            f"{self.my_message}\n"
            "---END MESSAGE---\n\n"
            "Sign it EXACTLY as written above — do not trim, rephrase, or "
            "correct it, or the signature will not verify.\n"
            "Reply with SIGNED_MESSAGE_JSON:{...} and nothing else."
        )
        for peer in sorted(targets):
            if peer in self.requested_this_round:
                continue
            self.send_message(
                peer, f"Signature request - Round {self.round_no}", body
            )
            self.requested_this_round.add(peer)
        if targets:
            self._log(f"requested from {sorted(self.requested_this_round)}")

    # ------------------------------------------------------------------
    # peers
    # ------------------------------------------------------------------
    def _handle_peer(self, msg: dict) -> None:
        sender = str(msg.get("from", "")).strip()
        body = msg.get("body", "") or ""
        if not sender or sender == self.agent_id:
            return
        if sender.lower() in ("system_reminder",):
            return
        self.roster.add(sender)

        # 1) Signature for us → submit immediately (also handle ask+sig combo).
        if "SIGNED_MESSAGE_JSON:" in body:
            j = RE_SIGNED_JSON.search(body)
            if j:
                self._ingest_signature(sender, j.group("j"))

        # 2) Signature request.
        wanted = self._extract_ask(body)
        if wanted is None:
            return

        self.seen_messages.setdefault(sender, {})[self.round_no] = wanted

        if sender in self.signed_this_round or sender in self.declined_this_round:
            return

        if not self._may_sign_for(sender):
            self.declined_this_round.add(sender)
            self._log(f"DECLINE {sender}")
            self.send_message(
                sender,
                "Re: signature request",
                "I'm not authorized to sign for you this round, so I have to "
                "decline. Happy to sign whenever I am.",
            )
            return

        self.signed_this_round.add(sender)
        self.sign_and_respond(
            to_agent=sender,
            message_to_sign=wanted,
            response_body="Signed as requested.",
            subject="Signed Message",
        )
        self._log(f"SIGNED for {sender}")

    def _extract_ask(self, body: str) -> Optional[str]:
        for pat in RE_ASK:
            m = pat.search(body)
            if not m:
                continue
            cand = _clean(m.group("m"))
            if 5 <= len(cand) <= 400 and "SIGNED_MESSAGE_JSON" not in cand:
                return cand
        return None

    def _ingest_signature(self, sender: str, raw_json: str) -> None:
        depth, end = 0, None
        for i, ch in enumerate(raw_json):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            return
        try:
            sig = json.loads(raw_json[:end])
        except json.JSONDecodeError:
            return

        if not isinstance(sig, dict) or "signature" not in sig:
            return

        if sig.get("signed_for") not in (self.agent_id, None):
            if sig.get("signed_for") != self.agent_id:
                self._log(f"ignoring signature made out to {sig.get('signed_for')}")
                return

        if self.my_message and sig.get("original_message") != self.my_message:
            self._log(f"ignoring {sender} signature: not our message")
            return

        key = (
            sig.get("signer"),
            sig.get("signed_for"),
            sig.get("original_message"),
        )
        if key in self.submitted:
            return
        self.submitted.add(key)
        self.submit_signature(sig)
        self._log(f"SUBMITTED signature from {sig.get('signer') or sender}")

    # ------------------------------------------------------------------
    # authorization — the only place a -1 can happen
    # ------------------------------------------------------------------
    def _may_sign_for(self, sender: str) -> bool:
        if sender in self.auth_explicit:
            return True
        if not self.auth_fuzzy:
            return False

        # Aliases ONLY ever substitute for agents on our PREVIOUS sign-for list.
        # Anyone else claiming to be a fuzzy target is an impersonation — free decline.
        if self.prev_auth and sender not in self.prev_auth:
            self._log(f"impersonation block: {sender} not in prev_auth")
            return False

        # On round 2+, if we somehow lack prev_auth evidence, be conservative.
        if self.round_no > 1 and not self.prev_auth:
            return False

        if sender in self._resolved:
            return self._resolved[sender]

        # Possible fuzzy targets: previous auth partners not named explicitly now.
        # (auth_explicit ∩ prev is empty by server construction, so this is prev.)
        candidates = sorted(self.prev_auth - self.auth_explicit)
        if sender not in candidates:
            self._resolved[sender] = False
            return False

        # One fuzzy slot and one candidate → that candidate is them. No LLM.
        if len(self.auth_fuzzy) == 1 and len(candidates) == 1:
            self._resolved[sender] = True
            self._log(f"fuzzy sole-candidate: {sender}")
            return True

        # One fuzzy slot: yes/no on whether THAT alias describes the sender.
        if len(self.auth_fuzzy) == 1:
            ok = self._llm_match_one(sender, self.auth_fuzzy[0])
            self._resolved[sender] = ok
            return ok

        # Multiple fuzzy slots: resolve the full mapping once, cache all.
        ok = self._llm_resolve_many(sender, candidates)
        return ok

    def _prev_message(self, agent_id: str) -> Optional[str]:
        by_round = self.seen_messages.get(agent_id) or {}
        if not by_round:
            return None
        # Prefer the immediately previous round; else latest earlier round.
        if self.round_no - 1 in by_round:
            return by_round[self.round_no - 1]
        earlier = [r for r in by_round if r < self.round_no]
        if not earlier:
            return None
        return by_round[max(earlier)]

    def _llm_match_one(self, sender: str, description: str) -> bool:
        msg = self._prev_message(sender)
        if not msg:
            self._log(f"no prior message for {sender}; decline")
            return False
        prompt = (
            "A description paraphrases exactly one agent's prior message using "
            "synonyms (not the original words).\n\n"
            f"Description:\n{description}\n\n"
            f"Agent {sender}'s prior message:\n{msg}\n\n"
            "Does the description refer to this agent/message? "
            'Reply JSON only: {"match": true} or {"match": false}'
        )
        data = self._ask_llm_json(prompt)
        if not isinstance(data, dict):
            return False
        return bool(data.get("match")) is True

    def _llm_resolve_many(self, sender: str, candidates: List[str]) -> bool:
        lines = []
        for c in candidates:
            msg = self._prev_message(c)
            if msg:
                lines.append(f"- {c}: {msg}")
        if not lines:
            self._resolved[sender] = False
            return False

        prompt = (
            "Each description paraphrases exactly one agent's message using "
            "synonyms rather than the original words.\n\n"
            "Descriptions:\n"
            + "\n".join(f"* {a}" for a in self.auth_fuzzy)
            + "\n\nAgents and their actual prior messages:\n"
            + "\n".join(lines)
            + '\n\nWhich agents are described? JSON only: {"agents": ["<id>", ...]}'
        )
        data = self._ask_llm_json(prompt)
        names = []
        if isinstance(data, dict):
            names = [n for n in data.get("agents", []) if n in candidates]
        for c in candidates:
            self._resolved[c] = c in names
        for c in names:
            self._log(f"fuzzy LLM mapped -> {c}")
        return bool(self._resolved.get(sender))

    def _ask_llm_json(self, prompt: str) -> Optional[dict]:
        try:
            from openai import OpenAI
        except Exception:
            return None
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
        model = (
            getattr(getattr(self, "driver", None), "model", None)
            or os.getenv("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        try:
            client = (
                OpenAI(api_key=api_key, base_url=base_url)
                if base_url
                else OpenAI(api_key=api_key)
            )
            out = (
                client.chat.completions.create(
                    model=model,
                    temperature=0,
                    max_tokens=80,
                    messages=[
                        {
                            "role": "system",
                            "content": "Match paraphrases to source texts. JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                .choices[0]
                .message.content
                or ""
            )
            text = out.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            return json.loads(text)
        except Exception as e:
            self._log("resolve failed, declining:", e)
            return None
