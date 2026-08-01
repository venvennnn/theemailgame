"""Persistent cross-session TrueSkill leaderboard for The Email Game.

The leaderboard is derived entirely from the session result JSON files in
session_results/ - there is no separate ratings database. Ratings are
recomputed by replaying every session in chronological order, so the
session files remain the single source of truth. A lightweight cache keyed
on the set of files + their modification times avoids recomputing on every
request while still picking up new sessions automatically.

Rating model (TrueSkill)
------------------------
Each game has up to NUM_AGENTS players. We rate the whole game as one
multiplayer result: agents are ranked by final score (ties = draws) and fed to
TrueSkill in a single update, which is the correct model for an N-player
free-for-all (no pairwise 1v1 approximation). Every agent's skill is tracked as
a Gaussian: a mean μ and an uncertainty σ. New agents start uncertain (high σ)
so their ratings move fast early and settle as they play.

Agents are ranked by the CONSERVATIVE estimate μ − 3σ ("we're confident they're
at least this good"), so topping the board requires being good AND having played
enough - a few lucky games can't crown an under-proven agent. For display the
conservative value is mapped onto a familiar 1000-anchored scale (a brand-new
agent's prior maps to INITIAL_RATING); the underlying μ/σ are also exposed.

An abandoned game (someone left mid-match) is a no-contest for the survivors
(rating frozen, game not counted) and a forfeit for the leaver (a loss applied
to the leaver only).
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import trueskill

from .tour import TOUR_CSS, TOUR_HTML, TOUR_LINK, tour_js

from src.game.config import PROJECT_ROOT
from src.brand import (BRAND_OVERRIDE, BRAND_FOOTER, BRAND_MARGIN_FX, BRAND_ARENA_CANVAS,
                       BRAND_ARENA_JS, BRAND_TIP_JS, BRAND_WINNER_CSS, BRAND_WINNER_JS)

# Display anchor: a brand-new agent's prior (conservative = μ0 − 3σ0 = 0) maps to
# this, and each unit of conservative skill is worth RATING_SCALE display points.
INITIAL_RATING = 1000.0
RATING_SCALE = 40.0

# Inactivity decay: for every day since an agent last played, inflate its rating
# uncertainty (σ) by this many σ-units, capped at the new-player prior σ0. Because
# the displayed rating is μ − 3σ, sitting idle lowers your shown rating WITHOUT
# corrupting the underlying skill mean (μ); playing again shrinks σ back down. The
# clock freezes at the window end, so a finished board stops decaying.
#
# The rate is END-LOADED (anti-camping): it stays at the gentle BASE rate for most
# of the competition, then RAMPS UP to the steep FINAL rate over the last
# RAMP_HOURS before COMPETITION_END_TIME — so a leader can't camp on a lead at the
# end; everyone must keep their agent active in the final stretch or their rating
# sinks. All default to 0/off: no behavior change until a host opts in. Typical
# live config: BASE 0 (or ~0.05), FINAL ~1.5-2.0, RAMP_HOURS 48 (steep decay only
# in the final two days; a few idle hours at the very end drops you noticeably).
INACTIVITY_DECAY_PER_DAY = float(os.getenv("EMAIL_GAME_INACTIVITY_DECAY_PER_DAY", "0"))
INACTIVITY_DECAY_FINAL_PER_DAY = float(os.getenv("EMAIL_GAME_INACTIVITY_DECAY_FINAL_PER_DAY", "0"))
INACTIVITY_DECAY_RAMP_HOURS = float(os.getenv("EMAIL_GAME_INACTIVITY_DECAY_RAMP_HOURS", "48"))

# Minimum completed games before an agent earns a numeric rank. Agents below this
# are shown as "provisional" (unranked), so a lucky few-game agent can't top the
# prize board and no one can lock a spot on a thin record. Pairs with inactivity
# decay (idle = rating falls) and wave matchmaking (equal games) to remove camping.
# 0 (default) = everyone is ranked; no behavior change.
MIN_GAMES_TO_RANK = int(os.getenv("EMAIL_GAME_MIN_GAMES_TO_RANK", "0"))

# Idle GRACE: the first N minutes of idleness never decay, at any point in the
# competition. Decay exists to kill camping, not to tax the between-games wait
# or a quick edit-and-relaunch - without this, a five-minute fix during the
# final ramp costs real points, which punishes exactly the behavior the event
# wants. 0 (default) = off, current behavior.
DECAY_GRACE_MIN = float(os.getenv("EMAIL_GAME_INACTIVITY_DECAY_GRACE_MIN", "0"))

# Show host-run filler agents on the boards (testing); hidden by default.
SHOW_FILLERS = os.getenv("EMAIL_GAME_SHOW_FILLERS", "").strip().lower() in ("1", "true", "yes", "on")

# TrueSkill environment. draw_probability is tunable (games here draw fairly
# often when scores tie); beta/tau keep the library defaults. Built once.
_TS = trueskill.TrueSkill(
    draw_probability=float(os.getenv("EMAIL_GAME_TS_DRAW_PROB", "0.15")),
)


def _conservative(r: "trueskill.Rating") -> float:
    """TrueSkill conservative skill estimate, μ − 3σ."""
    return r.mu - 3 * r.sigma


def _display_rating(r: "trueskill.Rating") -> int:
    """Map conservative skill onto the 1000-anchored display scale."""
    return round(INITIAL_RATING + _conservative(r) * RATING_SCALE)


def is_filler(agent_id: str) -> bool:
    """True for host-run filler agents that should be hidden from the boards.

    Fillers exist so games can form when too few people are queued. By default
    they are invisible EVERYWHERE a competitor is counted - rows and the live
    online/in-match/queued counts alike - so the header can never contradict the
    table beneath it, and our own bots are never reported as turnout.

    Set EMAIL_GAME_SHOW_FILLERS=1 to show them instead, which is what you want
    while testing: otherwise a bot-filled match reads as "1 player online" from
    inside a four-player game, and there is no way to watch what the fillers are
    doing.
    """
    if SHOW_FILLERS:
        return False
    return agent_id.startswith("house_bot")


def _sigma0() -> float:
    """The new-player prior σ - the ceiling for inactivity-inflated uncertainty."""
    return _TS.create_rating().sigma


def _idle_ref(end: Optional[str]) -> datetime:
    """The 'now' used for inactivity decay. Frozen at the window end once it has
    passed, so a finished/competition board stops decaying; wall-clock while open."""
    now = datetime.now()
    if end:
        try:
            end_dt = datetime.fromisoformat(end)
            if now >= end_dt:
                return end_dt
        except Exception:
            pass
    return now


def _idle_days(last_played: Optional[str], ref: datetime) -> float:
    """Days between an agent's last game and the decay reference time (>= 0)."""
    if not last_played:
        return 0.0
    try:
        lp = datetime.fromisoformat(last_played)
    except Exception:
        return 0.0
    return max(0.0, (ref - lp).total_seconds() / 86400.0)


def _decay_rate(ref: datetime, end: Optional[str]) -> float:
    """Per-idle-day σ inflation in effect at reference time ``ref``.

    Ramps linearly from the gentle BASE rate up to the steep FINAL rate over the
    last RAMP_HOURS before the window end, so decay is mild mid-competition and
    steep at the finish (anti-camping). Outside that window, or with FINAL <= BASE
    or no configured end, the flat BASE rate applies. A frozen board (ref == end)
    sits at the FINAL rate, so the final standings reflect end-of-competition
    activity."""
    base = INACTIVITY_DECAY_PER_DAY
    final = INACTIVITY_DECAY_FINAL_PER_DAY
    if final <= base or not end or INACTIVITY_DECAY_RAMP_HOURS <= 0:
        return base
    try:
        end_dt = datetime.fromisoformat(end)
    except Exception:
        return base
    ramp = timedelta(hours=INACTIVITY_DECAY_RAMP_HOURS)
    frac = (ref - (end_dt - ramp)).total_seconds() / ramp.total_seconds()
    frac = max(0.0, min(1.0, frac))
    return base + (final - base) * frac


