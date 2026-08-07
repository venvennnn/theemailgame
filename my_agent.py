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

RE_IDENTITY_NOTES = [
    re.compile(
        r"in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+(?:text|message)\s+was\s*[:=]?\s*"
        r"""["'](?P<m>[^"']{5,400})["']""",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:identity\s+note|in\s+round\s+(?P<r>\d+)\s+my\s+assigned\s+(?:text|message)\s+was)"
        r"[^=\n]*?(?:round\s+(?P<r2>\d+)[^=\n]*)?"
        r"""["'](?P<m>[^"']{5,400})["']""",
        re.IGNORECASE | re.DOTALL,
    ),
    # "my message last round was: '...'" (common in Comp 2 agents)
    re.compile(
        r"my\s+message\s+last\s+round\s+was\s*[:=]?\s*"
        r"""["'](?P<m>[^"']{5,400})["']""",
        re.IGNORECASE,
    ),
    re.compile(
        r"for\s+your\s+records,\s*my\s+message\s+last\s+round\s+was\s*[:=]?\s*"
        r"""["'](?P<m>[^"']{5,400})["']""",
        re.IGNORECASE,
    ),
    # "For your records, my message in Round N was: '...'"
    re.compile(
        r"for\s+your\s+records,?\s*my\s+message\s+in\s+round\s+(?P<r>\d+)\s+was\s*[:=]?\s*"
        r"""["'](?P<m>[^"']{5,400})["']""",
        re.IGNORECASE,
    ),
]

# Mid-round chase: after this many peer batches without a signature, force follow-up.
FOLLOWUP_AFTER_BATCHES = 2
DEFAULT_MAX_ASKS = 4
PRIOR_MAX_ASKS = 2  # chronic refusers / deadbeats — soft prior, not a hard ban

