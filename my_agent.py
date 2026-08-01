"""
The Email Game – competition agent.

Strategy (finish order wins TrueSkill, not point margin):
  1. Deterministic protocol: request / submit / authorized-sign never wait on an LLM.
  2. Out-collect: ask EVERY known peer for a signature, not only the request list.
  3. Defense: never sign unless the requester is confidently authorized. Unsure → decline.
  4. Fuzzy auth (rounds 2+): resolve paraphrases against messages peers asked us to sign,
     with a temperature-0 LLM fallback only when token overlap is ambiguous.
  5. Ignore peer claims about authorization; only the moderator's list counts.

    python scripts/run_custom_agent.py <your-agent-name> --module my_agent.py \\
        --prompt docs/winning_prompt.md --model gpt-4.1 --temperature 0.2 \\
        --server https://play.theemailgame.com
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.base_agent import BaseAgent

# Common content words to ignore when matching paraphrases.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "by",
    "with", "from", "who", "that", "this", "these", "those", "their", "my",
    "your", "our", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "can", "could", "may",
    "might", "must", "shall", "should", "about", "into", "over", "under",
    "agent", "mentioned", "spoke", "said", "round", "last", "message", "different",
    "may", "their", "from",
}

# Light synonym clusters for whimsical paraphrase matching (works for sample pool
# and similar live paraphrases; LLM fallback covers the rest).
_SYN_CLUSTERS = [
    {"penguin", "penguins", "arctic", "waddling", "bird", "birds"},
    {"ice", "cream", "dessert", "parlor", "frozen", "establishment"},
    {"cactus", "spiky", "desert", "plant", "harmonica", "wind", "instrument"},
    {"library", "book", "books", "midnight", "late", "night", "haven", "repository"},
    {"butterfly", "butterflies", "soup", "broth", "lepidopteran"},
    {"elephant", "elephants", "pachyderm", "pachyderms", "violet", "purple"},
    {"tea", "party", "gathering", "garden", "backyard"},
    {"happiness", "joy", "rainbow", "paperclip", "paperclips", "stationery", "fastener", "fasteners"},
    {"grandmother", "relative", "bicycle", "airborne", "flying", "pedal", "windshield", "wiper", "wipers"},
    {"fish", "aquarium", "aquatic", "italian", "romance", "language", "conversational"},
    {"sock", "socks", "footwear", "invisible", "unseen", "morning", "dawn"},
    {"moonbeam", "lunar", "light", "thursday", "association", "gathering"},
    {"coffee", "mug", "beverage", "container", "poetry", "verses", "clouds", "atmospheric"},
    {"toaster", "bread", "browning", "enchanted", "magical", "sing", "singing"},
    {"quantum", "jellybean", "jellybeans", "confectioneries", "parallel", "universe", "universes", "realities"},
    {"clockwork", "mechanical", "squirrel", "squirrels", "rodent", "rodents", "nut", "seed", "revolution", "uprising"},
    {"shadow", "puppet", "silhouette", "theater", "interdimensional", "drama", "storytelling"},
    {"cosmic", "pickle", "jar", "celestial", "dreams", "aspirations", "essence", "spirit"},
    {"mushroom", "mushrooms", "fungi", "translucent", "classical", "music", "orchestral"},
    {"velvet", "hamster", "plush", "kitchen", "pantry", "culinary", "storage"},
    {"floating", "hovering", "teacup", "teacups", "storm", "cloud", "tempestuous", "brew", "infusion"},
    {"whispering", "calculator", "mathematical", "existential", "crisis", "dilemma"},
    {"crystalline", "butterfly", "butterflies", "insects", "migrate", "solstice", "winter"},
    {"laughing", "lighthouse", "maritime", "starlight", "celestial", "luminescence"},
    {"dragon", "mythical", "vegetarian", "pizza", "cheese", "dairy", "fire"},
    {"backwards", "clock", "timepiece", "egyptian", "hieroglyphs", "egypt", "symbols"},
    {"origami", "crane", "cranes", "paper", "airplane", "festival", "folded", "birds"},
    {"giraffe", "triangle", "marching", "band"},
    {"banana", "bananas", "fashion", "runway", "fridge", "refrigerator"},
]


def _tokens(text: str) -> Set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _expand(tokens: Set[str]) -> Set[str]:
    out = set(tokens)
    for cluster in _SYN_CLUSTERS:
        if tokens & cluster:
            out |= cluster
    return out


def _score_match(description: str, message: str) -> float:
    """Score how well a fuzzy description matches a known prior message."""
    d = _expand(_tokens(description))
    m = _expand(_tokens(message))
    if not d or not m:
        return 0.0
    overlap = len(d & m)
    # Prefer precision against the description (aliases are denser paraphrases).
    return overlap / max(1, len(d))


_REQUEST_PATTERNS = [
    re.compile(
        r"please\s+sign\s+(?:this\s+)?(?:message\s+)?(?:for\s+me\s*)?:?\s*[\"']?(.+?)[\"']?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"sign\s+(?:this\s+)?(?:exact\s+)?message\s*(?:for\s+me)?\s*:?\s*[\"'](.+?)[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        r"sign\s+(?:the\s+)?(?:following|this)\s*:?\s*[\"']?(.+?)[\"']?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"message\s+for\s+me\s*:?\s*[\"']?(.+?)[\"']?\s*$",
        re.IGNORECASE,
    ),
]


class CustomAgent(BaseAgent):
    """Deterministic protocol agent with aggressive collection and careful defense."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reset_game_state()

    def _reset_game_state(self) -> None:
        self.round_number = 0
        self.assigned_message: Optional[str] = None
        self.request_list: List[str] = []
        # Each entry is either an explicit agent id or a fuzzy description string.
        self.auth_entries: List[str] = []
        self.explicit_authorized: Set[str] = set()
        # Fuzzy description -> resolved agent id (only high-confidence).
        self.fuzzy_resolved: Dict[str, str] = {}
        self.fuzzy_rejected: Set[str] = set()
        # agent_id -> messages they asked us to sign (prior-round evidence).
        self.seen_messages: Dict[str, List[str]] = {}
        self.known_agents: Set[str] = set()
        self.requested_this_round: Set[str] = set()
        self.signed_for_this_round: Set[str] = set()
        self.declined_this_round: Set[str] = set()
        self.submitted_keys: Set[Tuple[str, str, str]] = set()

    def on_new_game(self) -> None:
        self._reset_game_state()

    def on_message_batch(self, messages: List[Dict]) -> None:
        # 1) Moderator first (assignments / scoring notices).
        for msg in messages:
            if msg.get("from") == "moderator":
                self._handle_moderator(msg)

        # 2) Auto-submit every signature payload before anything else.
        for msg in messages:
            if msg.get("from") == "moderator":
                continue
            self._maybe_submit_signed(msg)

        # 3) Learn peer identities / prior messages, then serve or decline requests.
        pending_llm: List[Dict] = []
        for msg in messages:
            sender = msg.get("from")
            if not sender or sender in ("moderator", "system_reminder"):
                continue
            self.known_agents.add(sender)
            handled = self._handle_peer(msg)
            if not handled:
                pending_llm.append(msg)

        # 4) Ask newly discovered peers for signatures on our assigned message.
        self._request_from_known_agents()

        # 5) LLM only for leftovers we could not classify (rare).
        if pending_llm:
            try:
                super().on_message_batch(pending_llm)
            except Exception as e:
                print(f"[{self.agent_id}] LLM fallback error: {e}")

    # ------------------------------------------------------------------
    # Moderator
    # ------------------------------------------------------------------
    def _handle_moderator(self, message: Dict) -> None:
        body = message.get("body", "") or ""
        subject = (message.get("subject") or "").lower()

        # Scoring notices / rejects — actionable only if we need to re-request.
        if "scoring:" in subject or "scoring:" in body.lower():
            print(f"[{self.agent_id}] Scoring notice: {message.get('subject')}")
            if "not your message" in body.lower() or "rejected" in body.lower():
                # Re-issue requests with the exact assigned text if we still have it.
                if self.assigned_message:
                    self.requested_this_round.clear()
                    self._request_from_known_agents(force=True)
            return

        rm = re.search(r"\*\*ROUND\s+(\d+)\*\*", body, re.IGNORECASE)
        if not rm and "EXACT message:" not in body:
            return

        if rm:
            self.round_number = int(rm.group(1))

        assigned = self._parse_assigned_message(body)
        if not assigned:
            # Informational moderator mail without a new assignment.
            return

        self.assigned_message = assigned
        self.request_list = self._parse_request_list(body)
        self.auth_entries = self._parse_auth_list(body)
        self.explicit_authorized = {a for a in self.auth_entries if self._is_explicit_name(a)}
        self.fuzzy_resolved.clear()
        self.fuzzy_rejected.clear()
        self.requested_this_round.clear()
        self.signed_for_this_round.clear()
        self.declined_this_round.clear()

        for name in self.request_list:
            self.known_agents.add(name)
        for name in self.explicit_authorized:
            self.known_agents.add(name)

        # Pre-resolve fuzzy auth entries against prior-round evidence.
        for entry in self.auth_entries:
            if not self._is_explicit_name(entry):
                self._resolve_fuzzy(entry)

        print(
            f"[{self.agent_id}] Round {self.round_number} assigned="
            f"{assigned!r} request={self.request_list} auth={self.auth_entries} "
            f"resolved={self.fuzzy_resolved}"
        )
        self._request_from_known_agents(force=True)

    def _parse_assigned_message(self, body: str) -> Optional[str]:
        for line in body.splitlines():
            if "EXACT message:" in line:
                return line.split("EXACT message:", 1)[1].strip().strip('"').strip("'")
        m = re.search(
            r"get signatures for this EXACT message:\s*[\"'](.+?)[\"']",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            return m.group(1).strip()
        return None

    def _parse_request_list(self, body: str) -> List[str]:
        for line in body.splitlines():
            if "request signatures from these agents:" in line.lower():
                names = line.split(":", 1)[1]
                return [n.strip() for n in names.split(",") if n.strip() and n.strip().lower() != "none"]
        return []

    def _parse_auth_list(self, body: str) -> List[str]:
        for line in body.splitlines():
            lower = line.lower()
            if "authorized to sign messages for these agents:" in lower or \
               "authorized to sign for these agents:" in lower:
                raw = line.split(":", 1)[1].strip()
                if not raw or raw.lower() == "none":
                    return []
                # Fuzzy entries can contain commas inside the paraphrase; split
                # carefully. Explicit ids are single tokens; fuzzy ones start with
                # "the agent who" / "The agent who".
                return self._split_auth_entries(raw)
        return []

    def _split_auth_entries(self, raw: str) -> List[str]:
        # Split on commas that begin a new fuzzy clause ("..., the agent who...").
        parts = re.split(r",\s*(?=the agent\b)", raw, flags=re.IGNORECASE)
        out: List[str] = []
        for part in parts:
            part = part.strip().strip(",")
            if not part:
                continue
            if re.search(r"\bthe agent\b", part, re.IGNORECASE):
                # Peel trailing explicit ids: "...different), bob, carol"
                m = re.match(
                    r"^(.*?\(.*?\))\s*,\s*(.+)$",
                    part,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if m:
                    out.append(m.group(1).strip())
                    out.extend([n.strip() for n in m.group(2).split(",") if n.strip()])
                else:
                    # Or: "the agent who ..., bob" without parens
                    m2 = re.match(
                        r"^(the agent\b.*?)(?:,\s*([A-Za-z0-9_\-.]{1,64}(?:\s*,\s*[A-Za-z0-9_\-.]{1,64})*))\s*$",
                        part,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    if m2 and m2.group(2):
                        out.append(m2.group(1).strip())
                        out.extend([n.strip() for n in m2.group(2).split(",") if n.strip()])
                    else:
                        out.append(part)
            else:
                out.extend([n.strip() for n in part.split(",") if n.strip()])
        return out

    def _is_explicit_name(self, entry: str) -> bool:
        e = entry.strip()
        if not e:
            return False
        if re.search(r"\bthe agent who\b", e, re.IGNORECASE):
            return False
        if " " in e and len(e) > 40:
            return False
        # Agent ids are short tokens (letters, digits, underscore, hyphen).
        return bool(re.fullmatch(r"[A-Za-z0-9_\-.]{1,64}", e.split()[0])) and " " not in e

    # ------------------------------------------------------------------
    # Requests / collection
    # ------------------------------------------------------------------
    def _request_from_known_agents(self, force: bool = False) -> None:
        if not self.assigned_message:
            return
        targets = set(self.request_list) | set(self.known_agents)
        targets.discard(self.agent_id)
        for agent_id in sorted(targets):
            if not force and agent_id in self.requested_this_round:
                continue
            self.send_message(
                to_agent=agent_id,
                subject=f"Signature Request - Round {self.round_number}",
                body=(
                    f"Hi {agent_id}, please sign this message for me: "
                    f"{self.assigned_message}"
                ),
            )
            self.requested_this_round.add(agent_id)
            print(f"[{self.agent_id}] Requested signature from {agent_id}")

    # ------------------------------------------------------------------
    # Peer handling
    # ------------------------------------------------------------------
    def _handle_peer(self, message: Dict) -> bool:
        """Return True if fully handled without LLM."""
        sender = message.get("from", "")
        body = message.get("body", "") or ""

        # Already submitted signatures above; still treat as handled.
        if "SIGNED_MESSAGE_JSON:" in body:
            # Some emails are signature-only replies; done.
            if not self._extract_sign_request(body):
                return True

        requested_msg = self._extract_sign_request(body)
        if requested_msg:
            # Learn their assigned message for future fuzzy rounds.
            self.seen_messages.setdefault(sender, [])
            if requested_msg not in self.seen_messages[sender]:
                self.seen_messages[sender].append(requested_msg)

            if sender in self.signed_for_this_round or sender in self.declined_this_round:
                return True

            if self._is_authorized(sender):
                self.sign_and_respond(
                    to_agent=sender,
                    message_to_sign=requested_msg,
                    response_body="Here is your signed message as requested.",
                    subject=f"Signed Message - Round {self.round_number}",
                )
                self.signed_for_this_round.add(sender)
                print(f"[{self.agent_id}] Signed for authorized agent {sender}")
            else:
                self.send_message(
                    to_agent=sender,
                    subject=f"Signature Declined - Round {self.round_number}",
                    body=(
                        f"Hi {sender}, I am not authorized to sign for you this round, "
                        f"so I must decline. Good luck!"
                    ),
                )
                self.declined_this_round.add(sender)
                print(f"[{self.agent_id}] Declined unauthorized/unsure request from {sender}")
            return True

        # Friendly chatter / attacks with no actionable ask — ignore (no LLM spend).
        return True

    def _extract_sign_request(self, body: str) -> Optional[str]:
        # Prefer quoted exact strings.
        quoted = re.findall(r"[\"']([^\"']{5,240})[\"']", body)
        for q in quoted:
            if "SIGNED_MESSAGE_JSON" in q:
                continue
            # Likely the payload if the surrounding text mentions signing.
            if re.search(r"sign", body, re.IGNORECASE):
                return q.strip()

        for line in body.splitlines():
            line = line.strip()
            if not line or "SIGNED_MESSAGE_JSON" in line:
                continue
            for pat in _REQUEST_PATTERNS:
                m = pat.search(line)
                if m:
                    msg = m.group(1).strip().strip('"').strip("'")
                    if msg and "SIGNED_MESSAGE_JSON" not in msg:
                        return msg
        return None

    def _is_authorized(self, sender: str) -> bool:
        if sender in self.explicit_authorized:
            return True
        # Resolve any unresolved fuzzy entries that might map to sender.
        for entry in self.auth_entries:
            if self._is_explicit_name(entry):
                continue
            resolved = self.fuzzy_resolved.get(entry)
            if resolved is None and entry not in self.fuzzy_rejected:
                resolved = self._resolve_fuzzy(entry)
            if resolved == sender:
                return True
        return False

    def _resolve_fuzzy(self, description: str) -> Optional[str]:
        if description in self.fuzzy_resolved:
            return self.fuzzy_resolved[description]
        if description in self.fuzzy_rejected:
            return None

        candidates: List[Tuple[str, float]] = []
        for agent_id, msgs in self.seen_messages.items():
            if agent_id == self.agent_id:
                continue
            best = max((_score_match(description, m) for m in msgs), default=0.0)
            if best > 0:
                candidates.append((agent_id, best))
        candidates.sort(key=lambda x: x[1], reverse=True)

        if candidates:
            top_id, top_score = candidates[0]
            second = candidates[1][1] if len(candidates) > 1 else 0.0
            # High confidence: clear winner with meaningful overlap.
            if top_score >= 0.22 and (top_score - second) >= 0.08:
                self.fuzzy_resolved[description] = top_id
                print(f"[{self.agent_id}] Fuzzy resolved {description!r} -> {top_id} ({top_score:.2f})")
                return top_id

        # LLM fallback for ambiguous paraphrases.
        resolved = self._llm_resolve_fuzzy(description, candidates)
        if resolved:
            self.fuzzy_resolved[description] = resolved
            print(f"[{self.agent_id}] Fuzzy LLM resolved {description!r} -> {resolved}")
            return resolved

        self.fuzzy_rejected.add(description)
        print(f"[{self.agent_id}] Fuzzy UNRESOLVED (will decline): {description!r}")
        return None

    def _llm_resolve_fuzzy(
        self, description: str, candidates: List[Tuple[str, float]]
    ) -> Optional[str]:
        """Single focused LLM call. Returns agent id or None. Never guesses wildly."""
        if not getattr(self, "driver", None):
            return None
        if not self.seen_messages:
            return None

        evidence_lines = []
        for agent_id, msgs in self.seen_messages.items():
            for m in msgs[-3:]:
                evidence_lines.append(f"- {agent_id}: {m}")
        if not evidence_lines:
            return None

        hint = ", ".join(f"{a}:{s:.2f}" for a, s in candidates[:4]) or "none"
        prompt = (
            "You map fuzzy agent descriptions to agent ids for The Email Game.\n"
            "Rules: Only answer with a single agent id from the evidence, or UNKNOWN.\n"
            "Do not invent ids. If unsure, answer UNKNOWN.\n\n"
            f"Description: {description}\n"
            f"Heuristic scores: {hint}\n"
            "Evidence (agent id asked you to sign this text in a prior round):\n"
            + "\n".join(evidence_lines)
            + "\n\nAnswer with exactly one token: <agent_id> or UNKNOWN"
        )
        try:
            # Use the driver's raw completion path if present; else OpenAI client.
            answer = self._ask_llm_text(prompt)
        except Exception as e:
            print(f"[{self.agent_id}] Fuzzy LLM error: {e}")
            return None

        if not answer:
            return None
        token = answer.strip().split()[0].strip("`'\".,:;()")
        if token.upper() == "UNKNOWN":
            return None
        if token in self.seen_messages or token in self.known_agents:
            return token
        # Accept only ids we have actually seen.
        return None

    def _ask_llm_text(self, prompt: str) -> Optional[str]:
        """Minimal chat completion via the same OpenAI settings as the harness."""
        import os

        try:
            from openai import OpenAI
        except Exception:
            return None

        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
        model = getattr(self.driver, "model", None) or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=20,
            messages=[
                {"role": "system", "content": "Answer with one token only."},
                {"role": "user", "content": prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    # ------------------------------------------------------------------
    # Submissions
    # ------------------------------------------------------------------
    def _maybe_submit_signed(self, message: Dict) -> None:
        body = message.get("body", "") or ""
        if "SIGNED_MESSAGE_JSON:" not in body:
            return

        # Extract every JSON object after markers (usually one).
        parts = body.split("SIGNED_MESSAGE_JSON:")
        for part in parts[1:]:
            signed = self._parse_signed_json(part)
            if not signed:
                continue
            if signed.get("signed_for") not in (self.agent_id, None):
                # Only our signatures score when we submit.
                if signed.get("signed_for") != self.agent_id:
                    print(
                        f"[{self.agent_id}] Ignoring signature made out to "
                        f"{signed.get('signed_for')}"
                    )
                    continue
            original = signed.get("original_message", "")
            if self.assigned_message and original != self.assigned_message:
                print(
                    f"[{self.agent_id}] Ignoring signature for wrong message: "
                    f"{original!r} (want {self.assigned_message!r})"
                )
                continue
            key = (
                signed.get("signer", ""),
                signed.get("signed_for", ""),
                original,
            )
            if key in self.submitted_keys:
                continue
            result = self.submit_signature(signed)
            if result.get("success"):
                self.submitted_keys.add(key)
                print(f"[{self.agent_id}] Submitted signature from {signed.get('signer')}")

    def _parse_signed_json(self, blob: str) -> Optional[Dict]:
        text = blob.strip()
        # Trim trailing commentary after the JSON object.
        try:
            start = text.index("{")
        except ValueError:
            return None
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except Exception:
            # Fallback: cut at last }
            try:
                end = text.rindex("}") + 1
                obj = json.loads(text[start:end])
            except Exception:
                return None
        if not isinstance(obj, dict):
            return None
        if "signature" not in obj or "original_message" not in obj:
            return None
        return obj
