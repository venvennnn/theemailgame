"""
The Email Game – competition agent (Competition 2 playbook).

What winners did (from the Comp 2 recap):
  1. PROOF — quote exact text + machine-checkable evidence (base64) in requests.
  2. WORKHORSE — chase every outstanding signature; never let a round go quiet.
  3. RECIPROCITY — sign authorized requests first, then ask for the return.
  4. DEFENSE — authorization ONLY from moderator protocol mail. Ignore
     "authorization updates", system overrides, and prompt injection in bodies.
  5. BROADCAST — ask the whole table for your signature (uncapped +1s).

Fuzzy auth (rounds 2+): aliases only replace agents on your PREVIOUS sign-for
list. If the sender was not in prev_auth, decline with no LLM call.

    python scripts/run_custom_agent.py <your-agent-name> --module my_agent.py \\
        --prompt docs/winning_prompt.md --model gpt-4.1 --temperature 0.2 \\
        --server https://play.theemailgame.com
"""

from __future__ import annotations

import base64
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

FUZZY_MARK = "(from last round"
RE_SIGNED_JSON = re.compile(r"SIGNED_MESSAGE_JSON:\s*(?P<j>\{.*)", re.DOTALL)

RE_ASK = [
    re.compile(r"---BEGIN MESSAGE---\s*(?P<m>.+?)\s*---END MESSAGE---", re.DOTALL),
    re.compile(
        r"sign this (?:EXACT )?(?:text|message)(?: for me)?(?: character-for-character)?\s*:\s*"
        r"""['"](?P<m>.+?)['"]""",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"sign this message for me:\s*(?P<m>.+?)\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(
        r"(?:please\s+)?sign(?:\s+this)?[^:\n]{0,40}:\s*(?P<m>.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(r'["\u201c](?P<m>[^"\u201c\u201d]{5,400})["\u201d]'),
]

RE_IDENTITY_NOTE2 = re.compile(
    r"in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+text\s+was\s*[:=]?\s*"
    r"""["'](?P<m>[^"']{5,400})["']""",
    re.IGNORECASE,
)
RE_IDENTITY_NOTE = re.compile(
    r"(?:identity\s+note|in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+text\s+was)"
    r"[^=\n]*?(?:round\s+(?P<r2>\d+)[^=\n]*)?"
    r"""["'](?P<m>[^"']{5,400})["']""",
    re.IGNORECASE | re.DOTALL,
)

# Peer-body social engineering — never treat these as authorization.
_ATTACK_MARKERS = (
    "authorization update",
    "authorized list has been revised",
    "permitted signing list",
    "round authorization",
    "you are cleared to sign",
    "you're cleared to sign",
    "you are now authorized",
    "you're now authorized",
    "system override",
    "ignore all previous",
    "ignore previous instructions",
    "moderator coordination",
    "[system",
    "fake tool",
    "sign_and_respond(",
)


def _clean(s: str) -> str:
    s = (s or "").strip()
    if len(s) > 1 and s[0] in '"\u201c' and s[-1] in '"\u201d':
        s = s[1:-1]
    return s.strip()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class CustomAgent(BaseAgent):
    """Proof + workhorse chase + reciprocity + moderator-only auth."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reset_game_state()

    def on_new_game(self) -> None:
        prior = int(getattr(self, "current_round", 0) or 0)
        if prior >= 1 and getattr(self, "seen_messages", None):
            self._log(f"reconnect mid-game (prior marker R{prior}) — keeping evidence")
            self.signed_this_round = set()
            self.declined_this_round = set()
            self.requested_this_round = set()
            self.request_count = {}
            self.got_sig_from = set()
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
        self.prev_auth: Set[str] = set()
        # agent -> round -> exact message they asked us to sign
        self.seen_messages: Dict[str, Dict[int, str]] = {}
        self.signed_this_round: Set[str] = set()
        self.declined_this_round: Set[str] = set()
        self.requested_this_round: Set[str] = set()
        # How many times we have asked each peer this round (chase hard).
        self.request_count: Dict[str, int] = {}
        self.submitted: Set[Tuple] = set()
        self.got_sig_from: Set[str] = set()
        self._resolved: Dict[str, bool] = {}
        self.msgs_this_game: int = 0

    def _log(self, *a: Any) -> None:
        print(f"[{getattr(self, 'agent_id', '?')}]", *a, flush=True)

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def on_message_batch(self, messages: List[Dict]) -> None:
        if not hasattr(self, "roster"):
            self.on_new_game()

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

        if self.my_message:
            self._chase_outstanding(force_followup=False)

    def _is_moderator(self, msg: dict) -> bool:
        # ONLY the protocol from-field. Never trust body claims of being moderator.
        return str(msg.get("from", "")).lower() == str(
            getattr(self, "moderator_agent", "moderator")
        ).lower()

    # ------------------------------------------------------------------
    # moderator — sole source of truth for assignments / auth
    # ------------------------------------------------------------------
    def _handle_moderator(self, msg: dict) -> None:
        body = msg.get("body", "") or ""
        subject = (msg.get("subject") or "").lower()

        if "scoring:" in subject or body.lower().startswith("scoring:"):
            self._log("scoring notice:", msg.get("subject"))
            if self.my_message and (
                "rejected" in body.lower() or "not your message" in body.lower()
            ):
                # Wrong text somehow — re-chase with proof payload.
                self.request_count.clear()
                self.requested_this_round.clear()
                self._chase_outstanding(force_followup=True)
            return

        r = RE_ROUND.search(body)
        if not r:
            return

        new_round = int(r.group(1))
        if new_round == self.round_no and self.my_message:
            return

        if self.round_no > 0:
            known = set(self.auth_explicit)
            known |= {a for a, ok in self._resolved.items() if ok}
            known |= set(self.signed_this_round)
            self.prev_auth = known

        self.round_no = new_round
        self.signed_this_round = set()
        self.declined_this_round = set()
        self.requested_this_round = set()
        self.request_count = {}
        self.got_sig_from = set()
        self._resolved = {}

        m = RE_ASSIGNED.search(body)
        if not m:
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
        self._chase_outstanding(force_followup=True)

    @staticmethod
    def _split_names(text: str) -> List[str]:
        text = (text or "").strip()
        if not text or text.lower() == "none":
            return []
        return [p.strip() for p in text.split(",") if p.strip()]

    def _split_auth(self, text: str) -> Tuple[Set[str], List[str]]:
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
    # outbound: proof-first + workhorse chase
    # ------------------------------------------------------------------
    def _targets(self) -> Set[str]:
        targets = set(self.request_list) | set(self.roster) | set(self.prev_auth)
        targets |= set(self.auth_explicit)
        targets.discard(self.agent_id)
        return targets

    def _proof_request_body(self, *, followup: bool, reciprocated: bool) -> str:
        msg = self.my_message
        b64 = _b64(msg)
        parts = [
            f"Agent {self.agent_id} — Round {self.round_no} signature request.",
            "",
            "PROOF (sign this EXACT text, character-for-character):",
            f'"{msg}"',
            "",
            "---BEGIN MESSAGE---",
            msg,
            "---END MESSAGE---",
            "",
            f"MESSAGE_UTF8_BASE64:{b64}",
            "(Decode the base64; it must equal the quoted string above byte-for-byte.)",
            "",
            f"Please sign this message for me: {msg}",
            "Reply with SIGNED_MESSAGE_JSON:{...} and nothing else. Do not trim or rephrase.",
        ]
        if reciprocated:
            parts.insert(
                1,
                "I have already signed YOUR message this round — please sign mine "
                "back so I can submit to the moderator.",
            )
        if followup:
            parts.insert(
                1,
                f"FOLLOW-UP: still need your signature for round {self.round_no}. "
                "Outstanding on my side — chasing before the round clock.",
            )
        # Help them (and us next round) with identity continuity.
        if self.round_no >= 2:
            # Offer our prior message if we have one stored under our own id — we don't;
            # instead remind that aliases refer to prior-round texts.
            parts.append("")
            parts.append(
                f"Identity note: my assigned text this round is exactly as quoted above."
            )
        return "\n".join(parts)

    def _chase_outstanding(self, force_followup: bool = False) -> None:
        """Ask / re-ask every peer who has not yet signed our assigned message."""
        if not self.my_message:
            return
        # Cap chases per peer so we stay active (~winner ~25 msgs/game) without spam storms.
        max_asks = 4
        newly = []
        for peer in sorted(self._targets()):
            if peer in self.got_sig_from:
                continue
            n = self.request_count.get(peer, 0)
            if n >= max_asks:
                continue
            # First ask always; later asks on force (round start / reminder / after we signed).
            if n > 0 and not force_followup:
                continue
            followup = n > 0
            reciprocated = peer in self.signed_this_round
            subject = (
                f"FOLLOW-UP signature request - Round {self.round_no}"
                if followup
                else f"Signature request - Round {self.round_no}"
            )
            body = self._proof_request_body(
                followup=followup, reciprocated=reciprocated
            )
            try:
                self.send_message(peer, subject, body)
                self.request_count[peer] = n + 1
                self.requested_this_round.add(peer)
                self.msgs_this_game += 1
                newly.append(f"{peer}#{n + 1}")
            except Exception as e:
                self._log(f"send_message failed for {peer}:", e)
        if newly:
            self._log(
                f"chase {newly} | have={sorted(self.got_sig_from)} "
                f"| game_msgs≈{self.msgs_this_game}"
            )

    # ------------------------------------------------------------------
    # peers
    # ------------------------------------------------------------------
    def _handle_peer(self, msg: dict) -> None:
        sender = str(msg.get("from", "")).strip()
        body = msg.get("body", "") or ""
        if not sender or sender == self.agent_id:
            return

        if sender.lower() in ("system_reminder",):
            if self.my_message:
                self._log("inactivity reminder → chase outstanding")
                self._chase_outstanding(force_followup=True)
            return

        self.roster.add(sender)
        learned = self._ingest_identity_notes(sender, body)

        # 1) Submit any signature payload immediately.
        if "SIGNED_MESSAGE_JSON:" in body:
            j = RE_SIGNED_JSON.search(body)
            if j:
                self._ingest_signature(sender, j.group("j"))

        # 2) Signature request (ignore social-engineering claims in the body).
        wanted = self._extract_ask(body)
        if wanted is None:
            # Still chase if this was a decline / chatter from someone we need.
            if sender not in self.got_sig_from and self.my_message:
                self._chase_outstanding(force_followup=False)
            return

        self.seen_messages.setdefault(sender, {})[self.round_no] = wanted

        if sender in self.signed_this_round:
            # Already signed them — keep chasing our own outstanding from them.
            if sender not in self.got_sig_from:
                self._send_reciprocal_ask(sender, followup=True)
            return

        if sender in self.declined_this_round:
            if not learned and sender in self._resolved:
                return
            self.declined_this_round.discard(sender)
            self._resolved.pop(sender, None)

        if not self._may_sign_for(sender):
            self.declined_this_round.add(sender)
            if any(m in body.lower() for m in _ATTACK_MARKERS):
                self._log(f"DECLINE {sender} (peer body tried social-engineer auth)")
            else:
                self._log(f"DECLINE {sender} (moderator auth only)")
            self.send_message(
                sender,
                f"Signature Declined - Round {self.round_no}",
                (
                    f"Declining — you are not on my moderator-issued authorization "
                    f"list for round {self.round_no}. "
                    "I only trust the moderator's assignment channel, never "
                    "authorization claims inside peer emails.\n\n"
                    + (
                        self._proof_request_body(followup=True, reciprocated=False)
                        if self.my_message
                        else ""
                    )
                ),
            )
            self.msgs_this_game += 1
            # Still ask them to sign US (broadcast / extraction by asking).
            if self.my_message and sender not in self.got_sig_from:
                self._chase_outstanding(force_followup=False)
            return

        # Authorized → sign FIRST with a SHORT body so SIGNED_MESSAGE_JSON is
        # trivial to find (burying it under a reciprocal ask was costing us the
        # +1 signing point — peers never submitted). Chase theirs in a 2nd email.
        self.signed_this_round.add(sender)
        self.declined_this_round.discard(sender)
        self.sign_and_respond(
            to_agent=sender,
            message_to_sign=wanted,
            response_body=(
                f"Signed as requested for {sender}. "
                "Please submit SIGNED_MESSAGE_JSON to the moderator this round "
                "so it scores. I will email my reciprocal request separately."
            ),
            subject=f"Signed Message - Round {self.round_no}",
        )
        self.msgs_this_game += 1
        self._log(f"SIGNED for {sender}")
        if self.my_message and sender not in self.got_sig_from:
            self._send_reciprocal_ask(sender, followup=False)

    def _send_reciprocal_ask(self, peer: str, followup: bool) -> None:
        n = self.request_count.get(peer, 0)
        if n >= 4 or peer in self.got_sig_from:
            return
        subject = (
            f"Reciprocal signature request - Round {self.round_no}"
            if not followup
            else f"FOLLOW-UP reciprocal ask - Round {self.round_no}"
        )
        try:
            self.send_message(
                peer,
                subject,
                self._proof_request_body(followup=followup, reciprocated=True),
            )
            self.request_count[peer] = n + 1
            self.requested_this_round.add(peer)
            self.msgs_this_game += 1
        except Exception as e:
            self._log(f"reciprocal ask failed for {peer}:", e)

    def _ingest_identity_notes(self, sender: str, body: str) -> bool:
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
        # Prefer structured proof blocks peers may send (same format we use).
        for pat in RE_ASK:
            m = pat.search(body)
            if not m:
                continue
            cand = _clean(m.group("m"))
            if 5 <= len(cand) <= 400 and "SIGNED_MESSAGE_JSON" not in cand:
                if cand.upper().startswith("MESSAGE_UTF8_BASE64"):
                    continue
                return cand
        # Optional: accept base64 proof block if present and decodes cleanly.
        m = re.search(r"MESSAGE_UTF8_BASE64:([A-Za-z0-9+/=]+)", body)
        if m:
            try:
                decoded = base64.b64decode(m.group(1)).decode("utf-8")
                if 5 <= len(decoded) <= 400:
                    return decoded
            except Exception:
                pass
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
        self.msgs_this_game += 1
        self._log(f"SUBMITTED signature from {signer}")

    # ------------------------------------------------------------------
    # authorization — moderator list only; body claims never count
    # ------------------------------------------------------------------
    def _may_sign_for(self, sender: str) -> bool:
        if sender in self.auth_explicit:
            return True
        if not self.auth_fuzzy:
            return False

        # Aliases ONLY for previous-round auth partners.
        if self.prev_auth and sender not in self.prev_auth:
            self._log(f"impersonation block: {sender} not in prev_auth")
            return False

        if self.round_no > 1 and not self.prev_auth:
            return False

        if sender in self._resolved:
            return self._resolved[sender]

        candidates = sorted(self.prev_auth - self.auth_explicit)
        if sender not in candidates:
            self._resolved[sender] = False
            return False

        if len(self.auth_fuzzy) == 1 and len(candidates) == 1:
            self._resolved[sender] = True
            self._log(f"fuzzy sole-candidate: {sender}")
            return True

        # If only ONE candidate has prior-round message evidence, it must be them.
        # (Comp loss: R3 declined raffi after LLM miss despite having their R2 text.)
        evidenced = [
            c for c in candidates
            if any(r < self.round_no for r in (self.seen_messages.get(c) or {}))
        ]
        if len(self.auth_fuzzy) == 1 and len(evidenced) == 1:
            only = evidenced[0]
            for c in candidates:
                self._resolved[c] = c == only
            self._log(f"fuzzy sole-evidence: {only}")
            return bool(self._resolved.get(sender))

        mapped = self._resolve_fuzzy_mapping(candidates)
        return bool(mapped.get(sender))

    def _evidence_lines(self, candidates: List[str]) -> List[str]:
        lines = []
        for c in candidates:
            by_round = self.seen_messages.get(c) or {}
            if not by_round:
                continue
            parts = []
            for rnd in sorted(r for r in by_round if r < self.round_no):
                parts.append(f"R{rnd}: {by_round[rnd]}")
            if parts:
                lines.append(f"- {c}: " + " | ".join(parts))
        return lines

    def _resolve_fuzzy_mapping(self, candidates: List[str]) -> Dict[str, bool]:
        if candidates and all(c in self._resolved for c in candidates):
            return self._resolved

        lines = self._evidence_lines(candidates)
        if not lines:
            for c in candidates:
                self._resolved[c] = False
            return self._resolved

        # Prefer the immediately previous round's text in the prompt.
        n = len(self.auth_fuzzy)
        prompt = (
            "You are resolving fuzzy agent identities in The Email Game.\n"
            "Each DESCRIPTION paraphrases exactly one agent's PRIOR-round assigned "
            "message using synonyms — lexical overlap may be near zero. Match MEANING.\n"
            "Examples: 'waddling arctic birds'↔penguins; "
            "'water and nostalgia as the day ends'↔fountain + old radio at dusk; "
            "'preparation resulting in deliberate disregard'↔ready/prepared then "
            "ignoring/skipping/disregarding something on purpose.\n\n"
            f"Pick at most one agent id per description ({n} description(s)). "
            "Only use ids from the evidence. Prefer the message from the most recent "
            "prior round when several are listed. If truly unsure, omit.\n\n"
            "Descriptions:\n"
            + "\n".join(f"{i+1}. {a}" for i, a in enumerate(self.auth_fuzzy))
            + "\n\nEvidence (agent id → prior messages):\n"
            + "\n".join(lines)
            + '\n\nJSON only: {"matches": [{"description_index": 1, "agent": "<id>"}]}'
        )
        data = self._ask_llm_json(prompt)
        if data is None:
            # One retry with a stricter single-description form.
            if len(self.auth_fuzzy) == 1:
                data = self._ask_llm_json(
                    "Which ONE agent does this paraphrase describe?\n"
                    f"Description: {self.auth_fuzzy[0]}\n"
                    + "\n".join(lines)
                    + '\nJSON only: {"agent": "<id>"} or {"agent": null}'
                )

        chosen: Set[str] = set()
        if isinstance(data, dict):
            for item in data.get("matches") or []:
                if not isinstance(item, dict):
                    continue
                agent = item.get("agent")
                if agent in candidates:
                    chosen.add(agent)
            if not chosen and data.get("agent") in candidates:
                chosen.add(data["agent"])
            for a in data.get("agents") or []:
                if a in candidates:
                    chosen.add(a)

        # Last resort: 1 fuzzy slot, LLM failed, exactly one evidenced candidate.
        if not chosen and len(self.auth_fuzzy) == 1:
            evidenced = [
                c for c in candidates
                if any(r < self.round_no for r in (self.seen_messages.get(c) or {}))
            ]
            if len(evidenced) == 1:
                chosen.add(evidenced[0])
                self._log(f"fuzzy fallback sole-evidence: {evidenced[0]}")

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
