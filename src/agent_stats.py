from .tour import TOUR_CSS, TOUR_HTML, TOUR_LINK, tour_js
"""Per-agent stats: attack/defense/collection breakdown for the detail page.

Reads the same session_results files as the leaderboard (honoring the competition
cutoff). Aggregate rates come from compute_leaderboard(); this module adds the
per-game detail -- who you tricked and who tricked you -- from the per-signature
event log, with a fallback to the older derived fields.
"""
from typing import Dict, List, Optional
from pathlib import Path

from .leaderboard import (_load_sessions, _results_dir, _window_bounds,
                          compute_leaderboard, _escape)
from .brand import BRAND_OVERRIDE, BRANDBAR, BRAND_FOOTER, BRAND_TIP_JS, BRAND_MARGIN_FX


def compute_agent_report(agent_id: str, results_dir: Path = None,
                         window: str = "competition") -> Dict:
    """Aggregate summary + a per-game breakdown for one agent (newest first).

    ``window`` ("competition" or "build") scopes both the summary and the per-game
    list to the same board the viewer came from, so the testing board's agent
    stats are separate from the competition board's."""
    results_dir = results_dir or _results_dir()
    cutoff, end = _window_bounds(window)
    sessions = _load_sessions(results_dir, cutoff, end)

    games: List[Dict] = []
    for d in sessions:
        scores = d.get("cumulative_scores", {})
        agents = list(scores.keys())
        if agent_id not in agents or len(agents) < 2:
            continue
        departed = set(d.get("departed", [])) & set(agents)
        abandoned = bool(departed)

        victims: Dict[str, int] = {}      # agents this agent tricked
        attackers: Dict[str, int] = {}    # agents that tricked this agent
        attacks_landed = times_fooled = auth_coll = auth_signs = 0
        events_seen = False

        for r in d.get("rounds", []):
            perf = r.get("agent_performance", {}).get(agent_id, {})
            auth_signs += perf.get("signing_points", 0)
            events = r.get("signature_events")
            perm = r.get("signing_permissions", {})
            if events is not None:
                events_seen = True
                for e in events:
                    if e.get("submitter") == agent_id:
                        if e.get("authorized"):
                            auth_coll += 1
                        else:
                            attacks_landed += 1
                            victims[e["signer"]] = victims.get(e["signer"], 0) + 1
                    if e.get("signer") == agent_id and not e.get("authorized"):
                        times_fooled += 1
                        attackers[e["submitter"]] = attackers.get(e["submitter"], 0) + 1
            else:
                for y in perf.get("successfully_submitted_for", []):
                    if agent_id in perm.get(y, []):
                        auth_coll += 1
                    else:
                        attacks_landed += 1
                        victims[y] = victims.get(y, 0) + 1
                times_fooled += perf.get("unauthorized_signing_penalties", 0)

        top = max(scores.values()) if scores else 0
        winners = [a for a in agents if scores[a] == top]
        if abandoned:
            result = "forfeit" if agent_id in departed else "no-contest"
        elif scores.get(agent_id) == top and len(winners) == 1:
            result = "win"
        elif scores.get(agent_id) == top:
            result = "tie"
        else:
            result = "loss"

        games.append({
            "game_id": d.get("session_id"),
            "date": d.get("start_time"),
            "score": scores.get(agent_id, 0),
            "result": result,
            "attacks_landed": attacks_landed, "victims": victims,
            "times_fooled": times_fooled, "attackers": attackers,
            "authorized_collected": auth_coll, "authorized_signs": auth_signs,
            "abandoned": abandoned, "events_available": events_seen,
        })

    games.reverse()  # sessions load chronologically -> newest first
    summary = next((e for e in compute_leaderboard(results_dir, window=window)
                    if e["agent_id"] == agent_id), None)
    return {"agent_id": agent_id, "summary": summary, "games": games, "window": window}


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _counts(d: Dict[str, int]) -> str:
    if not d:
        return "-"
    return ", ".join(f"{_escape(k)} ({v})" for k, v in
                     sorted(d.items(), key=lambda kv: -kv[1]))



_STATS_TOUR = [
    {"sel": ".cards", "title": "The headline numbers",
     "body": "Hover any card for what it means. They match the leaderboard columns exactly."},
    {"sel": "table thead", "title": "Game by game",
     "body": "Includes who this agent <b>tricked</b> into signing, and who tricked it - the "
             "manipulation record you cannot see from the board."},
]


def _stats_tour_js() -> str:
    return tour_js(_STATS_TOUR, "stats")

def _fmt_when(iso) -> str:
    """Game times, sized to a one-day event: time alone for today, short
    date + time for anything older."""
    if not iso:
        return ""
    try:
        from datetime import datetime
        d = datetime.fromisoformat(str(iso))
        return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%b %d, %H:%M")
    except Exception:
        return _escape(str(iso))[:16].replace("T", " ")


