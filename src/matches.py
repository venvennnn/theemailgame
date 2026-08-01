from .tour import TOUR_CSS, TOUR_HTML, TOUR_LINK, tour_js
"""Competitor-facing match history.

After a game ends its live mailbox is purged, so the watch page can't replay it.
But every completed game is fully recorded in session_results/session_arena_*.json
(per-round messages, assignments, scores, signature events). This module reads
those files to give a competitor:

  - a list of their own past matches (opponents, score, placement, breakdown), and
  - a per-match detail view (per-round messages + scoring).

Privacy: during the competition the detail is the competitor's OWN perspective
only (the mail they sent and received), exactly like the live watch. After
COMPETITION_END_TIME the full transcript (all agents) is unlocked as a
post-mortem. The serving endpoints gate access by the agent's own token.
"""
from .brand import BRAND_OVERRIDE, BRANDBAR, BRAND_FOOTER, BRAND_TIP_JS, BRAND_MARGIN_FX

from datetime import datetime
from typing import Dict, List, Optional

from src.leaderboard import (
    _results_dir,
    _load_sessions,
    _window_bounds,
    competition_start,
    competition_end,
)

_PAIR_SEP = "↔"  # the "↔" used in conversation keys, e.g. "alice↔moderator"


def _window_sessions(window: str = "competition") -> List[Dict]:
    """Sessions for ONE board window (chronological).

    Match history is scoped to the same window as its board so the phases stay
    separate: the build-week board / history shows only build-week games, the
    competition board / history shows only competition games, and nothing leaks
    across. ``window`` is "build" or "competition" (see leaderboard._window_bounds)."""
    cutoff, end = _window_bounds(window)
    return _load_sessions(_results_dir(), cutoff, end)


def competition_ended() -> bool:
    """True once COMPETITION_END_TIME has passed (full transcripts unlocked)."""
    end = competition_end()
    if not end:
        return False
    try:
        return datetime.now().isoformat() >= end
    except Exception:
        return False


def _norm_msg(m: Dict, round_number=None) -> Dict:
    return {
        "from": m.get("from"),
        "to": m.get("to"),
        "subject": m.get("subject"),
        "body": m.get("body"),
        "timestamp": m.get("timestamp"),
        "message_id": m.get("message_id"),
        "round": round_number,
    }


def list_matches_for_agent(agent_id: str, window: str = "competition") -> List[Dict]:
    """Summaries of this agent's matches in one board window, newest first."""
    out: List[Dict] = []
    for d in _window_sessions(window):
        scores = d.get("cumulative_scores") or {}
        ids = d.get("agent_ids") or list(scores.keys())
        if agent_id not in ids:
            continue
        my = scores.get(agent_id, 0)
        top = max(scores.values()) if scores else 0
        sole_first = bool(scores) and my == top and list(scores.values()).count(top) == 1
        rank = 1 + sum(1 for v in scores.values() if v > my)
        departed = d.get("departed") or []

        sub = sig = pen = 0
        for r in d.get("rounds", []):
            perf = (r.get("agent_performance") or {}).get(agent_id, {})
            sub += perf.get("submission_points", 0)
            sig += perf.get("signing_points", 0)
            pen += perf.get("unauthorized_signing_penalties", 0)

        if agent_id in departed:
            result = "left"
        elif departed:
            # someone ELSE disconnected: the game is a no-contest for survivors
            # (rating frozen, not counted) - must NOT show as a win/tie/loss, to
            # match how the leaderboard scores it.
            result = "no contest"
        elif sole_first:
            result = "win"
        elif scores and my == top:
            result = "tie"
        else:
            result = "loss"

        out.append({
            "game_id": d.get("session_id"),
            "start_time": d.get("start_time"),
            "end_time": d.get("end_time"),
            "rounds": d.get("total_rounds") or len(d.get("rounds", [])),
            "opponents": [a for a in ids if a != agent_id],
            "your_score": my,
            "rank": rank,
            "field": len(scores),
            "result": result,
            "scores": scores,
            "breakdown": {"collected": sub, "signed": sig, "penalties": pen},
            "abandoned": bool(departed),
            "departed": departed,
        })
    out.reverse()  # _window_sessions is chronological asc; show newest first
    return out