def _decayed_rating(r: "trueskill.Rating", idle_days: float, rate: float) -> Tuple[int, float]:
    """(display_rating, σ_effective) after inactivity decay at the given per-day
    ``rate``. When decay is off or the agent is current, this is just the normal
    rating and its real σ."""
    if rate <= 0 or idle_days <= 0:
        return _display_rating(r), r.sigma
    sigma_eff = min(_sigma0(), r.sigma + idle_days * rate)
    eff = _TS.create_rating(mu=r.mu, sigma=sigma_eff)
    return _display_rating(eff), sigma_eff

# Simple in-memory cache: (signature) -> computed entries
_cache: Dict[str, Tuple] = {}   # window -> (signature, entries)


def competition_start() -> Optional[str]:
    """Return the competition start cutoff (ISO-8601) if configured, else None.

    When COMPETITION_START_TIME is set, only sessions that started at or after
    that timestamp count toward the leaderboard. This gives a clean board for a
    competition without deleting any session history.
    """
    val = os.environ.get("COMPETITION_START_TIME", "").strip()
    return val or None


def competition_end() -> Optional[str]:
    """Return the competition end cutoff (ISO-8601) if configured, else None.

    When COMPETITION_END_TIME is set, sessions that started at or after that
    timestamp do not count toward the leaderboard. Mirrors competition_start();
    together they freeze the board to a fixed window without deleting history.
    """
    val = os.environ.get("COMPETITION_END_TIME", "").strip()
    return val or None


def _results_dir() -> Path:
    return PROJECT_ROOT / "session_results"


def _session_files(results_dir: Path) -> List[Path]:
    return sorted(results_dir.glob("session_arena_*.json"))


def _signature(files: List[Path]) -> Tuple:
    """A signature that changes whenever sessions are added or modified."""
    return tuple((f.name, f.stat().st_mtime) for f in files)


def _load_sessions(results_dir: Path, cutoff: Optional[str] = None,
                   end: Optional[str] = None) -> List[Dict]:
    """Load valid session dicts sorted chronologically by start_time.

    Sessions that started before `cutoff` or at/after `end` (both ISO-8601) are
    skipped, so the board reflects only the competition window.
    """
    sessions = []
    for fp in _session_files(results_dir):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not d.get("cumulative_scores"):
            continue
        st = d.get("start_time") or ""
        if cutoff and st < cutoff:
            continue
        if end and st >= end:
            continue
        sessions.append(d)
    # start_time is ISO-8601 → lexicographic sort == chronological.
    # Fall back to session_id so ordering is stable if start_time missing.
    sessions.sort(key=lambda d: (d.get("start_time") or "", d.get("session_id") or ""))
    return sessions


def _blank_stats() -> Dict:
    return {
        "games": 0, "wins": 0, "total_score": 0, "rounds": 0,
        "sub": 0, "sig": 0, "pen": 0,
        # collection accumulators (authorized signatures collected vs requested;
        # non-abandoned games only). Attack/defense metrics were removed - in
        # practice they were near-zero for everyone (the game discourages
        # unauthorized signing) and relied on inference, so they added clutter,
        # not signal.
        "authcoll": 0, "req": 0,
    }


def _window_bounds(window: str) -> Tuple[Optional[str], Optional[str]]:
    """(cutoff, end) for a board window.

    "competition" counts games inside [COMPETITION_START_TIME, COMPETITION_END_TIME).
    "build" is the build-week / testing board: everything BEFORE the competition
    starts (end = COMPETITION_START_TIME). With no start configured there is no
    competition yet, so every game is build-week."""
    if window == "build":
        return (None, competition_start())
    return (competition_start(), competition_end())


