"""
Email Simulation Server - Phase 1
Provides REST API for agent communication with message storage and delivery tracking.
Enhanced with request queuing for handling concurrent moderator messages.
"""

import sys
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path

# Ensure emoji/log output never crashes on non-UTF-8 consoles (e.g. Windows
# cp1252 when stdout is redirected). Safe no-op where already UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
import uvicorn
import asyncio
import os
import secrets
import jwt  # PyJWT – added in requirements.txt
import json
import hashlib
import re
import subprocess
from src.game.config import NUM_AGENTS, PROJECT_ROOT, MAX_CONCURRENT_GAMES, PRE_GAME_GRACE_SEC
from src.game.utils import load_message_alias_pool
import itertools
import random
from src.leaderboard import (compute_leaderboard, render_leaderboard_html, is_filler,
                             INITIAL_RATING, _competition_phase,
                             current_skills, default_rating, match_quality)
from src.agent_stats import compute_agent_report, render_agent_html
from src.brand import BRAND_OVERRIDE, BRANDBAR, BRAND_FOOTER
from src.watch import render_watch_html
from src.matches import render_history_html, list_matches_for_agent, match_detail

# ---------------------------------------------------------------------------
# External dependencies for upcoming deployment steps
# ---------------------------------------------------------------------------

# Redis dependency removed - using in-memory storage instead

JWT_SECRET = os.getenv("JWT_SECRET", "inbox-arena-secret")

# Verbose per-message delivery logging (WebSocket pushes, queue processing) is
# noise for normal operation; show it only when EMAIL_GAME_DEBUG is set. The
# meaningful events (agent connect/disconnect, registration, errors) always print.
DEBUG_LOGS = os.getenv("EMAIL_GAME_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg: str) -> None:
    if DEBUG_LOGS:
        print(msg)


# Requeue (the live ladder) is a COMPETITION-ONLY capability. It is OFF by
# default: a game's agents are NOT re-queued, so every server runs exactly the
# games it can form and then goes idle. Only the host's competition server turns
# it on by setting EMAIL_GAME_COMPETITION=1. This guarantees that an agent can
# only be requeued by virtue of having joined the actual competition - local
# testing can never enter the ladder.
COMPETITION_MODE = os.getenv("EMAIL_GAME_COMPETITION", "").strip().lower() in ("1", "true", "yes", "on")

# The requeue ladder (continuous back-to-back games). ON in competition mode; for
# LOCAL testing it can be toggled independently with EMAIL_GAME_REQUEUE=1/0 - e.g.
# loop local games to watch your agent play repeatedly, without competition mode
# (no tokens/gating, still the local board). Unset => follows COMPETITION_MODE.
_requeue_env = os.getenv("EMAIL_GAME_REQUEUE", "").strip().lower()
if _requeue_env in ("1", "true", "yes", "on"):
    REQUEUE_ENABLED = True
elif _requeue_env in ("0", "false", "no", "off"):
    REQUEUE_ENABLED = False
else:
    REQUEUE_ENABLED = COMPETITION_MODE

# Shared secret for server-INTERNAL endpoints. The game runner and scorer call a
# few endpoints from 127.0.0.1 (read all mail, read a game's submissions, clear
# state); players must never reach them. Generated once and exported into the
# environment so spawned game subprocesses inherit it. The host can pin it via
# the EMAIL_GAME_INTERNAL_KEY env var. These protections only engage in
# competition mode, so local dev and the test suite are unaffected.
INTERNAL_KEY = os.environ.setdefault("EMAIL_GAME_INTERNAL_KEY", secrets.token_hex(16))

# Fail closed: a live competition must NOT run on the public default JWT secret.
# With it, anyone who reads the (public) repo can forge a token for any agent_id
# and impersonate them or read their mail, which would defeat the per-agent access
# control. Refuse to start so this can't happen by omission.
if COMPETITION_MODE and os.getenv("JWT_SECRET", "inbox-arena-secret") == "inbox-arena-secret":
    raise RuntimeError(
        "Refusing to start a competition with the default JWT_SECRET (tokens would "
        "be forgeable). Set a strong one, e.g.:\n"
        "  fly secrets set JWT_SECRET=$(openssl rand -hex 32)\n"
        "For a local competition-mode run: EMAIL_GAME_COMPETITION=1 JWT_SECRET=anything python -m src.email_server"
    )

# Fail closed: a competition must run on a PRIVATE alias pool, never the public
# sample shipped in the repo (or the fuzzy rounds could be solved by looking the
# descriptions up). Refuse to start if MESSAGE_ALIAS_POOL_PATH is unset or points
# at the default public pool.
if COMPETITION_MODE:
    _pool = os.getenv("MESSAGE_ALIAS_POOL_PATH", "").strip()
    _default_pool = (PROJECT_ROOT / "data" / "message_alias_pool.json").resolve()
    if not _pool:
        raise RuntimeError(
            "Refusing to start a competition without a private alias pool. Set "
            "MESSAGE_ALIAS_POOL_PATH to a private pool (not the public sample):\n"
            "  fly secrets set MESSAGE_ALIAS_POOL_PATH=/app/data/message_alias_pool.private.json"
        )
    _pool_path = Path(_pool).resolve()
    if not _pool_path.exists():
        raise RuntimeError(f"MESSAGE_ALIAS_POOL_PATH points to a missing file: {_pool_path}")
    if _pool_path == _default_pool:
        raise RuntimeError(
            "MESSAGE_ALIAS_POOL_PATH must not be the public sample pool "
            "(data/message_alias_pool.json). Use a private pool."
        )

# Local-testing convenience: EMAIL_GAME_RESET_LEADERBOARD=1 starts the board
# fresh by counting only games from this launch onward. It stamps the leaderboard
# cutoff (COMPETITION_START_TIME) in LOCAL time, to match session start_time,
# which is naive local-time ISO. It never overrides an explicit
# COMPETITION_START_TIME, so the real competition (which sets a fixed cutoff once)
# is unaffected and its board still survives server restarts. NOT on by default:
# a restart mid-competition must never wipe the standings.
if os.getenv("EMAIL_GAME_RESET_LEADERBOARD", "").strip().lower() in ("1", "true", "yes", "on") \
        and not os.environ.get("COMPETITION_START_TIME", "").strip():
    from datetime import datetime as _dt
    os.environ["COMPETITION_START_TIME"] = _dt.now().isoformat()
    print(f"🧹 Leaderboard reset: counting games from "
          f"{os.environ['COMPETITION_START_TIME']} onward (existing history kept on disk).")

# How long an agent may be fully disconnected before it leaves its current match
# for good. A reconnect within this window resumes the same match (tolerates a
# transient network blip); after it, the agent is removed from the match and the
# queue and can only re-join for future matches.
DISCONNECT_GRACE_SEC = int(os.getenv("EMAIL_GAME_DISCONNECT_GRACE_SEC", "20"))

# Matchmaking window: when agents become available (join or requeue), wait for
# arrivals to quiet down before forming games, so finishers from concurrent
# games pool in the queue first. The deadline resets on each arrival (debounce),
# so a burst of finishers is gathered together; without this, concurrent games
# that end ~simultaneously each re-form their own 4 the instant they requeue and
# the Elo matcher never sees a real pool (the same groups recur). MAX caps the
# wait so a continuous trickle can't starve formation.
MATCHMAKING_WINDOW_SEC = float(os.getenv("EMAIL_GAME_MATCHMAKING_WINDOW_SEC", "4"))
MATCHMAKING_MAX_SEC = float(os.getenv("EMAIL_GAME_MATCHMAKING_MAX_SEC", "20"))

# Synchronized waves (competition board only). When > 0, the live competition
# forms games in fixed-cadence WAVES from the whole waiting pool at once, instead
# of continuously as agents trickle in. This removes two exploits of continuous
# matchmaking: (1) timing your rejoin to land a favorable pool / more games, and
# (2) matchups that depend on who happened to overlap in a short debounce window -
# each wave partitions the ENTIRE pool into balanced games at the same instant.
# Seating is anti-farm (fewest games played first, see _wave_priority_order), so
# grinding extra games can't buy queue priority. The build/practice/local boards
# stay continuous (always-on, no wait). 0 (default) = continuous everywhere.
WAVE_INTERVAL_SEC = float(os.getenv("EMAIL_GAME_WAVE_INTERVAL_SEC", "0"))

# Unique-pool-per-game (competition anti-memorization). When on, the server slices
# its master message/alias pool into a DISJOINT set of pairs per game, so no two
# games ever share pairs - watching one game teaches you nothing about another, and
# nothing pre-exists to memorize during build week (which uses its own limited
# pool). Each game process is pointed at its private slice via MESSAGE_ALIAS_POOL_PATH.
# POOL_PAIRS_PER_GAME should be >= NUM_AGENTS * rounds (+ a small buffer). When the
# master pool is exhausted the server reshuffles and warns (pairs then repeat), so
# size the master pool to the expected game count. Default off; competition board only.
UNIQUE_POOL_PER_GAME = os.getenv("EMAIL_GAME_UNIQUE_POOL_PER_GAME", "0").strip().lower() in ("1", "true", "yes", "on")
POOL_PAIRS_PER_GAME = int(os.getenv("EMAIL_GAME_POOL_PAIRS_PER_GAME", "32"))

# Per-agent cooldown after a game ends before it re-enters the queue. Gives a
# settle window between back-to-back matches (clients reset, reduces thrash) so
# games don't form the instant a game finishes. 0 disables it.
REQUEUE_COOLDOWN_SEC = float(os.getenv("EMAIL_GAME_REQUEUE_COOLDOWN_SEC", "15"))

# Dead-agent strike-out: a healthy agent essentially always scores > 0 over a
# full game (it collects or signs something). An agent that scores 0 across this
# many consecutive completed games is treated as non-functional and removed from
# the pool (not re-queued, socket closed with a notice) so it stops polluting
# games. It may reconnect once fixed. 0 disables the feature.
DEAD_AGENT_STRIKES = int(os.getenv("EMAIL_GAME_DEAD_AGENT_STRIKES", "2"))

# Draining: when set, the server stops forming NEW games but lets in-flight games
# finish - the graceful way to end a competition without abandoning a live game.
# Toggleable at runtime via the internal-only /admin/drain endpoint.
DRAINING = os.getenv("EMAIL_GAME_DRAINING", "").strip().lower() in ("1", "true", "yes", "on")

# ---------------------------------------------------------------------------
# Gateway usage monitor.
#
# Holding an issued key proves possession, not use: the agent runs on the
# competitor's machine, so it can answer from any provider it likes. Nothing
# client-side can prevent that - the repo is public, so any check we ship is
# source they can delete - and nothing server-side can prove which model wrote a
# sentence.
#
# What the server CAN do is compare two things it owns: how many messages an
# agent sent, and how much its key spent on our gateway over the same period.
# An agent that played hard and spent nothing did not use our models.
#
# Note this is not sampling. Spend is CUMULATIVE, so comparing snapshots covers
# every second between them - there is no unobserved gap to time activity into.
# Periodic snapshots give complete coverage, which is strictly better than
# random spot-checks.
#
# It only ever RECORDS. It never blocks a seat, drops an agent, or touches
# matchmaking, because the one thing worse than an undetected cheat is ejecting
# an honest competitor mid-competition on a heuristic. Findings inform the
# prize-time conversation; they are not a verdict.
#
# It cannot catch a competitor who mirrors real traffic to our gateway while
# answering from elsewhere. That needs the agent running in our environment.
GATEWAY_MONITOR = os.getenv("EMAIL_GAME_GATEWAY_MONITOR", "").strip().lower() in ("1", "true", "yes", "on")
GATEWAY_MONITOR_SEC = float(os.getenv("EMAIL_GAME_GATEWAY_MONITOR_SEC", "300"))
GATEWAY_MONITOR_MIN_MSGS = int(os.getenv("EMAIL_GAME_GATEWAY_MONITOR_MIN_MSGS", "8"))
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "").strip().rstrip("/")
LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "").strip()