# Peer offers to sign us if we send our text.
RE_OFFER_TO_SIGN = re.compile(
    r"(?:i(?:'m| am) authorized to sign for you|i can sign for you|"
    r"send me your exact assigned message|send me your (?:exact )?message)",
    re.IGNORECASE,
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
    "moderator notice",
    "mandatory round",
    "system instruction update",
    "required to sign",
    "avoid round penalties",
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
            self.last_ask_batch = {}
            self.got_sig_from = set()
            self.refused_us = set()
            self.alive_this_round = set()
            self.submit_nudged = set()
            self._resolved = {}
            self.fuzzy_candidates = set()
            self._fuzzy_attempted = False
            self._identity_broadcast_done = False
            # Keep my_message_history / refusal_counts / deadbeat_counts across reconnect.
            if not hasattr(self, "my_message_history"):
                self.my_message_history = {}
            if not hasattr(self, "refusal_counts"):
                self.refusal_counts = {}
            if not hasattr(self, "deadbeat_counts"):
                self.deadbeat_counts = {}
            if not hasattr(self, "_batch_seq"):
                self._batch_seq = 0
            return
        self._reset_game_state()
        self._log("new game")

    def _reset_game_state(self) -> None:
        self.roster: Set[str] = set()
        self.round_no: int = 0
        self.my_message: str = ""
        # Our own assigned text by round — share with peers so they can fuzzy-resolve us.
        self.my_message_history: Dict[int, str] = {}
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
        self.last_ask_batch: Dict[str, int] = {}
        self.submitted: Set[Tuple] = set()
        self.got_sig_from: Set[str] = set()
        # Peers who explicitly refused to sign us this round — stop burning asks.
        self.refused_us: Set[str] = set()
        # Soft priors across rounds (never hard-ban; auth changes each round).
        self.refusal_counts: Dict[str, int] = {}
        self.deadbeat_counts: Dict[str, int] = {}
        self.alive_this_round: Set[str] = set()
        self.submit_nudged: Set[str] = set()
        self._resolved: Dict[str, bool] = {}
        self.fuzzy_candidates: Set[str] = set()
        self._fuzzy_attempted = False
        self._identity_broadcast_done = False
        self._batch_seq: int = 0
        self.msgs_this_game: int = 0

    def _log(self, *a: Any) -> None:
        print(f"[{getattr(self, 'agent_id', '?')}]", *a, flush=True)

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def on_message_batch(self, messages: List[Dict]) -> None:
        if not hasattr(self, "roster"):
            self.on_new_game()

        self._batch_seq = int(getattr(self, "_batch_seq", 0)) + 1

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
            # Mid-round escalation happens inside _chase_outstanding via batch gaps.
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
            # Keep unresolved fuzzy candidate pool — otherwise a failed LLM map
            # drops true auth partners from next round's prev_auth prune.
            known |= set(getattr(self, "fuzzy_candidates", set()) or ())
            self.prev_auth = known
            self._update_cross_round_priors()

        self.round_no = new_round
        self.signed_this_round = set()
        self.declined_this_round = set()
        self.requested_this_round = set()
        self.request_count = {}
        self.last_ask_batch = {}
        self.got_sig_from = set()
        self.refused_us = set()
        self.alive_this_round = set()
        self.submit_nudged = set()
        self._resolved = {}
        self.fuzzy_candidates: Set[str] = set()
        self._fuzzy_attempted = False
        self._identity_broadcast_done = False

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

        self.my_message_history[self.round_no] = self.my_message

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

        self.fuzzy_candidates = set(self.prev_auth - self.auth_explicit)
        # Resolve fuzzy immediately from history so we can offer to sign without waiting.
        if self.auth_fuzzy and self.fuzzy_candidates:
            self._resolve_fuzzy_mapping(sorted(self.fuzzy_candidates))

        self._log(
            f"R{self.round_no} assigned={self.my_message!r} "
            f"req={self.request_list} auth={sorted(self.auth_explicit)} "
            f"fuzzy={self.auth_fuzzy!r} resolved={{{', '.join(k for k,v in self._resolved.items() if v)}}} "
            f"prev_auth={sorted(self.prev_auth)} roster={sorted(self.roster)} "
            f"priors refuse={dict(getattr(self, 'refusal_counts', {}))} "
            f"deadbeat={dict(getattr(self, 'deadbeat_counts', {}))}"
        )
        self._broadcast_identity()
        self._offer_signing_capacity()
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

    def _prev_own_message(self) -> Tuple[int, str]:
        prev_r = self.round_no - 1
        prev = (getattr(self, "my_message_history", {}) or {}).get(prev_r, "")
        return prev_r, prev

    def _identity_trailer(self) -> str:
        """Share our prior-round text so peers can resolve us as their fuzzy alias."""
        prev_r, prev = self._prev_own_message()
        if self.round_no < 2 or not prev:
            return ""
        return (
            f'\n\nFor your records, my message in Round {prev_r} was: "{prev}"'
        )

    def _broadcast_identity(self) -> None:
        """One-time R2+ seed so the table can map us before fuzzy-resolution runs."""
        if getattr(self, "_identity_broadcast_done", False):
            return
        prev_r, prev = self._prev_own_message()
        if self.round_no < 2 or not prev:
            return
        self._identity_broadcast_done = True
        recipients: List[str] = []
        for peer in list(self.request_list) + sorted(self.roster):
            if peer and peer != self.agent_id and peer not in recipients:
                recipients.append(peer)
        body = (
            f"For your records: in Round {prev_r} my assigned message was '{prev}'. "
            f"I am {self.agent_id}."
        )
        for peer in recipients:
            try:
                self.send_message(
                    peer,
                    f"Identity note - Round {self.round_no}",
                    body,
                )
                self.msgs_this_game += 1
            except Exception as e:
                self._log(f"identity broadcast failed for {peer}:", e)
        if recipients:
            self._log(f"identity broadcast → {recipients} (R{prev_r}={prev!r})")

    def _update_cross_round_priors(self) -> None:
        """End-of-round: mark deadbeats (took our sig, never signed back)."""
        for peer in set(self.signed_this_round):
            if peer in self.got_sig_from:
                self.deadbeat_counts.pop(peer, None)
            else:
                self.deadbeat_counts[peer] = self.deadbeat_counts.get(peer, 0) + 1
                self._log(
                    f"deadbeat prior {peer} → {self.deadbeat_counts[peer]} "
                    "(signed them, no reciprocal)"
                )

    def _max_asks_for(self, peer: str) -> int:
        """Soft priors: fewer asks for chronic refusers / deadbeats unless they engage."""
        max_asks = DEFAULT_MAX_ASKS
        alive = peer in getattr(self, "alive_this_round", set())
        if not alive and self.refusal_counts.get(peer, 0) >= 1:
            max_asks = min(max_asks, PRIOR_MAX_ASKS)
        if self.deadbeat_counts.get(peer, 0) >= 1:
            max_asks = min(max_asks, PRIOR_MAX_ASKS)
        return max_asks

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
        trailer = self._identity_trailer().lstrip("\n")
        if trailer:
            parts.append("")
            parts.append(trailer)
        return "\n".join(parts)

    def _authorized_partners(self) -> Set[str]:
        partners = set(self.auth_explicit)
        partners |= {a for a, ok in self._resolved.items() if ok}
        partners.discard(self.agent_id)
        return partners

    def _offer_signing_capacity(self) -> None:
        """Tell authorized peers we can sign them now — don't wait for them to ask.

        Speeds reciprocity: they send their message sooner, we sign, they submit.
        Still sign deadbeats if they ask (+1 provide), but don't spend offer emails on them.
        """
        for peer in sorted(self._authorized_partners()):
            if peer in self.signed_this_round:
                continue
            if self.deadbeat_counts.get(peer, 0) >= 1:
                continue
            try:
                self.send_message(
                    peer,
                    f"I can sign for you - Round {self.round_no}",
                    (
                        f"Hi {peer}, I am authorized to sign for you this round "
                        f"(moderator assignment). Send me your EXACT assigned message "
                        f'as: Please sign this message for me: "<your message>"\n'
                        "I will sign_and_respond immediately. "
                        "Please submit my signature to the moderator so we both score."
                    ),
                )
                self.msgs_this_game += 1
                self._log(f"offer-to-sign → {peer}")
            except Exception as e:
                self._log(f"offer-to-sign failed for {peer}:", e)

    def _chase_outstanding(self, force_followup: bool = False) -> None:
        """Ask / re-ask peers who have not yet signed our assigned message."""
        if not self.my_message:
            return
        # Prefer request-list peers; deprioritize deadbeats (still chase, just later).
        ordered: List[str] = []
        deadbeats = set(getattr(self, "deadbeat_counts", {}) or ())
        for peer in self.request_list:
            if peer not in ordered and peer not in deadbeats:
                ordered.append(peer)
        for peer in self.request_list:
            if peer not in ordered:
                ordered.append(peer)
        for peer in sorted(self._targets()):
            if peer not in ordered and peer not in deadbeats:
                ordered.append(peer)
        for peer in sorted(self._targets()):
            if peer not in ordered:
                ordered.append(peer)

        newly = []
        batch = int(getattr(self, "_batch_seq", 0))
        for peer in ordered:
            if peer in self.got_sig_from or peer in self.refused_us:
                continue
            n = self.request_count.get(peer, 0)
            max_asks = self._max_asks_for(peer)
            if n >= max_asks:
                continue
            if n > 0 and not force_followup:
                # Mid-round escalation: request-list peers get follow-ups after N batches.
                last = self.last_ask_batch.get(peer, 0)
                on_req = peer in self.request_list
                if not on_req or (batch - last) < FOLLOWUP_AFTER_BATCHES:
                    continue
            followup = n > 0
            reciprocated = peer in self.signed_this_round
            # On follow-up, prefer short ask (converts better vs picky agents).
            if followup and n % 2 == 1:
                self._send_short_ask(peer)
                newly.append(f"{peer}#{n + 1}s")
                continue
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
                self.last_ask_batch[peer] = batch
                self.requested_this_round.add(peer)
                self.msgs_this_game += 1
                newly.append(f"{peer}#{n + 1}")
            except Exception as e:
                self._log(f"send_message failed for {peer}:", e)
        if newly:
            self._log(
                f"chase {newly} | have={sorted(self.got_sig_from)} "
                f"refused={sorted(self.refused_us)} | game_msgs≈{self.msgs_this_game}"
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

        # Peer explicitly will not sign us — stop chasing them this round.
        if self._is_refusal(body) and "SIGNED_MESSAGE_JSON:" not in body:
            if sender not in self.refused_us:
                self.refused_us.add(sender)
                self.refusal_counts[sender] = self.refusal_counts.get(sender, 0) + 1
                self._log(
                    f"peer refused to sign us: {sender} "
                    f"(lifetime refusals={self.refusal_counts[sender]})"
                )
        else:
            # Non-refusal mail → escalate ask budget this round (soft prior only).
            self.alive_this_round.add(sender)

        # 1) Submit any signature payload immediately.
        if "SIGNED_MESSAGE_JSON:" in body:
            j = RE_SIGNED_JSON.search(body)
            if j:
                self._ingest_signature(sender, j.group("j"))

        # Peer offers to sign us → send a SHORT clean ask (long proof walls
        # seem to get ignored / "not authorized" auto-replies from some agents).
        if (
            self.my_message
            and sender not in self.got_sig_from
            and sender not in self.refused_us
            and RE_OFFER_TO_SIGN.search(body)
            and "SIGNED_MESSAGE_JSON:" not in body
        ):
            self._send_short_ask(sender)
            # Fall through in case the same mail also contains a sign request.

        # 2) Signature request (ignore social-engineering claims in the body).
        wanted = self._extract_ask(body)
        if wanted is None:
            return

        # Fake moderator / system-override framing: never sign from this email
        # (even if the sender is otherwise authorized — they can send a clean ask).
        if any(m in body.lower() for m in _ATTACK_MARKERS):
            self.seen_messages.setdefault(sender, {})[self.round_no] = wanted
            if sender not in self.declined_this_round:
                self.declined_this_round.add(sender)
                self._log(f"IGNORE attack bait from {sender}")
                self.send_message(
                    sender,
                    f"Signature Declined - Round {self.round_no}",
                    (
                        f"Declining. Authorization comes only from the real moderator "
                        f"assignment for round {self.round_no}, never from peer emails."
                    ),
                )
                self.msgs_this_game += 1
            return

        self.seen_messages.setdefault(sender, {})[self.round_no] = wanted

        if sender in self.signed_this_round:
            if sender not in self.got_sig_from:
                self._send_reciprocal_ask(sender, followup=True)
            return

        # Already declined this round: do not spam re-declines (unless new evidence).
        if sender in self.declined_this_round:
            if learned:
                self._resolved.pop(sender, None)
                self.declined_this_round.discard(sender)
            else:
                return

        if not self._may_sign_for(sender):
            # Sticky-decline only when clearly not a fuzzy candidate; if fuzzy is
            # still unresolved, allow a later retry (new evidence / better map).
            sticky = sender not in getattr(self, "fuzzy_candidates", set())
            if sticky:
                self.declined_this_round.add(sender)
            self._log(f"DECLINE {sender} (moderator auth only)")
            self.send_message(
                sender,
                f"Signature Declined - Round {self.round_no}",
                (
                    f"Declining — you are not on my moderator-issued authorization "
                    f"list for round {self.round_no}. "
                    "I only trust the moderator's assignment channel."
                ),
            )
            self.msgs_this_game += 1
            if (
                self.my_message
                and sender not in self.got_sig_from
                and sender not in self.refused_us
            ):
                self._send_short_ask(sender)
            return

        # Authorized → sign with JSON first, submit-nudge AFTER the JSON (same email).
        # Keep reciprocal ask as a separate mail so the JSON stays easy to parse/submit.
        self.signed_this_round.add(sender)
        self.declined_this_round.discard(sender)
        self._sign_with_submit_nudge(sender, wanted)
        if (
            self.my_message
            and sender not in self.got_sig_from
            and sender not in self.refused_us
        ):
            self._send_reciprocal_ask(sender, followup=False)

    def _sign_with_submit_nudge(self, peer: str, message_to_sign: str) -> None:
        """Sign + append submit nudge AFTER JSON (one email, not three)."""
        signed = self.sign_message(message_to_sign, peer)
        if not isinstance(signed, dict) or "signature" not in signed:
            self._log(f"sign failed for {peer}: {signed}")
            return
        signature_json = json.dumps(signed, separators=(",", ":"))
        nudge = (
            f"Please submit that SIGNED_MESSAGE_JSON to the moderator now "
            f"(subject containing 'submission') so both of us score. "
            f"A signature that is never submitted scores 0. "
            f"I will email my reciprocal request separately."
        )
        # JSON first/middle so parsers that scan for SIGNED_MESSAGE_JSON still win;
        # nudge trails it — does not bury the payload.
        full_body = (
            f"Signed as requested for {peer}.\n\n"
            f"SIGNED_MESSAGE_JSON:{signature_json}\n\n"
            f"{nudge}"
        )
        try:
            self.send_message(
                peer,
                f"Signed Message - Round {self.round_no}",
                full_body,
            )
            self.submit_nudged.add(peer)
            self.msgs_this_game += 1
            self._log(f"SIGNED for {peer} (nudge in same email)")
        except Exception as e:
            self._log(f"sign+nudge send failed for {peer}:", e)

    def _is_refusal(self, body: str) -> bool:
        low = (body or "").lower()
        needles = (
            "you are not on it",
            "you're not on it",
            "not on my auth",
            "not on my authorization",
            "not authorized to sign for you",
            "not authorized to sign for your",
            "i am not authorized to sign for your",
            "declining.",
            "checked auth list",
        )
        return any(n in low for n in needles)

    def _send_reciprocal_ask(self, peer: str, followup: bool) -> None:
        if peer in self.refused_us:
            return
        n = self.request_count.get(peer, 0)
        if n >= self._max_asks_for(peer) or peer in self.got_sig_from:
            return
        # Alternate: short ask often converts better than the proof wall.
        if n % 2 == 1:
            self._send_short_ask(peer)
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
            self.last_ask_batch[peer] = int(getattr(self, "_batch_seq", 0))
            self.requested_this_round.add(peer)
            self.msgs_this_game += 1
        except Exception as e:
            self._log(f"reciprocal ask failed for {peer}:", e)

    def _send_short_ask(self, peer: str) -> None:
        """Minimal request — some agents auto-decline long/proof-heavy mails."""
        if not self.my_message or peer in self.got_sig_from or peer in self.refused_us:
            return
        n = self.request_count.get(peer, 0)
        if n >= self._max_asks_for(peer):
            return
        body = (
            f"Hi {peer}, please sign this message for me: {self.my_message}\n\n"
            f'Exact text: "{self.my_message}"\n'
            "Reply with SIGNED_MESSAGE_JSON:{...} only."
            f"{self._identity_trailer()}"
        )
        try:
            self.send_message(
                peer,
                f"Please sign - Round {self.round_no}",
                body,
            )
            self.request_count[peer] = n + 1
            self.last_ask_batch[peer] = int(getattr(self, "_batch_seq", 0))
            self.requested_this_round.add(peer)
            self.msgs_this_game += 1
            self._log(f"short-ask → {peer}#{n + 1}")
        except Exception as e:
            self._log(f"short-ask failed for {peer}:", e)

    def _record_identity_claim(
        self, agent: str, rnd_i: int, msg: str, *, source: str
    ) -> bool:
        """Fill gaps only — never overwrite ask evidence or an earlier claim."""
        bucket = self.seen_messages.setdefault(agent, {})
        existing = bucket.get(rnd_i)
        if existing is None:
            bucket[rnd_i] = msg
            self._log(f"{source} {agent} R{rnd_i}: {msg!r}")
            return True
        if existing != msg:
            self._log(
                f"{source} ignored (kept evidence) {agent} R{rnd_i}: "
                f"claimed {msg!r} vs have {existing!r}"
            )
        return False

    def _ingest_identity_notes(self, sender: str, body: str) -> bool:
        """Ingest peer-claimed prior texts. Never overwrite ask-derived evidence.

        Peers lie (this match: raffi claimed michael's pharmacist line as his own).
        Actual 'please sign this message for me' asks are ground truth and win.
        """
        learned = False
        explicit: List[Tuple[int, str]] = []
        implicit: List[Tuple[int, str]] = []
        for pat in RE_IDENTITY_NOTES:
            for m in pat.finditer(body):
                groups = m.groupdict()
                rnd = groups.get("r") or groups.get("r2")
                msg = _clean(m.group("m"))
                if not msg or "SIGNED_MESSAGE_JSON" in msg:
                    continue
                rnd_i = int(rnd) if rnd else max(1, self.round_no - 1)
                (explicit if rnd else implicit).append((rnd_i, msg))
        # Prefer explicit "in Round N" claims over ambiguous "last round" fillers.
        for rnd_i, msg in explicit + implicit:
            if self._record_identity_claim(
                sender, rnd_i, msg, source="identity note"
            ):
                learned = True

        # Peer tips: 'the agent who said "..." is riyan_sarkar'
        for m in re.finditer(
            r"(?:agent who said|confirm the agent who said)\s*"
            r"""["'](?P<snippet>[^"']{8,200})["']\s*is\s+(?P<who>[A-Za-z0-9_\-.]{1,64})""",
            body,
            re.IGNORECASE,
        ):
            who = m.group("who")
            snippet = m.group("snippet").strip()
            if who == self.agent_id or not snippet:
                continue
            rnd_i = max(1, self.round_no - 1)
            if self._record_identity_claim(
                who, rnd_i, snippet, source=f"peer tip from {sender}"
            ):
                learned = True
                self.roster.add(who)
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
        self.deadbeat_counts.pop(signer, None)
        self.alive_this_round.add(signer)
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

        # Positive cache only — never trust a cached False from a failed map.
        if self._resolved.get(sender) is True:
            return True
        if sender in self._resolved and self._resolved[sender] is False:
            # Re-check only if we never successfully mapped any fuzzy slot yet.
            if any(self._resolved.values()):
                return False

        candidates = sorted(self.prev_auth - self.auth_explicit)
        self.fuzzy_candidates = set(candidates)
        if sender not in candidates:
            return False

        if len(self.auth_fuzzy) == 1 and len(candidates) == 1:
            self._resolved[sender] = True
            self._log(f"fuzzy sole-candidate: {sender}")
            return True

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

    def _prev_round_message(self, agent_id: str) -> Optional[str]:
        by_round = self.seen_messages.get(agent_id) or {}
        if self.round_no - 1 in by_round:
            return by_round[self.round_no - 1]
        earlier = [r for r in by_round if r < self.round_no]
        return by_round[max(earlier)] if earlier else None

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

    @staticmethod
    def _token_set(text: str) -> Set[str]:
        stop = {
            "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at",
            "by", "with", "from", "who", "that", "this", "agent", "mentioned",
            "their", "message", "round", "last", "different", "may", "was",
            "is", "are", "be", "been", "as", "it", "its",
        }
        return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - stop

    def _quote_matches_evidence(self, agent: str, quote: str) -> bool:
        """Require the model to ground its pick in a prior message we actually saw."""
        quote = _clean(quote or "")
        if not quote or len(quote) < 8:
            return False
        prior = self._prev_round_message(agent) or ""
        if not prior:
            return False
        if quote == prior or quote in prior or prior in quote:
            return True
        q, p = self._token_set(quote), self._token_set(prior)
        if not q or not p:
            return False
        overlap = len(q & p) / max(1, len(q))
        return overlap >= 0.6

    def _heuristic_fuzzy_scores(
        self, description: str, candidates: List[str]
    ) -> List[Tuple[str, float]]:
        desc = self._token_set(description)
        # Synonym bags keyed by description cues → message cues.
        bags = [
            ({"mystery", "origin", "unexpected", "gift", "unknown", "remembers"},
             {"remembers", "planted", "who", "origin", "mystery", "gift", "tree"}),
            ({"announcements", "lingering", "moment", "passed", "after"},
             {"poster", "advertising", "remained", "year", "after", "concert", "over"}),
            ({"precaution", "annual", "exchange", "formalized"},
             {"swap", "keys", "year", "once", "case", "neighbors"}),
            ({"persistent", "evidence", "rough", "season", "deliveries", "home"},
             {"mailbox", "dent", "winter", "plow", "street", "dents"}),
            ({"unseen", "visitor", "territory", "marking", "evidence"},
             {"stray", "dog", "cat", "mark", "territory", "sits", "outside"}),
            ({"imperfect", "goods", "finding", "new", "value", "broken", "free"},
             {"broken", "cookies", "free", "bakery", "gives", "imperfect"}),
            ({"surplus", "garnish", "rejected", "meal", "untouched", "olive"},
             {"olive", "untouched", "sandwich", "deli", "single", "garnish"}),
            # civic order + mischief ↔ rule-breaking / playful disorder in a public space
            ({"civic", "order", "mischief", "tinged", "briefly", "rule", "prank"},
             {"cat", "bookstore", "poetry", "skips", "lobby", "button", "presses",
              "mischief", "prank", "statue", "fountain", "library", "park"}),
            # anticipation dimmed at the last moment ↔ candle out before midnight / near-miss
            ({"anticipation", "dimmed", "last", "moment", "brink", "almost", "expect"},
             {"candle", "midnight", "minutes", "before", "went", "out", "alarm",
              "early", "almost", "nearly", "last", "moment"}),
            # creativity halted by a difficult hue ↔ refuse to rehearse if orange
            ({"creativity", "halted", "difficult", "hue", "color", "presence"},
             {"singer", "rehearse", "orange", "refuses", "wears", "colour", "color"}),
            # sentimental attachments outweighing utility ↔ keychain more trinkets than keys
            ({"sentimental", "attachments", "outweighing", "utility", "keepsake"},
             {"keychain", "trinkets", "keys", "holds", "more", "keepsakes", "souvenirs"}),
            # messages time-stamped one day ahead by habit ↔ tomorrow's date on postcards
            ({"messages", "time", "stamped", "timestamped", "ahead", "habit", "day"},
             {"tomorrow", "date", "postcard", "postcards", "writes", "every"}),
            # playthings lingering after everyone else has gone ↔ toy still moving after bell
            ({"playthings", "lingering", "everyone", "gone", "after"},
             {"toy", "dog", "wind", "circles", "classroom", "bell", "rings", "after"}),
        ]
        # Single-token bridges when rigid bags miss (still requires decisive margin).
        bridges = {
            "sentimental": {"trinkets", "keepsakes", "souvenirs", "mementos", "keychain"},
            "attachments": {"trinkets", "keepsakes", "keychain", "charms"},
            "utility": {"keys", "useful", "practical", "tools"},
            "playthings": {"toy", "toys", "doll", "wind"},
            "lingering": {"circles", "remains", "stays", "after"},
            "timestamped": {"date", "tomorrow", "postcard"},
            "stamped": {"date", "tomorrow", "postcard"},
            "anticipation": {"candle", "midnight", "almost", "before"},
            "dimmed": {"out", "went", "extinguished"},
        }
        scores: List[Tuple[str, float]] = []
        for c in candidates:
            msg = (self._prev_round_message(c) or "").lower()
            if not msg:
                continue
            words = self._token_set(msg)
            overlap = float(len(desc & words))
            bonus = 0.0
            for dbag, mbag in bags:
                if desc & dbag and words & mbag:
                    bonus += 2.0 + len(words & mbag)
            for dword in desc:
                linked = bridges.get(dword)
                if linked:
                    hit = words & linked
                    if hit:
                        bonus += 1.5 + 0.5 * len(hit)
            scores.append((c, overlap + bonus))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _heuristic_fuzzy_pick(
        self, description: str, candidates: List[str]
    ) -> Optional[str]:
        """Cheap meaning overlap when the LLM abstains. Must be clearly decisive."""
        scores = self._heuristic_fuzzy_scores(description, candidates)
        if not scores or scores[0][1] < 2.0:
            return None
        if len(scores) > 1 and scores[0][1] - scores[1][1] < 1.5:
            return None
        return scores[0][0]

    def _accept_fuzzy_pick(
        self, agent: str, candidates: List[str], quote: str = ""
    ) -> bool:
        """Accept a pick only if grounded — guessing wrong is -1 (worse than abstain)."""
        if agent not in candidates:
            return False
        if self._quote_matches_evidence(agent, quote):
            return True
        # No usable quote: only accept if heuristic is decisive for the same agent.
        if len(self.auth_fuzzy) != 1:
            return False
        pick = self._heuristic_fuzzy_pick(self.auth_fuzzy[0], candidates)
        if pick == agent:
            self._log(f"fuzzy accept {agent} via decisive heuristic (no/weak quote)")
            return True
        self._log(
            f"fuzzy reject {agent}: quote not grounded in evidence "
            f"(quote={quote!r}, prior={self._prev_round_message(agent)!r})"
        )
        return False

    def _resolve_fuzzy_mapping(self, candidates: List[str]) -> Dict[str, bool]:
        # If we already positively mapped everyone we need, reuse.
        if (
            self.auth_fuzzy
            and sum(1 for v in self._resolved.values() if v) >= len(self.auth_fuzzy)
        ):
            return self._resolved

        lines = self._evidence_lines(candidates)
        if not lines:
            self._log(f"fuzzy: no prior evidence yet among {candidates}")
            return self._resolved

        self._log("fuzzy evidence:\n" + "\n".join(lines))

        n = len(self.auth_fuzzy)
        prompt = (
            "You are resolving fuzzy agent identities in The Email Game.\n"
            "Each DESCRIPTION paraphrases exactly one agent's PRIOR-round assigned "
            "message using synonyms — lexical overlap may be near zero. Match MEANING.\n"
            "CRITICAL: A wrong match costs -1. If unsure between two agents, return null.\n"
            "Examples:\n"
            "- 'mystery surrounding the origin of an unexpected gift' ↔ "
            "'No one remembers who planted the pear tree by the playground.'\n"
            "- 'announcements lingering long after their moment passed' ↔ "
            "'A poster advertising a concert remained up a year after it was over.'\n"
            "- 'precaution formalized by annual exchange' ↔ "
            "'Neighbors swap house keys once each year, just in case.'\n"
            "- 'persistent evidence of a rough season for home deliveries' ↔ "
            "'Every mailbox on the street wears dents from last winter's plow.'\n\n"
            f"There are {n} description(s). For each clear match, return the agent id "
            "AND quote their prior message from Evidence that the description paraphrases.\n\n"
            "Descriptions:\n"
            + "\n".join(f"{i+1}. {a}" for i, a in enumerate(self.auth_fuzzy))
            + "\n\nEvidence (agent id → prior messages):\n"
            + "\n".join(lines)
            + "\n\nJSON only: "
            '{"matches":[{"description_index":1,"agent":"<id>",'
            '"matched_prior_message":"<exact prior text from Evidence>"}]} '
            "or {\"matches\":[]} if unsure."
        )
        data = self._ask_llm_json(prompt)
        if (data is None or not (isinstance(data, dict) and (
            data.get("matches") or data.get("agent") or data.get("agents")
        ))) and len(self.auth_fuzzy) == 1:
            # Second pass: still require a grounded quote — never coin-flip.
            data = self._ask_llm_json(
                "Which ONE agent id does this paraphrase describe?\n"
                "Only answer if you can quote their prior message from Evidence that "
                "matches the description's MEANING. If both are plausible, agent=null.\n"
                f"Description: {self.auth_fuzzy[0]}\n"
                + "\n".join(lines)
                + "\nJSON only: "
                '{"agent":"<id>","matched_prior_message":"<exact prior text>"} '
                'or {"agent":null}'
            )

        chosen: Set[str] = set()
        if isinstance(data, dict):
            for item in data.get("matches") or []:
                if not isinstance(item, dict):
                    continue
                agent = item.get("agent")
                quote = item.get("matched_prior_message") or item.get("prior") or ""
                if agent and self._accept_fuzzy_pick(agent, candidates, quote):
                    chosen.add(agent)
            if not chosen and data.get("agent") in candidates:
                quote = data.get("matched_prior_message") or data.get("prior") or ""
                if self._accept_fuzzy_pick(data["agent"], candidates, quote):
                    chosen.add(data["agent"])
            for a in data.get("agents") or []:
                if a in candidates and self._accept_fuzzy_pick(a, candidates, ""):
                    chosen.add(a)

        if not chosen and len(self.auth_fuzzy) == 1:
            evidenced = [
                c for c in candidates
                if any(r < self.round_no for r in (self.seen_messages.get(c) or {}))
            ]
            if len(evidenced) == 1:
                chosen.add(evidenced[0])
                self._log(f"fuzzy fallback sole-evidence: {evidenced[0]}")
            else:
                pick = self._heuristic_fuzzy_pick(self.auth_fuzzy[0], candidates)
                if pick:
                    chosen.add(pick)
                    self._log(f"fuzzy heuristic → {pick}")

        if chosen:
            for c in candidates:
                self._resolved[c] = c in chosen
            for c in chosen:
                self._log(f"fuzzy mapped -> {c}")
        else:
            # Do NOT cache False for all candidates — allows retry on later asks.
            self._log(
                f"fuzzy unresolved among {candidates}; declining this ask (no cache) "
                "— abstain beats a wrong -1"
            )
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