def compute_leaderboard(results_dir: Path = None, window: str = "competition",
                        in_match: Optional[set] = None) -> List[Dict]:
    """Replay all sessions in order and return ranked leaderboard entries.

    ``window`` selects the board: "competition" (official) or "build" (build-week
    testing board, games before the competition starts). ``in_match`` is the set
    of agents currently INSIDE a running game: they are actively playing, so
    inactivity decay does not apply to them for the duration.

    Each entry: rank, agent_id, elo, games, wins, win_rate, total_score,
    avg_score_per_round, penalties.
    """
    results_dir = results_dir or _results_dir()
    if not results_dir.exists():
        return []

    cutoff, end = _window_bounds(window)
    files = _session_files(results_dir)
    # Include the results dir in the signature: two different dirs can hold
    # files with identical names/mtimes (e.g. in tests), and without the path
    # they would collide in the cache.
    sig = (window, str(results_dir), cutoff, end) + _signature(files)
    # Inactivity decay depends on wall-clock, so the file-mtime signature alone
    # would serve stale (un-decayed) values. Add an hourly bucket of the decay
    # reference time; once the window ends the ref is frozen, so the cache is
    # stable again (post-competition boards don't churn).
    if INACTIVITY_DECAY_PER_DAY > 0 or INACTIVITY_DECAY_FINAL_PER_DAY > 0:
        sig = sig + (int(_idle_ref(end).timestamp() // 3600), frozenset(in_match or ()))
    cached = _cache.get(window)
    if cached and cached[0] == sig:
        return cached[1]

    sessions = _load_sessions(results_dir, cutoff, end)

    ratings: Dict[str, "trueskill.Rating"] = {}
    stats: Dict[str, Dict] = {}
    last_played: Dict[str, str] = {}   # agent_id -> most recent game time (for decay)

    for d in sessions:
        scores: Dict[str, int] = d["cumulative_scores"]
        agents = list(scores.keys())
        if len(agents) < 2:
            continue
        for a in agents:
            ratings.setdefault(a, _TS.create_rating())

        # Record activity: the most recent game each agent appeared in (sessions
        # are chronological, so a later game overwrites with a later time). Used
        # only for inactivity decay at display time.
        seen_at = d.get("end_time") or d.get("start_time") or ""
        if seen_at:
            for a in agents:
                last_played[a] = seen_at

        # Agents who left mid-match. An abandoned game is a no-contest for the
        # survivors (their rating is frozen, the game isn't counted for them) and
        # a forfeit for the leavers (a loss applied to the leaver only).
        departed = set(d.get("departed", [])) & set(agents)
        abandoned = bool(departed)

        if abandoned:
            # Forfeit each leaver against the field, leaving survivors untouched:
            # rate the leaver vs a single synthetic opponent at the survivors'
            # average rating, with the leaver ranked last, and keep only the
            # leaver's updated rating.
            survivors = [a for a in agents if a not in departed]
            if survivors:
                opp_mu = sum(ratings[s].mu for s in survivors) / len(survivors)
                opp_sigma = sum(ratings[s].sigma for s in survivors) / len(survivors)
                field = _TS.create_rating(mu=opp_mu, sigma=opp_sigma)
                for leaver in departed:
                    before = ratings[leaver]
                    new, _ = _TS.rate(
                        [(before,), (field,)], ranks=[1, 0])  # leaver loses
                    # Apply only the skill (mu) penalty; keep the prior sigma so a
                    # forfeit never *raises* the leaver's conservative rating by
                    # reducing uncertainty (quitting must not look like progress).
                    ratings[leaver] = _TS.create_rating(mu=new[0].mu, sigma=before.sigma)
        else:
            # Rate the whole game as one multiplayer result. Lower rank = better;
            # higher score = better, ties share a rank (a draw).
            groups = [(ratings[a],) for a in agents]
            ranks = [sum(1 for b in agents if scores[b] > scores[a]) for a in agents]
            for a, g in zip(agents, _TS.rate(groups, ranks=ranks)):
                ratings[a] = g[0]

            # Tie fairness: TrueSkill gives agents in the "middle" of a draw a
            # slightly lower sigma than those at the edges (more adjacent
            # comparisons), so agents with the SAME score would otherwise get
            # different displayed ratings purely from input order. Equalize sigma
            # within each tied group (same score) so a true tie ranks identically.
            # mu is left as TrueSkill computed it (a draw between unequally-rated
            # agents should still move their means toward each other).
            by_score: Dict[int, List[str]] = {}
            for a in agents:
                by_score.setdefault(scores[a], []).append(a)
            for tied in by_score.values():
                if len(tied) > 1:
                    avg_sigma = sum(ratings[a].sigma for a in tied) / len(tied)
                    for a in tied:
                        ratings[a] = _TS.create_rating(mu=ratings[a].mu, sigma=avg_sigma)

        # Aggregate stats. An abandoned game is not a real contest, so it counts
        # toward NO agent's games/win%/avg/round stats - neither the survivors
        # (no-contest) nor the leaver. The leaver's only consequence is the Elo
        # forfeit applied above (update_set = departed), which persists in their
        # rating for when they next complete a real game.
        num_rounds = d.get("total_rounds") or len(d.get("rounds", []))
        if abandoned:
            counted = set()
            sole_winner = None
        else:
            counted = set(agents)
            top = max(scores.values())
            winners = [a for a in agents if scores[a] == top]
            sole_winner = winners[0] if len(winners) == 1 else None

        for a in counted:
            st = stats.setdefault(a, _blank_stats())
            st["games"] += 1
            st["total_score"] += scores[a]
            st["rounds"] += num_rounds
            if a == sole_winner:
                st["wins"] += 1

        for r in d.get("rounds", []):
            perf = r.get("agent_performance", {})
            req = r.get("request_lists", {})
            events = r.get("signature_events")
            for a, p in perf.items():
                if a not in counted or a not in stats:
                    continue
                stats[a]["sub"] += p.get("submission_points", 0)
                stats[a]["sig"] += p.get("signing_points", 0)
                stats[a]["pen"] += p.get("unauthorized_signing_penalties", 0)
                # Collection rate: of the signatures you were assigned to request,
                # how many authorized ones you actually collected. Computed from
                # the per-signature event log (ground truth); denominator is the
                # recorded request assignment. Abandoned games and rounds without
                # an event log are skipped. (Attack/defense metrics were removed -
                # near-zero signal in practice and reliant on inference.)
                if abandoned or events is None:
                    continue
                stats[a]["req"] += len(req.get(a, []))
                stats[a]["authcoll"] += sum(1 for e in events
                                            if e.get("submitter") == a and e.get("authorized"))

    ref = _idle_ref(end)   # decay reference clock (frozen once the window ends)
    rate = _decay_rate(ref, end)   # end-loaded: ramps up near the window end
    entries = []
    for a, st in stats.items():
        if is_filler(a):
            continue   # filler/house bots never appear on the leaderboards
        r = ratings.get(a) or _TS.create_rating()
        # An agent inside a running match is the opposite of idle - decay
        # pauses the moment a game seats it. Outside a match, the first
        # GRACE minutes of idleness are free (see DECAY_GRACE_MIN).
        idle = 0.0 if (in_match and a in in_match) else _idle_days(last_played.get(a), ref)
        idle = max(0.0, idle - DECAY_GRACE_MIN / 1440.0)
        elo, sigma_eff = _decayed_rating(r, idle, rate)
        entries.append({
            "agent_id": a,
            # "elo" is kept as the headline rating key for compatibility, but it
            # now carries the TrueSkill conservative score on a 1000-anchored
            # scale, after any inactivity decay. mu/sigma/conservative expose the
            # underlying (un-decayed) skill estimate.
            "elo": elo,
            "mu": round(r.mu, 2),
            "sigma": round(r.sigma, 2),
            "sigma_effective": round(sigma_eff, 2),
            "idle_days": round(idle, 2),
            # Display points currently lost to inactivity decay (0 when current).
            "decay": max(0, _display_rating(r) - elo),
            "conservative": round(_conservative(r), 2),
            "games": st["games"],
            "wins": st["wins"],
            "win_rate": round(st["wins"] / st["games"], 3) if st["games"] else 0.0,
            "total_score": st["total_score"],
            "avg_score_per_round": round(st["total_score"] / st["rounds"], 2) if st["rounds"] else 0.0,
            "penalties": st["pen"],
            # Collection: authorized signatures collected / requested (None until
            # the agent has had a request opportunity in an event-logged game).
            "collection_rate": (round(st["authcoll"] / st["req"], 3)
                                if st["req"] else None),
            "authorized_collected": st["authcoll"],
            "total_requests": st["req"],
            # Below the game threshold -> unranked (shown separately, no numeric rank).
            "provisional": st["games"] < MIN_GAMES_TO_RANK,
        })

    # Qualified agents rank first (by rating); provisional agents sort to the bottom
    # and get no numeric rank. With MIN_GAMES_TO_RANK=0 nothing is provisional, so
    # this is the original ordering (ranks 1..n).
    entries.sort(key=lambda e: (e["provisional"], -e["elo"], -e["games"]))
    rank = 0
    for e in entries:
        if e["provisional"]:
            e["rank"] = None
        else:
            rank += 1
            e["rank"] = rank

    _cache[window] = (sig, entries)
    return entries


# ---------------------------------------------------------------------------
# Matchmaking support: expose the TrueSkill skill estimates and match quality so
# the server can form balanced games (see _select_matched_group in email_server).
# ---------------------------------------------------------------------------

def default_rating() -> "trueskill.Rating":
    """The prior for an agent that hasn't played yet (TrueSkill μ0, σ0)."""
    return _TS.create_rating()


def current_skills(window: str = "competition", results_dir: Path = None) -> Dict[str, "trueskill.Rating"]:
    """Map agent_id -> TrueSkill Rating (μ, σ) from the current board."""
    return {
        e["agent_id"]: _TS.create_rating(mu=e["mu"], sigma=e["sigma"])
        for e in compute_leaderboard(results_dir, window=window)
    }


def match_quality(ratings: List["trueskill.Rating"]) -> float:
    """TrueSkill match quality (≈ probability of a draw) for a free-for-all of
    1-player teams. Higher = more balanced/competitive. 1.0 for <2 players."""
    if len(ratings) < 2:
        return 1.0
    return _TS.quality([(r,) for r in ratings])


# Inline SVG icons (Lucide-style, stroke = currentColor) used in place of emoji
# on the public leaderboard page. Self-contained so the page needs no asset host.
_ICON_TROPHY = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/>'
    '<path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/>'
    '<path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/>'
    '<path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/>'
    '<path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>'
)
_ICON_CALENDAR = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="18" rx="2"/>'
    '<path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/></svg>'
)
_ICON_MEDAL = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M7.21 15 2.66 7.14a2 2 0 0 1 .13-2.2L4.4 2.8A2 2 0 0 1 6 2h12a2 2 0 0 1 '
    '1.6.8l1.6 2.14a2 2 0 0 1 .14 2.2L16.79 15"/>'
    '<path d="M11 12 5.12 2.2"/><path d="m13 12 5.88-9.8"/><path d="M8 7h8"/>'
    '<circle cx="12" cy="17" r="5"/>'
    '<text x="12" y="17" text-anchor="middle" dominant-baseline="central" '
    'font-size="6.5" font-weight="700" stroke="none" fill="currentColor" '
    'font-family="JetBrains Mono, ui-monospace, monospace">{n}</text></svg>'
)
# Metallic tints for the top three ranks (gold / silver / bronze).
_MEDAL_COLORS = {1: "#f5b301", 2: "#9aa3b0", 3: "#cd7f32"}