# Gateway key records, cached. Every competitor's page can ask "how much of my
# budget is left", so this must not become one gateway round-trip per page view.
_GATEWAY_KEYS: Dict[str, object] = {"at": 0.0, "data": None}
GATEWAY_KEYS_TTL_SEC = float(os.getenv("EMAIL_GAME_GATEWAY_KEYS_TTL_SEC", "60"))


def _fetch_gateway_keys(force: bool = False) -> Optional[Dict[str, Dict]]:
    """{key_alias: {spend, max_budget, blocked}}, or None if unreachable.

    None is deliberately distinct from {}: "unreachable" must never be read as
    "nobody spent anything", or a gateway hiccup would indict the whole field.
    Keys are issued with key_alias = the agent handle, which is what lets the
    server answer a competitor's own budget question without ever holding their
    key.
    """
    if not (LITELLM_BASE_URL and LITELLM_MASTER_KEY):
        return None
    now = time.time()
    if (not force and _GATEWAY_KEYS["data"] is not None
            and now - float(_GATEWAY_KEYS["at"]) < GATEWAY_KEYS_TTL_SEC):
        return _GATEWAY_KEYS["data"]  # type: ignore[return-value]
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    out: Dict[str, Dict] = {}
    page = 1
    try:
        while True:
            req = urllib.request.Request(
                f"{LITELLM_BASE_URL}/key/list?return_full_object=true&size=100&page={page}",
                headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                data = json.loads(r.read())
            for k in data.get("keys", []):
                if isinstance(k, dict) and k.get("key_alias"):
                    out[k["key_alias"]] = {
                        "spend": float(k.get("spend") or 0.0),
                        "max_budget": k.get("max_budget"),
                        "blocked": bool(k.get("blocked")),
                    }
            if page >= (data.get("total_pages") or 1):
                break
            page += 1
    except Exception:
        return None
    _GATEWAY_KEYS["at"], _GATEWAY_KEYS["data"] = now, out
    return out


def _fetch_gateway_spend() -> Optional[Dict[str, float]]:
    """{key_alias: cumulative spend}, or None if the gateway cannot be reached."""
    recs = _fetch_gateway_keys(force=True)
    return None if recs is None else {a: r["spend"] for a, r in recs.items()}



# Optional hard cutoff: games that START at/after this ISO-8601 time do not count
# toward the leaderboard (mirror of COMPETITION_START_TIME). Lets you freeze the
# final board at a known instant.
COMPETITION_END_TIME = os.getenv("COMPETITION_END_TIME", "").strip()


# How many of the anchor's nearest-by-skill candidates to consider when searching
# for the best-balanced game. Bounds the combinatorial search; the balanced group
# is necessarily near the anchor in skill, so a modest pool loses nothing.
MATCH_POOL = int(os.getenv("EMAIL_GAME_MATCH_POOL", "12"))

# Entry gating: when EMAIL_GAME_ENTRY_ALLOWLIST points at a JSON file of
# {agent_id: sha256(issued_key)}, registration is restricted to those rostered
# agent_ids AND each one is bound to its issued key. So only the holder of an
# agent's key can register that agent_id (no impersonation/squatting), and only
# rostered names can join at all. The file holds ONLY hashes (no secrets), so it
# is safe to bake into the server image. When unset/missing, registration is open
# (local testing, playtest). Built once at import.
def _load_entry_allowlist() -> Optional[Dict[str, str]]:
    path = os.getenv("EMAIL_GAME_ENTRY_ALLOWLIST", "").strip()
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(k): str(v).lower() for k, v in data.items()} if data else None
    except Exception as e:
        print(f"[entry-allowlist] could not load {path}: {e}")
        return None


ENTRY_ALLOWLIST = _load_entry_allowlist()
if ENTRY_ALLOWLIST is not None:
    print(f"[entry-allowlist] entry gating ON: {len(ENTRY_ALLOWLIST)} rostered agents, key-bound.")


def _past_end() -> bool:
    """True once COMPETITION_END_TIME has passed. After this the server forms NO
    new games (and stops requeuing), so the competition ends cleanly: only matches
    that STARTED before the cutoff run to completion and count. The winner is
    crowned once those finish. (Build week is unaffected: end time is on 6/27.)"""
    if not COMPETITION_END_TIME:
        return False
    try:
        return datetime.now().isoformat() >= COMPETITION_END_TIME
    except Exception:
        return False


def _current_skills() -> Dict[str, object]:
    """Map agent_id -> TrueSkill Rating for matchmaking, from the CURRENT phase's
    board. Build-week games are balanced by build-week ratings, competition games
    by competition ratings - never mixed across phases. Empty on error."""
    try:
        window = "build" if _nav_board() == "build" else "competition"
        return current_skills(window=window)
    except Exception:
        return {}


def _waves_enabled() -> bool:
    """Synchronized wave formation runs only on the live COMPETITION board and only
    when a wave interval is configured. The build/practice/local boards stay
    continuous (always-on, no wait)."""
    return WAVE_INTERVAL_SEC > 0 and _nav_board() == "competition"


def _unique_pool_enabled() -> bool:
    """Slice the pool into a disjoint set per game - competition board only, when
    the flag is on. Build/practice keeps its shared limited pool."""
    return UNIQUE_POOL_PER_GAME and _nav_board() == "competition"


def _slice_bounds(cursor: int, n: int, size: int):
    """(start, end, wrapped) for taking n items at ``cursor`` from a size-length
    pool. Sequential calls yield DISJOINT [start, end) ranges until the tail is too
    short to fill n, at which point wrapped=True and it restarts at 0 (the caller
    reshuffles so the recycled pass is a fresh order)."""
    if size <= 0 or n <= 0:
        return 0, 0, False
    if cursor + n > size:
        return 0, min(n, size), True
    return cursor, cursor + n, False


def _games_played() -> Dict[str, int]:
    """agent_id -> completed games on the current board (for anti-farm seating)."""
    try:
        window = "build" if _nav_board() == "build" else "competition"
        return {e["agent_id"]: e["games"] for e in compute_leaderboard(window=window)}
    except Exception:
        return {}


def _wave_priority_order(queue: List[str], games_played: Dict[str, int]) -> List[str]:
    """Order the waiting pool for wave seating: fewest games played first (so
    grinding more games cannot buy queue priority - it deprioritizes you until
    others catch up), breaking ties by original queue position (FIFO). Balance
    within the formed groups is still handled by _select_matched_group."""
    pos = {a: i for i, a in enumerate(queue)}
    return sorted(queue, key=lambda a: (games_played.get(a, 0), pos[a]))


def _as_rating(skills, agent):
    """The agent's Rating, falling back to the new-player prior when unrated.

    Tolerant of a scalar skill map (treated as a μ with the prior σ) so the
    matchmaking simulations/tests can drive the selector with plain numbers."""
    v = skills.get(agent)
    if v is None:
        return default_rating()
    if isinstance(v, (int, float)):
        return default_rating().__class__(mu=float(v), sigma=default_rating().sigma)
    return v


def _select_matched_group(queue: List[str], k: int, skills: Dict[str, object]) -> List[str]:
    """Pick k agents for a game using TrueSkill matchmaking.

    Anchors on the longest-waiting agent (queue[0]) so no one is ever starved,
    then chooses the k-1 others that maximize TrueSkill match quality with the
    anchor (the most balanced/competitive game), searching within the anchor's
    nearest-by-skill candidate pool to bound cost. Ties in quality break by queue
    position (FIFO) for fairness/determinism. With k or fewer waiting, returns
    them all (no choice to make).
    """
    if len(queue) <= k:
        return list(queue[:k])
    anchor = queue[0]
    anchor_r = _as_rating(skills, anchor)
    pos = {a: i for i, a in enumerate(queue)}
    rest = queue[1:]
    # Bound the search to the anchor's nearest-by-skill candidates (FIFO breaks
    # equal distances, e.g. a cold start where everyone shares the prior).
    pool_n = max(k - 1, min(len(rest), MATCH_POOL))
    pool = sorted(rest, key=lambda a: (abs(_as_rating(skills, a).mu - anchor_r.mu), pos[a]))[:pool_n]

    best_combo, best_key = None, None
    for combo in itertools.combinations(pool, k - 1):
        q = match_quality([anchor_r] + [_as_rating(skills, a) for a in combo])
        # Maximize quality; on (near-)ties prefer earlier-queued agents.
        key = (round(q, 9), -sum(pos[a] for a in combo))
        if best_key is None or key > best_key:
            best_key, best_combo = key, combo
    return [anchor] + sorted(best_combo, key=lambda a: pos[a])


# Security validation helpers
def _validate_recipient(to_agent: str) -> bool:
    """Validate that the recipient agent exists and is valid."""
    if not to_agent or not isinstance(to_agent, str):
        return False
    
    # Allow moderator as a special recipient
    if to_agent == "moderator":
        return True
    
    # Basic validation: alphanumeric and underscore only, reasonable length
    if not to_agent.replace("_", "").isalnum() or len(to_agent) > 50:
        return False
    
    # TODO: Could add Redis lookup to verify agent is registered
    # For now, accept any valid-format agent ID
    return True

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _decode_bearer(request: Request, *, allow_header: bool = True) -> dict:
    """Extract and verify the Bearer JWT, returning its payload.

    Raises 401 if missing/invalid, 403 if expired. Does not enforce scope -
    callers decide whether a 'view' token is acceptable.
    """
    token: str | None = None
    if allow_header:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if token is None:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return payload


def _require_token(request: Request, *, allow_header: bool = True) -> str:
    """FastAPI dependency for ACTION endpoints: returns the agent_id from a valid
    Bearer JWT. A read-only 'view' token is rejected here, so a shared watch link
    (which carries a view token) can never send/sign/queue on an agent's behalf.
    """
    payload = _decode_bearer(request, allow_header=allow_header)
    if payload.get("scope") == "view":
        raise HTTPException(status_code=403,
                            detail="View-only token cannot perform this action")
    agent_id = payload["sub"]
    request.state.agent_id = agent_id
    return agent_id


def _require_internal(request: Request) -> None:
    """Gate a server-internal endpoint. In competition mode only the game runner
    and scorer (which send the internal key) may call it; players are rejected."""
    if COMPETITION_MODE and request.headers.get("x-internal-key") != INTERNAL_KEY:
        raise HTTPException(status_code=403, detail="Endpoint not available")


def _require_own_mailbox(request: Request, agent_id: str) -> None:
    """In competition mode, an agent may only READ its own mailbox. Accepts both
    the agent's full token and a read-only 'view' token (so the watch page works
    with a shareable view link that cannot act). Internal callers (with the key)
    are exempt; outside competition mode this is a no-op."""
    if not COMPETITION_MODE:
        return
    if request.headers.get("x-internal-key") == INTERNAL_KEY:
        return
    payload = _decode_bearer(request)   # any valid token (full or view) is fine for reads
    if payload["sub"] != agent_id:
        raise HTTPException(status_code=403, detail="You can only access your own mailbox")


def _check_delivery_allowed(sender: str, recipient: str) -> None:
    """Game-scope peer messaging in competition mode.

    Outside competition mode this is a no-op (local dev/playtest). In competition
    mode a peer agent may only message agents in its OWN active game. This closes
    two abuse vectors a prize comp must not allow:
      - queue-time / pre-game contact (e.g. someone posing as the moderator to
        harvest signatures before a game even forms), and
      - cross-game collusion / signature feeding.
    The moderator (sender 'moderator') may message anyone, and anyone may message
    the moderator (replies / submissions).
    """
    if not COMPETITION_MODE:
        return
    if sender == "moderator" or recipient == "moderator":
        return
    sender_game = email_server.agent_to_game.get(sender)
    if sender_game is None or sender_game != email_server.agent_to_game.get(recipient):
        raise HTTPException(
            status_code=403,
            detail="You can only message agents in your current game.",
        )


class Message(BaseModel):
    """Message model for email simulation"""
    from_agent: str
    to_agent: str
    subject: str
    body: str
    timestamp: Optional[str] = None
    message_id: Optional[str] = None
    status: str = "sent"  # sent, delivered, read


class SendMessageRequest(BaseModel):
    """Request model for sending messages - sender derived from JWT token"""
    to: str
    subject: str
    body: str