def match_detail(game_id: str, agent_id: str, full: Optional[bool] = None,
                 window: str = "competition") -> Optional[Dict]:
    """Per-round detail for one match. own-perspective unless ``full`` (defaults to
    competition_ended()). Scoped to ``window`` so a game id from one phase can't be
    opened from another. Returns None if not found in this window or the agent
    didn't play in it."""
    if full is None:
        full = competition_ended()
    for d in _window_sessions(window):
        if d.get("session_id") != game_id:
            continue
        scores = d.get("cumulative_scores") or {}
        ids = d.get("agent_ids") or list(scores.keys())
        if agent_id not in ids:
            return None
        rounds = []
        for r in d.get("rounds", []):
            rnum = r.get("round_number")
            msgs = []
            for pair, lst in (r.get("conversations") or {}).items():
                parts = pair.split(_PAIR_SEP)
                if not full and agent_id not in parts:
                    continue
                for m in (lst or []):
                    if not full and not (m.get("from") == agent_id or m.get("to") == agent_id):
                        continue
                    msgs.append(_norm_msg(m, rnum))
            msgs.sort(key=lambda x: x.get("timestamp") or "")
            rounds.append({
                "round_number": rnum,
                "your_assigned_message": (r.get("agent_messages") or {}).get(agent_id),
                "your_request_list": (r.get("request_lists") or {}).get(agent_id),
                "your_sign_perms": (r.get("signing_permissions") or {}).get(agent_id),
                "your_score": (r.get("agent_scores") or {}).get(agent_id),
                "performance": (r.get("agent_performance") or {}).get(agent_id, {}),
                "messages": msgs,
            })
        return {
            "game_id": game_id,
            "agent_id": agent_id,
            "full": full,
            "start_time": d.get("start_time"),
            "end_time": d.get("end_time"),
            "agent_ids": ids,
            "cumulative_scores": scores,
            "rounds": rounds,
        }
    return None



_HISTORY_TOUR = [
    {"sel": "#app", "title": "Every game you have played",
     "body": "One row per finished game. Click a row to replay that match round by round; "
             "<b>&lsaquo; All matches</b> at the top takes you back to this list."},
    # Only present once a match is open, and dropped from the tour otherwise.
    {"sel": ".filters", "title": "Reading one match",
     "body": "<b>All</b>, <b>Sent</b>, <b>Received</b> and <b>Moderator</b> narrow the "
             "transcript - <b>Moderator</b> is the game's own mail, including the "
             "instructions you were given. The <b>with</b> dropdown limits it to a single "
             "opponent so you can follow one negotiation straight through."},
]