def _fmt_rate(x) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _decay_badge(e: Dict) -> str:
    """Bare animated marker on a rating currently decaying from inactivity - no
    cumulative number; the visible story is the rating itself dripping down."""
    if (e.get("decay") or 0) < 1:
        return ""
    return ('<span class="decaybadge" data-tip="This rating is decaying from '
            'inactivity. It stops the moment the agent plays; a finished game '
            'restores it.">&#9662;</span>')


def _rank_cell(rank) -> str:
    """Top three ranks get a tinted medal icon; the rest get a plain number.

    Both are wrapped in the same fixed-size centered badge so the numbers line
    up vertically and horizontally with the medals above them. A provisional
    (unranked) agent has rank None and shows a dash.
    """
    if rank is None:
        return ('<span class="rank-badge" data-tip="Provisional: not enough games '
                'to rank yet">&ndash;</span>')
    color = _MEDAL_COLORS.get(rank)
    if color:
        return (f'<span class="rank-badge medal" style="color:{color}" '
                f'aria-label="Rank {rank}">{_ICON_MEDAL.format(n=rank)}</span>')
    return f'<span class="rank-badge">{rank}</span>'



# Guided tour: shared widget, page-specific steps. Four stops - the header
# links explain themselves, so they do not get one.
def _local_tour_steps():
    """The board tour with an opening step that tells the truth about the local
    sandbox: private, unscored, this machine only - the rest is unchanged."""
    return [{"title": "Your private practice board",
             "body": "Games on this page ran on YOUR machine only - nothing here counts "
                     "toward the hosted competition or is visible to anyone else. "
                     "It works exactly like the real board, so what you learn here carries."}
            ] + _TOUR_STEPS[1:]


_TOUR_STEPS = [
    # No target: which board you are on is a fact about the page as a whole, and
    # the heading already says it - pointing at the heading would add nothing.
    {"title": "One board, every game counts",
     "body": "This is the scored competition. There is no practice phase: standings "
             "count from the very first game of the day, so this table is live from "
             "11:00 AM PT on."},
    {"sel": ".status-banner", "title": "Where things stand",
     "body": "Whether scoring is coming, running or finished, and the exact window. "
             "One rule to remember: <b>idle ratings decay</b> - gently all day, steeply "
             "in the final two hours - and playing a game restores them."},
    {"sel": ".lb thead", "title": "The columns",
     "body": "<b>Rating</b> sets your rank. <b>Collection</b> is the share of your assigned "
             "signatures you actually got. <b>Penalties</b> are unauthorized signings. "
             "Hover any heading for detail."},
    # Two variants of the same step, because the buttons on offer DIFFER by
    # whether the viewer has opened their agent's link. Only one of these
    # targets is ever on the page - the agent card removes the hint-line nav
    # when it renders - so the tour keeps whichever exists and drops the other.
    # A single step described My stats and Jump to my row to people who had
    # neither.
    {"sel": ".youcard .ynav", "title": "Your agent's pages",
     "body": "<b>Watch live</b> follows your current match. <b>Match history</b> replays "
             "finished ones. <b>My stats</b> is your private scoring breakdown - click any "
             "game there to read it. <b>Jump to my row</b> appears once you are ranked."},
    {"sel": ".lb-nav", "title": "Watching and replaying",
     "body": "<b>Watch live</b> follows a match as it happens; <b>Match history</b> replays "
             "finished ones. Start your agent and open the link it prints in your terminal - "
             "that link identifies you, and unlocks your own stats here too."},
]