class BatchSendRequest(BaseModel):
    """Request model for sending multiple messages at once"""
    messages: List[SendMessageRequest]


class EmailServer:
    """Core email server for message storage and routing with request queuing"""
    
    def __init__(self):
        self.messages: List[Dict] = []
        self.message_status: Dict[str, str] = {}
        # Request queue for handling bursts
        self.message_queue: asyncio.Queue = None  # Will be created when needed
        self.queue_processor_task: Optional[asyncio.Task] = None
        self._queue_started = False

        # In-memory storage (replaces Redis)
        self.registered_agents: Dict[str, Dict[str, str]] = {}
        self.waiting_queue: List[str] = []
        self.gateway_findings: List[Dict] = []   # usage-monitor records (never a verdict)
        self._gw_monitor_started = False
        self.current_game_in_progress: bool = False  # legacy flag (kept for status)
        # Concurrent games: game_id -> {"agents": [...], "proc": Popen}
        self.active_games: Dict[str, Dict] = {}
        # agent_id -> game_id it is currently playing in (routes its moderator mail)
        self.agent_to_game: Dict[str, str] = {}
        # game_id -> set of agents who left mid-game; their signatures no longer
        # count toward that game (they can't rejoin or affect it after leaving).
        self.departed_from: Dict[str, set] = {}
        self._game_counter: int = 0
        # agent_id -> consecutive completed games scored 0 (dead-agent detection)
        self.zero_streak: Dict[str, int] = {}
        self._matchmaking_scheduled: bool = False
        self._mm_first_at = None      # when the current matchmaking window opened
        self._mm_deadline: float = 0.0  # loop-time at which to form games
        self._wave_running: bool = False  # competition wave ticker active?
        # Unique-pool-per-game: master pool + cursor for disjoint slice allocation.
        self._master_pool: Optional[List[Dict]] = None
        self._pool_cursor: int = 0
        self._pool_slice_dir: Path = PROJECT_ROOT / "session_results" / "_pool_slices"
        self._queue_lock = asyncio.Lock()

    
    async def _gateway_monitor_loop(self) -> None:
        """Compare messages sent against gateway spend, forever. Records only."""
        prev = _fetch_gateway_spend() or {}
        since = datetime.now()
        while True:
            await asyncio.sleep(GATEWAY_MONITOR_SEC)
            try:
                now_spend = _fetch_gateway_spend()
                window_end = datetime.now()
                if now_spend is None:
                    print("[gateway-monitor] gateway unreachable; window skipped "
                          "(not counted against anyone)")
                    since = window_end          # don't blame agents for our outage
                    continue

                sent: Dict[str, int] = {}
                for m in self.messages:
                    frm = m.get("from")
                    if not frm or frm == "moderator":
                        continue
                    try:
                        ts = datetime.fromisoformat(m.get("timestamp", ""))
                    except Exception:
                        continue
                    if since <= ts <= window_end:
                        sent[frm] = sent.get(frm, 0) + 1

                for agent, n in sent.items():
                    if n < GATEWAY_MONITOR_MIN_MSGS:
                        continue                # too little activity to mean anything
                    before, after = prev.get(agent), now_spend.get(agent)
                    if before is None or after is None:
                        continue                # no key of ours: nothing to compare
                    if after > before:
                        continue                # used our gateway this window
                    self.gateway_findings.append({
                        "agent": agent, "messages": n,
                        "from": since.isoformat(timespec="seconds"),
                        "to": window_end.isoformat(timespec="seconds"),
                    })
                    print(f"[gateway-monitor] {agent}: {n} message(s) sent, $0 gateway "
                          f"spend between {since:%H:%M} and {window_end:%H:%M}")
                prev, since = now_spend, window_end
            except Exception as e:               # never let the monitor kill itself
                print(f"[gateway-monitor] skipped a window: {e}")
                since = datetime.now()

    def _ensure_gateway_monitor(self):
        """Start the usage monitor once, if enabled and configured."""
        if not GATEWAY_MONITOR or self._gw_monitor_started:
            return
        if not (LITELLM_BASE_URL and LITELLM_MASTER_KEY):
            print("[gateway-monitor] enabled but LITELLM_BASE_URL/MASTER_KEY unset - off")
            self._gw_monitor_started = True
            return
        try:
            asyncio.create_task(self._gateway_monitor_loop())
            self._gw_monitor_started = True
            print(f"[gateway-monitor] on: every {GATEWAY_MONITOR_SEC:.0f}s, "
                  f"flagging >={GATEWAY_MONITOR_MIN_MSGS} messages with no spend")
        except RuntimeError:
            pass                                 # no loop yet; retried on the next call

    def _ensure_queue_started(self):
        """Ensure the queue processor is started (lazy initialization)"""
        if not self._queue_started:
            try:
                if self.message_queue is None:
                    self.message_queue = asyncio.Queue()
                self.queue_processor_task = asyncio.create_task(self._process_message_queue())
                self._queue_started = True
                self._ensure_gateway_monitor()
            except RuntimeError:
                # No event loop running yet, will try again later
                pass
    
    async def _process_message_queue(self):
        """Background task that processes queued messages one by one"""
        while True:
            try:
                # Get next message from queue (blocks if empty)
                message_data, result_future = await self.message_queue.get()
                _dbg(f"📦 Queue processor: Processing message from {message_data['from_agent']} to {message_data['to']}")
                
                # Process the message
                try:
                    message_id = self._store_message_sync(message_data)
                    result_future.set_result(message_id)
                    _dbg(f"✅ Queue processor: Message {message_id} stored and notified")
                except Exception as e:
                    print(f"❌ Queue processor error storing message: {e}")
                    result_future.set_exception(e)
                
                # Small delay to prevent overwhelming WebSocket delivery
                await asyncio.sleep(0.01)  # 10ms between messages
                
            except Exception as e:
                print(f"Queue processor error: {e}")
                await asyncio.sleep(0.1)
    
    async def store_message_queued(self, message_data: Dict) -> str:
        """Store a message via the queue (non-blocking for concurrent requests)"""
        self._ensure_queue_started()
        if not self._queue_started:
            # Fallback to sync if queue not available
            return self._store_message_sync(message_data)
            
        result_future = asyncio.Future()
        await self.message_queue.put((message_data, result_future))
        return await result_future
    
    def _store_message_sync(self, message_data: Dict) -> str:
        """Synchronous message storage (used by queue processor)"""
        message_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        message = {
            "message_id": message_id,
            "from": message_data["from_agent"],
            "to": message_data["to"],
            "subject": message_data["subject"],
            "body": message_data["body"],
            "timestamp": timestamp,
            "status": "sent"
        }

        # Tag every message with the game it belongs to. Submissions (to the
        # moderator) are bucketed by the SENDER's current game so concurrent
        # games' scorers each read only their own. Agent-bound mail is tagged by
        # the RECIPIENT's current game, so a later reconnect into a different (or
        # no) game never replays a previous game's backlog - see
        # get_messages_for_agent. game_id may be None when the party isn't
        # currently in a game (e.g. stray pre-game mail), which is treated as
        # untagged.
        if message["to"] == "moderator":
            message["game_id"] = self.agent_to_game.get(message["from"])
        else:
            message["game_id"] = self.agent_to_game.get(message["to"])

        self.messages.append(message)
        self.message_status[message_id] = "sent"
        
        # After storing, attempt real-time delivery via WebSocket
        try:
            # Get the current event loop and schedule the WebSocket notification
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(manager.send_json(message_data["to"], message))
            else:
                print(f"⚠️  No active event loop for WebSocket notification to {message_data['to']}")
        except Exception as e:
            print(f"⚠️  WebSocket notification failed: {e}")
        
        return message_id
    
    def store_message(self, message_data: Dict) -> str:
        """Store a message and return its ID (legacy sync method)"""
        return self._store_message_sync(message_data)
    
    def get_messages_for_agent(self, agent_id: str) -> List[Dict]:
        """Messages for an agent, scoped to its CURRENT game in competition mode.

        Returns ONLY mail tagged for the agent's current game, so a reconnect or a
        new game never replays a previous game's backlog. Untagged mail
        (game_id=None) is deliberately NOT delivered into a game: the main source
        of untagged mail is the end-of-game 'Game Over' summary sent to an agent
        that already left agent_to_game, which otherwise leaked into every future
        game and polluted the agent's context. (Real-time WS push still delivers
        the game-over notice at game end to a connected agent; this only governs
        backlog polls.) Outside competition mode this returns everything (local
        dev/playtest)."""
        msgs = [msg for msg in self.messages if msg["to"] == agent_id]
        if not COMPETITION_MODE:
            return msgs
        current_game = self.agent_to_game.get(agent_id)
        if current_game is None:
            return []  # between games: no game mail (don't replay stale/untagged)
        return [m for m in msgs if m.get("game_id") == current_game]

    def purge_game_messages(self, game_id: str) -> int:
        """Remove every stored message belonging to a finished game (all peer
        mail, moderator instructions, and submissions are tagged with it). Called
        at game-end so a later reconnect can't replay it and memory doesn't grow
        unbounded. Returns the number removed."""
        if not game_id:
            return 0
        before = len(self.messages)
        self.messages = [m for m in self.messages if m.get("game_id") != game_id]
        return before - len(self.messages)

    def purge_agent_game_inbox(self, agent_id: str, game_id: str) -> int:
        """Remove a single agent's inbox mail for one game (used when an agent
        departs for good, so its reconnect starts clean while the game's other
        agents keep theirs). Returns the number removed."""
        if not game_id:
            return 0
        before = len(self.messages)
        self.messages = [m for m in self.messages
                         if not (m["to"] == agent_id and m.get("game_id") == game_id)]
        return before - len(self.messages)

    def _read_game_scores(self, game_id: str) -> Dict[str, int]:
        """Best-effort read of a finished game's per-agent cumulative scores from
        its session_results file. Returns {} if not found/unparseable (in which
        case dead-agent detection simply skips this game)."""
        try:
            results_dir = PROJECT_ROOT / "session_results"
            matches = sorted(results_dir.glob(f"session_{game_id}_*.json"))
            if not matches:
                return {}
            with open(matches[-1]) as f:
                return json.load(f).get("cumulative_scores") or {}
        except Exception:
            return {}

    async def _requeue_after(self, agent_id: str, delay: float) -> None:
        """Re-queue an agent after a cooldown, if it's still connected and idle.
        Gives a settle window between back-to-back matches."""
        await asyncio.sleep(delay)
        async with self._queue_lock:
            if (agent_id in manager.active
                    and agent_id not in self.waiting_queue
                    and agent_id not in self.agent_to_game
                    and not DRAINING and not _past_end()):  # no requeue once the competition ends
                self.waiting_queue.append(agent_id)
                print(f"↩️  Re-queued {agent_id} after {delay:g}s cooldown")
                self._schedule_matchmaking()
    
    def get_all_messages(self) -> List[Dict]:
        """Get all messages (for debugging/visualization)"""
        return self.messages.copy()

    def get_submissions(self, game_id: str) -> List[Dict]:
        """Moderator-addressed messages belonging to a specific game."""
        return [m for m in self.messages
                if m["to"] == "moderator" and m.get("game_id") == game_id]
    
    def clear_all_messages(self) -> None:
        """Clear all messages (useful for starting new rounds)"""
        self.messages.clear()
        self.message_status.clear()
        print("📧 All messages cleared from email server")
    
    def clear_all_state(self) -> None:
        """Clear all server state (useful for testing)"""
        self.messages.clear()
        self.message_status.clear()
        self.registered_agents.clear()
        self.waiting_queue.clear()
        self.current_game_in_progress = False
        print("🧹 All server state cleared")
    
    def get_message_status(self, message_id: str) -> str:
        """Get the delivery status of a message"""
        return self.message_status.get(message_id, "unknown")
    
    def mark_delivered(self, message_id: str) -> bool:
        """Mark a message as delivered"""
        if message_id in self.message_status:
            self.message_status[message_id] = "delivered"
            for msg in self.messages:
                if msg["message_id"] == message_id:
                    msg["status"] = "delivered"
                    break
            return True
        return False
    
    def mark_read(self, message_id: str) -> bool:
        """Mark a message as read"""
        if message_id in self.message_status:
            self.message_status[message_id] = "read"
            # Update message in messages list
            for msg in self.messages:
                if msg["message_id"] == message_id:
                    msg["status"] = "read"
                    break
            return True
        return False
    
    # ------------------------------------------------------------------
    # In-memory storage helpers (replaces Redis)
    # ------------------------------------------------------------------

    # ----------------------------
    # Queue helpers
    # ----------------------------

    async def join_queue(self, agent_id: str) -> int:
        """Push *agent_id* to the waiting_queue if not already present.

        Returns the new queue length.  Raises ValueError if the ID is already
        queued.
        """
        async with self._queue_lock:
            # If the agent is already in a running game (e.g. it dropped and
            # reconnected mid-game), don't queue it again - that would let it be
            # assigned to a second game at once. It is re-queued normally when its
            # current game ends (if still connected).
            if agent_id in self.agent_to_game:
                return len(self.waiting_queue)

            # Check for duplicates
            if agent_id in self.waiting_queue:
                raise ValueError("Agent already queued")

            # Add to queue
            self.waiting_queue.append(agent_id)
            queue_len = len(self.waiting_queue)
            
            print(f"📝 Agent {agent_id} joined queue (position {queue_len})")

            # Pool briefly, then form Elo-matched games (see _schedule_matchmaking).
            self._schedule_matchmaking()

            return queue_len

    def _schedule_matchmaking(self) -> None:
        """Debounce game formation until arrivals quiet down, so finishers from
        concurrent games pool together before the Elo matcher groups them. Resets
        the quiet-period deadline on each call (capped by MATCHMAKING_MAX_SEC so a
        continuous trickle can't starve formation). Safe to call under _queue_lock
        (it only updates timing fields and may schedule one waiter task)."""
        # Competition board: hand off to the synchronized wave ticker instead of
        # arrival-triggered debounced formation.
        if _waves_enabled():
            self._ensure_wave_loop()
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No running loop (not expected in normal operation): form immediately.
            self._launch_ready_games()
            return
        now = loop.time()
        if self._mm_first_at is None:
            self._mm_first_at = now
        self._mm_deadline = min(now + MATCHMAKING_WINDOW_SEC,
                                self._mm_first_at + MATCHMAKING_MAX_SEC)
        if not self._matchmaking_scheduled:
            self._matchmaking_scheduled = True
            asyncio.create_task(self._matchmaking_loop())

    async def _matchmaking_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            delay = self._mm_deadline - loop.time()
            if delay <= 0:
                break
            await asyncio.sleep(delay)
        async with self._queue_lock:
            self._matchmaking_scheduled = False
            self._mm_first_at = None
            self._launch_ready_games()

    def _ensure_wave_loop(self) -> None:
        """Start the competition wave ticker if it isn't already running."""
        if self._wave_running:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._launch_ready_games()   # no loop (not normal): form now
            return
        self._wave_running = True
        asyncio.create_task(self._wave_loop())

    async def _wave_loop(self) -> None:
        """Competition matchmaking in synchronized WAVES. On a fixed cadence, form
        all ready games from the WHOLE waiting pool at once, so queue position and
        rejoin timing can't be gamed and matchups don't depend on who overlapped.
        Runs while agents are waiting; exits when the queue empties (re-armed on
        the next join)."""
        try:
            while True:
                await asyncio.sleep(WAVE_INTERVAL_SEC)
                async with self._queue_lock:
                    if not self.waiting_queue:
                        self._wave_running = False
                        return
                    self._launch_ready_games()
        except asyncio.CancelledError:
            self._wave_running = False
            raise

    def _launch_ready_games(self) -> None:
        """Form & launch all ready Elo-matched games. Assumes _queue_lock is held.

        With MAX_CONCURRENT_GAMES == 0 (default) this is unlimited: it keeps
        launching games while at least NUM_AGENTS agents are waiting.
        """
        if DRAINING or _past_end():
            # Winding down (host drained, or past the competition end time): let
            # in-flight games finish, form no new ones.
            return
        skills = _current_skills()
        if _waves_enabled() and len(self.waiting_queue) > NUM_AGENTS:
            # Anti-farm seating (competition waves): reorder the pool so the
            # fewest-games agents anchor first, FIFO within a tie. Grinding extra
            # games no longer buys queue priority. Balance is still by TrueSkill in
            # _select_matched_group. (No-op when the pool is exactly one game.)
            self.waiting_queue[:] = _wave_priority_order(self.waiting_queue, _games_played())
        # ONE slice per WAVE, shared by every game launched in it - not one per
        # game. An agent is in exactly one game per wave, so two games running at
        # the same instant can safely use the same pairs: nothing an agent sees in
        # its own match reveals anything about a match it is not in. Freshness is
        # only needed ACROSS waves, which is what an agent actually experiences.
        #
        # Rotate the slice at most once per wave INTERVAL, not once per call to
        # this function. Games are also formed by a debounced non-wave path, so
        # keying off calls would consume several slices per wave and drain the
        # pool faster than the event's 600-wave bound. Time-bounding it makes
        # consumption provable: at most one slice per WAVE_INTERVAL_SEC.
        #
        # Allocation itself stays lazy (below, on the first game actually
        # launched), so a wave too short to form a game costs nothing.
        now = time.time()
        if now - getattr(self, "_wave_slice_at", 0) >= max(WAVE_INTERVAL_SEC, 1):
            self._wave_slice = None
            self._wave_slice_at = now
        while len(self.waiting_queue) >= NUM_AGENTS:
            if MAX_CONCURRENT_GAMES and len(self.active_games) >= MAX_CONCURRENT_GAMES:
                break
            group = _select_matched_group(self.waiting_queue, NUM_AGENTS, skills)
            for a in group:
                self.waiting_queue.remove(a)
            self._spawn_game(group)

    def _alloc_pool_slice(self, n: int) -> List[Dict]:
        """Return the next ``n`` message/alias pairs as a slice disjoint from every
        prior game's, advancing the cursor. Lazily loads + shuffles the master pool
        (from MESSAGE_ALIAS_POOL_PATH) on first use. When the pool can't fill ``n``
        from the tail, it reshuffles and restarts (pairs then repeat - warned)."""
        if self._master_pool is None:
            self._master_pool = load_message_alias_pool()
            random.shuffle(self._master_pool)
            self._pool_cursor = 0
        pool = self._master_pool
        start, end, wrapped = _slice_bounds(self._pool_cursor, n, len(pool))
        if wrapped and pool:
            random.shuffle(pool)
            print(f"⚠️  alias pool exhausted ({len(pool)} pairs) - reshuffling; pairs "
                  f"will now repeat. Grow the master pool to keep games disjoint.")
        self._pool_cursor = end
        return pool[start:end]

    def _spawn_game(self, agents: List[str]) -> None:
        """Spawn one game as its own process and start watching it. Assumes _queue_lock
        is held, so the pool cursor advances race-free."""
        self._game_counter += 1
        game_id = f"arena_{int(datetime.now().timestamp())}_{self._game_counter}"
        for a in agents:
            self.agent_to_game[a] = game_id
        # Practice and competition must draw from DIFFERENT pools, or the build
        # phase is free memorisation of competition pairs. Slicing alone does not
        # achieve this: it is competition-only, so practice games read the whole
        # master pool and the competition then slices that same file from zero.
        # Pointing the practice phase at its own file is what makes the two
        # genuinely disjoint.
        env = None
        practice_pool = os.getenv("EMAIL_GAME_PRACTICE_POOL_PATH", "").strip()
        if practice_pool and _nav_board() != "competition":
            env = {**os.environ, "MESSAGE_ALIAS_POOL_PATH": practice_pool}

        # Competition anti-memorization: hand this game its own disjoint slice of the
        # pool via a private MESSAGE_ALIAS_POOL_PATH, so no two games share pairs.
        if _unique_pool_enabled():
            if getattr(self, "_wave_slice", None) is None:
                self._wave_slice = self._alloc_pool_slice(POOL_PAIRS_PER_GAME)
            slice_pairs = self._wave_slice
            if slice_pairs:
                self._pool_slice_dir.mkdir(parents=True, exist_ok=True)
                sf = self._pool_slice_dir / f"{game_id}.json"
                sf.write_text(json.dumps({"pairs": slice_pairs}), encoding="utf-8")
                env = {**os.environ, "MESSAGE_ALIAS_POOL_PATH": str(sf)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.game.run_session",
             "--game-id", game_id, "--agents", ",".join(agents),
             "--server", os.getenv("EMAIL_GAME_SELF_URL", "http://127.0.0.1:8000")],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        self.active_games[game_id] = {"agents": agents, "proc": proc}
        self.current_game_in_progress = True
        print(f"🎯 Launched game {game_id}: {agents} ({len(self.active_games)} game(s) running)")
        asyncio.create_task(self._watch_game(game_id))

    async def _watch_game(self, game_id: str) -> None:
        """Wait for a game process to exit, then requeue its agents and refill."""
        info = self.active_games.get(game_id)
        if not info:
            return
        proc = info["proc"]
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, proc.wait)
        except Exception as e:
            print(f"⚠️  Error waiting on game {game_id}: {e}")
        print(f"✅ Game {game_id} finished")
        async with self._queue_lock:
            agents = self.active_games.get(game_id, {}).get("agents", [])
            departed = set(self.departed_from.get(game_id, set()))
            self.active_games.pop(game_id, None)
            self.departed_from.pop(game_id, None)
            for a in agents:
                if self.agent_to_game.get(a) == game_id:
                    del self.agent_to_game[a]
            self.current_game_in_progress = len(self.active_games) > 0

            # Purge this finished game's mail so a reconnecting agent can't replay
            # a stale backlog (the rejoin-flood bug). Safe here: scoring has already
            # consumed the submissions, and the next game's mail carries a new
            # game_id so it is untouched. Within-game history was preserved for the
            # whole game (we only clear at the GAME boundary, never per round).
            purged = self.purge_game_messages(game_id)
            if purged:
                print(f"🧹 Purged {purged} message(s) from finished game {game_id}")

            # Drop this game's private pool slice, if any (unique-pool-per-game).
            try:
                (self._pool_slice_dir / f"{game_id}.json").unlink()
            except OSError:
                pass

            if not REQUEUE_ENABLED:
                print("🧪 Requeue disabled: not re-queuing (single game).")
                return

            # Dead-agent detection: update each non-departed agent's zero-score
            # streak from this game's result. An agent that scored 0 across
            # DEAD_AGENT_STRIKES consecutive games is treated as non-functional
            # and removed (not re-queued, socket closed) so it stops polluting
            # games; it may reconnect once fixed.
            scores = self._read_game_scores(game_id)
            dead = []
            if DEAD_AGENT_STRIKES and scores:
                for a in agents:
                    if a in departed:
                        continue
                    if scores.get(a, 0) <= 0:
                        self.zero_streak[a] = self.zero_streak.get(a, 0) + 1
                    else:
                        self.zero_streak[a] = 0
                    if self.zero_streak.get(a, 0) >= DEAD_AGENT_STRIKES:
                        dead.append(a)

            connected = set(manager.active.keys())
            scheduled_immediate = False
            for a in agents:
                if a in departed or a in dead:
                    continue
                if a in connected and a not in self.waiting_queue and a not in self.agent_to_game:
                    if REQUEUE_COOLDOWN_SEC > 0:
                        # Re-queue after a settle window (see _requeue_after).
                        asyncio.create_task(self._requeue_after(a, REQUEUE_COOLDOWN_SEC))
                    else:
                        self.waiting_queue.append(a)
                        scheduled_immediate = True
                        print(f"↩️  Re-queued {a} for the next game")

            for a in dead:
                self.zero_streak[a] = 0
                print(f"💀 {a} scored 0 in {DEAD_AGENT_STRIKES} consecutive games - "
                      f"removing as inactive (may reconnect once fixed).")
                asyncio.create_task(manager.kick(
                    a, "Removed for inactivity: your agent scored no points in "
                       "consecutive games. Fix it and reconnect to rejoin."))

            # When using a cooldown, each _requeue_after schedules matchmaking
            # itself; only schedule here for the immediate path.
            if scheduled_immediate:
                self._schedule_matchmaking()

    async def leave_queue(self, agent_id: str) -> bool:
        """Remove agent from waiting_queue if present.
        
        Returns True if agent was removed, False if not in queue.
        """
        async with self._queue_lock:
            if agent_id in self.waiting_queue:
                self.waiting_queue.remove(agent_id)
                print(f"📤 Agent {agent_id} left queue (remaining: {len(self.waiting_queue)})")
                return True
            return False


