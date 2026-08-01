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

# Peers often volunteer prior-round text to help fuzzy resolution.
RE_IDENTITY_NOTE = re.compile(
    r"(?:identity\s+note|in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+text\s+was)"
    r"[^=\n]*?(?:round\s+(?P<r2>\d+)[^=\n]*)?"
    r"""["'](?P<m>[^"']{5,400})["']""",
    re.IGNORECASE | re.DOTALL,
)
RE_IDENTITY_NOTE2 = re.compile(
    r"in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+text\s+was\s*[:=]?\s*"
    r"""["'](?P<m>[^"']{5,400})["']""",
    re.IGNORECASE,
)


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
        # BaseAgent also calls this on reconnect when in_game was false — even
        # mid-match. If we already have round evidence, keep it so fuzzy auth
        # still works; only clear per-round action sets.
        prior = int(getattr(self, "current_round", 0) or 0)
        if prior >= 1 and getattr(self, "seen_messages", None):
            self._log(f"reconnect mid-game (prior marker R{prior}) — keeping evidence")
            self.signed_this_round = set()
            self.declined_this_round = set()
            self.requested_this_round = set()
            self._resolved = {}
            return
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
        # signer ids who already gave us a valid signature this round
        self.got_sig_from: Set[str] = set()
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
        self.got_sig_from = set()
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
            f"R{self.round_no} assigned={self.my_message!r} "
            f"req={self.request_list} auth={sorted(self.auth_explicit)} "
            f"fuzzy={self.auth_fuzzy!r} prev_auth={sorted(self.prev_auth)} "
            f"roster={sorted(self.roster)}"
        )
        # Force outbound immediately — a silent round is a 0.
        self.requested_this_round.clear()
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
        targets = set(self.request_list) | set(self.roster) | set(self.prev_auth)
        targets |= set(self.auth_explicit)
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
        newly = []
        for peer in sorted(targets):
            if peer in self.requested_this_round or peer in self.got_sig_from:
                continue
            try:
                self.send_message(
                    peer, f"Signature request - Round {self.round_no}", body
                )
                self.requested_this_round.add(peer)
                newly.append(peer)
            except Exception as e:
                self._log(f"send_message failed for {peer}:", e)
        if newly:
            self._log(f"requested from {newly} (have sigs from {sorted(self.got_sig_from)})")

    # ------------------------------------------------------------------
    # peers
    # ------------------------------------------------------------------
    def _handle_peer(self, msg: dict) -> None:
        sender = str(msg.get("from", "")).strip()
        body = msg.get("body", "") or ""
        if not sender or sender == self.agent_id:
            return
        # Harness inactivity ping: re-ask anyone who hasn't signed us yet.
        if sender.lower() in ("system_reminder",):
            if self.my_message:
                self._log("inactivity reminder → rebroadcast")
                for peer in list(self.requested_this_round):
                    if peer in self.got_sig_from:
                        continue
                    self.requested_this_round.discard(peer)
                self._broadcast_requests()
            return
        self.roster.add(sender)

        # Learn prior-round texts peers volunteer (helps hard fuzzy paraphrases).
        learned = self._ingest_identity_notes(sender, body)

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

        if sender in self.signed_this_round:
            return

        # If we declined earlier but new identity evidence arrived, retry auth.
        if sender in self.declined_this_round:
            if not learned and sender in self._resolved:
                return
            self.declined_this_round.discard(sender)
            self._resolved.pop(sender, None)

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
        self.declined_this_round.discard(sender)
        self.sign_and_respond(
            to_agent=sender,
            message_to_sign=wanted,
            response_body="Signed as requested.",
            subject="Signed Message",
        )
        self._log(f"SIGNED for {sender}")

    def _ingest_identity_notes(self, sender: str, body: str) -> bool:
        """Store volunteered prior-round messages. Returns True if new evidence."""
        learned = False
        for pat in (RE_IDENTITY_NOTE2, RE_IDENTITY_NOTE):
            for m in pat.finditer(body):
                rnd = m.groupdict().get("r") or m.groupdict().get("r2")
                msg = _clean(m.group("m"))
                if not rnd or not msg or "SIGNED_MESSAGE_JSON" in msg:
                    continue
                rnd_i = int(rnd)
                bucket = self.seen_messages.setdefault(sender, {})
                if bucket.get(rnd_i) != msg:
                    bucket[rnd_i] = msg
                    learned = True
                    self._log(f"identity note {sender} R{rnd_i}: {msg!r}")
        return learned

    def _extract_ask(self, body: str) -> Optional[str]:
        for pat in RE_ASK:
            m = pat.search(body)
            if not m:
                continue
            cand = _clean(m.group("m"))
            # Avoid capturing identity-note lines as the message to sign.
            if cand.lower().startswith("the old ") and "identity note" in (m.string or "").lower():
                pass
            if 5 <= len(cand) <= 400 and "SIGNED_MESSAGE_JSON" not in cand:
                # Prefer BEGIN/END or "sign this message for me" hits over loose quotes.
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
        signer = sig.get("signer") or sender
        self.got_sig_from.add(signer)
        self.submit_signature(sig)
        self._log(f"SUBMITTED signature from {signer}")

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

        # Resolve comparatively across all candidates (yes/no on one agent is
        # too brittle for hard paraphrases like
        # "invitation left unanswered in the evening" ↔ unused dance floor at dusk).
        mapped = self._resolve_fuzzy_mapping(candidates)
        return bool(mapped.get(sender))

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

    def _evidence_lines(self, candidates: List[str]) -> List[str]:
        lines = []
        for c in candidates:
            by_round = self.seen_messages.get(c) or {}
            if not by_round:
                continue
            # Include all prior-round texts we know (R1+R2 help R3 aliases).
            parts = []
            for rnd in sorted(r for r in by_round if r < self.round_no):
                parts.append(f"R{rnd}: {by_round[rnd]}")
            if parts:
                lines.append(f"- {c}: " + " | ".join(parts))
        return lines

    def _resolve_fuzzy_mapping(self, candidates: List[str]) -> Dict[str, bool]:
        """Map each fuzzy description to at most one candidate; cache on self._resolved."""
        # If we already fully answered for every candidate, reuse.
        if candidates and all(c in self._resolved for c in candidates):
            return self._resolved

        lines = self._evidence_lines(candidates)
        if not lines:
            for c in candidates:
                self._resolved[c] = False
            return self._resolved

        n = len(self.auth_fuzzy)
        prompt = (
            "You are resolving fuzzy agent identities in The Email Game.\n"
            "Each DESCRIPTION is a deliberate synonym paraphrase of exactly one "
            "agent's PRIOR-round assigned message. Word overlap may be near zero.\n"
            "Think about meaning: e.g. 'waddling arctic birds'↔penguins; "
            "'water and nostalgia mixing as the day ends'↔fountain + old radio at dusk; "
            "'invitation left unanswered in the evening'↔a dance floor no one uses after dusk.\n\n"
            f"There are {n} description(s). Pick at most one agent id per description. "
            "Use only ids from the evidence. If unsure for a description, omit it.\n\n"
            "Descriptions:\n"
            + "\n".join(f"{i+1}. {a}" for i, a in enumerate(self.auth_fuzzy))
            + "\n\nEvidence (agent id → prior messages):\n"
            + "\n".join(lines)
            + '\n\nJSON only: {"matches": [{"description_index": 1, "agent": "<id>"}]}'
        )
        data = self._ask_llm_json(prompt)
        chosen: Set[str] = set()
        if isinstance(data, dict):
            for item in data.get("matches") or []:
                if not isinstance(item, dict):
                    continue
                agent = item.get("agent")
                if agent in candidates:
                    chosen.add(agent)

        # Fallback: if one description and model returned {"agent": "..."} / {"agents":[...]}
        if not chosen and isinstance(data, dict):
            if data.get("agent") in candidates:
                chosen.add(data["agent"])
            for a in data.get("agents") or []:
                if a in candidates:
                    chosen.add(a)

        for c in candidates:
            self._resolved[c] = c in chosen
        for c in chosen:
            self._log(f"fuzzy mapped -> {c}")
        if not chosen:
            self._log(f"fuzzy unresolved among {candidates}; declining")
        return self._resolved

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