def render_leaderboard_html(entries: List[Dict], live: Dict = None,
                            board: str = "competition",
                            online: set = None, ingame: set = None) -> str:
    """Render the leaderboard as a standalone HTML page.

    ``live`` (optional) carries point-in-time arena activity for the header bar:
    ``{"players": int, "matches": int, "in_game": int, "queued": int}``.
    ``board`` is "competition" (official) or "build" (build-week testing board).
    """
    online = online or set()
    ingame = ingame or set()
    ended = board == "competition" and _competition_phase(live or {}) == "ended"
    # Auto-refresh every 120s, but as a JS timer rather than <meta refresh> so it
    # can WAIT while a guided tour is open. A hard meta-refresh would reload the
    # page out from under someone mid-tour, which reads as the tour resetting
    # itself. Once the tour closes, the deferred reload happens on the next check.
    refresh_meta = "" if ended else (
        '<script>(function(){'
        'function due(){'
        'if(document.documentElement.classList.contains("tr-on")){setTimeout(due,4000);return;}'
        'location.reload();}'
        'setTimeout(due,120000);})();</script>'
    )
    # Celebratory winner modal, only once the competition has ended with a winner.
    winner_modal = ""
    _top3 = [e for e in entries if e.get("rank") in (1, 2, 3)]
    _top3.sort(key=lambda e: e["rank"])
    if ended and _top3:
        trophy = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
                  'stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/>'
                  '<path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/>'
                  '<path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/>'
                  '<path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/>'
                  '<path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>')

        def _pod(e):
            n = e["rank"]
            medal = {1: "gold", 2: "silver", 3: "bronze"}[n]
            place = {1: "Champion", 2: "2nd place", 3: "3rd place"}[n]
            return (f'<div class="pod p{n}">'
                    f'<div class="pod-medal {medal}">{_ICON_MEDAL.format(n=n)}</div>'
                    f'<div class="pod-place">{place}</div>'
                    f'<div class="pod-name">{_escape(e["agent_id"])}</div>'
                    f'<div class="pod-stats">{e["elo"]} &middot; {e["wins"]}W / {e["games"]}G</div>'
                    f'<div class="pod-bar"><span>{n}</span></div>'
                    f'</div>')

        # Podium order: 2 | 1 | 3 (missing places just leave a gap).
        by_rank = {e["rank"]: e for e in _top3}
        cols = "".join(_pod(by_rank[r]) for r in (2, 1, 3) if r in by_rank)
        winner_modal = (
            '<div class="win-modal" id="winModal" role="dialog" aria-modal="true" aria-label="Final standings">'
            '<canvas class="win-confetti" id="winConfetti" aria-hidden="true"></canvas>'
            '<div class="win-card">'
            f'<div class="win-head"><span class="win-trophy">{trophy}</span>'
            '<div><div class="win-eyebrow">The Email Game &middot; Final standings</div>'
            '<div class="win-sub">Every game counted. These three held the board.</div></div></div>'
            f'<div class="podium">{cols}</div>'
            '<div class="win-note">Top 3: submit your agent code within the hour to claim your '
            'prize &mdash; <code>python scripts/submit_code.py --name &lt;your-agent&gt;</code> '
            '(details in the README).</div>'
            '<button class="win-close" id="winClose">View the full board</button>'
            '</div></div>'
        )
    is_build = board == "build"
    is_local = board == "local"
    if is_local:
        page_title = "The Email Game - Local Testing Leaderboard"
        heading = f"{_ICON_TROPHY} Local Testing Leaderboard"
        subtitle = ""   # parity with the official board: the tour explains the sandbox
        notice = ""
    elif is_build:
        page_title = "The Email Game - Testing Leaderboard"
        heading = f"{_ICON_TROPHY} Testing Leaderboard"
        subtitle = ""
        notice = '<a class="navbtn i-board" href="/leaderboard">Official leaderboard</a>'
    else:
        page_title = "The Email Game - Competition Leaderboard"
        heading = f"{_ICON_TROPHY} Competition Leaderboard"
        subtitle = ""   # the tour + column tooltips explain the maths; no explainer line
        notice = ''   # the practice board is retired; one board, every game counts
    if entries:
        rows = []
        for e in entries:
            rank_label = _rank_cell(e["rank"])
            aid = e['agent_id']
            rcls = f"rank-{e['rank']}" if e['rank'] in (1, 2, 3) else ""
            if aid in ingame:
                dot = '<span class="statusdot ingame" data-tip="In a match right now."></span>'
            elif aid in online:
                dot = '<span class="statusdot online" data-tip="Connected and waiting for a match."></span>'
            else:
                dot = '<span class="statusdot off" data-tip="Not connected right now."></span>'
            rows.append(f"""
            <tr class="{rcls}">
              <td class="rank">{rank_label}</td>
              <td class="agent" data-agent="{_escape(aid)}"><span class="arow">{dot}<span class="aname">{_escape(aid)}</span></span></td>
              <td class="elo" data-a="{_escape(aid)}" data-elo="{e['elo']}" data-decaying="{1 if (e.get('decay') or 0) >= 1 else 0}">{_decay_badge(e)}<span class="elo-n">{e['elo']}</span></td>
              <td>{e['games']}</td>
              <td>{e['wins']}</td>
              <td>{e['win_rate']*100:.0f}%</td>
              <td>{e['avg_score_per_round']:.2f}</td>
              <td>{_fmt_rate(e.get('collection_rate'))}</td>
              <td>{e['penalties']}</td>
            </tr>""")
        table_body = "".join(rows)
    else:
        table_body = """
            <tr><td colspan="9" class="empty">No games played yet. Run a session to populate the leaderboard.</td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>{page_title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      --accent-blue:#2f54ff; --ink:#0a0a0b; --ink-2:#3c3f45; --muted:#6b7078;
      --line:#e7e9ec; --line-2:#d9dce0; --bg-soft:#fafafa; --green:#0a7d44; --green-bg:#e7f5ee;
      --radius:14px; color-scheme: light;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-soft); color: var(--ink); margin: 0; padding: 40px 20px;
      -webkit-font-smoothing: antialiased;
    }}
    .wrap {{ max-width: 920px; margin: 0 auto; }}
    .brandbar {{ display:flex; align-items:center; gap:8px; margin:0 0 14px;
      font-family:"Space Grotesk","Inter",sans-serif; font-weight:700; font-size:1.05rem; color: var(--ink); }}
    .brandbar svg {{ height:17px; width:auto; display:block; }}
    h1 {{ font-family:"Space Grotesk","Inter",sans-serif; font-size: 1.85rem; font-weight:600;
      letter-spacing:-.02em; margin: 0 0 6px; display: flex; align-items: center; gap: .5rem; }}
    .icon {{ display: inline-block; width: 1em; height: 1em; vertical-align: -0.125em;
      stroke: currentColor; fill: none; }}
    h1 .icon {{ color: var(--accent-blue); }}
    /* The decay rule, on the board itself - amber note; urgent red in the
       final ramp. The row badge bobs downward so a decaying rating is
       unmissable at a glance. */
    .decay-note {{ margin: .5rem 0 .9rem; padding: .55rem .8rem; border-radius: 10px;
      font-size: .86rem; background: var(--amber-bg, #fff7e6);
      border: 1px solid var(--amber-line, #f0d9a8); color: var(--amber-ink, #7a5c00); }}
    .decay-note.urgent {{ background: var(--red-bg, #fdeceb);
      border-color: var(--red-line, #f3c0bb); color: var(--red-ink, #a52a24); }}
    td.elo {{ position: relative; padding-left: 2.6rem; white-space: nowrap; }}
    /* One drip per observed decrement: the points just lost fall away from
       the rating and fade - the decay is WATCHED, not totted up. */
    .elo-drip {{ position: absolute; left: .35rem; top: 50%; line-height: 1;
      font-size: .72rem; font-weight: 700; color: var(--red-ink, #c5221f); pointer-events: none;
      animation: eloDrip 1.6s ease-out forwards; }}
    @keyframes eloDrip {{ from {{ opacity: 1; transform: translateY(-50%); }}
      to {{ opacity: 0; transform: translateY(55%); }} }}
    @media (prefers-reduced-motion: reduce) {{ .elo-drip {{ animation: none; opacity: 0; }} }}
    /* The decay note is the card's own second row - full width, after
       everything on row one. */
    .youcard {{ flex-wrap: wrap; }}
    .youcard .ydecay {{ flex-basis: 100%; order: 99; color: var(--red-ink, #c5221f);
      font-size: .8rem; font-weight: 600; margin-top: .15rem; }}
    .youcard .ygrace {{ flex-basis: 100%; order: 99; color: var(--amber-ink, #7a5c00);
      font-size: .8rem; font-weight: 600; margin-top: .15rem;
      font-variant-numeric: tabular-nums; }}
    .youcard .ybudget {{ font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: .78rem; cursor: help; white-space: nowrap; }}
    .youcard .ystat {{ white-space: nowrap; }}
    .youcard .ybudget.low {{ color: var(--amber-ink, #7a5c00); font-weight: 700; }}
    .youcard .ybudget.bad {{ color: var(--red-ink, #c5221f); font-weight: 700; }}
    .decaybadge {{ display: inline-block; margin-right: .3rem; font-size: .74rem;
      font-weight: 700; color: var(--red-ink, #c5221f); cursor: help;
      vertical-align: middle; position: relative; top: -1px;
      animation: decaybob 1.6s ease-in-out infinite; }}

    @keyframes decaybob {{ 0%, 100% {{ transform: translateY(0); }}
      50% {{ transform: translateY(3px); }} }}
    @media (prefers-reduced-motion: reduce) {{ .decaybadge {{ animation: none; }} }}
    .rank-badge {{ display: inline-flex; align-items: center; justify-content: center;
      width: 1.7em; height: 1.7em; line-height: 1; }}
    .rank-badge .icon {{ width: 1.3em; height: 1.3em; }}
    .sub {{ color: var(--muted); margin: 0 0 12px; font-size: .95rem; line-height:1.5; max-width:74ch; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
      border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden;
      box-shadow: 0 1px 2px rgba(10,10,11,.04), 0 8px 30px rgba(10,10,11,.05); }}
    th, td {{ padding: .8rem 1rem; text-align: center; border-bottom: 1px solid var(--line);
      font-variant-numeric: tabular-nums; vertical-align: middle; }}
    /* Agent column is the flexible one: it absorbs slack (shows the full name when
       there is room) and truncates only as much as needed so the table never grows
       wider than its container. Numeric columns keep their natural auto widths. */
    td.agent {{ position: relative; max-width: 0; width: 100%; }}
    .arow {{ display: flex; align-items: center; min-width: 0; }}
    .arow .statusdot, .arow .you-badge {{ flex: none; }}
    .aname {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    th {{ background: var(--bg-soft); color: var(--muted); font-weight: 600;
      font-size: .73rem; text-transform: uppercase; letter-spacing: .08em; }}
    th.agent, td.agent {{ text-align: left; }}
    th.rank, td.rank {{ text-align: center; }}
    td.agent {{ font-weight: 600; font-family:"Space Grotesk","Inter",sans-serif; }}
    /* Ink, not link-blue: blue means interactive (or you) on this site,
       and ratings are neither. */
    td.elo {{ font-weight: 700; color: var(--ink); font-family:"JetBrains Mono",monospace; }}
    td.rank {{ font-size: 1.05rem; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: #fcfcfd; }}
    td.empty {{ text-align: center; color: var(--muted); padding: 2.5rem; }}
    .legend {{ color: var(--muted); font-size: .84rem; margin-top: 1.25rem; line-height: 1.7; max-width:80ch; }}
    code {{ background: var(--bg-soft); border:1px solid var(--line); padding: .1rem .4rem; border-radius: 5px;
      font-family:"JetBrains Mono",monospace; font-size:.85em; }}
    .live {{ display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; margin: 0 0 .7rem; }}
    .chip {{ background: #fff; border: 1px solid var(--line-2); border-radius: 999px;
      padding: .4rem .9rem; font-size: .88rem; font-weight: 600; color: var(--ink-2); }}
    .chip .dot {{ display: inline-block; width: .55rem; height: .55rem;
      border-radius: 50%; background: var(--green); margin-right: .45rem; vertical-align: middle; }}
    .live-detail {{ color: var(--muted); font-size: .82rem; }}
    .you-badge {{ display:inline-block; margin-left:.5rem; vertical-align:middle;
      font-size:.6rem; font-weight:800; letter-spacing:.06em; color:#fff;
      background:var(--accent-blue); padding:.1rem .4rem; border-radius:5px; }}
    tr.you td.agent .aname {{ font-weight: 700; }}
    a, a.plain {{ color: var(--accent-blue); }}
    .xlink {{ margin: 0 0 1.25rem; font-size: .9rem; }}
    .testbanner {{ background: var(--amber-bg, #fff8e6); border: 1px solid var(--amber-line, #f0dca8); color: var(--amber-ink, #7a5c00);
      border-radius: 10px; padding: .8rem 1.1rem; margin: 0 0 1.25rem; font-size: .9rem; line-height: 1.55; }}
    .youcard {{ position: sticky; top: 0; z-index: 10; background: #eef1ff;
      border: 1px solid var(--blue-line, #ccd6ff); border-radius: 10px; padding: .8rem 1.1rem;
      margin: 0 0 1rem; display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
      box-shadow: 0 8px 24px rgba(47,84,255,.08); font-size: .92rem; }}
    .youcard .ylabel {{ font-weight: 700; color: var(--accent-blue); }}
    .youcard {{ flex-wrap: wrap; }}
    .youcard .ystat {{ color: var(--ink-2); }}
    .youcard .yname {{ min-width: 0; max-width: 16rem; font-weight: 700;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .youcard .ystat strong {{ color: var(--accent-blue); }}
    .youcard .ynav {{ display: flex; align-items: center; gap: .2rem .7rem; flex: none; flex-wrap: nowrap; }}
    .youcard a {{ margin-left: 0; font-size: .82rem; font-weight: 600;
      color: var(--accent-blue); text-decoration: none; white-space: nowrap;
      display: inline-flex; align-items: center; gap: .3rem; }}
    .youcard a svg {{ width: 14px; height: 14px; }}
    .youcard a:hover {{ text-decoration: underline; }}
    .youcard .spacer {{ flex: 1; }}
{TOUR_CSS}
  </style>
{BRAND_OVERRIDE}
{BRAND_WINNER_CSS}
</head>
<body>
  <div class="wrap">
    <div class="arena-head">
    {BRAND_ARENA_CANVAS}
    <a class="brandbar" href="https://theemailgame.com"><svg viewBox="0 4.5 24 16" fill="none" stroke="#2f54ff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6H18C19.6 6 20.4 7.1 20.9 8.7 21.7 11.2 22.6 15 21 17.6 20 19.3 17.7 19.1 16.7 17.2 15.7 15.3 14.4 14.7 12 14.7 9.6 14.7 8.3 15.3 7.3 17.2 6.3 19.1 4 19.3 3 17.6 1.4 15 2.3 11.2 3.1 8.7 3.6 7.1 4.4 6 6 6Z"/><path d="M5 6.6 12 12.1 19 6.6"/></svg> The Email Game</a>
    <h1>{heading}</h1>
    <p class="sub">{subtitle}</p>
    </div>
    <div class="lb-meta">
      <span class="lb-hint"><span class="lb-nav"><a class="navbtn i-eye" href="/watch">Watch live</a><a class="navbtn i-hist" href="/history">Match history</a></span><span class="tip">Hover a column heading for details</span></span>
      <span class="lb-spacer"></span>
      <span class="dotkey"><span class="dk"><span class="statusdot ingame"></span>in a match</span><span class="dk"><span class="statusdot online"></span>online</span></span>
      {TOUR_LINK}
      {notice}
    </div>
    <div id="youcard" class="youcard" style="display:none"></div>
    <div id="live-root">
    {_banner_html(live, entries, board=board)}
    {_decay_notice_html(board)}
    <table class="lb">
      <thead>
        <tr>
          <th class="rank" data-tip="Rank on the board, ordered by Rating."><span class="rank-badge">#</span></th>
          <th class="agent"><span class="statusdot off" data-tip="Not connected right now."></span>Agent</th>
          <th data-tip="Conservative skill estimate (TrueSkill, mu - 3 sigma): you must be good and have played enough to rank high, so a couple of lucky games can't top the board. Rank is by Rating only; wins don't affect it. Idle ratings decay (&#9662;) - gently all day, steeply in the final two hours. Time in a match never decays, and short breaks are free: a ~10-minute grace that tapers away over the final ramp. Playing a game restores what idling cost.">Rating</th>
          <th data-tip="How many games (not rounds) you have played.">Games</th>
          <th data-tip="Games where you finished alone in first by total score. A tie for the top counts for no one, so wins can be fewer than games.">Wins</th>
          <th data-tip="Wins divided by games.">Win&nbsp;%</th>
          <th data-tip="Your lifetime points per round.">Avg/Round</th>
          <th data-tip="Share of your assigned signature requests that you collected and submitted.">Collection</th>
          <th data-tip="Unauthorized signatures you gave (-1 each).">Penalties</th>
        </tr>
      </thead>
      <tbody>{table_body}
      </tbody>
    </table>
    </div>
  </div>
  {winner_modal}
{TOUR_HTML}
  <script>{tour_js(_local_tour_steps() if is_local else _TOUR_STEPS, "board")}</script>
  {BRAND_FOOTER}
  {BRAND_MARGIN_FX}
  {BRAND_ARENA_JS}
  {BRAND_TIP_JS}
  {BRAND_WINNER_JS}
  <script>
    var LB_BOARD = "{board}";   // which board this page is (competition | build)
    // Remember the board the viewer is on, so watch/history/stats pages can send
    // them back to THIS board (not always the competition one).
    try {{ localStorage.setItem("emailgame_board", LB_BOARD); }} catch (e) {{}}
    // Shared inline icons (Lucide, ISC) for the own-row shortcuts and the You card.
    var IC_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
    var IC_HIST = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l3 2"/></svg>';
    var IC_STATS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M8 17v-5"/><path d="M13 17V8"/><path d="M18 17v-3"/></svg>';
    var IC_JUMP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17V3"/><path d="m6 11 6 6 6-6"/><path d="M19 21H5"/></svg>';
    // Show watch/history/stats shortcuts only on the viewer's OWN row. Identity
    // comes from the watch page, which saves {{agent, token}} to localStorage when
    // opened. The leaderboard never learns anyone else's identity, so you can only
    // ever get a one-click link to your own match/stats (no peeking at opponents).
    function injectRowLinks() {{
      var me;
      try {{ me = JSON.parse(localStorage.getItem("emailgame_watch") || "null"); }} catch (e) {{}}
      if (!me || !me.agent) return;
      // Competition/build boards require a token (tokened identity). The local
      // board needs no login, so identify "you" by agent alone there - and never
      // let a stale tokened identity from another board mark "you" here.
      if (LB_BOARD !== "local" && !me.token) return;
      var tq = me.token ? ("&token=" + encodeURIComponent(me.token)) : "";
      var cell = document.querySelector('td.agent[data-agent="' + (window.CSS && CSS.escape
        ? CSS.escape(me.agent) : me.agent.replace(/"/g, '\\\\"')) + '"]');
      if (cell && !cell.querySelector(".you-badge")) {{
        cell.closest("tr").classList.add("you");
        var badge = document.createElement("span");
        badge.className = "you-badge"; badge.textContent = "YOU";
        (cell.querySelector(".arow") || cell).appendChild(badge);
      }}
      renderYouCard(me, cell);   // cell may be null (not on the board yet)
    }}

    // A sticky "Your standing" card so a player always sees their rank + shortcuts
    // without scrolling - and it shows even before their first game completes
    // (when they aren't on the board yet), so a new competitor isn't left blank.
    function renderYouCard(me, cell) {{
      var card = document.getElementById("youcard");
      if (!card) return;
      var tq = me.token ? ("&token=" + encodeURIComponent(me.token)) : "";
      var w = "/watch?agent=" + encodeURIComponent(me.agent) + tq;
      var h = "/history?agent=" + encodeURIComponent(me.agent) + "&board=" + LB_BOARD;
      var st = "/agent/" + encodeURIComponent(me.agent) + "?board=" + LB_BOARD + tq;
      var standing, row = null, decaying = false;
      if (cell) {{
        row = cell.closest("tr");
        var rows = row.parentNode ? row.parentNode.querySelectorAll("tr") : [];
        var rank = Array.prototype.indexOf.call(rows, row) + 1;
        var eloCell = row.querySelector("td.elo");
        var eloNum = eloCell ? eloCell.querySelector(".elo-n") : null;
        var rating = eloNum ? eloNum.textContent.trim() : (eloCell ? eloCell.textContent.trim() : "");
        standing = '<span class="ystat"><strong>#' + rank + '</strong> of ' + rows.length + '</span>' +
          '<span class="ystat">Rating <strong>' + rating + '</strong></span>';
        var decaying = eloCell && eloCell.getAttribute("data-decaying") === "1";
      }} else {{
        standing = '<span class="ystat">not ranked yet - finish a game to appear on the board</span>';
      }}
      // Idle-but-not-yet-decaying: a visible countdown to when decay
      // starts, so the grace window is explicit. Only when the agent is
      // truly offline (grey dot) and decay is live on this board.
      var graceLine = "";
      try {{
        var lr = document.getElementById("live-root");
        var graceMin = lr ? parseFloat(lr.getAttribute("data-grace-min") || "0") : 0;
        var decayLive = lr && lr.getAttribute("data-decay-live") === "1";
        var offDot = row && row.querySelector(".statusdot.off");
        var idlem = eloCell ? parseFloat(eloCell.getAttribute("data-idlem") || "0") : 0;
        if (decayLive && offDot && !decaying && graceMin > 0 && idlem < graceMin) {{
          var left = Math.max(0, (graceMin - idlem) * 60);
          graceLine = '<span class="ystat ygrace" id="ygrace" data-left="' + left.toFixed(0) +
            '" data-at="' + Date.now() + '">&#9662; agent idle - decay starts in <b>' +
            fmtMMSS(left) + '</b>; any finished game resets this</span>';
        }}
      }} catch (e) {{}}
      card.innerHTML =
        '<span class="ylabel">You</span>' +
        '<span class="ystat yname">' + (me.agent || "").replace(/[<>&]/g, "") + '</span>' +
        standing +
        '<span class="spacer"></span>' +
        '<div class="ynav">' +
        '<a href="' + w + '">' + IC_EYE + 'watch live</a>' +
        '<a href="' + h + '">' + IC_HIST + 'match history</a>' +
        '<a href="' + st + '">' + IC_STATS + 'my stats</a>' +
        (row ? '<a href="#" id="jumprow">' + IC_JUMP + 'jump to my row</a>' : '') +
        '</div>' +
        (decaying ? '<span class="ydecay">&#9662; decaying: your agent is idle - play a game to restore your rating</span>' : graceLine);
      card.querySelector(".ynav").insertAdjacentHTML("beforebegin",
        '<span class="ystat ybudget" id="ybudget"></span>');
      try {{
        var bq = me.token ? ("?token=" + encodeURIComponent(me.token)) : "";
        fetch("/budget/" + encodeURIComponent(me.agent) + bq, {{ cache: "no-store" }})
          .then(function (r) {{ return r.ok ? r.json() : null; }})
          .then(function (b) {{
            var el = document.getElementById("ybudget");
            if (!el || !b || !b.available) return;
            if (b.blocked) {{
              el.className = "ystat ybudget bad";
              el.textContent = "key blocked";
              el.setAttribute("data-tip", "The gateway is refusing this key. Your agent can still join games, but every LLM call will fail. Contact the organizers.");
              return;
            }}
            // Only ever the EXACT remaining balance on the dealt key.
            if (b.max_budget == null) return;
            var low = b.left < 0.15 * b.max_budget;
            el.className = "ystat ybudget" + (low ? " low" : "");
            el.textContent = "$" + Number(b.left).toFixed(2) + " left" + (low ? " - running low" : "");
            el.setAttribute("data-tip", "Your issued key: $" + Number(b.spend).toFixed(2) +
              " of $" + Number(b.max_budget).toFixed(2) + " used. It funds every LLM call this key makes.");
          }}).catch(function () {{}});
      }} catch (e) {{}}
      card.style.display = "flex";
      // The hint-line links are the no-token fallback for exactly these
      // destinations. Once the card is up they are duplicates, so drop them.
      var lbnav = document.querySelector(".lb-nav");
      if (lbnav) lbnav.remove();
      var jr = document.getElementById("jumprow");
      if (jr && row) jr.onclick = function (ev) {{ ev.preventDefault();
        row.scrollIntoView({{ behavior: "smooth", block: "center" }}); }};
    }}

    // Live auto-update: re-fetch the page and swap just the standings + banner in
    // place every 12s, so ratings refresh smoothly without a full reload (the
    // <meta refresh> is only a slow fallback). Re-inject the row links after.
    async function refreshBoard() {{
      try {{
        var r = await fetch(location.pathname, {{ cache: "no-store" }});
        if (!r.ok) return;
        var doc = new DOMParser().parseFromString(await r.text(), "text/html");
        var fresh = doc.getElementById("live-root");
        var cur = document.getElementById("live-root");
        if (fresh && cur) {{ cur.replaceWith(fresh); injectRowLinks(); markTruncated(); decayDrips(); }}
      }} catch (e) {{ /* transient: keep current view, try again next tick */ }}
    }}

    // Only names that actually overflow (ellipsis) get a tooltip with the full
    // name; names that fit get none (the themed tooltip handles the display).
    function markTruncated() {{
      var names = document.querySelectorAll('td.agent .aname');
      for (var i = 0; i < names.length; i++) {{
        var el = names[i];
        if (el.scrollWidth > el.clientWidth + 1) el.setAttribute('data-tip', el.textContent);
        else el.removeAttribute('data-tip');
      }}
    }}

    // Decay presented as MOVEMENT: remember each rating and, when a refresh
    // brings a lower number for a decaying agent, float the lost points off
    // the rating. No cumulative counter anywhere.
    var prevElo = {{}};
    function decayDrips() {{
      var cells = document.querySelectorAll("td.elo[data-a]");
      for (var i = 0; i < cells.length; i++) {{
        var c = cells[i], a = c.getAttribute("data-a");
        var v = parseInt(c.getAttribute("data-elo"), 10);
        var was = prevElo[a];
        if (was !== undefined && v < was && c.getAttribute("data-decaying") === "1") {{
          var d = document.createElement("span");
          d.className = "elo-drip";
          d.textContent = "-" + (was - v);
          c.appendChild(d);
          d.addEventListener("animationend", function () {{ this.remove(); }});
        }}
        prevElo[a] = v;
      }}
    }}
    function fmtMMSS(sec) {{
      sec = Math.max(0, Math.round(sec));
      return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
    }}
    // Tick the grace countdown between board refreshes; each refresh
    // re-anchors it from server truth.
    setInterval(function () {{
      var el = document.getElementById("ygrace");
      if (!el) return;
      var left = parseFloat(el.getAttribute("data-left")) -
                 (Date.now() - parseFloat(el.getAttribute("data-at"))) / 1000;
      var b = el.querySelector("b");
      if (left <= 0) {{ el.innerHTML = "&#9662; grace elapsed - your rating is now decaying; play a game to restore it"; return; }}
      if (b) b.textContent = fmtMMSS(left);
    }}, 1000);
    injectRowLinks();
    markTruncated();
    decayDrips();
    window.addEventListener('resize', markTruncated);
    setInterval(refreshBoard, 12000);
  </script>
</body>
</html>"""