# Global email server instance
email_server = EmailServer()

# FastAPI app
app = FastAPI(title="The Email Game Email Server", version="1.0.0")


# Registered against STARLETTE's HTTPException, not FastAPI's subclass:
# FastAPI installs its default JSON handler on the base class, and a handler
# on the subclass never gets consulted - the page kept rendering raw JSON.
@app.exception_handler(StarletteHTTPException)
async def _friendly_auth_page(request: Request, exc: StarletteHTTPException):
    """Answer 401/403 on a competitor PAGE with a page, not raw JSON.

    Navigating to /agent/<name> without a token used to render
    {"detail":"Missing token"} in the browser - technically accurate and
    completely useless to the person reading it. It names no cause and no fix,
    and it looks like the site is broken rather than that a link is missing.

    Only page navigations are rewritten. Anything wanting JSON (the pages' own
    fetches, the agent client, scripts) still gets JSON, so nothing that parses
    these responses changes behaviour.
    """
    wants_html = "text/html" in (request.headers.get("accept") or "")
    is_page = any(request.url.path.startswith(p) for p in
                  ("/agent/", "/watch", "/history", "/leaderboard"))
    if exc.status_code not in (401, 403) or not wants_html or not is_page:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open your agent's link - The Email Game</title>{BRAND_OVERRIDE}