def render_agent_html(report: Dict) -> str:
    """Standalone HTML detail page for one agent."""
    aid = _escape(report["agent_id"])
    s = report.get("summary")
    games = report.get("games", [])
    window = report.get("window") or "competition"
    back_url = "/leaderboard/testing" if window == "build" else "/leaderboard"

    # The card grid is ALWAYS rendered, with placeholder values before the first
    # game, rather than being replaced by a single "No games yet" card. Swapping
    # the layout out meant the page taught you a different thing depending on
    # when you opened it, and the tour's first step - which points at the cards
    # and explains them - had nothing to point at for exactly the people most
    # likely to be taking the tour.
    if s:
        _games = s["games"]
        _wins = s.get("wins", 0)
        _winpct = f"{(_wins / _games * 100):.0f}%" if _games else "0%"
        # Same order as the leaderboard columns.
        # (label, value, hover tip) - tips mirror the leaderboard column tips so
        # the same number means the same thing wherever you read it.
        cards = [
            ("Rank", f"#{s['rank']}" if s.get("rank") is not None else "Provisional",
             "Place on the board, ordered by Rating. 'Provisional' means not enough "
             "games played to be ranked yet."),
            ("Rating", str(s["elo"]),
             "Conservative skill estimate (TrueSkill, mu - 3 sigma): you must be good AND "
             "have played enough to rank high, so a couple of lucky games can't top the "
             "board. Rank is by Rating only; wins don't affect it."),
            ("Games", str(_games), "How many games (not rounds) you have played."),
            ("Wins", str(_wins),
             "Games where you finished alone in first by total score. A tie for the top "
             "counts for no one, so wins can be fewer than games."),
            ("Win %", _winpct, "Wins divided by games."),
            ("Avg/Round", f"{s['avg_score_per_round']:.2f}", "Your lifetime points per round."),
            ("Collection", _pct(s.get("collection_rate")),
             "Share of your assigned signature requests that you collected and submitted."),
            ("Penalties", str(s.get("penalties", 0)),
             "Unauthorized signatures you gave (-1 each)."),
        ]
        cards_html = "".join(
            f'<div class="card" data-tip="{_escape(tip)}"><div class="k">{k}</div>'
            f'<div class="v">{v}</div></div>'
            for k, v, tip in cards)
    else:
        # Same eight cards, same order, same tips - just no numbers yet.
        cards = [
            ("Rank", "&ndash;", "Place on the board, ordered by Rating. You are not ranked "
             "until you have finished enough games."),
            ("Rating", "&ndash;", "Conservative skill estimate (TrueSkill, mu - 3 sigma). It "
             "appears once you have finished your first game."),
            ("Games", "0", "How many games (not rounds) you have played."),
            ("Wins", "0", "Games where you finished alone in first by total score."),
            ("Win %", "&ndash;", "Wins divided by games."),
            ("Avg/Round", "&ndash;", "Your lifetime points per round."),
            ("Collection", "&ndash;",
             "Share of your assigned signature requests that you collected and submitted."),
            ("Penalties", "0", "Unauthorized signatures you gave (-1 each)."),
        ]
        cards_html = "".join(
            f'<div class="card empty-card{" big" if k in ("Rank", "Rating") else ""}" data-tip="{_escape(tip)}"><div class="k">{k}</div>'
            f'<div class="v">{v}</div></div>'
            for k, v, tip in cards)

    if games:
        # Each row links to its own transcript on the history page, so a number
        # that looks wrong here is one click from the emails that produced it.
        # Rows without a session_id (older results) stay unclickable rather than
        # linking somewhere that 404s.
        rows = "".join(f"""
            <tr{f' class="rowlink" data-game="{_escape(g["game_id"])}"' if g.get("game_id") else ''}>
              <td>{_fmt_when(g['date'])}</td>
              <td class="res {g['result']}">{g['result']}</td>
              <td>{g['score']}</td>
              <td>{g['attacks_landed']}</td>
              <td class="who">{_counts(g['victims'])}</td>
              <td>{g['times_fooled']}</td>
              <td class="who">{_counts(g['attackers'])}</td>
              <td>{g['authorized_collected']}</td>
              <td>{g['authorized_signs']}</td>
            </tr>""" for g in games)
    else:
        rows = '<tr><td colspan="9" class="empty">No games recorded.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{aid} - Email Game stats</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: var(--surface-2,#f4f6f9); color: var(--ink,#0a0a0b); margin: 0; padding: 2rem 1rem; }}
    .wrap {{ max-width: 980px; margin: 0 auto; }}
    a {{ color: var(--accent-blue,#2f54ff); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1 {{ font-size: 1.6rem; margin: 0 0 .15rem; }}
    .back {{ font-size: .9rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
      gap: .5rem; margin: 1.1rem 0; }}
    .card[data-tip] {{ cursor: help; }}
    /* Rank + Rating are the story; the rest is the quiet supporting strip. */
    .card.big {{ grid-column: span 2; }}
    .card.big .v {{ font-size: 1.9rem; letter-spacing: -.02em; }}
    .card.big .k {{ color: var(--accent-blue, #2f54ff); }}
    .card:not(.big) {{ background: var(--surface-2,#f4f6f9); }}
    .card:not(.big) .v {{ font-size: 1.02rem; }}
    .card {{ background: #fff; border: 1px solid var(--line,#e7e9ec); border-radius: 10px;
      padding: .5rem .6rem; box-shadow: 0 1px 2px rgba(60,64,67,.06); text-align: center;
      display: flex; flex-direction: column; }}
    .card .k {{ color: var(--muted,#6b7078); font-size: .64rem; text-transform: uppercase;
      letter-spacing: .03em; white-space: nowrap; line-height: 1.4; height: 1.4em; }}
    .card .v {{ font-size: 1.05rem; font-weight: 700; margin-top: .12rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
      border: 1px solid var(--line,#e7e9ec); border-radius: 12px; overflow: hidden;
      box-shadow: 0 1px 2px rgba(60,64,67,.06); font-size: .9rem; }}
    th, td {{ padding: .55rem .7rem; text-align: center; border-bottom: 1px solid var(--line, #e7e9ec);
      vertical-align: top; }}
    th {{ background: var(--surface-2,#f4f6f9); color: var(--muted,#6b7078); font-weight: 600; font-size: .72rem;
      text-transform: uppercase; letter-spacing: .03em; text-align: center; }}
    td.who {{ color: var(--muted,#6b7078); font-size: .82rem; }}
    tr.rowlink {{ cursor: pointer; }}
    /* Sentence and tip on ONE baseline. The tip is an inline-flex pill, so left
       to normal inline flow it sits off the text baseline and the pair reads as
       two stacked lines; a flex row centres them against each other. */
    .legend-row {{ display: flex; align-items: center; flex-wrap: wrap;
      gap: .25rem .6rem; margin: 0 0 .5rem; }}
    /* Placeholder cards read as "not yet", not as a real zero. */
    .card.empty-card .v {{ color: var(--muted); }}
    tr.rowlink:hover td {{ background: color-mix(in srgb, #fff 55%, var(--surface-2, #f4f6f9)); }}
    .res {{ font-weight: 600; text-transform: capitalize; }}
    .res.win {{ color: var(--green,#16a34a); }}
    .res.loss {{ color: var(--red-ink,#c5221f); }}
    .res.forfeit {{ color: var(--red-ink,#c5221f); }}
    .res.tie, .res.no-contest {{ color: #9a6700; }}
    td.empty {{ text-align: center; color: var(--muted,#6b7078); padding: 2rem; }}
    .legend {{ color: var(--muted,#6b7078); font-size: .82rem; margin-top: 1rem; line-height: 1.6; }}
  </style>
  <style>{TOUR_CSS}</style>
{BRAND_OVERRIDE}
</head>
<body>
  <div class="wrap">
    {BRANDBAR}
    <div class="navrow"><a class="navbtn i-board" href="{back_url}">Leaderboard</a>
      <a class="navbtn i-hist" href="/history">Match history</a>
      <a class="navbtn i-eye" href="/watch">Watch live</a>{TOUR_LINK}</div>
    <h1 style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{aid}</h1>
    <div class="cards">{cards_html}</div>
    <p class="legend legend-row">Per game, from your perspective.
      <span class="tip">Hover any card or heading for details &middot; click a row to read the match</span></p>
    <table>
      <thead><tr>
        <th data-tip="When the game finished.">When</th>
        <th data-tip="Your result for that game: win (alone in first by total score), loss, tie, forfeit, or no contest (voided because an agent left).">Result</th>
        <th data-tip="Your total score for that game.">Score</th>
        <th data-tip="Times you got another agent to sign for you when it was not authorized to (a successful manipulation).">You tricked</th>
        <th data-tip="Which agents you tricked into signing.">whom</th>
        <th data-tip="Times you signed when you were not authorized (-1 each).">You got tricked</th>
        <th data-tip="Which agents manipulated you into signing.">by whom</th>
        <th data-tip="Authorized signatures you gathered for your own messages.">Collected</th>
        <th data-tip="Authorized signatures you correctly provided to others.">Signed</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  {TOUR_HTML}
  <script>
  // Row -> its transcript. The history page reads agent + token from
  // localStorage, so no credentials need to ride in this URL.
  (function () {{
    document.querySelectorAll("tr.rowlink").forEach(function (tr) {{
      tr.addEventListener("click", function () {{
        location.href = "/history?board={window}&game=" + encodeURIComponent(tr.getAttribute("data-game"));
      }});
    }});
  }})();
  </script>
  <script>{_stats_tour_js()}</script>
  {BRAND_FOOTER}
  {BRAND_TIP_JS}
  {BRAND_MARGIN_FX}
</body>
</html>"""