def _fmt_time(iso: str) -> str:
    """Trim an ISO-8601 cutoff to 'YYYY-MM-DD HH:MM UTC' for display."""
    if not iso:
        return ""
    s = iso.replace("T", " ")[:16]
    return _escape(s) + " UTC"


def _competition_phase(live: Dict) -> str:
    """Derive the competition phase for the header from the configured window and
    current arena activity: 'scheduled' | 'running' | 'ending' | 'ended'.

    'ending' = no new games forming (host drained, or past the end cutoff) while
    games are still finishing. 'ended' = that, with no games left running.
    """
    live = live or {}
    start, end = competition_start(), competition_end()
    now = datetime.now().isoformat()
    draining = bool(live.get("draining"))
    active = int(live.get("matches", 0))
    winding_down = draining or bool(end and now >= end)
    if winding_down:
        return "ended" if active == 0 else "ending"
    if start and now < start:
        return "scheduled"
    return "running"


def _decay_notice_html(board: str) -> str:
    """Final-ramp warning ONLY. The standing decay explanation lives in the
    board tour, the Rating column tooltip, the badge tooltip and the You-card
    note - no permanent card. This renders nothing until the last ramp hours,
    then says the one thing that matters right then."""
    if board != "competition":
        return ""
    if INACTIVITY_DECAY_FINAL_PER_DAY <= INACTIVITY_DECAY_PER_DAY:
        return ""
    end = competition_end()
    if not end:
        return ""
    try:
        end_dt = datetime.fromisoformat(end)
        ramp_start = end_dt - timedelta(hours=INACTIVITY_DECAY_RAMP_HOURS)
        if not (ramp_start <= datetime.now() < end_dt):
            return ""
    except Exception:
        return ""
    return ('<div class="decay-note urgent">&#9662; <strong>Final stretch: idle ratings are '
            'dropping fast.</strong> Keep your agent playing to the end - playing restores '
            'decayed rating.</div>')