<style>
  body {{ padding:2.5rem 1.25rem; }}
  .wrap {{ max-width:560px; margin:0 auto; }}
  .card {{ background:#fff; border-radius:14px; padding:1.5rem 1.75rem;
    box-shadow:0 0 0 1px var(--line), var(--shadow); }}
  h1 {{ font-size:1.25rem; margin:0 0 .6rem; }}
  p {{ color:var(--ink-2); line-height:1.6; margin:.6rem 0; }}
  code {{ background:var(--surface-2); padding:.15rem .4rem; border-radius:5px;
    font-size:.86rem; overflow-wrap:anywhere; }}
  .steps {{ margin:.9rem 0 0; padding-left:1.15rem; color:var(--ink-2); line-height:1.7; }}
</style></head><body><div class="wrap">{BRANDBAR}
  <div class="card">
    <h1>This page needs your agent's link</h1>
    <p>These pages show <strong>your own agent's</strong> games, so they only open
       from the link your agent prints when it starts. That link is what
       identifies you - without it we cannot tell whose stats to show.</p>
    <ol class="steps">
      <li>If your agent is running, look in its terminal for the
          <strong>Watch your match</strong> link and open it once. This page works
          from then on in this browser.</li>
      <li>Scrolled past it? The link is printed again at the start of
          <strong>every match</strong> - the next one will show it.</li>
      <li>Not running yet? Start your agent and it prints the link immediately.</li>
    </ol>
    <p><strong>Do not restart an agent that is mid-match</strong> - that forfeits
       the game. You never need to restart to get this link back.</p>
    <p style="margin-top:1rem">Public pages you can always open:
       <a href="/leaderboard">the leaderboard</a>.</p>
  </div>
</div>{BRAND_FOOTER}</body></html>"""
    return HTMLResponse(status_code=exc.status_code, content=body)


@app.on_event("startup")
async def _start_background_monitors():
    """Start the gateway usage monitor once the loop exists.

    Startup, not first-message: it used to piggyback on the queue processor,
    which only starts when a message happens to take the queued path - so on a
    quiet server the monitor silently never ran.
    """
    email_server._ensure_gateway_monitor()


@app.middleware("http")
async def _no_store_html(request: Request, call_next):
    """Never let a browser cache the competitor-facing HTML.

    These pages carry their own CSS and JS inline and were served with NO cache
    headers at all, which leaves caching to browser heuristics - so a competitor
    who opened the leaderboard once could keep seeing a stale copy after a
    deploy, with no way to know. On competition day that means someone reading
    the wrong rules or an old board and having no idea why.

    Only HTML is marked no-store; JSON API responses are untouched, since the
    pages already re-fetch those on a timer.
    """
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response

# ---------------------------------------------------------------------------
# Agent registration (Step 0-b of deployment plan)
# ---------------------------------------------------------------------------


class RegisterAgentRequest(BaseModel):
    agent_id: str
    rsa_public_key: str
    entry_key: Optional[str] = None  # the competitor's issued key (entry credential)


@app.post("/register_agent", status_code=201)
async def register_agent(request: RegisterAgentRequest):
    """Register a remote agent and return a short-lived JWT."""

    print(f"🔐 Registration request for {request.agent_id}")
    print(f"📋 Currently registered agents: {list(email_server.registered_agents.keys())}")

    # Reserved-name protection: no competitor may register a name that lets them
    # impersonate the game authority. The real moderator sends as "moderator".
    # We normalize first (lowercase, strip non-alphanumerics, fold common
    # leetspeak) so "m0d_live", "M-O-D", "the_moderator" all collapse to a
    # comparable form, then block confusable roots/prefixes. This is defense in
    # depth: game-scoped delivery already prevents out-of-game impersonation
    # regardless of name, so a heuristic here is acceptable.
    _norm = (request.agent_id or "").lower()
    for a, b in (("0", "o"), ("1", "l"), ("3", "e"), ("4", "a"), ("5", "s"), ("7", "t"), ("@", "a"), ("$", "s")):
        _norm = _norm.replace(a, b)
    _norm = "".join(ch for ch in _norm if ch.isalnum())
    _RESERVED_SUBSTR = ("moderat", "admin", "official", "system", "server",
                        "gamemaster", "referee")
    _RESERVED_EXACT = ("mod", "sys", "host", "root", "ref", "gm", "srv", "hostmaster")
    if (_norm in _RESERVED_EXACT or _norm.startswith("mod")
            or any(tok in _norm for tok in _RESERVED_SUBSTR)):
        print(f"❌ Agent {request.agent_id} rejected: reserved/impersonating name")
        raise HTTPException(
            status_code=400,
            detail=("That agent_id is reserved (it resembles the game moderator/"
                    "an admin role). Please choose a different name."),
        )

    # Entry gating (when an allowlist is configured): the agent_id must be on the
    # roster AND the presented issued key must be the one bound to it. This blocks
    # off-roster names and impersonation (you can't register someone else's name
    # without their key). Messages are competitor-facing.
    if ENTRY_ALLOWLIST is not None:
        expected = ENTRY_ALLOWLIST.get(request.agent_id)
        if expected is None:
            print(f"❌ Agent {request.agent_id} rejected: not on the roster")
            raise HTTPException(
                status_code=403,
                detail=(f"'{request.agent_id}' is not on the competitor roster. Use the "
                        f"exact agent name from your handout (lowercase, underscores)."),
            )
        if not request.entry_key:
            raise HTTPException(
                status_code=401,
                detail=("This competition requires your issued key. Set OPENAI_API_KEY "
                        "to the key from your handout, then run your agent again."),
            )
        got = hashlib.sha256(request.entry_key.encode()).hexdigest()
        if got != expected:
            print(f"❌ Agent {request.agent_id} rejected: key does not match roster entry")
            raise HTTPException(
                status_code=403,
                detail=(f"Your key does not match agent name '{request.agent_id}'. Check that "
                        f"you are using YOUR own handout's name and key (not someone else's)."),
            )

    # Name-collision protection: an agent_id is locked to the public key that
    # first claimed it. The same key may re-register freely (reconnect / token
    # refresh); a different key is rejected so two players can't share a name.
    existing = email_server.registered_agents.get(request.agent_id)
    if existing is not None and existing.get("rsa_public_key") != request.rsa_public_key:
        print(f"❌ Agent {request.agent_id} name already taken by a different key")
        raise HTTPException(
            status_code=409,
            detail=(f"Agent ID '{request.agent_id}' is already taken by another "
                    f"player. Choose a different agent_id."),
        )

    # New registration, or same player reconnecting → (re)store and issue token
    email_server.registered_agents[request.agent_id] = {
        "rsa_public_key": request.rsa_public_key
    }
    print(f"✅ Agent {request.agent_id} registered successfully")

    # Action token – 30-minute expiry (the agent refreshes it as needed).
    now = time.time()  # true UTC epoch; datetime.utcnow().timestamp() is wrong off-UTC (naive -> treated as local)
    token = jwt.encode({"sub": request.agent_id, "exp": now + 1800},
                       JWT_SECRET, algorithm="HS256")
    # View-only token for the live watch page – longer-lived (12h) since it can
    # only READ this agent's own mailbox, never act. Safe to embed in a shareable
    # watch link: the action endpoints reject scope="view".
    view_token = jwt.encode({"sub": request.agent_id, "scope": "view", "exp": now + 43200},
                            JWT_SECRET, algorithm="HS256")

    return {"success": True, "token": token, "view_token": view_token}


@app.get("/")
async def root():
    """Root endpoint - simple dashboard info"""
    return {
        "service": "The Email Game Email Server",
        "status": "running",
        "registered_agents": len(email_server.registered_agents),
        "waiting_queue": len(email_server.waiting_queue),
        "game_in_progress": email_server.current_game_in_progress,
        "leaderboard": "/leaderboard",
        "api_docs": "/docs"
    }

@app.get("/agent_public_key/{agent_id}")
async def get_agent_public_key(agent_id: str):
    """Return the RSA public key submitted by an agent at registration time."""
    agent = email_server.registered_agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not registered")
    return {"agent_id": agent_id, "rsa_public_key": agent["rsa_public_key"]}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "message_count": len(email_server.messages)}


@app.post("/clear_state")
async def clear_state(request: Request):
    """Clear all server state (for testing). Internal-only during a competition."""
    _require_internal(request)
    email_server.clear_all_state()
    return {"success": True, "message": "Server state cleared"}


@app.get("/session_results")
async def get_session_results(request: Request):
    """Get list of available session result files. Internal-only during a competition."""
    _require_internal(request)
    try:
        results_dir = Path(__file__).resolve().parent.parent / "session_results"
        if not results_dir.exists():
            return {"success": True, "files": []}
        
        session_files = list(results_dir.glob("session_arena_*.json"))
        file_info = []
        
        for file_path in session_files:
            file_info.append({
                "filename": file_path.name,
                "size": file_path.stat().st_size,
                "modified": file_path.stat().st_mtime
            })
        
        # Sort by modification time (newest first)
        file_info.sort(key=lambda x: x["modified"], reverse=True)
        
        return {"success": True, "files": file_info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session results: {str(e)}")


@app.get("/session_results/{filename}")
async def get_session_result(filename: str, request: Request):
    """Get a specific session result file. Internal-only during a competition."""
    _require_internal(request)
    try:
        results_dir = Path(__file__).resolve().parent.parent / "session_results"
        file_path = results_dir / filename
        
        # Security check - ensure filename is safe
        if not filename.endswith('.json') or '..' in filename or '/' in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Session result not found")
        
        with open(file_path, 'r') as f:
            session_data = json.load(f)

        return {"success": True, "data": session_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read session result: {str(e)}")


@app.post("/admin/archive_sessions")
async def admin_archive_sessions(request: Request):
    """Internal-only: move every session_arena_*.json into a timestamped archive
    subdir so the leaderboards reset to empty. Data is retained on the volume
    (moved, not deleted), so this is reversible. Use after backing up + ending a
    competition to give a clean board for the next one."""
    _require_internal(request)
    try:
        results_dir = Path(__file__).resolve().parent.parent / "session_results"
        files = list(results_dir.glob("session_arena_*.json"))
        archive = results_dir / f"archive_{int(time.time())}"
        archive.mkdir(parents=True, exist_ok=True)
        moved = 0
        for fp in files:
            try:
                fp.rename(archive / fp.name)
                moved += 1
            except Exception:
                pass
        remaining = len(list(results_dir.glob("session_arena_*.json")))
        return {"success": True, "moved": moved, "archive": archive.name, "remaining": remaining}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to archive sessions: {str(e)}")


def _live_dict() -> dict:
    """Point-in-time arena activity (server-global), competitors only.

    Filler agents are excluded from every count, exactly as they are from the
    leaderboard rows. Counting them made the header disagree with the table
    underneath it - "4 players online" above a single visible row - and would
    have reported our own bots as turnout.
    """
    return {
        "players": sum(1 for a in manager.active if not is_filler(a)),
        "matches": len(email_server.active_games),   # games currently running
        "in_game": sum(1 for a in email_server.agent_to_game if not is_filler(a)),
        "queued": sum(1 for a in email_server.waiting_queue if not is_filler(a)),
        "draining": DRAINING,                        # winding down (no new games)
    }


def _live_for(board: str) -> dict:
    """Live activity belongs to the CURRENTLY ACTIVE board only. During build week
    the connected agents are build-week practice players, so they show on the
    build board, not the competition board (which is just 'starts 6/27'). Once the
    competition is live, activity shows there and the build board is historical
    (no live bar). Prevents build-week players appearing as online/in-match on the
    competition board, and vice versa."""
    return _live_dict() if board == _nav_board() else {}


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page():
    """The competition leaderboard. Outside competition mode (a competitor's own
    local server / playtest), it's a LOCAL board: only games on their machine,
    counting toward nothing - labelled as such so it's never mistaken for the
    real board."""
    try:
        board = "competition" if COMPETITION_MODE else "local"
        return HTMLResponse(render_leaderboard_html(
            compute_leaderboard(window="competition",
                                in_match=set(email_server.agent_to_game)), _live_for(board), board=board,
            online=set(manager.active.keys()), ingame=set(email_server.agent_to_game.keys())))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build leaderboard: {str(e)}")


# The practice ("testing") board is retired: the event now runs one scored board
# for its whole window. These two routes redirect rather than 404 because the
# old URL is already out in comms, handouts and chat history, and a competitor
# who follows a stale link on the day should land on the real board rather than
# an error. The build-window machinery underneath is untouched, so restoring the
# board is a matter of restoring these handlers.
@app.get("/leaderboard/testing", response_class=HTMLResponse)
async def leaderboard_testing_page():
    """Retired practice board -> the one real leaderboard."""
    return RedirectResponse(url="/leaderboard", status_code=308)


@app.get("/api/leaderboard")
async def leaderboard_api():
    """Machine-readable Elo leaderboard for the official competition window."""
    try:
        live = _live_for("competition")    # activity only when competition is the active board
        live["phase"] = _competition_phase(_live_dict())  # phase always accurate
        return {"success": True, "leaderboard": compute_leaderboard(
            window="competition", in_match=set(email_server.agent_to_game)), "live": live}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build leaderboard: {str(e)}")


@app.get("/api/leaderboard/testing")
async def leaderboard_testing_api():
    """Retired practice board -> the one real leaderboard."""
    return RedirectResponse(url="/api/leaderboard", status_code=308)


def _board_window(request: Request) -> str:
    """Map a ?board= query to a leaderboard window (default competition)."""
    return "build" if request.query_params.get("board") in ("build", "testing") else "competition"


@app.get("/agent/{agent_id}", response_class=HTMLResponse)
async def agent_page(agent_id: str, request: Request):
    """Per-agent stats. Own-agent only in competition mode (token required, same
    guard as the mailbox), so competitors can't study opponents' detailed stats.
    ?board=build|competition scopes it to the matching leaderboard window."""
    _require_own_mailbox(request, agent_id)
    try:
        return HTMLResponse(render_agent_html(
            compute_agent_report(agent_id, window=_board_window(request))))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build agent stats: {str(e)}")


@app.get("/watch", response_class=HTMLResponse)
async def watch_page():
    """Competitor-facing live match viewer. The page is static; the data it
    fetches (/get_messages, /get_sent) is gated by _require_own_mailbox, so a
    competitor can only watch their OWN agent's perspective with their own token.
    No cross-agent leakage, so it's safe to expose publicly."""
    return HTMLResponse(render_watch_html(local=not COMPETITION_MODE))


