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
RE_PRIOR_MESSAGE_JSON = re.compile(
    r"PRIOR_MESSAGE_JSON:\s*(?P<j>\{.*?\})", re.DOTALL
)

# Structured ask extractors only — bare quoted-string fallback is attack surface.
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
]
RE_ASK_BARE_QUOTE = re.compile(r'["\u201c](?P<m>[^"\u201c\u201d]{5,400})["\u201d]')

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
            if not hasattr(self, "message_owners"):
                self.message_owners = {}
            if not hasattr(self, "silent_rounds"):
                self.silent_rounds = {}
            if not hasattr(self, "_batch_seq"):
                self._batch_seq = 0
            if not hasattr(self, "_sent_kinds"):
                self._sent_kinds = set()
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
        # Peers with a full silent round → cap at 1 outbound ask next rounds.
        self.silent_rounds: Dict[str, int] = {}
        self.alive_this_round: Set[str] = set()
        self.contacted_this_round: Set[str] = set()
        self.submit_nudged: Set[str] = set()
        self._resolved: Dict[str, bool] = {}
        self.fuzzy_candidates: Set[str] = set()
        self._fuzzy_attempted = False
        self._identity_broadcast_done = False
        self._batch_seq: int = 0
        self.msgs_this_game: int = 0
        # (round, normalized message) -> claimants. Never includes us.
        self.message_owners: Dict[Tuple[int, str], Set[str]] = {}
        self._sent_kinds: Set[Tuple[str, str]] = set()
        self._asked_this_batch: Set[str] = set()
        self._pending_asks: List[Dict[str, Any]] = []
        self._pending_short_asks: List[str] = []
        self._prior_false: Set[str] = set()

    def _log(self, *a: Any) -> None:
        print(f"[{getattr(self, 'agent_id', '?')}]", *a, flush=True)

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def on_message_batch(self, messages: List[Dict]) -> None:
        if not hasattr(self, "roster"):
            self.on_new_game()

        self._batch_seq = int(getattr(self, "_batch_seq", 0)) + 1
        self._pending_asks = []
        self._pending_short_asks = []
        self._asked_this_batch = set()
        force_chase = False

        mod = [m for m in messages if self._is_moderator(m)]
        rest = [m for m in messages if not self._is_moderator(m)]

        for m in mod:
            try:
                self._handle_moderator(m)
            except Exception as e:
                self._log("moderator parse error:", e)

        # Phase 1: pure state updates (refusals, sigs, evidence, queue asks).
        for m in rest:
            try:
                if self._ingest_peer(m):
                    force_chase = True
            except Exception as e:
                self._log("peer ingest error:", e)

        # Phase 2: all outbound actions after full batch state is known.
        for peer in list(dict.fromkeys(self._pending_short_asks)):
            if (
                self.my_message
                and peer not in self.got_sig_from
                and peer not in self.refused_us
            ):
                self._send_short_ask(peer)
        for item in self._pending_asks:
            try:
                self._act_on_ask(item)
            except Exception as e:
                self._log("peer act error:", e)

        if self.my_message:
            self._chase_outstanding(force_followup=force_chase)

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
            # Keep ONLY unresolved fuzzy candidates. Peers we positively mapped as
            # NOT the alias (resolved False) must not pollute next round's prev_auth
            # — that bloated R3 to 3 candidates vs 2 fuzzies and we declined both
            # true targets (khushvendra + raffi) after keeping kenny.
            known |= {
                c
                for c in (getattr(self, "fuzzy_candidates", set()) or ())
                if self._resolved.get(c) is not False
            }
            # Keep False-resolved agents for one-round re-expand if sole-candidate
            # fails a meaning check (wrong False purge caused the mitten cascade).
            self._prior_false = {
                a for a, ok in self._resolved.items() if ok is False
            }
            self.prev_auth = known
            self._log(
                f"prev_auth ← {sorted(known)} "
                f"(excluded False={sorted(self._prior_false)})"
            )
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
        self._sent_kinds = set()
        self._asked_this_batch = set()
        self._pending_asks = []
        self._pending_short_asks = []
        self.contacted_this_round = set()
        if not hasattr(self, "_prior_false"):
            self._prior_false = set()

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
        # Offer-to-sign is merged into the first chase ask (never quote a placeholder).
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
        prior_json = json.dumps(
            {"agent": self.agent_id, "round": prev_r, "message": prev},
            separators=(",", ":"),
        )
        return (
            f'\n\nFor your records, my message in Round {prev_r} was: "{prev}"\n'
            f"PRIOR_MESSAGE_JSON:{prior_json}"
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
        prior_json = json.dumps(
            {"agent": self.agent_id, "round": prev_r, "message": prev},
            separators=(",", ":"),
        )
        body = (
            f"For your records: in Round {prev_r} my assigned message was '{prev}'. "
            f"I am {self.agent_id}.\n"
            f"PRIOR_MESSAGE_JSON:{prior_json}"
        )
        for peer in recipients:
            if self.silent_rounds.get(peer, 0) >= 1:
                continue  # dead peers: skip identity, keep the one allowed ask elsewhere
            try:
                self.send_message(
                    peer,
                    f"Identity note - Round {self.round_no}",
                    body,
                )
                self.contacted_this_round.add(peer)
                self.msgs_this_game += 1
            except Exception as e:
                self._log(f"identity broadcast failed for {peer}:", e)
        if recipients:
            self._log(f"identity broadcast → {recipients} (R{prev_r}={prev!r})")

    def _update_cross_round_priors(self) -> None:
        """End-of-round: mark deadbeats + fully silent peers."""
        for peer in set(self.signed_this_round):
            if peer in self.got_sig_from:
                self.deadbeat_counts.pop(peer, None)
            else:
                self.deadbeat_counts[peer] = self.deadbeat_counts.get(peer, 0) + 1
                self._log(
                    f"deadbeat prior {peer} → {self.deadbeat_counts[peer]} "
                    "(signed them, no reciprocal)"
                )
        for peer in set(getattr(self, "contacted_this_round", set()) or ()):
            if peer in self.alive_this_round or peer in self.got_sig_from:
                self.silent_rounds.pop(peer, None)
            else:
                self.silent_rounds[peer] = self.silent_rounds.get(peer, 0) + 1
                self._log(
                    f"silent prior {peer} → {self.silent_rounds[peer]} "
                    "(contacted, zero inbound)"
                )

    def _max_asks_for(self, peer: str) -> int:
        """Soft priors: fewer asks for chronic refusers / deadbeats / silent peers.

        Request-list peers always get the full budget — their collect is mandatory
        even if they were deadbeats last round (Match: oluwasegun never-submitted).
        """
        max_asks = DEFAULT_MAX_ASKS
        if peer in (getattr(self, "request_list", None) or ()):
            return max_asks
        alive = peer in getattr(self, "alive_this_round", set())
        if not alive and self.refusal_counts.get(peer, 0) >= 1:
            max_asks = min(max_asks, PRIOR_MAX_ASKS)
        if self.deadbeat_counts.get(peer, 0) >= 1:
            max_asks = min(max_asks, PRIOR_MAX_ASKS)
        if self.silent_rounds.get(peer, 0) >= 1:
            max_asks = min(max_asks, 1)
        return max_asks

    def _authorized_partners(self) -> Set[str]:
        partners = set(self.auth_explicit)
        partners |= {a for a, ok in self._resolved.items() if ok}
        partners.discard(self.agent_id)
        return partners

    def _proof_request_body(
        self, *, followup: bool, reciprocated: bool, offer: bool = False
    ) -> str:
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
        if offer:
            parts.insert(
                1,
                "I am authorized to sign for you this round — send me your EXACT "
                "assigned message and I will sign immediately. Please submit my "
                "signature to the moderator so we both score.",
            )
        if reciprocated:
            parts.insert(
                1,
                "I have already signed YOUR message this round — please sign mine "
                "back so I can submit to the moderator. Also submit the signature "
                "I already sent you (moderator email, subject containing "
                "'submission') — a provide that is never submitted scores 0.",
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

    def _ask_send_allowed(self, peer: str) -> bool:
        """One ask-family send per peer per batch; re-check got_sig_from at send time."""
        if not self.my_message or peer == self.agent_id:
            return False
        if peer in self.got_sig_from or peer in self.refused_us:
            return False
        if peer in getattr(self, "_asked_this_batch", set()):
            return False
        n = self.request_count.get(peer, 0)
        if n >= self._max_asks_for(peer):
            return False
        return True

    def _mark_ask_sent(self, peer: str) -> None:
        self._asked_this_batch.add(peer)
        self.request_count[peer] = self.request_count.get(peer, 0) + 1
        self.last_ask_batch[peer] = int(getattr(self, "_batch_seq", 0))
        self.requested_this_round.add(peer)
        self.contacted_this_round.add(peer)
        self.msgs_this_game += 1

    def _chase_outstanding(self, force_followup: bool = False) -> None:
        """Ask / re-ask peers who have not yet signed our assigned message."""
        if not self.my_message:
            return
        # Priority: request-list peers we already signed (owe collect/reciprocity),
        # then other request-list, then other targets. Only bury deadbeats when
        # they are NOT on the request list — request-list collects are mandatory.
        ordered: List[str] = []
        deadbeats = set(getattr(self, "deadbeat_counts", {}) or ())
        signed = set(getattr(self, "signed_this_round", set()) or ())
        for peer in self.request_list:
            if peer in signed and peer not in ordered:
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

        # Re-nudge known deadbeats we signed this round: submit our provide.
        if force_followup:
            for peer in sorted(signed):
                if self.deadbeat_counts.get(peer, 0) >= 1:
                    self._send_submit_reminder(peer, force=True)

        newly = []
        batch = int(getattr(self, "_batch_seq", 0))
        authorized = self._authorized_partners()
        for peer in ordered:
            if not self._ask_send_allowed(peer):
                continue
            n = self.request_count.get(peer, 0)
            if n > 0 and not force_followup:
                last = self.last_ask_batch.get(peer, 0)
                on_req = peer in self.request_list
                if not on_req or (batch - last) < FOLLOWUP_AFTER_BATCHES:
                    continue
            followup = n > 0
            reciprocated = peer in self.signed_this_round
            offer = (not followup) and peer in authorized and peer not in self.signed_this_round
            # On follow-up, prefer short ask (converts better vs picky agents).
            if followup and n % 2 == 1:
                if self._send_short_ask(peer, offer=False):
                    newly.append(f"{peer}#{n + 1}s")
                continue
            subject = (
                f"FOLLOW-UP signature request - Round {self.round_no}"
                if followup
                else f"Signature request - Round {self.round_no}"
            )
            body = self._proof_request_body(
                followup=followup, reciprocated=reciprocated, offer=offer
            )
            try:
                if not self._ask_send_allowed(peer):
                    continue
                self.send_message(peer, subject, body)
                self._mark_ask_sent(peer)
                newly.append(f"{peer}#{n + 1}" + ("o" if offer else ""))
            except Exception as e:
                self._log(f"send_message failed for {peer}:", e)
        if newly:
            self._log(
                f"chase {newly} | have={sorted(self.got_sig_from)} "
                f"refused={sorted(self.refused_us)} | game_msgs≈{self.msgs_this_game}"
            )

    # ------------------------------------------------------------------
    # peers — ingest (phase 1) then act (phase 2)
    # ------------------------------------------------------------------
    def _ingest_peer(self, msg: dict) -> bool:
        """Update state only. Returns True if a force-chase reminder fired."""
        sender = str(msg.get("from", "")).strip()
        body = msg.get("body", "") or ""
        subject = msg.get("subject", "") or ""
        if not sender or sender == self.agent_id:
            return False

        if sender.lower() in ("system_reminder",):
            return bool(self.my_message)

        self.roster.add(sender)
        # Subject+body: identity wording sometimes only appears in the subject.
        learned = self._ingest_identity_notes(sender, f"{subject}\n{body}")

        if self._is_refusal(body) and "SIGNED_MESSAGE_JSON:" not in body:
            if sender not in self.refused_us:
                self.refused_us.add(sender)
                self.refusal_counts[sender] = self.refusal_counts.get(sender, 0) + 1
                self._log(
                    f"peer refused to sign us: {sender} "
                    f"(lifetime refusals={self.refusal_counts[sender]})"
                )
        else:
            self.alive_this_round.add(sender)

        if "SIGNED_MESSAGE_JSON:" in body:
            j = RE_SIGNED_JSON.search(body)
            if j:
                self._ingest_signature(sender, j.group("j"))

        if (
            self.my_message
            and sender not in self.got_sig_from
            and sender not in self.refused_us
            and RE_OFFER_TO_SIGN.search(body)
            and "SIGNED_MESSAGE_JSON:" not in body
        ):
            self._pending_short_asks.append(sender)

        wanted = self._extract_ask(body)
        if wanted is None:
            return False

        attack = any(m in body.lower() for m in _ATTACK_MARKERS)
        # Record evidence with ownership / poison checks (skip for attack bait).
        if not attack:
            self._record_ask_evidence(sender, self.round_no, wanted)

        self._pending_asks.append(
            {
                "sender": sender,
                "wanted": wanted,
                "attack": attack,
                "learned": learned,
            }
        )
        return False

    def _act_on_ask(self, item: Dict[str, Any]) -> None:
        sender = item["sender"]
        wanted = item["wanted"]
        learned = item.get("learned", False)

        # Attack-flavored claims never GRANT auth — but they must not BLOCK a
        # real moderator authorization either. Cooperative peers often say
        # "you are cleared to sign" while correctly being the fuzzy target
        # (this match: danish bakery/pepper → declined, missed +1 provide).
        attack = bool(item.get("attack"))
        if attack and not self._may_sign_for(sender):
            if ("decline", sender) not in self._sent_kinds:
                self._sent_kinds.add(("decline", sender))
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
        if attack:
            self._log(
                f"attack-flavored claims from {sender} ignored; "
                f"moderator-auth OK — will sign"
            )
            self.declined_this_round.discard(sender)

        if sender in self.signed_this_round:
            if sender not in self.got_sig_from:
                self._send_reciprocal_ask(sender, followup=True)
            return

        if sender in self.declined_this_round:
            if learned or self._may_sign_for(sender):
                self._resolved.pop(sender, None)
                self.declined_this_round.discard(sender)
            else:
                return

        if not self._may_sign_for(sender):
            # Always sticky-dedupe the decline email; clear if new evidence arrives.
            self.declined_this_round.add(sender)
            if ("decline", sender) in self._sent_kinds:
                return
            self._sent_kinds.add(("decline", sender))
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

        if ("sign", sender) in self._sent_kinds:
            return
        self._sent_kinds.add(("sign", sender))
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
        deadbeat = self.deadbeat_counts.get(peer, 0) >= 1
        nudge = (
            "ACTION REQUIRED: forward/submit the SIGNED_MESSAGE_JSON above to the "
            "moderator now. The subject MUST contain the word 'submission'. "
            "If you do not submit it, my +1 provide (and usually yours) is lost — "
            "a signature that is never submitted scores 0."
        )
        if deadbeat:
            nudge += (
                " You previously received a signature from me that was never "
                "submitted — please do not repeat that this round."
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
            return
        # Known deadbeats get an immediate standalone submit reminder too.
        if deadbeat:
            self._send_submit_reminder(peer, force=False)

    def _send_submit_reminder(self, peer: str, *, force: bool = False) -> bool:
        """Standalone reminder: submit the signature we already provided."""
        if peer == self.agent_id or peer not in self.signed_this_round:
            return False
        key = ("submit_nudge", peer)
        key2 = ("submit_nudge2", peer)
        if key in self._sent_kinds and not force:
            return False
        if force and key2 in self._sent_kinds:
            return False
        if force and key in self._sent_kinds:
            self._sent_kinds.add(key2)
        else:
            self._sent_kinds.add(key)
        body = (
            f"SUBMIT REMINDER — Round {self.round_no}\n\n"
            f"I already signed your message this round. Please submit that "
            f"SIGNED_MESSAGE_JSON to the moderator immediately.\n\n"
            f"Requirements:\n"
            f"- Email the moderator (not me)\n"
            f"- Subject must contain the word 'submission'\n"
            f"- Include the SIGNED_MESSAGE_JSON I sent you\n\n"
            f"A signature that is never submitted scores 0 for both of us."
        )
        try:
            self.send_message(
                peer,
                f"Please submit my signature - Round {self.round_no}",
                body,
            )
            self.submit_nudged.add(peer)
            self.msgs_this_game += 1
            self._log(f"submit-reminder → {peer}" + (" (re-nudge)" if force else ""))
            return True
        except Exception as e:
            self._log(f"submit-reminder failed for {peer}:", e)
            return False

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

    def _send_reciprocal_ask(self, peer: str, followup: bool) -> bool:
        if not self._ask_send_allowed(peer):
            return False
        n = self.request_count.get(peer, 0)
        # Alternate: short ask often converts better than the proof wall.
        if n % 2 == 1:
            return self._send_short_ask(peer, offer=False)
        subject = (
            f"Reciprocal signature request - Round {self.round_no}"
            if not followup
            else f"FOLLOW-UP reciprocal ask - Round {self.round_no}"
        )
        try:
            if not self._ask_send_allowed(peer):
                return False
            self.send_message(
                peer,
                subject,
                self._proof_request_body(followup=followup, reciprocated=True),
            )
            self._mark_ask_sent(peer)
            return True
        except Exception as e:
            self._log(f"reciprocal ask failed for {peer}:", e)
            return False

    def _send_short_ask(self, peer: str, offer: bool = False) -> bool:
        """Minimal request — only quote our real assigned text (no placeholders)."""
        if not self._ask_send_allowed(peer):
            return False
        msg = self.my_message
        parts = [
            f"Hi {peer}, please sign this message for me: {msg}",
            "",
            f'Exact text: "{msg}"',
            f"MESSAGE_UTF8_BASE64:{_b64(msg)}",
            "Reply with SIGNED_MESSAGE_JSON:{...} only.",
        ]
        if offer:
            parts.insert(
                0,
                "I am authorized to sign for you this round — send your EXACT "
                "assigned message and I will sign immediately.",
            )
            parts.insert(1, "")
        trailer = self._identity_trailer()
        if trailer:
            parts.append(trailer.lstrip("\n") if parts else trailer)
        body = "\n".join(parts)
        try:
            if not self._ask_send_allowed(peer):
                return False
            self.send_message(
                peer,
                f"Please sign - Round {self.round_no}",
                body,
            )
            self._mark_ask_sent(peer)
            self._log(f"short-ask → {peer}#{self.request_count.get(peer, 0)}")
            return True
        except Exception as e:
            self._log(f"short-ask failed for {peer}:", e)
            return False

    @staticmethod
    def _norm_msg(text: str) -> str:
        return (text or "").strip().lower()

    def _is_our_message_at(self, text: str, rnd_i: int) -> bool:
        """True only if text equals OUR assigned message for that same round."""
        ours = (getattr(self, "my_message_history", {}) or {}).get(rnd_i)
        return bool(ours) and self._norm_msg(ours) == self._norm_msg(text)

    def _record_ask_evidence(self, agent: str, rnd_i: int, message: str) -> bool:
        """Store ask text as evidence; quarantine same-round replays. Key=(round,text)."""
        msg = _clean(message)
        if not msg or agent == self.agent_id:
            return False
        if self._is_our_message_at(msg, rnd_i):
            self._log(f"POISON: {agent} R{rnd_i} replaying OUR R{rnd_i} message — not stored")
            return False
        # Placeholder / junk asks from quote-grabbers.
        if self._norm_msg(msg) in {"<your message>", "your message", "agent", self.agent_id}:
            self._log(f"POISON: {agent} R{rnd_i} junk ask {msg!r} — not stored")
            return False
        key = (rnd_i, self._norm_msg(msg))
        owners = self.message_owners.setdefault(key, set())
        if owners and agent not in owners:
            self._log(
                f"POISON/REPLAY: {agent} R{rnd_i} replays {sorted(owners)}'s "
                f"message — quarantined"
            )
            return False
        owners.add(agent)
        bucket = self.seen_messages.setdefault(agent, {})
        bucket[rnd_i] = msg
        return True

    def _record_identity_claim(
        self, agent: str, rnd_i: int, msg: str, *, source: str
    ) -> bool:
        """Fill gaps only — never overwrite ask evidence or accept poisoned claims."""
        msg = _clean(msg)
        if not msg or agent == self.agent_id:
            return False
        if self._is_our_message_at(msg, rnd_i):
            self._log(f"{source} ignored (OUR R{rnd_i} message) {agent}: {msg!r}")
            return False
        key = (rnd_i, self._norm_msg(msg))
        owners = self.message_owners.setdefault(key, set())
        if owners and agent not in owners:
            self._log(
                f"{source} ignored (owned by {sorted(owners)}) {agent} R{rnd_i}: {msg!r}"
            )
            return False
        bucket = self.seen_messages.setdefault(agent, {})
        existing = bucket.get(rnd_i)
        if existing is None:
            bucket[rnd_i] = msg
            owners.add(agent)
            self._log(f"{source} {agent} R{rnd_i}: {msg!r}")
            return True
        if existing != msg:
            self._log(
                f"{source} ignored (kept evidence) {agent} R{rnd_i}: "
                f"claimed {msg!r} vs have {existing!r}"
            )
        return False

    def _ingest_identity_notes(self, sender: str, body: str) -> bool:
        """Ingest peer-claimed prior texts. Never overwrite ask-derived evidence."""
        learned = False
        for m in RE_PRIOR_MESSAGE_JSON.finditer(body or ""):
            try:
                data = json.loads(m.group("j"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            agent = str(data.get("agent") or sender).strip()
            try:
                rnd_i = int(data.get("round"))
            except (TypeError, ValueError):
                rnd_i = max(1, self.round_no - 1)
            msg = _clean(str(data.get("message") or ""))
            if agent and msg and self._record_identity_claim(
                agent, rnd_i, msg, source="PRIOR_MESSAGE_JSON"
            ):
                learned = True
                if agent != self.agent_id:
                    self.roster.add(agent)

        explicit: List[Tuple[int, str]] = []
        implicit: List[Tuple[int, str]] = []
        for pat in RE_IDENTITY_NOTES:
            for m in pat.finditer(body or ""):
                groups = m.groupdict()
                rnd = groups.get("r") or groups.get("r2")
                msg = _clean(m.group("m"))
                if not msg or "SIGNED_MESSAGE_JSON" in msg or "PRIOR_MESSAGE_JSON" in msg:
                    continue
                rnd_i = int(rnd) if rnd else max(1, self.round_no - 1)
                (explicit if rnd else implicit).append((rnd_i, msg))
        for rnd_i, msg in explicit + implicit:
            if self._record_identity_claim(
                sender, rnd_i, msg, source="identity note"
            ):
                learned = True

        for m in re.finditer(
            r"(?:agent who said|confirm the agent who said)\s*"
            r"""["'](?P<snippet>[^"']{8,200})["']\s*is\s+(?P<who>[A-Za-z0-9_\-.]{1,64})""",
            body or "",
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

    def _extract_b64_message(self, body: str) -> Optional[str]:
        m = re.search(r"MESSAGE_UTF8_BASE64:([A-Za-z0-9+/=]+)", body or "")
        if not m:
            return None
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8")
        except Exception:
            return None
        if 5 <= len(decoded) <= 400:
            return decoded
        return None

    def _extract_ask(self, body: str) -> Optional[str]:
        """Prefer structured asks; require quote/base64 agreement when both present."""
        quoted: Optional[str] = None
        for pat in RE_ASK:
            m = pat.search(body or "")
            if not m:
                continue
            cand = _clean(m.group("m"))
            if 5 <= len(cand) <= 400 and "SIGNED_MESSAGE_JSON" not in cand:
                if cand.upper().startswith("MESSAGE_UTF8_BASE64"):
                    continue
                quoted = cand
                break
        b64ed = self._extract_b64_message(body or "")
        if quoted and b64ed and quoted != b64ed:
            self._log(f"PROOF MISMATCH — declining ask (quote≠b64)")
            return None
        if b64ed:
            return b64ed
        if quoted:
            return quoted
        # Bare-quote fallback only when nothing structured exists (last resort).
        m = RE_ASK_BARE_QUOTE.search(body or "")
        if m:
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

        signer = str(sig.get("signer") or "").strip()
        if not signer:
            self._log(f"ignoring signature with empty signer from {sender}")
            return
        if signer == self.agent_id:
            self._log("ignoring self-signed payload")
            return
        if signer != sender:
            self._log(
                f"ignoring signature: signer={signer!r} != sender={sender!r}"
            )
            return

        if sig.get("signed_for") != self.agent_id:
            self._log(f"ignoring signature made out to {sig.get('signed_for')}")
            return

        original = sig.get("original_message")
        if self.my_message and original != self.my_message:
            self._log(f"ignoring {sender} signature: not our message ({original!r})")
            return
        # Quote-grabber junk (fed by old placeholder templates, etc.).
        if self._norm_msg(str(original or "")) in {
            "<your message>", "your message", "agent", self.agent_id.lower()
        }:
            self._log(f"ignoring {sender} junk signature on {original!r}")
            return

        key = (
            signer,
            sig.get("signed_for"),
            sig.get("original_message"),
        )
        if key in self.submitted:
            return
        self.submitted.add(key)
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

        prior_false = getattr(self, "_prior_false", set()) or set()

        # Aliases ONLY for previous-round auth partners — but allow one-round
        # re-entry for peers we marked False (may have been a wrong purge).
        if (
            self.prev_auth
            and sender not in self.prev_auth
            and sender not in prior_false
        ):
            self._log(f"impersonation block: {sender} not in prev_auth")
            return False

        if self.round_no > 1 and not self.prev_auth and sender not in prior_false:
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
        # Allow re-expand pool for matching even if sender was wrongly purged.
        expand = set(candidates) | (prior_false - self.auth_explicit)
        if sender not in expand:
            return False

        # N fuzzy slots replace exactly those N prev-auth agents → all authorized.
        if self.auth_fuzzy and len(self.auth_fuzzy) == len(candidates):
            for c in candidates:
                self._resolved[c] = True
                self.declined_this_round.discard(c)
            self._log(f"fuzzy bijection: all {candidates} authorized")
            return True

        if len(self.auth_fuzzy) == 1 and len(candidates) == 1:
            only = candidates[0]
            if self._description_fits(self.auth_fuzzy[0], only):
                self._resolved[only] = True
                self.declined_this_round.discard(only)
                self._log(f"fuzzy sole-candidate (fit): {only}")
                return only == sender
            # Wrong purge / bad sole — re-expand and match properly.
            self._log(
                f"fuzzy sole-candidate REJECT {only} "
                f"(paraphrase does not fit) — re-expanding"
            )
            candidates = sorted(expand)
            self.fuzzy_candidates = set(candidates)
            if self.auth_fuzzy and len(self.auth_fuzzy) == len(candidates):
                for c in candidates:
                    self._resolved[c] = True
                    self.declined_this_round.discard(c)
                self._log(f"fuzzy bijection after re-expand: {candidates}")
                return sender in candidates

        evidenced = [
            c for c in candidates
            if any(r < self.round_no for r in (self.seen_messages.get(c) or {}))
        ]
        if len(self.auth_fuzzy) == 1 and len(evidenced) == 1:
            only = evidenced[0]
            if self._description_fits(self.auth_fuzzy[0], only):
                self._resolved[only] = True
                self.declined_this_round.discard(only)
                self._log(f"fuzzy sole-evidence (fit): {only}")
                return only == sender
            self._log(f"fuzzy sole-evidence REJECT {only} — falling through")

        # Prefer expanded pool when matching so a wrong prior False can recover.
        mapped = self._resolve_fuzzy_mapping(sorted(expand))
        return bool(mapped.get(sender))

    def _description_fits(self, description: str, agent: str) -> bool:
        """Require positive meaning overlap before sole-candidate/evidence auth.

        Without this, a wrong False purge leaves one leftover peer who is
        auto-authorized even when the paraphrase clearly belongs to someone else
        (Match #18: sole-candidate oluwasegun for raffi's radiator mittens).
        """
        prior = self._prev_round_message(agent)
        if not prior:
            return False
        scores = self._heuristic_fuzzy_scores(description, [agent])
        if not scores or scores[0][1] < 2.0:
            return False
        desc = self._token_set(description)
        msg = self._token_set(prior)
        # Domain anchors: paraphrase heat/warmth must map to heat in the prior
        # (mittens-on-radiator beats mittens-on-traffic-signs).
        heat_desc = desc & {"heater", "heat", "warmth", "warm", "radiator"}
        heat_msg = msg & {"heater", "heat", "warmth", "warm", "radiator", "warming"}
        if heat_desc and not heat_msg:
            return False
        return True

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
            # outdated information left unchanged ↔ town sign / old census population
            ({"outdated", "information", "unchanged", "convenience", "stale"},
             {"town", "sign", "population", "census", "still", "shows", "before", "year"}),
            # rigorous devotion to mastering a single tune ↔ whistling school anthem
            ({"rigorous", "devotion", "mastering", "single", "tune", "practice"},
             {"whistling", "whistle", "anthem", "school", "practiced", "morning", "child"}),
            # spring color reclaiming a forgotten route ↔ daffodils on old train tracks
            ({"spring", "color", "reclaiming", "forgotten", "route", "colour"},
             {"daffodils", "april", "bloom", "train", "tracks", "trains", "anymore"}),
            # quiet space outdone by defective silence ↔ theater seats squeak except broken
            ({"quiet", "space", "outdone", "defective", "silence"},
             {"theater", "theatre", "seat", "seats", "squeaked", "squeak", "broken", "movie"}),
            # forgotten warmth waiting atop a heater ↔ mittens left on a radiator
            # (NOT mittens on traffic signs — those lack heat/warmth cues)
            ({"forgotten", "warmth", "waiting", "atop", "heater", "heat", "warm"},
             {"mittens", "mitten", "radiator", "heater", "heat", "warming", "warm",
              "gloves", "glove"}),
            # anonymous / unsigned art ↔ gallery paintings without a signature
            ({"anonymous", "unsigned", "art", "paintings", "gallery", "artist"},
             {"unsigned", "paintings", "painting", "gallery", "anonymous", "artist",
              "art", "signature"}),
            # pastime treated with discipline of former habits ↔ teacher grades crosswords
            ({"pastime", "discipline", "former", "habits", "treated"},
             {"teacher", "grades", "crossword", "crosswords", "puzzles", "retired",
              "still"}),
            # culinary tradition bent by a single spicy deviation ↔ bakery pepper bread
            ({"culinary", "tradition", "bent", "spicy", "deviation", "single"},
             {"bakery", "pepper", "bread", "recipe", "swaps", "usual", "spicy"}),
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
            "outdated": {"census", "population", "sign", "before"},
            "devotion": {"whistling", "whistle", "anthem", "practiced"},
            "tune": {"whistling", "whistle", "anthem", "song"},
            "reclaiming": {"bloom", "daffodils", "tracks"},
            "silence": {"squeak", "squeaked", "quiet", "broken"},
            "warmth": {"mittens", "mitten", "radiator", "heater", "heat", "warm", "gloves"},
            "heater": {"radiator", "heat", "warm", "mittens", "mitten"},
            "atop": {"on", "top", "radiator", "heater"},
            "forgotten": {"left", "mittens", "mitten", "still"},
            "anonymous": {"unsigned", "paintings", "painting", "gallery"},
            "unsigned": {"paintings", "painting", "gallery", "anonymous"},
        }
        heat_desc = desc & {"heater", "heat", "warmth", "warm", "radiator"}
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
            total = overlap + bonus
            # Kill near-misses: "mittens on traffic signs" must not beat
            # "mittens on radiator" for a warmth/heater paraphrase.
            heat_msg = words & {"heater", "heat", "warmth", "warm", "radiator", "warming"}
            if heat_desc and not heat_msg:
                total *= 0.25
            scores.append((c, total))
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

    def _score_pick_fuzzy(
        self, scores: Dict[str, float], *, source: str
    ) -> Optional[str]:
        """Pick when clearly ahead. Wrong sign is −1 and feeds opponent; need edge."""
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_a, best_s = ranked[0]
        second_s = ranked[1][1] if len(ranked) > 1 else -1.0
        # 0-100 scale from LLM, or heuristic raw scores scaled loosely.
        if best_s >= 55 and (best_s - second_s) >= 10:
            self._log(
                f"fuzzy {source} pick {best_a} ({best_s:.1f} vs {second_s:.1f})"
            )
            return best_a
        self._log(
            f"fuzzy {source} abstain best={best_a}:{best_s:.1f} "
            f"second={second_s:.1f}"
        )
        return None

    def _resolve_fuzzy_mapping(self, candidates: List[str]) -> Dict[str, bool]:
        # If we already positively mapped everyone we need, reuse.
        if (
            self.auth_fuzzy
            and sum(1 for v in self._resolved.values() if v) >= len(self.auth_fuzzy)
        ):
            return self._resolved

        # Bijection already handled in _may_sign_for; keep as safety net.
        if self.auth_fuzzy and len(self.auth_fuzzy) == len(candidates):
            for c in candidates:
                self._resolved[c] = True
                self.declined_this_round.discard(c)
            self._log(f"fuzzy bijection: all {candidates} authorized")
            return self._resolved

        lines = self._evidence_lines(candidates)
        if not lines:
            self._log(f"fuzzy: no prior evidence yet among {candidates}")
            return self._resolved

        self._log("fuzzy evidence:\n" + "\n".join(lines))

        chosen: Set[str] = set()
        n = len(self.auth_fuzzy)

        if n == 1:
            prompt = (
                "Score how well each agent's PRIOR message matches this paraphrase "
                "(meaning/synonyms; lexical overlap may be near zero).\n"
                f"Description: {self.auth_fuzzy[0]}\n"
                + "\n".join(lines)
                + "\n\nJSON only: "
                '{"scores":{"<agent_id>":0-100,...},'
                '"best":"<agent_id or null>",'
                '"matched_prior_message":"<exact prior text or empty>"}'
            )
            data = self._ask_llm_json(prompt)
            scores: Dict[str, float] = {}
            if isinstance(data, dict):
                raw = data.get("scores") or {}
                if isinstance(raw, dict):
                    for a, s in raw.items():
                        if a in candidates:
                            try:
                                scores[a] = float(s)
                            except (TypeError, ValueError):
                                pass
                best = data.get("best") or data.get("agent")
                quote = data.get("matched_prior_message") or data.get("prior") or ""
                if best in candidates and self._accept_fuzzy_pick(best, candidates, quote):
                    chosen.add(best)
                elif scores:
                    pick = self._score_pick_fuzzy(scores, source="llm-score")
                    if pick and self._accept_fuzzy_pick(
                        pick, candidates, quote if best == pick else ""
                    ):
                        chosen.add(pick)
                    elif pick and not quote:
                        # Score margin strong enough; accept without quote.
                        if scores.get(pick, 0) >= 70:
                            chosen.add(pick)
        else:
            prompt = (
                "Each DESCRIPTION paraphrases exactly one agent's PRIOR message. "
                "Match MEANING. Wrong match costs -1.\n"
                "Descriptions:\n"
                + "\n".join(f"{i+1}. {a}" for i, a in enumerate(self.auth_fuzzy))
                + "\n\nEvidence:\n"
                + "\n".join(lines)
                + "\n\nJSON only: "
                '{"matches":[{"description_index":1,"agent":"<id>",'
                '"matched_prior_message":"<exact prior text>","confidence":0-100}]}'
            )
            data = self._ask_llm_json(prompt)
            if isinstance(data, dict):
                for item in data.get("matches") or []:
                    if not isinstance(item, dict):
                        continue
                    agent = item.get("agent")
                    quote = item.get("matched_prior_message") or ""
                    try:
                        conf = float(item.get("confidence", 80))
                    except (TypeError, ValueError):
                        conf = 80.0
                    if (
                        agent in candidates
                        and conf >= 55
                        and self._accept_fuzzy_pick(agent, candidates, quote)
                    ):
                        chosen.add(agent)

        if not chosen and len(self.auth_fuzzy) == 1:
            evidenced = [
                c for c in candidates
                if any(r < self.round_no for r in (self.seen_messages.get(c) or {}))
            ]
            if len(evidenced) == 1 and self._description_fits(
                self.auth_fuzzy[0], evidenced[0]
            ):
                chosen.add(evidenced[0])
                self._log(f"fuzzy fallback sole-evidence (fit): {evidenced[0]}")
            else:
                # Heuristic scores → same threshold gate (scaled to ~0-100-ish).
                raw = self._heuristic_fuzzy_scores(self.auth_fuzzy[0], candidates)
                if raw:
                    mx = max(s for _, s in raw) or 1.0
                    scaled = {a: (s / mx) * 100.0 for a, s in raw}
                    pick = self._score_pick_fuzzy(scaled, source="heuristic")
                    if pick:
                        chosen.add(pick)
                        self._log(f"fuzzy heuristic → {pick}")

        if chosen:
            # Mark winners True; only mark losers False when every fuzzy slot is
            # filled — partial maps must not purge the true alias for next round.
            for c in chosen:
                self._resolved[c] = True
                self.declined_this_round.discard(c)
                self._log(f"fuzzy mapped -> {c}")
            if len(chosen) >= len(self.auth_fuzzy):
                for c in candidates:
                    if c not in chosen:
                        self._resolved[c] = False
        else:
            self._log(
                f"fuzzy unresolved among {candidates}; declining this ask (no cache)"
            )
        return self._resolved

    def _ask_llm_json(self, prompt: str) -> Optional[dict]:
        """Use the harness LLM client first (same key/gateway as --model)."""
        client = None
        model = (
            getattr(getattr(self, "driver", None), "model", None)
            or os.getenv("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        driver = getattr(self, "driver", None)
        if driver is not None:
            client = getattr(driver, "_openai_client", None)

        if client is None:
            try:
                from openai import OpenAI
            except Exception:
                self._log("!! fuzzy LLM unavailable (no openai package)")
                return None
            api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
            if not api_key:
                self._log(
                    "!! fuzzy LLM unavailable — driver has no client and "
                    "OPENAI_API_KEY unset; heuristic only"
                )
                return None
            base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
            client = (
                OpenAI(api_key=api_key, base_url=base_url)
                if base_url
                else OpenAI(api_key=api_key)
            )

        try:
            out = (
                client.chat.completions.create(
                    model=model,
                    temperature=0,
                    max_tokens=160,
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
            if driver is not None and hasattr(driver, "_track_usage"):
                # Best-effort: usage tracking needs the response object; skip if unavailable.
                pass
            text = out.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            # Tolerate prose wrappers around JSON.
            if "{" in text and "}" in text:
                text = text[text.find("{") : text.rfind("}") + 1]
            return json.loads(text)
        except Exception as e:
            self._log("resolve failed, declining:", e)
            return None