def render_history_html(local: bool = False) -> str:
    """Standalone competitor match-history page (HTML + inline JS).

    Link-only, like the watch page: it reads agent + view token from the URL (or
    localStorage), scrubs the token from the address bar, and calls
    /matches/{agent} and /match/{game}/{agent} with it. ``local`` enables
    build-week local-testing mode (works with just ?agent=NAME, no token)."""
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Match history - The Email Game</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    background: var(--surface-2,#f4f6f9); color: var(--ink,#0a0a0b); margin: 0; padding: 1.5rem 1rem; }
  .wrap { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  .sub { color: var(--muted,#6b7078); font-size: .9rem; margin: 0 0 1.25rem; }
  a.plain { color: var(--accent-blue,#2f54ff); }
  .crumbs { font-size: .85rem; margin: 0 0 1rem; }
  .matches { display: flex; flex-direction: column; gap: .5rem; }
  .match { display: block; text-align: left; width: 100%; cursor: pointer;
    background: #fff; border: 1px solid var(--line,#e7e9ec);
    border-radius: 8px; padding: .7rem .9rem; box-shadow: 0 1px 2px rgba(60,64,67,.05);
    font: inherit; color: inherit; transition: background .12s ease; }
  /* Same hover feedback as a clickable row on the stats page - both are "click
     this to read the match", so both should respond the same way. */
  .match.win { border-color:var(--green-line,#bfe6cf); background:var(--green-bg,#e7f5ee); }
  .match.loss { border-color:var(--red-line,#f5c6c0); background:var(--red-bg,#fdecea); }
  .match.tie { border-color:var(--amber-line,#f0dca8); background:var(--amber-bg,#fff8e6); }
  .match.left { border-color:var(--line,#e7e9ec); background:var(--surface-2,#f4f6f9); }
  /* Opponent identity: the map's colored discs, reused as chips. */
  .oppchip { display:inline-flex; align-items:center; gap:.3rem; margin-left:.5rem;
    min-width:0; }
  .oppdisc { width:1.05rem; height:1.05rem; border-radius:50%; flex:none; color:#fff;
    font-size:.6rem; font-weight:700; display:inline-flex; align-items:center;
    justify-content:center; font-family:"Inter",sans-serif; }
  .match .row1 { display: flex; justify-content: space-between; gap: .6rem; align-items: baseline; }
  .match .res { font-weight: 700; text-transform: uppercase; font-size: .75rem; letter-spacing: .03em; }
  .res.win { color: var(--green,#16a34a); } .res.loss { color: var(--red-ink,#c5221f); }
  .res.tie { color: var(--amber-ink,#7a5c00); } .res.left { color: var(--muted,#6b7078); }
  .match .opp { color: var(--ink-2,#3c3f45); font-size: .9rem; min-width:0; max-width:100%;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .match .meta { color: var(--muted,#6b7078); font-size: .8rem; margin-top: .2rem; }
  .pill { display:inline-block; font-size:.72rem; font-weight:700; padding:.05rem .4rem;
    border-radius:4px; background:#eef1f5; color:var(--ink-2,#3c3f45); margin-left:.35rem; }
  .empty { color:var(--muted,#6b7078); text-align:center; padding:2rem; }
  .err { background:var(--red-bg,#fdecea); border:1px solid var(--red-line,#f5c6c0); color:var(--red-ink,#c5221f); padding:.7rem .9rem;
    border-radius:8px; font-size:.9rem; margin:0 0 1rem; }
  .gate { background:#fff; border:1px solid var(--line,#e7e9ec); border-radius:12px; padding:1.25rem 1.5rem;
    box-shadow:0 1px 2px rgba(60,64,67,.06); max-width:520px; }
  .gate h2 { font-size:1.05rem; margin:0 0 .4rem; }
  .gate code { background:var(--surface-2,#f4f6f9); padding:.1rem .35rem; border-radius:4px; font-size:.82rem; overflow-wrap:anywhere; }
  .scorebar { background:#fff; border:1px solid var(--line,#e7e9ec); border-radius:10px; padding:.5rem .7rem;
    margin:0 0 1rem; font-size:.9rem; }
  .sb-head { font-size:.78rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em;
    color:var(--muted,#6b7078); margin:.1rem .2rem .45rem; }
  .sb-row { display:flex; align-items:center; gap:.6rem; padding:.32rem .5rem; border-radius:6px; }
  .sb-row:nth-child(even) { background:#fafbfc; }
  .sb-row.you { background:var(--blue-bg,#eef1ff); }
  .sb-rank { width:1.6rem; text-align:center; font-weight:700; color:var(--muted,#6b7078); }
  .sb-name { flex:1; font-weight:600; color:var(--ink,#0a0a0b); }
  .sb-you { color:var(--accent-blue-d,#2546e6); font-weight:700; font-size:.8rem; }
  .sb-score { font-weight:700; color:var(--accent-blue,#2f54ff); min-width:1.5rem; text-align:right; }
  .roundhdr { font-weight:700; margin:1.1rem 0 .5rem; font-size:1rem;
    border-bottom:1px solid var(--line,#e7e9ec); padding-bottom:.3rem; }
  .assign { color:var(--muted,#6b7078); font-size:.82rem; margin:0 0 .5rem; }
  .feed { display:flex; flex-direction:column; gap:.5rem; }
  .msg { background:#fff; border:1px solid var(--line,#e7e9ec);
    border-radius:8px; padding:.6rem .8rem; box-shadow:0 1px 2px rgba(60,64,67,.05); }
  .msg.inc { border-color:var(--green-line,#bfe6cf); background:var(--green-bg,#e7f5ee); }
  .msg.out { border-color:var(--blue-line,#ccd6ff); background:var(--blue-bg,#eef1ff); }
  .msg.mod { border-color:var(--amber-line,#f0dca8); background:var(--amber-bg,#fff8e6); }
  .msg .meta2 { font-size:.78rem; color:var(--muted,#6b7078); display:flex; gap:.5rem; flex-wrap:wrap; margin-bottom:.25rem; }
  .msg .who { font-weight:700; color:var(--ink,#0a0a0b); }
  .msg .subj { font-weight:600; font-size:.92rem; margin-bottom:.15rem; }
  .msg .body { font-size:.88rem; white-space:pre-wrap; word-break:break-word; color:var(--ink-2,#3c3f45); }
  .tag { font-size:.72rem; font-weight:700; padding:.05rem .4rem; border-radius:4px; }
  .tag.inc { background:var(--green-bg,#e7f5ee); color:var(--green,#16a34a); } .tag.out { background:var(--blue-bg,#eef1ff); color:var(--accent-blue-d,#2546e6); }
  .tag.mod { background:var(--amber-bg,#fff8e6); color:var(--amber-ink,#7a5c00); }
  .filters { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; margin:.6rem 0 1rem; }
  .fchip { background:#fff; border:1px solid var(--line-2,#d9dce0); border-radius:999px;
    padding:.3rem .8rem; font-size:.82rem; font-weight:600; color:var(--ink-2,#3c3f45); cursor:pointer; }
  .fchip.on { background:var(--accent-blue,#2f54ff); color:#fff; border-color:var(--accent-blue,#2f54ff); }
  /* Was flex:1, which shoved the "with <agent>" control against the right
     edge of the container - at common window widths it ended up clipped under
     the scrollbar, where its styling was impossible to see. It filters the same
     feed as the chips, so it belongs beside them. */
  .fspace { flex:0 0 .55rem; }
  .btabs { display:flex; align-items:center; gap:.4rem; margin:0 0 .9rem; }
  .btab { display:inline-flex; align-items:center; gap:.4rem; padding:.32rem .8rem;
    border-radius:999px; font-size:.85rem; font-weight:600; text-decoration:none; }
  .btab .bcount { font-size:.75rem; opacity:.7; font-variant-numeric:tabular-nums; }
  .flbl { font-size:.82rem; color:var(--muted,#6b7078); }
  .filters select { font-size:.82rem; padding:.25rem .4rem; border:1px solid var(--line-2,#d9dce0); border-radius:6px; }
  .sig { font-size:.72rem; font-weight:700; padding:.05rem .45rem; border-radius:4px;
    background:var(--green-bg,#e7f5ee); color:var(--green,#16a34a); border:1px solid var(--green-line,#bfe6cf); cursor:help; }
</style><style>""" + TOUR_CSS + r"""</style>""" + BRAND_OVERRIDE + r"""
</head>
<body>
<div class="wrap">
  """ + BRANDBAR + r"""
  <h1>Match history</h1>
  <div class="navrow">
    <a class="navbtn i-board" id="boardlink" href="/leaderboard">Leaderboard</a>
    <a class="navbtn i-eye" id="watchlink" href="/watch">Watch live</a>
    <a class="navbtn i-stats" id="statslink" href="#">My stats</a>
    """ + TOUR_LINK + r"""</div>
  <div id="app"></div>
</div>
""" + TOUR_HTML + r"""
<script>""" + tour_js(_HISTORY_TOUR, "history") + r"""</script>
<script>
(function () {
  // LOCAL mode (build-week local testing): no token needed; ?agent=NAME suffices.
  const LOCAL = /*__LOCAL__*/false;
  const app = document.getElementById("app");
  const qs = new URLSearchParams(location.search);
  let agent = qs.get("agent") || "";
  let token = qs.get("token") || "";
  let view = "list";   // "list" while browsing matches, "detail" while reading one
  let curDetail = null;        // the match detail currently open (for re-render on filter)
  let hDir = "all";            // detail filter: all | out (sent) | inc (received) | mod
  let hWho = "all";            // detail filter: counterparty agent id, or "all"

  function loadStored() {
    try { return JSON.parse(localStorage.getItem("emailgame_watch") || "null"); }
    catch (e) { return null; }
  }
  if (!token) {
    const s = loadStored();
    if (s && s.token && (!agent || agent === s.agent)) { agent = s.agent; token = s.token; }
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function fmtTime(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts), now = new Date();
      return d.toDateString() === now.toDateString()
        ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    } catch (e) { return ts; }
  }
  // Deterministic per-name hue: the same agent is the same color on every row,
  // echoing the match map's discs.
  function oppChips(list) {
    return (list || []).map(function (o) {
      var h = 0;
      for (var i = 0; i < o.length; i++) h = (h * 31 + o.charCodeAt(i)) % 360;
      return '<span class="oppchip"><span class="oppdisc" style="background:hsl(' + h +
        ',60%,42%)">' + esc((o[0] || "?").toUpperCase()) + '</span>' + esc(o) + '</span>';
    }).join("");
  }

  function splitSignature(body) {
    const s = String(body == null ? "" : body);
    const i = s.indexOf("SIGNED_MESSAGE_JSON:");
    if (i === -1) return { text: s, sig: null };
    const text = s.slice(0, i).replace(/\s+$/, "");
    let sig = { raw: true };
    try {
      const o = JSON.parse(s.slice(i + "SIGNED_MESSAGE_JSON:".length).trim());
      sig = { signer: o.signer, signed_for: o.signed_for, original: o.original_message };
    } catch (e) {}
    return { text: text, sig: sig };
  }

  // Shorten long base64 signature blobs so the transcript stays readable;
  // clearly marked as truncated.
  function clipSigs(s) {
    return String(s == null ? "" : s).replace(/[A-Za-z0-9+\/]{80,}={0,2}/g, function (b) {
      return b.slice(0, 12) + "…[signature truncated, " + b.length + " chars]";
    });
  }

  async function pull(path) {
    const q = token ? ((path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token)) : "";
    const r = await fetch(path + q);
    if (r.status === 401 || r.status === 403) throw new Error("auth");
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }

  function gate() {
    if (LOCAL) {
      app.innerHTML =
        '<div class="gate"><h2>Local match history</h2>' +
        '<p>Add <code>?agent=NAME</code> to this URL to see that agent’s local matches ' +
        '(e.g. <code>/history?agent=myagent</code>). No login needed when testing locally.</p>' +
        '<div class="navrow"><a class="navbtn i-board" href="/leaderboard">Local leaderboard</a></div></div>';
      return;
    }
    app.innerHTML =
      '<div class="gate"><h2>Open your link to see your matches</h2>' +
      '<p>This page shows your own agent’s match history. There is nothing to type in.</p>' +
      '<p class="sub">Open the <strong>Watch your match</strong> link your agent prints ' +
      'on startup once; then this page works too. ' +
      'The link prints again at the start of every match, so you never need to ' +
      'restart to get it back - and restarting mid-match forfeits that game.</p></div>';
  }

  let navBoard = qs.get("board") || null;   // which board this competitor is in
  function curBoard() {
    var b = navBoard;
    if (!b) { try { b = localStorage.getItem("emailgame_board"); } catch (e) {} }
    return b || "";
  }
  // Scope match-history fetches to the current board's window, so build-week and
  // competition history stay separate (server maps board -> window).
  function boardQS() {
    var b = curBoard();
    return b ? "?board=" + encodeURIComponent(b) : "";
  }
  function boardUrl() {
    return curBoard() === "build" ? "/leaderboard/testing" : "/leaderboard";
  }
  async function refreshNavBoard() {
    try {
      const r = await fetch("/queue_status");
      if (r.ok) {
        var nb = (await r.json()).nav_board;
        if (nb && nb !== navBoard && !qs.get("board")) {  // URL board wins if set
          navBoard = nb; setLinks();
          if (view === "list") showList();   // re-fetch in the right window
        }
      }
    } catch (e) {}
  }

  function setLinks() {
    const wl = document.getElementById("watchlink");
    if (wl && agent) wl.href = "/watch?agent=" + encodeURIComponent(agent) + (token ? "&token=" + encodeURIComponent(token) : "");
    const bl = document.getElementById("boardlink");
    if (bl) bl.href = boardUrl();
    // Same board as the matches being shown, so the numbers agree with the list.
    // Token in the query: /agent/<id> is server-rendered and its guard reads the
    // token from there, so a link without it lands on a 403 instead of stats.
    const sl = document.getElementById("statslink");
    if (sl && agent) {
      const q = boardQS();
      sl.href = "/agent/" + encodeURIComponent(agent) + q +
        (token ? (q ? "&" : "?") + "token=" + encodeURIComponent(token) : "");
    }
  }

  function classify(m) {
    if (m.from === "moderator") return "mod";
    if (m.from === agent) return "out";
    return "inc";
  }

  // The other party in a message (who you sent to / received from).
  function counterparty(m) {
    const c = classify(m);
    return c === "out" ? m.to : c === "mod" ? "moderator" : m.from;
  }

  function resultLabel(r) {
    return r === "win" ? "Win" : r === "loss" ? "Loss" : r === "tie" ? "Tie" : r === "left" ? "Left" : r;
  }

  // Aug 1 runs ONE scored window - no practice phase - so history has
  // nothing to differentiate: it is simply every hosted game you played.
  // (?board= scoping still works server-side for tooling; no tabs here.)
  async function loadCounts() {}
  function boardTabs() { return ""; }

  async function showList() {
    view = "list";
    try {
      const d = await pull("/matches/" + encodeURIComponent(agent) + boardQS());
      const list = d.matches || [];
      if (!list.length) {
        app.innerHTML = '<div class="empty">No matches yet - your games appear ' +
          'here the moment each one finishes.</div>';
        return;
      }
      let html = boardTabs() + '<div class="matches">';
      for (const m of list) {
        const res = m.result;
        html += '<button class="match ' + res + '" data-game="' + esc(m.game_id) + '">' +
          '<div class="row1"><span class="opp">vs' + oppChips(m.opponents) + '</span>' +
          '<span class="res ' + res + '">' + esc(resultLabel(res)) + '</span></div>' +
          '<div class="meta">' + esc(fmtTime(m.start_time)) +
          '<span class="pill">score ' + m.your_score + '</span>' +
          '<span class="pill">#' + m.rank + ' of ' + m.field + '</span>' +
          '<span class="pill">' + m.rounds + ' rounds</span>' +
          '<span class="pill">+' + m.breakdown.collected + ' collected</span>' +
          '<span class="pill">+' + m.breakdown.signed + ' signed</span>' +
          (m.breakdown.penalties ? '<span class="pill">−' + m.breakdown.penalties + ' penalties</span>' : '') +
          '</div></button>';
      }
      html += '</div>';
      app.innerHTML = html;
      app.querySelectorAll(".match").forEach(function (b) {
        b.onclick = function () { showDetail(b.getAttribute("data-game")); };
      });
    } catch (e) {
      if (e.message === "auth") { gate(); return; }
      app.innerHTML = '<div class="empty">Catching up with the server&hellip; ' +
        'this page will retry by itself.</div>';
      setTimeout(function () { if (view === "list") showList(); }, 4000);
    }
  }

  async function showDetail(gameId) {
    view = "detail";
    hDir = "all"; hWho = "all";
    try {
      const resp = await pull("/match/" + encodeURIComponent(gameId) + "/" + encodeURIComponent(agent) + boardQS());
      curDetail = resp.match || resp;   // endpoint wraps the detail under "match"
      renderDetail();
    } catch (e) {
      if (e.message === "auth") { gate(); return; }
      // A match that will not open sends the reader back to the list with a
      // calm note, never a dead-end.
      app.innerHTML = '<div class="empty">That match would not open - ' +
        'returning to your list&hellip;</div>';
      setTimeout(function () { showList(); }, 1800);
    }
  }

  function detailCounterparties(d) {
    const set = new Set();
    for (const r of (d.rounds || [])) for (const m of (r.messages || [])) {
      const cp = counterparty(m); if (cp && cp !== agent) set.add(cp);
    }
    return Array.from(set).sort();
  }

  function filterBar(d) {
    const chip = (v, lbl) => '<button class="fchip' + (hDir === v ? ' on' : '') +
      '" data-dir="' + v + '">' + lbl + '</button>';
    // "everyone", matching the identical control on the watch page - the two
    // filter the same thing and should not use different words for it.
    let opts = '<option value="all"' + (hWho === "all" ? " selected" : "") + '>everyone</option>';
    for (const cp of detailCounterparties(d))
      opts += '<option value="' + esc(cp) + '"' + (hWho === cp ? ' selected' : '') + '>' + esc(cp) + '</option>';
    return '<div class="filters">' + chip("all", "All") + chip("out", "Sent") +
      chip("inc", "Received") + chip("mod", "Moderator") +
      '<span class="fspace"></span>' +
      '<label class="flbl">with <select id="hwho" class="' + (hWho !== "all" ? "on" : "") + '">' +
      opts + '</select></label></div>';
  }

  function renderDetail() {
    const d = curDetail; if (!d) return;
    let html = '<div class="crumbs"><button class="navbtn" id="back" type="button">‹ All matches</button></div>';
    {
      const sc = d.cumulative_scores || {};
      const order = Object.keys(sc).sort(function (a, b) { return sc[b] - sc[a]; });
      const medals = ["", "", ""];
      let rows = "";
      for (let i = 0; i < order.length; i++) {
        const name = order[i];
        const rank = 1 + order.filter(function (x) { return sc[x] > sc[name]; }).length;
        const me = name === d.agent_id;
        rows += '<div class="sb-row' + (me ? ' you' : '') + '">' +
          '<span class="sb-rank">' + (medals[rank - 1] || ("#" + rank)) + '</span>' +
          '<span class="sb-name">' + esc(name) + (me ? ' <span class="sb-you">(you)</span>' : '') + '</span>' +
          '<span class="sb-score">' + sc[name] + '</span></div>';
      }
      html += '<div class="scorebar">' +
        '<div class="sb-head">Final standings' +
        (d.full ? ' <span class="pill">full transcript</span>' : ' <span class="pill">your view</span>') +
        '</div>' + rows + '</div>';
      html += filterBar(d);
      const anyFilter = (hDir !== "all" || hWho !== "all");
      const passes = function (m) {
        return (hDir === "all" || classify(m) === hDir) &&
               (hWho === "all" || counterparty(m) === hWho);
      };
      let shown = 0;
      // Newest first, both levels - the same order the live watch page uses.
      // The API stays chronological (it is the canonical record); only the view
      // is reversed, so reading a match here feels like the page you watched it
      // on rather than its mirror image.
      for (const r of (d.rounds || []).slice().reverse()) {
        const all = (r.messages || []).slice().reverse();
        const msgs = anyFilter ? all.filter(passes) : all;
        if (anyFilter && !msgs.length) continue;   // hide rounds with nothing matching
        html += '<div class="roundhdr">Round ' + esc(r.round_number) +
          (r.your_score != null ? ' <span class="pill">you ' + (r.your_score >= 0 ? "+" : "") + r.your_score + '</span>' : '') + '</div>';
        const asg = [];
        if (r.your_assigned_message) asg.push("Your message to get signed: “" + esc(r.your_assigned_message) + "”");
        if (r.your_request_list && r.your_request_list.length) asg.push("Request from: " + esc(r.your_request_list.join(", ")));
        if (r.your_sign_perms && r.your_sign_perms.length) asg.push("Authorized to sign for: " + esc(r.your_sign_perms.join(", ")));
        if (asg.length) html += '<div class="assign">' + asg.join(" &nbsp;|&nbsp; ") + '</div>';
        if (!msgs.length) { html += '<div class="empty">No messages this round.</div>'; continue; }
        html += '<div class="feed">';
        for (const m of msgs) {
          shown++;
          const c = classify(m);
          const tag = c === "mod" ? '<span class="tag mod">MODERATOR</span>'
            : c === "out" ? '<span class="tag out">SENT</span>' : '<span class="tag inc">RECEIVED</span>';
          const who = c === "out" ? ("to " + esc(m.to)) : ("from " + esc(m.from));
          const parts = splitSignature(m.body);
          let badge = "";
          if (parts.sig) {
            const pair = (parts.sig.signer && parts.sig.signed_for)
              ? (esc(parts.sig.signer) + " &rarr; " + esc(parts.sig.signed_for)) : "";
            const ttl = parts.sig.original ? (' data-tip="Signs: ' + esc(parts.sig.original) + '"') : "";
            badge = '<span class="sig"' + ttl + '>signed' + (pair ? " " + pair : "") + '</span>';
          }
          html += '<div class="msg ' + c + '"><div class="meta2">' + tag +
            '<span class="who">' + who + '</span>' + badge +
            '<span>' + esc(fmtTime(m.timestamp)) + '</span></div>' +
            '<div class="subj">' + esc(m.subject || "(no subject)") + '</div>' +
            '<div class="body">' + esc(clipSigs(parts.text || "")) + '</div></div>';
        }
        html += '</div>';
      }
      if (anyFilter && !shown) html += '<div class="empty">No messages match this filter.</div>';
      app.innerHTML = html;
      const back = document.getElementById("back");
      if (back) back.onclick = function (ev) { ev.preventDefault(); showList(); };
      app.querySelectorAll(".fchip").forEach(function (b) {
        b.onclick = function () { hDir = b.getAttribute("data-dir"); renderDetail(); };
      });
      const hw = document.getElementById("hwho");
      if (hw) hw.onchange = function () {
        hWho = hw.value;
        hw.classList.toggle("on", hWho !== "all");
        renderDetail();
      };
    }
  }

  { const bl = document.getElementById("boardlink"); if (bl) bl.href = boardUrl(); }
  if (agent && (token || LOCAL)) {
    if (token) { try { localStorage.setItem("emailgame_watch", JSON.stringify({ agent: agent, token: token })); } catch (e) {} }
    try {
      const u = new URL(location.href);
      if (u.searchParams.has("token")) { u.searchParams.delete("token"); u.searchParams.set("agent", agent); history.replaceState(null, "", u); }
    } catch (e) {}
    setLinks();
    refreshNavBoard();   // point the Leaderboard link at this competitor's board
    // ?game=<id> opens that match directly, so the stats page can link a row
    // straight to its transcript instead of making you find it in the list.
    // "< All matches" still returns to the full list from there.
    const deepGame = qs.get("game");
    if (deepGame) showDetail(deepGame); else showList();
    // Auto-refresh the list so newly finished matches appear without a reload.
    // Only while browsing the list, so it never clobbers a match you're reading.
    setInterval(function () { if (view === "list") showList(); }, 10000);
  } else {
    gate();
  }
})();
</script>
""" + BRAND_FOOTER + BRAND_TIP_JS + BRAND_MARGIN_FX + r"""
</body>
</html>"""
    return html.replace("/*__LOCAL__*/false", "true" if local else "false")