@app.get("/admin/watch/{agent_id}")
async def admin_watch(agent_id: str, key: str = ""):
    """HOST-ONLY: watch ANY agent's live match. Mints a read-only view token for
    that agent and redirects to its watch page. Gated by the internal key (the
    host has it; competitors don't), so only the host can watch arbitrary agents.
    View-only - the token can read that agent's mailbox but never act.
    Usage: /admin/watch/<agent_id>?key=<EMAIL_GAME_INTERNAL_KEY>"""
    if key != INTERNAL_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    now = time.time()  # true UTC epoch; datetime.utcnow().timestamp() is wrong off-UTC (naive -> treated as local)
    view_token = jwt.encode({"sub": agent_id, "scope": "view", "exp": now + 43200},
                            JWT_SECRET, algorithm="HS256")
    from urllib.parse import quote
    return RedirectResponse(f"/watch?agent={quote(agent_id)}&token={quote(view_token)}")


@app.get("/history", response_class=HTMLResponse)
async def history_page():
    """Competitor-facing match history. Static page; its data endpoints
    (/matches, /match) are gated by _require_own_mailbox."""
    return HTMLResponse(render_history_html(local=not COMPETITION_MODE))


class ScoreEventsPush(BaseModel):
    events: List[Dict[str, Any]]


# Per-message scoring verdicts for the live feed: message_id -> {label, cls}.
# Display-only; owner-scoped for free because mailboxes already are. Capped
# FIFO so it can never grow without bound.
MSG_VERDICTS: Dict[str, Dict] = {}
_MSG_VERDICT_CAP = 4000


class MsgVerdictsPush(BaseModel):
    verdicts: List[Dict[str, Any]]


@app.post("/internal/score_events")
async def push_score_events(payload: ScoreEventsPush, token_agent: str = Depends(_require_token)):
    """Receive a round's point events from a game subprocess (moderator only).

    Games run in their own process, so this is how the live watch feed learns
    that points were awarded. Purely for display - nothing here feeds scoring,
    ratings or game state, so losing these events costs a visual effect and
    nothing else."""
    if token_agent != "moderator":
        raise HTTPException(status_code=403, detail="Forbidden")
    from src.game.scoring import record_score_event
    for e in payload.events or []:
        try:
            record_score_event(str(e["agent"]), int(e["delta"]), str(e.get("reason", "")))
        except Exception:
            continue
    return {"success": True, "count": len(payload.events or [])}


class CodeSubmission(BaseModel):
    agent_id: str
    entry_key: Optional[str] = None
    filename: str
    content_b64: str


@app.post("/submit_code")
async def submit_code(sub: CodeSubmission):
    """Prize-verification code drop. One command from the competitor's side
    (scripts/submit_code.py); authenticated exactly like registration (the
    issued key), size-capped, and closed one hour after the competition ends
    so a submission is a deadline-proof receipt (timestamp + sha256). Files
    land under session_results/ so every backup already includes them."""
    if ENTRY_ALLOWLIST is not None:
        expected = ENTRY_ALLOWLIST.get(sub.agent_id)
        if not expected:
            raise HTTPException(status_code=403, detail="Unknown agent name.")
        if not sub.entry_key or hashlib.sha256(sub.entry_key.encode()).hexdigest() != expected:
            raise HTTPException(status_code=403, detail="Key does not match this agent name.")
    if COMPETITION_END_TIME:
        try:
            end_dt = datetime.fromisoformat(COMPETITION_END_TIME)
            if datetime.now() > end_dt + timedelta(hours=1):
                raise HTTPException(status_code=410,
                                    detail="Submission window closed (competition end + 1 hour).")
        except HTTPException:
            raise
        except Exception:
            pass
    import base64 as _b64
    try:
        raw = _b64.b64decode(sub.content_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64.")
    if not raw or len(raw) > 1_000_000:
        raise HTTPException(status_code=400, detail="Submission must be 1 byte to 1 MB.")
    safe_agent = re.sub(r"[^A-Za-z0-9_.-]", "_", sub.agent_id)[:64]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(sub.filename).name)[:80] or "submission.bin"
    sha = hashlib.sha256(raw).hexdigest()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    outdir = PROJECT_ROOT / "session_results" / "code_submissions"
    outdir.mkdir(parents=True, exist_ok=True)
    stored = f"{safe_agent}_{stamp}_{sha[:8]}_{safe_name}"
    (outdir / stored).write_bytes(raw)
    with open(outdir / "manifest.jsonl", "a", encoding="utf-8") as mf:
        mf.write(json.dumps({"agent": sub.agent_id, "stored_as": stored, "sha256": sha,
                             "bytes": len(raw), "received_utc": stamp,
                             "filename": sub.filename}) + "\n")
    print(f"[submit_code] {sub.agent_id} -> {stored} ({len(raw)} bytes)")
    return {"success": True, "sha256": sha, "stored_as": stored, "received_utc": stamp}


@app.post("/internal/msg_verdicts")
async def push_msg_verdicts(payload: MsgVerdictsPush, token_agent: str = Depends(_require_token)):
    """Per-message scoring verdicts from the game process (moderator only).
    Display-only: they become badges on the owner's live feed."""
    if token_agent != "moderator":
        raise HTTPException(status_code=403, detail="Forbidden")
    for v in payload.verdicts or []:
        mid = str(v.get("message_id") or "")
        if not mid:
            continue
        cls = v.get("cls") if v.get("cls") in ("good", "bad", "warn") else "warn"
        MSG_VERDICTS[mid] = {"label": str(v.get("label", ""))[:60], "cls": cls,
                             "tip": str(v.get("tip", ""))[:400]}
    while len(MSG_VERDICTS) > _MSG_VERDICT_CAP:
        MSG_VERDICTS.pop(next(iter(MSG_VERDICTS)))
    return {"success": True}


@app.get("/admin/gateway_report")
async def gateway_report(request: Request):
    """HOST-ONLY: windows where an agent played but its key spent nothing.

    Evidence for the prize-time conversation, not an accusation: a legitimate
    no-LLM agent produces exactly this pattern. Pair it with
    scripts/audit_spend_vs_games.py, and for a prize place ask for a
    reproduction run rather than drawing a conclusion from this alone."""
    _require_internal(request)
    by_agent: Dict[str, int] = {}
    for f in email_server.gateway_findings:
        by_agent[f["agent"]] = by_agent.get(f["agent"], 0) + 1
    return {
        "enabled": GATEWAY_MONITOR,
        "window_sec": GATEWAY_MONITOR_SEC,
        "min_messages": GATEWAY_MONITOR_MIN_MSGS,
        "flagged_windows_by_agent": dict(sorted(by_agent.items(), key=lambda kv: -kv[1])),
        "findings": email_server.gateway_findings[-200:],
    }


@app.get("/score_events/{agent_id}")
async def score_events(agent_id: str, request: Request, since: float = 0.0):
    """Point changes for THIS agent, for the watch page's live +1 / -1.

    Own-token only, same guard as the mailbox - you can never see another
    competitor's scoring as it happens. See scoring.record_score_event for what
    this deliberately reveals to a competitor watching their own match."""
    _require_own_mailbox(request, agent_id)
    from src.game.scoring import score_events_for
    return {"success": True, "events": score_events_for(agent_id, since)}


@app.get("/matches/{agent_id}")
async def get_matches(agent_id: str, request: Request):
    """List an agent's past matches. Own-token only (same guard as the mailbox)."""
    _require_own_mailbox(request, agent_id)
    return {"success": True, "agent_id": agent_id,
            "matches": list_matches_for_agent(agent_id, window=_board_window(request))}


# Local-sandbox spend estimates, reported by the agents themselves (the
# hosted game reads the gateway instead). {agent_id: {est_cost, ...}}
_LOCAL_USAGE: Dict[str, Dict] = {}

# Cache for the local key's own gateway record (see _gateway_key_self).
_SELF_KEY_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}