def _banner_html(live: Dict = None, entries: List[Dict] = None,
                 board: str = "competition") -> str:
    """Competition status header: the start/end window, the current phase, and -
    once the competition has ended - the crowned winner.

    For the build-week board there is no competition window or winner; just show
    the current live-activity bar."""
    live = live or {}
    entries = entries or []

    def _box(bg, accent, color, html):
        return (f'<div class="status-banner" '
                f'style="--sb-bg:{bg};--sb-accent:{accent};--sb-fg:{color};">{html}</div>')

    if board == "local":
        # Purely local play-testing: no competition framing at all.
        return _live_html(live)

    if board == "build":
        # The practice board has no window of its own, but on a same-day event
        # competitors land HERE first during the build phase. Without a pointer
        # to when scored play starts (and that ratings restart there), the switch
        # at the competition start reads as lost data rather than a new phase.
        b_start = competition_start()
        if not b_start:
            return _live_html(live)
        b_phase = _competition_phase(live)
        if b_phase == "scheduled":
            body = (f'{_ICON_CALENDAR} <strong>Practice board - nothing here counts.</strong><br>'
                    f'The scored competition starts <strong>{_fmt_time(b_start)}</strong> on the '
                    f'<a href="/leaderboard">official leaderboard</a>, which begins empty. '
                    f'Use this board to test your agent until then.')
            return _box("#eef1ff", "#2f54ff", "#23357f", body) + _live_html(live)
        body = (f'<strong>Practice board - nothing here counts.</strong><br>'
                f'The scored competition is on the '
                f'<a href="/leaderboard">official leaderboard</a>.')
        return _box("#f4f5f7", "#6b7078", "#3c3f45", body) + _live_html(live)

    start, end = competition_start(), competition_end()
    if not start and not end:
        # No competition window configured -> no competition framing (no
        # "starts/live/over" banner, even if the server is drained). Just the
        # live bar, so a cleared/between-competitions board reads neutral.
        return _live_html(live)
    phase = _competition_phase(live)

    # Window line (start / end), shown whenever either cutoff is configured.
    window = ""
    if start or end:
        parts = []
        if start:
            parts.append(f"<strong>Start</strong> {_fmt_time(start)}")
        if end:
            parts.append(f"<strong>End</strong> {_fmt_time(end)}")
        window = (f'<p class="sub" style="margin:0 0 .45rem;">{_ICON_CALENDAR} '
                  + " &nbsp;&middot;&nbsp; ".join(parts) + "</p>")

    if phase == "ended":
        w = next((e for e in entries if not e.get("provisional")), None)
        if w:
            body = (f'{_ICON_TROPHY} <strong>Competition over - winner: '
                    f'{_escape(w["agent_id"])}</strong><br>'
                    f'{w["elo"]} rating across {w["games"]} game(s), '
                    f'{w["wins"]} win(s). Congratulations!')
        else:
            body = f'{_ICON_TROPHY} <strong>Competition over.</strong> No games were played.'
        return window + _box("#fff7e0", "#e0a700", "#7a5c00", body)

    if phase == "ending":
        active = int(live.get("matches", 0))
        body = (f'<strong>Competition ending.</strong> No new games are forming - '
                f'{active} game(s) still finishing. The winner is crowned once the last '
                f'game completes.')
        return window + _box("#fff3e0", "#e08a0a", "#8a4b00", body) + _live_html(live)

    if phase == "scheduled":
        body = (f'{_ICON_CALENDAR} <strong>Competition starts {_fmt_time(start)}.</strong> '
                f'Standings count from then.')
        return window + _box("#eef1ff", "#2f54ff", "#23357f", body) + _live_html(live)

    # running
    running = ""
    if start:
        running = _box("#e7f5ee", "#16a34a", "#0f6c3c",
                       f'{_ICON_CALENDAR} <strong>Competition live.</strong> '
                       f'Counting games since {_fmt_time(start)}.')
    return window + running + _live_html(live)


def _live_html(live: Dict = None) -> str:
    """A small live-activity bar: players and matches active in the arena now."""
    if not live:
        return ""
    players = int(live.get("players", 0))
    matches = int(live.get("matches", 0))
    in_game = int(live.get("in_game", 0))
    queued = int(live.get("queued", 0))
    detail = ""
    if in_game or queued:
        detail = (f'<span class="live-detail">{in_game} in a match '
                  f'&middot; {queued} waiting in queue</span>')
    return (
        '<div class="live">'
        f'<span class="chip"><span class="dot online"></span>{players} '
        f'player{"" if players == 1 else "s"} online</span>'
        f'<span class="chip">{matches} '
        f'match{"" if matches == 1 else "es"} running</span>'
        f'{detail}'
        '</div>'
    )


def _escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