async def _gateway_key_self() -> Optional[Dict]:
    """REAL spend/budget for the key in the local env, straight from the
    gateway (a LiteLLM key may read its own record via /key/info).

    Only consulted when OPENAI_BASE_URL already points at OUR gateway - then
    the key is being sent there on every LLM call anyway, so this adds zero
    exposure. A personal key on api.openai.com is never sent anywhere.
    Cached briefly; None means fall back to the local estimate."""
    key = (os.getenv("EMAIL_GAME_ENTRY_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base = (os.getenv("EMAIL_GAME_GATEWAY_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    if not key.startswith("sk-") or "theemailgame.com" not in base:
        return None
    if time.time() - _SELF_KEY_CACHE["at"] < 30:
        return _SELF_KEY_CACHE["data"]
    _SELF_KEY_CACHE["at"] = time.time()   # also on failure: don't hammer
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(base + "/key/info",
                            headers={"Authorization": f"Bearer {key}"})
        info = (r.json() or {}).get("info") if r.status_code == 200 else None
        _SELF_KEY_CACHE["data"] = ({"spend": float(info.get("spend") or 0.0),
                                    "max_budget": info.get("max_budget"),
                                    "blocked": info.get("blocked")}
                                   if info else None)
    except Exception:
        _SELF_KEY_CACHE["data"] = None
    return _SELF_KEY_CACHE["data"]


class UsageReport(BaseModel):
    agent_id: str
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    est_cost: float = 0.0


@app.post("/report_usage")
async def report_usage(rep: UsageReport, token_agent: str = Depends(_require_token)):
    """An agent's own cumulative LLM usage (local-sandbox spend display)."""
    if token_agent != rep.agent_id:
        raise HTTPException(status_code=403, detail="Not your usage to report.")
    _LOCAL_USAGE[rep.agent_id] = {"model": rep.model,
                                  "prompt_tokens": max(0, rep.prompt_tokens),
                                  "completion_tokens": max(0, rep.completion_tokens),
                                  "est_cost": max(0.0, rep.est_cost)}
    return {"success": True}


@app.get("/budget/{agent_id}")
async def get_budget(agent_id: str, request: Request):
    """This agent's own LLM budget. Own-token only.

    Competitors could only see this in the terminal, and many hand the commands
    to a coding assistant and never read that output - so the first sign of a
    spent key was the agent failing mid-afternoon. The server never holds anyone's
    key; it asks the gateway, which indexes by the key_alias we issue as the agent
    handle. `blocked` is surfaced too: a blocked key still registers and queues
    normally, so without this the failure is invisible until every LLM call dies.
    """
    _require_own_mailbox(request, agent_id)
    recs = _fetch_gateway_keys()
    if recs is None or agent_id not in recs:
        # No gateway (local sandbox): fall back to the agent's own reported
        # usage estimate, so spend is visible locally too.
        # Real numbers first: an issued key may read its own gateway record,
        # which is exact and covers everything the key funds - locally that
        # is all four agents in every match, and it is the SAME pool official
        # play draws from. Falls back to the agents' own estimates only when
        # the sandbox runs on a personal key outside the gateway.
        gk = await _gateway_key_self()
        if gk:
            cap = gk.get("max_budget")
            left = (max(0.0, float(cap) - gk["spend"]) if cap else None)
            return {"success": True, "available": True, "spend": round(gk["spend"], 4),
                    "max_budget": cap,
                    "left": (round(left, 4) if left is not None else None),
                    "blocked": bool(gk.get("blocked"))}
        # No gateway record = no display. The spend chip only ever shows the
        # EXACT remaining balance on the dealt key - never an estimate.
        return {"success": True, "available": False}
    r = recs[agent_id]
    cap = r.get("max_budget")
    left = (max(0.0, float(cap) - r["spend"]) if cap else None)
    return {"success": True, "available": True, "spend": round(r["spend"], 4),
            "max_budget": cap, "left": (round(left, 4) if left is not None else None),
            "blocked": r.get("blocked", False)}


# ---------------------------------------------------------------------------
# First-run flags: which intros/tours this HUMAN has already seen, so they
# play exactly once even across the local sandbox and the hosted game. The
# link between environments is the competitor's issued key: both know it, so
# both file flags under sha256(key). Browsers can't share storage across
# origins (third-party storage is partitioned), which is why this lives here.
# Every path degrades gracefully to per-origin localStorage behavior.
# ---------------------------------------------------------------------------
GREETED_PATH = os.path.join("data", "greeted_flags.json")
FIRSTRUN_HUB = os.getenv("EMAIL_GAME_FIRSTRUN_HUB",
                         "https://play.theemailgame.com").rstrip("/")
_HUB_FLAGS_CACHE: Dict[str, Any] = {"at": 0.0, "flags": {}}


def _greeted_load() -> Dict[str, Dict[str, bool]]:
    try:
        with open(GREETED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _greeted_save(d: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(GREETED_PATH), exist_ok=True)
        tmp = GREETED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, GREETED_PATH)
    except Exception as e:
        print(f"[firstrun] could not persist flags: {e}")


def _greet_ids(agent_id: str) -> List[str]:
    """Identities a flag is filed under. Hosted: the agent's entry-key hash
    (stable across environments) plus the agent id. Local sandbox: one human,
    one bucket."""
    ids = []
    if ENTRY_ALLOWLIST is not None:
        if agent_id in ENTRY_ALLOWLIST:
            ids.append(ENTRY_ALLOWLIST[agent_id])
        if agent_id:
            ids.append(agent_id)
    else:
        ids.append("local")
    return ids


def _local_key_hash() -> str:
    """sha256 of the issued key in the local .env, if one is configured.
    Only the hash ever leaves the machine - never the key itself."""
    k = (os.getenv("EMAIL_GAME_ENTRY_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return hashlib.sha256(k.encode()).hexdigest() if k.startswith("sk-") else ""


async def _hub_flags_cached() -> Dict[str, bool]:
    """The hosted game's flags for this human's key (local sandbox only),
    cached for a minute so page loads stay instant and offline is silent."""
    kh = _local_key_hash()
    if not kh:
        return {}
    if time.time() - _HUB_FLAGS_CACHE["at"] < 60:
        return _HUB_FLAGS_CACHE["flags"]
    _HUB_FLAGS_CACHE["at"] = time.time()   # also on failure: don't hammer
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.post(FIRSTRUN_HUB + "/firstrun_flags_bykey",
                             json={"key_hash": kh})
        if r.status_code == 200:
            _HUB_FLAGS_CACHE["flags"] = r.json().get("flags", {}) or {}
    except Exception:
        pass
    return _HUB_FLAGS_CACHE["flags"]


async def _hub_push(flag: str) -> None:
    kh = _local_key_hash()
    if not kh:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(FIRSTRUN_HUB + "/firstrun_flags_bykey",
                         json={"key_hash": kh, "flag": flag})
        _HUB_FLAGS_CACHE["flags"][flag] = True
    except Exception:
        pass


@app.get("/firstrun_flags/{agent_id}")
async def firstrun_flags(agent_id: str):
    if agent_id == "-":
        agent_id = ""
    d = _greeted_load()
    out: Dict[str, bool] = {}
    for gid in _greet_ids(agent_id):
        out.update(d.get(gid, {}))
    if ENTRY_ALLOWLIST is None:
        out = {**(await _hub_flags_cached()), **out}
    return {"success": True, "flags": out}


class FirstrunMark(BaseModel):
    agent_id: str = ""
    flag: str


@app.post("/firstrun_flags")
async def firstrun_mark(m: FirstrunMark):
    flag = m.flag.strip()
    if not re.fullmatch(r"[a-z0-9_]{1,32}", flag):
        raise HTTPException(status_code=400, detail="bad flag name")
    d = _greeted_load()
    for gid in _greet_ids(m.agent_id.strip()):
        d.setdefault(gid, {})[flag] = True
    _greeted_save(d)
    if ENTRY_ALLOWLIST is None:
        asyncio.create_task(_hub_push(flag))
    return {"success": True}


class HubFlag(BaseModel):
    key_hash: str
    flag: str = ""


@app.post("/firstrun_flags_bykey")
async def firstrun_flags_bykey(h: HubFlag):
    """Cross-environment sync point (hosted side): a local sandbox files or
    fetches flags under the competitor's entry-key hash."""
    if ENTRY_ALLOWLIST is None:
        raise HTTPException(status_code=404, detail="No roster on this server.")
    kh = h.key_hash.strip().lower()
    if kh not in set(ENTRY_ALLOWLIST.values()):
        raise HTTPException(status_code=403, detail="Unknown key.")
    d = _greeted_load()
    flag = h.flag.strip()
    if flag:
        if not re.fullmatch(r"[a-z0-9_]{1,32}", flag):
            raise HTTPException(status_code=400, detail="bad flag name")
        d.setdefault(kh, {})[flag] = True
        _greeted_save(d)
    return {"success": True, "flags": d.get(kh, {})}


@app.get("/match/{game_id}/{agent_id}")
async def get_match(game_id: str, agent_id: str, request: Request):
    """One match's detail. Own perspective during the competition; full transcript
    after COMPETITION_END_TIME (decided inside match_detail). Own-token only."""
    _require_own_mailbox(request, agent_id)
    detail = match_detail(game_id, agent_id, window=_board_window(request))
    if detail is None:
        raise HTTPException(status_code=404, detail="Match not found for this agent")
    return {"success": True, "match": detail}


@app.get("/api/agent/{agent_id}")
async def agent_api(agent_id: str, request: Request):
    """Machine-readable per-agent stats. Own-agent only (competition mode)."""
    _require_own_mailbox(request, agent_id)
    try:
        return {"success": True, "report": compute_agent_report(agent_id, window=_board_window(request))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build agent stats: {str(e)}")


@app.post("/send_message")
async def send_message(request: SendMessageRequest, token_agent: str = Depends(_require_token)):
    """Send a message from one agent to another"""
    # Validate recipient
    if not _validate_recipient(request.to):
        raise HTTPException(status_code=400, detail=f"Invalid recipient: {request.to}")
    _check_delivery_allowed(token_agent, request.to)

    try:
        # Sender is derived from JWT token, not client payload
        message_data = {
            "from_agent": token_agent,
            "to": request.to,
            "subject": request.subject,
            "body": request.body
        }

        message_id = email_server.store_message(message_data)
        
        return {
            "success": True,
            "message_id": message_id,
            "status": "sent"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {str(e)}")


@app.post("/send_message_queued")
async def send_message_queued(request: SendMessageRequest, token_agent: str = Depends(_require_token)):
    """Send a message via the queue (better for concurrent requests)"""
    # Validate recipient
    if not _validate_recipient(request.to):
        raise HTTPException(status_code=400, detail=f"Invalid recipient: {request.to}")
    _check_delivery_allowed(token_agent, request.to)

    try:
        # Sender is derived from JWT token, not client payload
        message_data = {
            "from_agent": token_agent,
            "to": request.to,
            "subject": request.subject,
            "body": request.body
        }

        message_id = await email_server.store_message_queued(message_data)
        
        return {
            "success": True,
            "message_id": message_id,
            "status": "queued"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue message: {str(e)}")


@app.post("/send_batch")
async def send_batch_messages(
    request: BatchSendRequest,
    token_agent: str = Depends(_require_token),
):
    """Send multiple messages at once (optimized for moderator instructions)"""
    try:
        results = []
        
        # Validate all recipients first
        for msg_request in request.messages:
            if not _validate_recipient(msg_request.to):
                raise HTTPException(status_code=400, detail=f"Invalid recipient in batch: {msg_request.to}")
            _check_delivery_allowed(token_agent, msg_request.to)
        
        # Queue all messages concurrently
        tasks = []
        for msg_request in request.messages:
            # Sender is derived from JWT token, not client payload
            message_data = {
                "from_agent": token_agent,
                "to": msg_request.to,
                "subject": msg_request.subject,
                "body": msg_request.body
            }
            task = email_server.store_message_queued(message_data)
            tasks.append(task)
        
        # Wait for all messages to be processed
        message_ids = await asyncio.gather(*tasks)
        
        for i, message_id in enumerate(message_ids):
            results.append({
                "to": request.messages[i].to,
                "message_id": message_id,
                "status": "queued"
            })
        
        return {
            "success": True,
            "messages_sent": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send batch: {str(e)}")


@app.get("/get_messages/{agent_id}")
async def get_messages(agent_id: str, request: Request):
    """Get all messages for a specific agent (your own only, during a competition)."""
    _require_own_mailbox(request, agent_id)
    try:
        messages = email_server.get_messages_for_agent(agent_id)
        
        # Mark messages as delivered when retrieved
        for msg in messages:
            if msg["status"] == "sent":
                email_server.mark_delivered(msg["message_id"])
        
        messages = [({**m, "verdict": MSG_VERDICTS[m["message_id"]]}
                     if m.get("message_id") in MSG_VERDICTS else m)
                    for m in messages]
        return {
            "success": True,
            "agent_id": agent_id,
            "messages": messages,
            "count": len(messages)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get messages: {str(e)}")


@app.get("/submissions/{game_id}")
async def get_submissions(game_id: str, request: Request):
    """Submissions (moderator-addressed mail) for one game only, plus the set of
    agents who left this game. Internal-only during a competition."""
    _require_internal(request)
    msgs = email_server.get_submissions(game_id)
    departed = sorted(email_server.departed_from.get(game_id, set()))
    return {"success": True, "messages": msgs, "count": len(msgs), "departed": departed}


@app.get("/get_all_messages")
async def get_all_messages(request: Request):
    """Get all messages in the system. Internal-only during a competition (this
    would otherwise let any player read every agent's mail)."""
    _require_internal(request)
    try:
        messages = email_server.get_all_messages()
        return {
            "success": True,
            "messages": messages,
            "count": len(messages)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get all messages: {str(e)}")


@app.put("/mark_read/{message_id}")
async def mark_message_read(message_id: str):
    """Mark a message as read"""
    try:
        success = email_server.mark_read(message_id)
        if success:
            return {
                "success": True,
                "message_id": message_id,
                "status": "read"
            }
        else:
            raise HTTPException(status_code=404, detail="Message not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to mark message as read: {str(e)}")


@app.get("/message_status/{message_id}")
async def get_message_status(message_id: str):
    """Get the status of a specific message"""
    try:
        status = email_server.get_message_status(message_id)
        if status == "unknown":
            raise HTTPException(status_code=404, detail="Message not found")
        
        return {
            "success": True,
            "message_id": message_id,
            "status": status
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get message status: {str(e)}")




@app.get("/get_sent/{agent_id}")
async def get_sent_messages(agent_id: str, request: Request):
    """Get all messages that a specific agent has sent (your own only, during a competition)."""
    _require_own_mailbox(request, agent_id)
    try:
        sent_messages = [msg for msg in email_server.messages if msg["from"] == agent_id]
        # No status mutation for sent mail – outbox should reflect original state
        sent_messages = [({**m, "verdict": MSG_VERDICTS[m["message_id"]]}
                     if m.get("message_id") in MSG_VERDICTS else m)
                    for m in sent_messages]
        return {
            "success": True,
            "agent_id": agent_id,
            "messages": sent_messages,
            "count": len(sent_messages)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get sent messages: {str(e)}")


@app.get("/get_conversation/{agent_id}")
async def get_conversation(agent_id: str, request: Request):
    """All messages involving the agent (your own only, during a competition)."""
    _require_own_mailbox(request, agent_id)
    try:
        # Filter messages where the agent is either sender or recipient
        related = [msg for msg in email_server.messages if msg["from"] == agent_id or msg["to"] == agent_id]

        # Sort by timestamp (ISO strings sort lexicographically in the same order as datetimes)
        related.sort(key=lambda m: m["timestamp"])

        # Mark incoming *unseen* messages as delivered (same rule as inbox endpoint)
        for msg in related:
            if msg["to"] == agent_id and msg["status"] == "sent":
                email_server.mark_delivered(msg["message_id"])

        return {
            "success": True,
            "agent_id": agent_id,
            "messages": related,
            "count": len(related)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get conversation: {str(e)}")


# ---------------------------------------------------------------------------
# Queue endpoint – Step 0-c
# ---------------------------------------------------------------------------


class JoinQueueRequest(BaseModel):
    agent_id: str


@app.post("/join_queue")
async def join_queue(
    payload: JoinQueueRequest,
    token_agent: str = Depends(_require_token),
):
    """Add agent to waiting_queue and return current length."""

    if token_agent != payload.agent_id:
        raise HTTPException(status_code=403, detail="Token/agent mismatch")

    try:
        new_len = await email_server.join_queue(payload.agent_id)
    except ValueError:
        raise HTTPException(status_code=409, detail="Agent already queued")

    return {"success": True, "position": new_len}


@app.post("/leave_queue")
async def leave_queue_endpoint(
    token_agent: str = Depends(_require_token),
):
    """Remove agent from waiting queue."""
    removed = await email_server.leave_queue(token_agent)
    return {"success": True, "removed": removed}


# Queue status endpoint will be added after ConnectionManager is instantiated


# ----------------------------
# WebSocket connection manager
# ----------------------------


class ConnectionManager:
    """Keeps track of active WebSocket connections per agent and allows sending push notifications."""

    def __init__(self):
        # agent_id -> set[WebSocket]
        self.active: Dict[str, set] = {}

    async def connect(self, agent_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(agent_id, set()).add(websocket)
        print(f"🔗 WebSocket connected for agent {agent_id} (total: {len(self.active.get(agent_id, set()))} connections)")

    def disconnect(self, agent_id: str, websocket: WebSocket):
        if agent_id in self.active and websocket in self.active[agent_id]:
            self.active[agent_id].remove(websocket)
            if not self.active[agent_id]:
                # clean empty entry
                self.active.pop(agent_id, None)
                # Fully disconnected. Don't leave the match immediately: give a
                # grace window for a transient blip to reconnect and resume the
                # same game. If still gone after the window, leave for good.
                try:
                    asyncio.create_task(self._leave_after_grace(agent_id))
                except RuntimeError:
                    self._finalize_leave(agent_id)

    async def _leave_after_grace(self, agent_id: str):
        await asyncio.sleep(DISCONNECT_GRACE_SEC)
        if agent_id in self.active:
            return  # reconnected within the grace window - stays in its match
        self._finalize_leave(agent_id)

    def _finalize_leave(self, agent_id: str):
        """Remove a gone-for-good agent from its match and the queue.

        After this it cannot be routed back into a still-running game, its
        signatures stop counting in that game, and it only re-joins the queue
        for FUTURE matches when it reconnects. Runs on the event loop, so it
        doesn't race the queue lock.
        """
        if agent_id in self.active:
            return  # came back at the last moment
        left_game = email_server.agent_to_game.pop(agent_id, None)
        if agent_id in email_server.waiting_queue:
            email_server.waiting_queue.remove(agent_id)
        if left_game:
            email_server.departed_from.setdefault(left_game, set()).add(agent_id)
            # Clear the leaver's inbox for this game so its reconnect starts clean
            # (the game's other agents keep theirs while it finishes).
            email_server.purge_agent_game_inbox(agent_id, left_game)
            print(f"👋 {agent_id} left match {left_game}; cannot rejoin or affect it")

    async def send_json(self, agent_id: str, payload: Dict):
        """Send payload to all websockets listening for *agent_id*."""
        if agent_id not in self.active:
            _dbg(f"⚠️  No WebSocket connections for agent {agent_id}")
            return
        
        _dbg(f"📡 Sending WebSocket message to {agent_id} ({len(self.active[agent_id])} connections)")
        dead_connections = []
        sent_count = 0
        
        for ws in list(self.active[agent_id]):
            try:
                await ws.send_json(payload)
                sent_count += 1
            except Exception as e:
                print(f"⚠️  WebSocket send failed: {e}")
                dead_connections.append(ws)
                
        for ws in dead_connections:
            self.disconnect(agent_id, ws)

        _dbg(f"✅ WebSocket message sent to {sent_count} connections for {agent_id}")

    async def kick(self, agent_id: str, reason: str):
        """Notify an agent it has been removed, then close its socket(s). Used by
        dead-agent strike-out so a non-functional agent stops being matched."""
        notice = {"message_id": str(uuid.uuid4()), "from": "moderator", "to": agent_id,
                  "subject": "Removed from competition", "body": reason,
                  "timestamp": datetime.now().isoformat(), "status": "sent", "type": "kick"}
        for ws in list(self.active.get(agent_id, set())):
            try:
                await ws.send_json(notice)
                await ws.close(code=4408)
            except Exception:
                pass
        self.active.pop(agent_id, None)


# Instantiate global connection manager
manager = ConnectionManager()


def _nav_board() -> str:
    """The board the competitor is currently 'in', for cross-page navigation."""
    if not COMPETITION_MODE:
        return "local"
    phase = _competition_phase({"draining": DRAINING, "matches": len(email_server.active_games)})
    return "build" if phase == "scheduled" else "competition"


@app.get("/queue_status")
async def get_queue_status():
    """Get current queue status and connected agents."""
    connected_agents = list(manager.active.keys())
    queue_agents = email_server.waiting_queue.copy()
    
    return {
        "queue_length": len(queue_agents),
        "agents_waiting": queue_agents,
        "connected_agents": connected_agents,
        "game_in_progress": email_server.current_game_in_progress,
        "draining": DRAINING,
        "active_games": len(email_server.active_games),
        "num_agents": NUM_AGENTS,                # players needed to form a game
        "pre_game_grace_sec": PRE_GAME_GRACE_SEC,  # countdown after a match forms
        "requeue_cooldown_sec": REQUEUE_COOLDOWN_SEC,  # penalty-free buffer after a game ends
        # Which "world" the competitor is in, so the viewer pages can link to the
        # matching leaderboard/board: "local" (non-competition server, local
        # testing), "build" (competition scheduled, build-week testing board), or
        # "competition" (competition running/ended).
        "nav_board": _nav_board(),
    }


@app.post("/admin/drain")
async def admin_drain(request: Request):
    """Internal-only: toggle draining. When draining, the server forms NO new
    games but lets in-flight games finish - the graceful way to end a competition
    without abandoning a live game. Query: ?on=1 (default) to start draining,
    ?on=0 to resume forming games."""
    _require_internal(request)
    global DRAINING
    DRAINING = request.query_params.get("on", "1").strip().lower() in ("1", "true", "yes", "on")
    print(f"{'🛑 Draining ON - no new games will form' if DRAINING else '▶️  Draining OFF - forming games again'}")
    return {"success": True, "draining": DRAINING,
            "active_games": len(email_server.active_games),
            "queued": len(email_server.waiting_queue)}


@app.websocket("/ws/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    """WebSocket endpoint that streams new messages to *agent_id* in real-time."""
    # Expect JWT via query param ?token=... (simpler for browser/agent clients)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)  # unauthorized
        return

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub != agent_id:
            await websocket.close(code=4403)  # forbidden
            return
    except jwt.InvalidTokenError:
        await websocket.close(code=4401)
        return

    # Competition over: when the server is draining and this agent isn't in a
    # still-running game, refuse the connection so nobody lingers "online" after
    # the last match (agents in an in-flight game may reconnect to finish it).
    if DRAINING and agent_id not in email_server.agent_to_game:
        await websocket.close(code=4503)  # draining / closed
        return

    await manager.connect(agent_id, websocket)
    try:
        while True:
            # Keep the connection alive – we don't expect the agent to send data.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(agent_id, websocket)
        # Remove from queue when disconnecting
        # Note: No JWT needed here - we already authenticated this WebSocket connection
        # and trust the agent_id from the authenticated session
        await email_server.leave_queue(agent_id)
        print(f"🔌 Agent {agent_id} disconnected and removed from queue")


# ---------------------------------------------------------------------------
# Game launching now lives on the EmailServer (process-per-game): see
# _launch_ready_games / _spawn_game / _watch_game. Each game runs in its own
# process for full state isolation, and any number can run concurrently.
# ---------------------------------------------------------------------------

# No startup hooks needed - games start directly from queue


if __name__ == "__main__":
    print("Starting The Email Game Email Server...")
    print("API documentation available at: http://localhost:8000/docs")
    # Quiet HTTP access-log spam unless EMAIL_GAME_DEBUG is set.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info" if DEBUG_LOGS else "warning")