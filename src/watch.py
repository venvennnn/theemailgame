from .tour import TOUR_CSS, TOUR_HTML, TOUR_LINK, tour_js
from .tutorial import TUTORIAL_CSS, TUTORIAL_HTML, tutorial_js
"""Competitor-facing live match viewer ("watch your game as it happens").

Renders a self-contained HTML+JS page served at /watch by the email server. It
shows ONLY the viewing agent's own perspective - the messages it received and
sent - which is exactly what its agent already has access to. It never exposes
other agents' private mail, so it is not a relay-cheating channel.

Security model: the page is static; the data it fetches is protected. The
browser calls /get_messages/{agent} and /get_sent/{agent} with the agent's own
JWT, and the server's _require_own_mailbox guard enforces that the token's
subject matches the requested agent. So a competitor can only ever watch their
own games, and only sees what their agent sees.
"""
from .brand import BRAND_OVERRIDE, BRANDBAR, BRAND_FOOTER, BRAND_TIP_JS, BRAND_MARGIN_FX



# Manual-only (auto=False below): the interactive walkthrough owns first
# contact on this page, so the tour never auto-runs - it opens from the
# "How this page works" button for whoever wants the reference card.
_WATCH_TOUR = [
    {"sel": "#app", "title": "Your side of the match",
     "body": "Every email your agent sends and receives, round by round, as it plays. You "
             "never see an opponent's private mail. Open this page from the link your "
             "agent prints on startup."},
    {"sel": "#wmap", "title": "The match map",
     "body": "Your live match at a glance: pulses are mail on the move, green flashes are "
             "signatures, and dashed links are the mail you cannot see. <b>Expand</b> it "
             "for the full view with names. Between matches it shows the queue instead."},
    # These only exist while a match is actually running. A step whose
    # target is missing is dropped, so between matches the tour skips them
    # rather than pointing at nothing.
    {"sel": ".bar", "title": "The status strip",
     "body": "<b>Match #</b> counts the games you have played today, <b>Round</b> is where "
             "this game is (three rounds per game), and <b>in / out</b> is how many emails "
             "you have received versus sent. It stays pinned as you scroll."},
    {"sel": ".filters", "title": "Filtering the feed",
     "body": "<b>All</b> / <b>Sent</b> / <b>Received</b> / <b>Moderator</b> narrow by "
             "direction - <b>Moderator</b> is the game's own mail to you, where your "
             "instructions arrive. The <b>with</b> dropdown narrows to your traffic with one "
             "chosen agent; <b>everyone</b> is its off position (no narrowing). Both combine, "
             "and the <b>with</b> pick resets when a new match starts, since the agents "
             "change."},
]

def render_watch_html(local: bool = False) -> str:
    """Return the standalone watch page (HTML + inline JS, no dependencies).

    ``local`` enables build-week local-testing mode: the page works with just
    ?agent=NAME and sends no token (the local server requires none)."""
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watch your match - The Email Game</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;1,500&family=Space+Grotesk:wght@600;700&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    background: var(--surface-2,#f4f6f9); color: var(--ink,#0a0a0b); margin: 0; padding: 1.5rem 1rem; }
  .wrap { max-width: 820px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  .sub { color: var(--muted,#6b7078); font-size: .9rem; margin: 0 0 1.25rem; }
  .bar { display: flex; gap: .4rem .45rem; flex-wrap: nowrap; align-items: center;
    box-sizing: border-box; justify-content: space-between; width: fit-content;
    /* A proper padded panel rather than a bare tinted strip: the pills used to
       sit flush against the top and bottom edges, which read as a stray grey
       band under the header instead of a container. */
    position: sticky; top: .5rem; z-index: 5; margin: 0 0 1.25rem;
    padding: .55rem .7rem; border-radius: 12px;
    background: rgba(255,255,255,.92); backdrop-filter: blur(6px);
    border: 1px solid var(--line,#e7e9ec); box-shadow: 0 1px 2px rgba(60,64,67,.06); }
  /* Chip type rides --chipfs so sizeBar() can step it down to guarantee the
     pills fit the card's fixed width; the ch-based floors scale with it. */
  .bar .chip { background:var(--surface-2,#f4f6f9); font-variant-numeric:tabular-nums;
    justify-content:center; font-size:var(--chipfs, .8rem); }
  .bar .c-mr  { min-width:18ch; }
  .bar .c-bud { font-family:"JetBrains Mono",ui-monospace,monospace;
    font-size:calc(var(--chipfs, .8rem) * .95); cursor:help; white-space:nowrap; }
  .bar .c-bud.low { color:var(--amber-ink,#7a5c00); font-weight:700; }
  .bar .c-tr  { min-width:22ch; }
  .bar .c-upd { min-width:9.5ch; cursor:help; }
  .btn { display: inline-block; background:var(--accent-blue,#2f54ff); color:#fff; text-decoration:none;
    border:none; border-radius:8px; padding:.55rem 1rem; font-size:.92rem; font-weight:600;
    cursor:pointer; }
  .btn:hover { background:#1666d0; }
  /* inline-flex, not inline-block + vertical-align:middle - "middle" aligns to
     the middle of the x-height, not the line, which sat the dot visibly low
     against the cap-height text next to it. */
  /* gap, not margin + literal spaces: a flex container drops the whitespace
     between its items, so "Watching <span>name</span>" rendered as one word
     once this became inline-flex. gap restores the spacing structurally. */
  .chip { display: inline-flex; align-items: center; gap: .3rem; background: #fff;
    border: 1px solid var(--line,#e7e9ec); border-radius: 8px;
    padding: .28rem .65rem; font-size: .8rem; font-weight: 600;
    box-shadow: 0 1px 2px rgba(60,64,67,.06); }
  .chip .dot { width:.55rem; height:.55rem; border-radius:50%; flex:none;
    background:var(--muted,#6b7078); }
  /* Same colour language as the leaderboard: BLUE (pulsing) = in a match,
     GREEN = connected and waiting for one. This chip used to show green while
     in a match, which said the opposite of the board's dot key. */
  /* Solid while in a match; the ring fires ONCE per new message rather than
     looping forever. A permanent pulse is decoration - it says the same thing
     every second, so you stop reading it. Firing on activity makes the dot mean
     "your agent just sent or received something", which is worth looking at. */
""" + TUTORIAL_CSS + r"""
  .chip.warn { background:var(--amber-bg); border-color:var(--amber-line); color:var(--amber-ink); }
  .chip.bad  { background:var(--red-bg);   border-color:var(--red-line);   color:var(--red-ink); }
  .chip.live .dot { background:#2f54ff; }
  .chip.wait .dot { background:#34a853; }
  .chip .dot.ping { animation:wpulse .9s ease-out 1; }
  @keyframes wpulse { 0%{box-shadow:0 0 0 0 rgba(47,84,255,.55);}
    70%{box-shadow:0 0 0 9px rgba(47,84,255,0);}
    100%{box-shadow:0 0 0 0 rgba(47,84,255,0);} }
  .chip .me { color:#2f54ff; max-width:16ch; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .gate { background:#fff; border:1px solid var(--line,#e7e9ec); border-radius:12px; padding:1.25rem 1.5rem;
    box-shadow:0 1px 2px rgba(60,64,67,.06); max-width:520px; }
  .gate h2 { font-size:1.05rem; margin:0 0 .4rem; }
  .gate p { margin:.4rem 0; font-size:.92rem; color:var(--ink-2,#3c3f45); }
  .gate code { background:var(--surface-2,#f4f6f9); padding:.1rem .35rem; border-radius:4px; font-size:.82rem;
    overflow-wrap:anywhere; }
  .hint { color:var(--muted,#6b7078); font-size:.8rem; margin-top:.6rem; line-height:1.5; }
  .feed { display:flex; flex-direction:column; gap:.5rem; }
  .msg { background:#fff; border:1px solid var(--line,#e7e9ec);
    border-radius:8px; padding:.6rem .8rem; box-shadow:0 1px 2px rgba(60,64,67,.05); }
  .msg.inc { border-color:var(--green-line,#bfe6cf); background:var(--green-bg,#e7f5ee); }
  .msg.out { border-color:var(--blue-line,#ccd6ff); background:var(--blue-bg,#eef1ff); }
  .msg.mod { border-color:var(--amber-line,#f0dca8); background:var(--amber-bg,#fff8e6); }
  .msg .meta { font-size:.78rem; color:var(--muted,#6b7078); display:flex; gap:.5rem; flex-wrap:wrap;
    margin-bottom:.25rem; }
  .msg .who { font-weight:700; color:var(--ink,#0a0a0b); max-width:28ch;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .msg .subj { font-weight:600; font-size:.92rem; margin-bottom:.15rem; }
  .msg .body { font-size:.88rem; white-space:pre-wrap; word-break:break-word; color:var(--ink-2,#3c3f45); }
  .tag { font-size:.72rem; font-weight:700; padding:.05rem .4rem; border-radius:4px; }
  .tag.inc { background:var(--green-bg,#e7f5ee); color:var(--green,#16a34a); }
  .tag.out { background:var(--blue-bg,#eef1ff); color:var(--accent-blue-d,#2546e6); }
  .tag.mod { background:var(--amber-bg,#fff8e6); color:var(--amber-ink,#7a5c00); }
  .sig { font-size:.72rem; font-weight:700; padding:.05rem .45rem; border-radius:4px;
    background:var(--green-bg,#e7f5ee); color:var(--green,#16a34a); border:1px solid var(--green-line,#bfe6cf); cursor:help; }
  .empty { color:var(--muted,#6b7078); text-align:center; padding:2rem; }
  .mm { text-align:center; padding:2rem 1rem; }
  .mm-big { font-size:1.15rem; font-weight:700; color:var(--ink,#0a0a0b); }
  .mm-sub { color:var(--muted,#6b7078); font-size:.9rem; margin-top:.4rem; }
  .mm-pips { display:inline-flex; gap:.4rem; margin-top:.7rem; }
  .mm-pips .pip { width:.7rem; height:.7rem; border-radius:50%; background:#d7dbe0; }
  .mm-pips .pip.on { background:#34a853; }
  .filters { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; margin:0 0 .8rem; }
  .fchip { background:#fff; border:1px solid var(--line-2,#d9dce0); border-radius:999px;
    padding:.3rem .8rem; font-size:.82rem; font-weight:600; color:var(--ink-2,#3c3f45); cursor:pointer; }
  .fchip.on { background:var(--accent-blue,#2f54ff); color:#fff; border-color:var(--accent-blue,#2f54ff); }
  /* Was flex:1, which shoved the "with <agent>" control against the right
     edge of the container - at common window widths it ended up clipped under
     the scrollbar, where its styling was impossible to see. It filters the same
     feed as the chips, so it belongs beside them. */
  .fspace { flex:0 0 .55rem; }
  .flbl { font-size:.82rem; color:var(--muted,#6b7078); }
  .filters select { font-size:.82rem; padding:.25rem .4rem; border:1px solid var(--line-2,#d9dce0); border-radius:6px; }
  .roundhdr { font-weight:700; font-size:.95rem; color:var(--ink,#0a0a0b); margin:1rem 0 .5rem;
    border-bottom:1px solid var(--line,#e7e9ec); padding-bottom:.3rem; }
  .roundhdr .rcount { font-weight:600; font-size:.78rem; color:var(--muted,#6b7078); }
  .result { background:var(--amber-bg,#fff8e6); border:1px solid var(--amber-line,#f0dca8); border-radius:10px;
    padding:.8rem 1rem; margin:0 0 .9rem; box-shadow:0 1px 2px rgba(60,64,67,.06); }
  .result-h { font-weight:700; color:var(--amber-ink,#7a5c00); margin-bottom:.5rem; }
  .result-b { font-size:.88rem; white-space:pre-wrap; word-break:break-word; color:var(--ink-2,#3c3f45); }
  .result .sb-row { display:flex; align-items:center; gap:.6rem; padding:.3rem .4rem; border-radius:6px; }
  .result .sb-row.you { background:var(--blue-bg,#eef1ff); }
  .result .sb-rank { width:2rem; text-align:center; font-weight:700; color:var(--amber-ink,#7a5c00); }
  .result .sb-name { flex:1; font-weight:600; color:var(--ink,#0a0a0b); min-width:0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .result .sb-score { font-weight:700; color:var(--accent-blue,#2f54ff); min-width:1.5rem; text-align:right; }
  .newmatch { background:var(--blue-bg,#eef1ff); border:1px solid var(--blue-line,#ccd6ff); color:var(--accent-blue-d,#2546e6);
    font-weight:700; text-align:center; padding:.6rem .9rem; border-radius:8px;
    margin:0 0 .6rem; animation:fadein .25s ease; }
  @keyframes fadein { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:none; } }
  /* Live match map: the tutorial's network, now showing the real match.
     A small picture-in-picture in the corner rather than a band in the flow -
     it keeps the match visible while you read the feed, and never costs
     reading room. Frosted card so it stays legible over scrolled text. */
  /* The column stays centred (so the margin ambience plays on BOTH sides);
     the map fits inside the right half-margin. */
  #wmap { position:fixed; right:20px; top:20px; z-index:40;
    width:min(clamp(280px, calc((100vw - 988px) / 2), 500px), 63vh);
    aspect-ratio: 10 / 7; height:auto;
    display:flex; flex-direction:column;
    background:rgba(255,255,255,.93); backdrop-filter:blur(8px);
    border:1px solid var(--line,#e7e9ec); border-radius:14px;
    box-shadow:0 16px 40px -22px rgba(20,26,48,.45); overflow:hidden;
    /* Expanding and minimizing glide; the canvas re-measures every frame,
       so the drawing rides along instead of snapping. */
    transition:width .35s ease, height .35s ease; }
  #wmap[hidden] { display:none; }
  /* The header holds exactly two things, in the SAME spots in mini and
     expanded: the state at top-left, the toggle at top-right. Both always
     show in full - the mini card is wide enough for the pair. */
  .wmap-hd { display:flex; align-items:center; gap:.5rem;
    padding:.4rem .65rem 0; font:600 10px "JetBrains Mono",ui-monospace,monospace;
    color:#6b7078; letter-spacing:.04em; text-transform:uppercase; flex:none;
    white-space:nowrap; }
  #wmap-state { flex:1 1 auto; text-align:left; min-width:max-content; }
  /* "your view" keeps to the card's bottom-right corner - also the same
     spot in both views - with a soft backing so it reads over the links. */
  #wmap-eye { position:absolute; right:.55rem; bottom:.4rem; z-index:2;
    font:600 10px "JetBrains Mono",ui-monospace,monospace; letter-spacing:.04em;
    color:#8a8f98; cursor:help; background:rgba(255,255,255,.72);
    border-radius:6px; padding:.05rem .3rem; }
  /* The note uses the shared BRAND_TIP_JS bubble (data-tip), so its tooltip
     is themed like every other on the site - no native balloon. */
  .wmap-hd .eye { cursor:help; color:#8a8f98; text-transform:none; flex:none; }
  .wmap-hd button.eye { background:none; border:0; font:inherit; cursor:pointer;
    padding:0 .1rem; font-size:12px; }
  /* Expanded: a real look at the match, names and all. A deliberate user
     action, so it may sit over the column; collapse restores the corner. */
  /* Width-only override: the base aspect-ratio keeps the expanded view
     proportional, and the 94vh term caps its height at ~66vh through
     that same ratio. */
  #wmap.max { width:min(660px, calc(100vw - 40px), 94vh); }
  #wmap canvas { width:100%; flex:1; min-height:0; display:block; }
  /* While a tour is open, its Skip button owns the top-right corner. */
  .tr-on #wmap { top:64px; }
  /* During a tour the map steps aside - translucent and click-through - so
     it never covers what a step is highlighting; it comes back solid for
     its own step, and fully once the tour ends. */
  .tr-on #wmap { opacity:.15; pointer-events:none; transition:opacity .25s ease; }
  html[data-tour-sel="#wmap"] #wmap { opacity:1; pointer-events:auto; }
  /* With the map on screen, the intro line stops short of it instead of
     running underneath. Zero once the margin holds the map on its own. */
  body:has(#wmap:not([hidden])) .sub {
    padding-right: max(0px, calc(316px - max(calc((100vw - 820px)/2), 16px))); }
  /* One constant card width, set inline by sizeBar(): the nav-pill extent,
     capped at the map's MEASURED left edge (CSS constants for the berth kept
     drifting a few px wide or tight across viewports/expansion). Chips step
     down via --chipfs until they fit. This rule is only the pre-measure
     fallback. */
  .bar { width:fit-content; max-width:100%; }

  /* Relocated into .navrow at boot: contents-level so the button itself is a
     flex peer of the nav pills (row gap applies), while [hidden] still hides. */
  #tut-replay-wrap { display:contents; }

  /* Scoring verdict badge: the point (or the reason there is none),
     pinned to the exact message that caused it. */
  /* Filled solid so a skimmer catches every point at a glance. */
  .vtag { font-size:.74rem; font-weight:800; padding:.12rem .5rem; border-radius:5px;
    letter-spacing:.01em; color:#fff; }
  .vtag.good { background:var(--green,#16a34a); }
  .vtag.bad { background:var(--red-ink,#c5221f); }
  .vtag.warn { background:#b45309; }
  .mini5 { max-width:420px; margin:1.1rem auto 0; background:#fff;
    border:1px solid var(--line,#e7e9ec); border-radius:12px; padding:.6rem .8rem;
    box-shadow:0 1px 2px rgba(60,64,67,.06); text-align:left; }
  .m5hd { font:600 10px "JetBrains Mono",ui-monospace,monospace; color:#6b7078;
    letter-spacing:.05em; text-transform:uppercase; margin-bottom:.35rem; }
  .m5row { display:flex; gap:.6rem; align-items:center; padding:.18rem .2rem;
    font-size:.85rem; }
  .m5row.you { background:var(--blue-bg,#eef1ff); border-radius:6px; font-weight:600; }
  .m5r { width:1.4rem; text-align:right; color:#6b7078;
    font-family:"JetBrains Mono",ui-monospace,monospace; }
  .m5n { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .m5e { font-weight:700; font-family:"JetBrains Mono",ui-monospace,monospace; }
  .err { background:var(--red-bg,#fdecea); border:1px solid var(--red-line,#f5c6c0); color:var(--red-ink,#c5221f); padding:.7rem .9rem;
    border-radius:8px; font-size:.9rem; margin:0 0 1rem; }
  a.plain { color:var(--accent-blue,#2f54ff); }
</style><style>""" + TOUR_CSS + r"""</style>""" + BRAND_OVERRIDE + r"""
</head>
<body>
<div class="wrap">
  """ + BRANDBAR + r"""
  <h1>Watch your match</h1>
  <div class="navrow">
    <a class="navbtn i-board" id="boardlink" href="/leaderboard">Leaderboard</a>
    <a class="navbtn i-hist" id="historylink" href="/history">Match history</a>
    <a class="navbtn i-stats" id="statslink" href="#">My stats</a>
    """ + TOUR_LINK + r"""</div>
  """ + TUTORIAL_HTML + r"""
  <div id="app"></div>
</div>
""" + TOUR_HTML + r"""
<script>""" + tour_js(_WATCH_TOUR, "watch", auto=False) + r"""</script>
""" + tutorial_js("agent") + r"""
<script>
(function () {
  // LOCAL mode (build-week local testing): the server isn't in competition mode,
  // so mailbox reads need no token. The page then works with just ?agent=NAME.
  const LOCAL = /*__LOCAL__*/false;
  const app = document.getElementById("app");
  const qs = new URLSearchParams(location.search);
  let agent = qs.get("agent") || "";
  let token = qs.get("token") || "";
  let timer = null;
  let cdTimer = null;       // 1s refresher for the matchmaking countdown
  const seen = new Map();   // message_id -> msg
  let curGame = null;       // game_id currently on screen
  let matchNum = 0;         // the agent's current match ordinal (server truth)
  let flashNewMatch = false; // show the "new match" banner on the next render only
  let everSawMatch = false; // distinguishes "first match" from "between matches"
  let seedReady = false;    // prior-play seed resolved: only then may the
                            // walkthrough auto-open (else a returning agent
                            // races the fetch and sometimes sees it again)
  let fDir = "all";          // feed filter: all | out (sent) | inc (received) | mod
  let fWho = "all";          // feed filter: counterparty agent id, or "all"
  let q = { waiting: false, len: 0, need: 4, grace: 3 };  // matchmaking status
  let foundAt = 0;           // ms timestamp when a match was found (pre-game countdown)
  let endedGameId = null;    // game_id of the match that just finished (awaiting its
                             // result from history)
  let lastResult = null;     // {gameId, scores} of the just-finished match, sourced
                             // from match history (durable), shown as a result card
                             // through the between-matches buffer until the next game.

  // The moderator's end-of-game summary. Not a round - kept out of the round feed
  // so it's never bucketed under "Round 1".
  function isGameOver(m) {
    return m && m.from === "moderator" &&
      /game over|final result/i.test((m.subject || "") + " " + (m.body || ""));
  }

  // Fetch the final standings of the just-ended game from history (the session
  // file is written before the live mail is purged, and /matches reads fresh, so
  // it's available; retry each tick until it appears, then it persists).
  async function refreshResult() {
    if (!endedGameId || (lastResult && lastResult.gameId === endedGameId)) return;
    try {
      var bq = q.navBoard ? "?board=" + encodeURIComponent(q.navBoard) : "";
      var d = await pull("/matches/" + encodeURIComponent(agent) + bq);
      var m = (d.matches || []).find(function (x) { return x.game_id === endedGameId; });
      if (m && m.scores) lastResult = { gameId: endedGameId, scores: m.scores };
    } catch (e) { /* try again next tick */ }
  }

  // Final-standings card (rank, name, score; you highlighted) from match scores.
  function resultCard(scores) {
    var names = Object.keys(scores).sort(function (a, b) { return scores[b] - scores[a]; });
    var rows = names.map(function (n) {
      var rank = 1 + names.filter(function (x) { return scores[x] > scores[n]; }).length;
      var me = n === agent;
      return '<div class="sb-row' + (me ? ' you' : '') + '"><span class="sb-rank">#' + rank +
        '</span><span class="sb-name">' + esc(n) + (me ? ' (you)' : '') +
        '</span><span class="sb-score">' + scores[n] + '</span></div>';
    }).join("");
    return '<div class="result"><div class="result-h">🏁 Game over - final result</div>' +
      rows + '</div>';
  }

  // The other party in a message (who you sent to / received from).
  function counterparty(m) {
    const c = classify(m);
    return c === "out" ? m.to : c === "mod" ? "moderator" : m.from;
  }

  // The match number is derived from the server (completed games + this one), so
  // it's the agent's true ordinal and survives navigating away and back, rather
  // than a count of what this browser tab happened to observe.
  async function fetchCompletedCount() {
    // Scope the count to the current board's window (match history is windowed),
    // else during build week this reads the empty competition window and the
    // match number never advances.
    var bq = q.navBoard ? "?board=" + encodeURIComponent(q.navBoard) : "";
    try { const d = await pull("/matches/" + encodeURIComponent(agent) + bq); return (d.matches || []).length; }
    catch (e) { return null; }
  }

  function loadStored() {
    try { return JSON.parse(localStorage.getItem("emailgame_watch") || "null"); }
    catch (e) { return null; }
  }

  // If the URL has no token (e.g. we scrubbed it after a prior open, or the user
  // reloaded), recover it from storage - but only for the same agent, so one
  // browser can't read another agent's stored token by changing ?agent=.
  if (!token) {
    const s = loadStored();
    if (s && s.token && (!agent || agent === s.agent)) { agent = s.agent; token = s.token; }
  }

  // One real leaderboard: the practice board is retired.
  function boardUrl() { return "/leaderboard"; }
  (function () { var el = document.getElementById("boardlink"); if (el) el.href = boardUrl(); })();

  // Watching is link-only: the watch URL your agent prints carries your id and a
  // read-only token. Players never type those by hand, so when there is no token
  // (or it stopped working) we show guidance - never an editable, pre-filled form
  // that looks like the player entered something wrong.
  function gate(state) {
    if (timer) { clearInterval(timer); timer = null; }
    if (LOCAL) {
      app.innerHTML =
        '<div class="gate"><h2>Local match view</h2>' +
        '<p>Add <code>?agent=NAME</code> to this URL to watch that agent’s local game ' +
        '(e.g. <code>/watch?agent=myagent</code>). No login needed when testing locally.</p>' +
        '<p class="hint">Your local run prints the exact link. ' +
        '</p><div class="navrow"><a class="navbtn i-board" href="/leaderboard">Local leaderboard</a></div></div>';
      return;
    }
    var heading, lead;
    if (state === "ended") {
      heading = "Watch session ended";
      lead = "This watch link is no longer live - your agent stopped, the match ended, " +
        "or the link expired. It is not something you typed wrong.";
    } else {
      heading = "Open your watch link to start";
      lead = "This page shows your own agent's match. There is nothing to type in.";
    }
    // If we have a stored identity, offer a one-click resume so the user never
    // touches the raw token. (Skipped on "ended": that token is known bad.)
    var resume = "";
    var s = state === "ended" ? null : loadStored();
    if (s && s.agent && s.token) {
      resume = '<p><a class="btn" id="g_resume" href="#">Watch ' + esc(s.agent) + '&rsquo;s match</a></p>';
    }
    app.innerHTML =
      '<div class="gate">' +
      '<h2>' + esc(heading) + '</h2>' +
      '<p>' + esc(lead) + '</p>' +
      resume +
      '<p class="hint">When your agent starts it prints a ready-to-click ' +
      '<strong>Watch your match</strong> link - open that to watch here. Once you have, a ' +
      '<strong>watch ›</strong> shortcut also appears on your row of the ' +
      '<a class="plain" href="/leaderboard">leaderboard</a>. ' +
      'The link prints again at the start of every match, so you never need to ' +
      'restart to get it back - and restarting mid-match forfeits that game.</p>' +
      '</div>';
    var btn = document.getElementById("g_resume");
    if (btn) btn.onclick = function (ev) {
      ev.preventDefault();
      var st = loadStored();
      if (st && st.agent && st.token) { agent = st.agent; token = st.token; start(); }
    };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function fmtTime(ts) {
    if (!ts) return "";
    try { return new Date(ts).toLocaleTimeString(); } catch (e) { return ts; }
  }

  // A genuine signature is appended to the body as "SIGNED_MESSAGE_JSON:{...}".
  // Split it off so the feed shows the readable text plus a compact "signed"
  // badge, instead of a screenful of raw base64. The original text is never
  // altered - we only separate the machine payload from the human prose.
  function splitSignature(body) {
    const s = String(body == null ? "" : body);
    const i = s.indexOf("SIGNED_MESSAGE_JSON:");
    if (i === -1) return { text: s, sig: null };
    const text = s.slice(0, i).replace(/\s+$/, "");
    let sig = { raw: true };
    try {
      const obj = JSON.parse(s.slice(i + "SIGNED_MESSAGE_JSON:".length).trim());
      sig = { signer: obj.signer, signed_for: obj.signed_for, original: obj.original_message };
    } catch (e) { /* malformed payload: still show a generic badge */ }
    return { text: text, sig: sig };
  }

  // Shorten long base64 signature blobs (e.g. in submission JSON) so the feed
  // stays readable; clearly marked as truncated.
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

  function classify(m) {
    if (m.from === "moderator") return "mod";
    if (m.from === agent) return "out";
    return "inc";
  }

  function renderMsg(m) {
    const c = classify(m);
    const tag = c === "mod" ? '<span class="tag mod">MODERATOR</span>'
      : c === "out" ? '<span class="tag out">SENT</span>'
      : '<span class="tag inc">RECEIVED</span>';
    const who = c === "out" ? ("to " + esc(m.to)) : ("from " + esc(m.from));
    const parts = splitSignature(m.body);
    let badge = "";
    if (parts.sig) {
      const pair = (parts.sig.signer && parts.sig.signed_for)
        ? (esc(parts.sig.signer) + " &rarr; " + esc(parts.sig.signed_for)) : "";
      const ttl = parts.sig.original ? (' data-tip="Signs: ' + esc(parts.sig.original) + '"') : "";
      badge = '<span class="sig"' + ttl + '>signed' + (pair ? " " + pair : "") + '</span>';
    }
    const v = m.verdict;
    const vtag = v ? '<span class="vtag ' + esc(v.cls || "warn") +
      '" data-tip="' + esc(v.tip || "Scoring verdict for this message.") + '">' +
      esc(v.label || "") + '</span>' : "";
    return '<div class="msg ' + c + '">' +
      '<div class="meta">' + tag + '<span class="who">' + who + '</span>' + badge + vtag +
      '<span>' + esc(fmtTime(m.timestamp)) + '</span></div>' +
      '<div class="subj">' + esc(m.subject || "(no subject)") + '</div>' +
      '<div class="body">' + esc(clipSigs(parts.text || "")) + '</div></div>';
  }

  // Tag every message (chronological) with the round it belongs to, from the
  // latest moderator "ROUND N" marker. Done on the full set so round detection
  // still works even when the moderator messages are filtered out of the view.
  function tagRounds(ascMsgs) {
    let cur = 1;
    for (const m of ascMsgs) {
      if (m.from === "moderator") {
        const mt = /\bROUND\s+(\d+)/i.exec((m.subject || "") + " " + (m.body || ""));
        if (mt) cur = parseInt(mt[1], 10);
      }
      m._round = cur;
    }
  }

  // The view is split into three persistent containers so the filter dropdown is
  // NOT destroyed on every message refresh: the status bar and feed re-render each
  // tick, but the filter bar (with the open-able select) is only rebuilt when its
  // own state changes (direction, between-state, or the set of counterparties).
  let lastFilterSig = null;
  let lastMsgCount = 0;      // for the activity pulse: only fire when it grows
  let lastPointTs = 0;       // newest score-event timestamp already shown
  let pendingPoints = [];    // point changes waiting to be floated off the dot
  // The toggle's two faces are the same drawing with the corners flipped:
  // pointing out to expand, folded in to minimize.
  var MAP_ICO = {
    expand: '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><path d="M7.8 1.5h2.7v2.7M10.5 7.8v2.7H7.8M4.2 10.5H1.5V7.8M1.5 4.2V1.5h2.7"/></svg> expand',
    min: '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><path d="M7.8 1.5v2.7h2.7M10.5 7.8H7.8v2.7M4.2 10.5V7.8H1.5M1.5 4.2h2.7V1.5"/></svg> minimize'
  };
  function ensureLayout() {
    if (document.getElementById("wfeed")) return;
    app.innerHTML = '<div id="wbar"></div><div id="wmap" hidden>' +
      '<div class="wmap-hd"><span id="wmap-state">match</span>' +
      '<button class="eye" id="wmap-x" type="button" aria-label="Expand the match map">' + MAP_ICO.expand + '</button></div>' +
      '<canvas id="wmap-cv"></canvas>' +
      '<span id="wmap-eye" tabindex="0" data-tip="Your agent\'s own view: only its ' +
      'sent and received mail. The other agents\' mail is private to them - ' +
      'that\'s why their links stay dashed.">your view &#9432;</span></div>' +
      '<div id="wfilters"></div><div id="wfeed"></div>';
    lastFilterSig = null;   // force the filter bar to rebuild into the fresh layout
  }

  // Float a +1 / -1 off the live indicator, the way the landing hero pops
  // scores off its nodes.
  //
  // Every point in a round IS awarded at the same instant - the round runs for a
  // fixed time and then scores once - so these always arrive as a batch. Rather
  // than pretend otherwise, the batch is dealt out one pop at a time with a
  // little horizontal scatter, so six points read as six events instead of one
  // stacked smudge. The spread shrinks as the batch grows, so a big round stays
  // within a couple of seconds instead of trickling past the next one.
  function floatPoints(events) {
    var dot = document.querySelector(".bar .chip .dot");
    if (!dot || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // Spread wide enough that each pop is read as its own event. The window
    // stretches with the batch but the per-pop gap never drops below ~260ms,
    // which is about the point where they stop registering separately.
    var step = Math.max(260, Math.min(550, 3000 / Math.max(1, events.length)));
    events.forEach(function (e, n) {
      setTimeout(function () {
        var r = dot.getBoundingClientRect();
        if (!r.width) return;                       // indicator gone (match ended)
        var el = document.createElement("span");
        el.className = "ptfloat " + (e.delta > 0 ? "up" : "down");
        el.textContent = (e.delta > 0 ? "+" : "") + e.delta;
        el.title = e.reason || "";
        // Alternate left/right of the dot, widening slightly, so simultaneous
        // pops never overlap even when the stagger is short.
        // Always to the RIGHT of the dot: the dot sits at the card's left
        // edge, so leftward pops clipped against the card border.
        var spread = 6 + n * 7;
        el.style.left = (r.left + r.width / 2 + spread) + "px";
        el.style.top = r.top + "px";
        document.body.appendChild(el);
        el.addEventListener("animationend", function () { el.remove(); });
      }, n * step);
    });
  }

  function buildStatus(between, round, all, inc, out) {
    var liveClass = between ? "chip wait" : "chip live";
    // No agent name while waiting: nothing is being watched yet, so trailing
    // ": name" read as a stray fragment. The name belongs on the "Watching"
    // chip, where it says whose match you are looking at.
    var s = between
      ? '<span class="' + liveClass + '"><span class="dot"></span>' +
        (everSawMatch ? "Between matches" : "Waiting for your first match") + '</span>'
      : '<span class="' + liveClass + '"><span class="dot"></span>Watching<span class="me">' + esc(agent) + '</span></span>';
    if (!between) {
      s += '<span class="chip c-mr">Match #' + matchNum + ' &middot; Round ' + esc(round) + '</span>' +
        '<span class="chip c-tr">' + all.length + ' msgs &middot; ' + inc + ' in / ' + out + ' out</span>';
    } else if (everSawMatch && matchNum > 0) {
      s += '<span class="chip">' + matchNum + ' match' + (matchNum === 1 ? '' : 'es') + ' played</span>';
    }
    return s + budgetChip() + '<span class="chip c-upd" data-tip="Last updated">' +
      new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) + '</span>';
  }

  // Your remaining LLM budget. Previously only ever printed to the terminal, so
  // anyone who let an assistant run the commands never saw it - and a spent or
  // blocked key first showed up as the agent mysteriously failing.
  var budget = null, budgetTimer = null;
  function budgetChip() {
    if (!budget || !budget.available) return "";
    if (budget.blocked) {
      return '<span class="chip bad" data-tip="The gateway is refusing this key. ' +
             'Your agent can still join games, but every LLM call will fail. ' +
             'Contact the organizers.">key blocked</span>';
    }
    // Only ever the EXACT remaining balance on the dealt key - no estimates.
    if (budget.max_budget == null) return "";
    var left = budget.left, cap = budget.max_budget;
    var low = left < 0.15 * cap;
    return '<span class="chip c-bud' + (low ? ' low' : '') + '" data-tip="Your issued key: $' +
      Number(budget.spend).toFixed(2) + ' of $' + Number(cap).toFixed(2) +
      ' used. It funds every LLM call this key makes - locally that is all ' +
      'four agents in each match, from the same pool as official play' +
      (low ? ' - running low' : '') + '.">$' +
      Number(left).toFixed(2) + ' left</span>';
  }
  function refreshBudget() {
    if (!agent) return;
    pull("/budget/" + encodeURIComponent(agent))
      .then(function (d) { if (d) budget = d; })
      .catch(function () {});   // never let a status chip break the feed
  }

  function buildFilters(whoSet) {
    function dchip(val, label) {
      return '<button class="fchip' + (fDir === val ? ' on' : '') + '" data-dir="' + val + '">' + label + '</button>';
    }
    var whoOpts = '<option value="all"' + (fWho === "all" ? " selected" : "") + '>everyone</option>';
    for (var i = 0; i < whoSet.length; i++) {
      var wname = whoSet[i];
      whoOpts += '<option value="' + esc(wname) + '"' + (fWho === wname ? " selected" : "") + '>' + esc(wname) + '</option>';
    }
    return dchip("all", "All") + dchip("out", "Sent") + dchip("inc", "Received") + dchip("mod", "Moderator") +
      '<span class="fspace"></span>' +
      '<label class="flbl">with <select id="fwho" class="' + (fWho !== "all" ? "on" : "") + '">' + whoOpts + '</select></label>';
  }

  // Between matches: show the matchmaking phase (finding a match -> match found
  // -> starting countdown) so the wait is legible, not a blank "waiting".
  function matchmakingPanel() {
    var foundElapsed = foundAt ? (Date.now() - foundAt) / 1000 : 999;
    if (foundAt && foundElapsed < q.grace + 8) {
      var left = Math.ceil(q.grace - foundElapsed);
      var cd = left > 0 ? ("Starting in " + left + "s") : "Starting your match...";
      return '<div class="mm"><div class="mm-big">Match found</div>' +
        '<div class="mm-sub">' + cd + '</div></div>';
    }
    if (q.waiting) {
      var ready = Math.min(q.len, q.need);
      var dots = '<span class="mm-pips">';
      for (var i = 0; i < q.need; i++) dots += '<span class="pip' + (i < ready ? ' on' : '') + '"></span>';
      dots += '</span>';
      var note = q.len >= q.need ? "Match forming..." : (q.len + " of " + q.need + " players ready");
      return '<div class="mm"><div class="mm-big">Finding a match</div>' + dots +
        '<div class="mm-sub">' + note + '</div></div>';
    }
    return '<div class="empty">' +
      (everSawMatch
        ? 'That match has ended. Waiting for your next match to start; it will appear here automatically.'
        : 'No match yet. When your first game starts, it will appear here live.') +
      '<div id="mini5"></div>' +
      '</div>';
  }

  function wireFilters() {
    document.querySelectorAll("#wfilters .fchip").forEach(function (b) {
      b.onclick = function () { fDir = b.getAttribute("data-dir"); render(); };
    });
    var fw = document.getElementById("fwho");
    // Toggle the active look immediately: the filter bar is only rebuilt when
    // its signature changes (which excludes this value, so an open dropdown
    // survives polling), so the class has to be set here.
    if (fw) fw.onchange = function () {
      fWho = fw.value;
      fw.classList.toggle("on", fWho !== "all");
      render();
    };
  }

  // The status card is exactly as wide as the nav pill row above it, so the
  // two read as one aligned block. Falls back to fit-content if unmeasurable.
  function sizeBar() {
    const b = document.querySelector("#wbar .bar"), nr = document.querySelector(".navrow");
    if (!b || !nr) return;
    // The nav row is a full-width block; what we mirror is how far its PILLS
    // actually stretch - the span from the first pill to the last visible one.
    let left = Infinity, right = -Infinity;
    // A display:contents wrapper (the relocated replay pill) has no box of
    // its own - measure its children instead of skipping it.
    function span(k) {
      if (!k.offsetWidth) { Array.prototype.forEach.call(k.children, span); return; }
      const r = k.getBoundingClientRect();
      if (r.left < left) left = r.left;
      if (r.right > right) right = r.right;
    }
    Array.prototype.forEach.call(nr.children, span);
    const w = right - left;
    // Hard cap: the card stops 16px short of the corner map's REAL left edge
    // (measured, so mini vs expanded vs any viewport all resolve exactly -
    // CSS constants kept drifting a few px on one side or the other).
    let cap = Infinity;
    const wm = document.getElementById("wmap");
    if (wm && !wm.hidden) {
      const room = wm.getBoundingClientRect().left - 16 - b.getBoundingClientRect().left;
      if (room > 120) cap = room;
    }
    if (!(w > 50 && isFinite(w))) return;
    // ONE constant width - the nav-pill extent (bounded by the map's edge).
    // The card never grows or shrinks with its contents.
    b.style.width = Math.min(w, cap) + "px";
    b.style.minWidth = "";
    b.style.maxWidth = "";
    // The chips must then FIT that width: step the chip type down a notch at
    // a time (rarely needed - a narrow window or a long agent name) so the
    // pills always fit entirely with no wrap and no overflow.
    b.style.removeProperty("--chipfs");
    let fs = 0.8, guard = 8;
    while (b.scrollWidth > b.clientWidth + 1 && guard-- > 0) {
      fs -= 0.03;
      b.style.setProperty("--chipfs", fs.toFixed(2) + "rem");
    }
  }
  try {
    const ro = new ResizeObserver(function () { sizeBar(); });
    ro.observe(document.querySelector(".navrow"));
    const wmEl = document.getElementById("wmap");
    if (wmEl) {
      ro.observe(wmEl);   // mini<->expanded transitions re-cap the card live
      wmEl.addEventListener("transitionend", sizeBar);
    }
  } catch (e) {}
  window.addEventListener("resize", sizeBar);
  function render() {
    const rawAsc = Array.from(seen.values()).sort((a, b) =>
      String(a.timestamp || "").localeCompare(String(b.timestamp || "")));
    tagRounds(rawAsc);
    // The Game Over summary is a result, not a round - keep it out of the round
    // feed (otherwise, once the round mail is purged, it gets mislabeled Round 1).
    const asc = rawAsc.filter(m => !isGameOver(m));
    const all = asc.slice().reverse(); // newest first (for counts)
    const round = asc.length ? asc[asc.length - 1]._round : "-";
    const inc = all.filter(m => classify(m) === "inc").length;
    const out = all.filter(m => classify(m) === "out").length;
    const whoSet = Array.from(new Set(asc.map(counterparty))).filter(Boolean).sort();
    if (fWho !== "all" && whoSet.length && whoSet.indexOf(fWho) === -1) fWho = "all";
    const shown = asc.filter(m =>
      (fDir === "all" || classify(m) === fDir) &&
      (fWho === "all" || counterparty(m) === fWho));
    const between = !all.length;

    ensureLayout();
    // The welcome animation fills the queue wait and yields the moment a real
    // match exists - a scored round must never be competing with a tutorial.
    // A SCORED match always wins the screen. The local sandbox is different:
    // its game starts within seconds, which used to kill the first-run
    // walkthrough almost immediately - and local play is most competitors'
    // first contact, exactly where the walkthrough matters most. Locally the
    // intro runs to its end; the match is waiting right behind it.
    if (all.length && !LOCAL && !window.__tutUserRun) { if (window.tutorialStop) window.tutorialStop(); }
    else if (seedReady && (!everSawMatch || LOCAL) && window.tutorialMaybeStart) {
      // Wait for the shared first-run state (seen-on-the-other-site check)
      // before deciding; its 1.5s timeout keeps a true first visit instant.
      if (window.EG_FIRSTRUN) window.EG_FIRSTRUN.ready(function () { window.tutorialMaybeStart(); });
      else window.tutorialMaybeStart();
    }
    document.getElementById("wbar").innerHTML = '<div class="bar">' + buildStatus(between, round, all, inc, out) + '</div>';
    sizeBar();
    if (between && agent) refreshMini5();
    // Pulse the dot when the message count actually grew. The bar is rebuilt on
    // every tick, so the class has to be applied after that rebuild; the
    // animation is one-shot and the element is fresh each time, so there is
    // nothing to reset.
    if (all.length > lastMsgCount) {
      var pd = document.querySelector(".bar .chip .dot");
      if (pd) pd.classList.add("ping");
    }
    lastMsgCount = all.length;
    if (pendingPoints.length) { floatPoints(pendingPoints); pendingPoints = []; }

    // Rebuild the filter bar ONLY when its state changes (so an open dropdown
    // survives ordinary message updates). Signature = between + direction + whos.
    var sig = between ? "between" : (fDir + "||" + whoSet.join(","));
    var fbar = document.getElementById("wfilters");
    if (sig !== lastFilterSig) {
      fbar.innerHTML = between ? "" : '<div class="filters">' + buildFilters(whoSet) + '</div>';
      wireFilters();
      lastFilterSig = sig;
    }

    // Feed (every tick).
    var feed = "";
    // Final-standings card for the just-finished match, shown through the
    // between-matches buffer so the result is reliably visible until the next game.
    if (lastResult && lastResult.scores) {
      feed += resultCard(lastResult.scores);
    }
    if (between) {
      feed += matchmakingPanel();
    } else {
      if (flashNewMatch) {
        feed += '<div class="newmatch">New match started (Match #' + matchNum + ')</div>';
        flashNewMatch = false;
      }
      const byRound = new Map();
      for (const m of shown) {
        if (!byRound.has(m._round)) byRound.set(m._round, []);
        byRound.get(m._round).push(m);
      }
      const roundNums = Array.from(byRound.keys()).sort((a, b) => b - a);
      if (!shown.length) feed += '<div class="empty">No messages match this filter.</div>';
      for (const rn of roundNums) {
        const msgs = byRound.get(rn).slice().reverse();
        feed += '<div class="roundhdr">Round ' + esc(rn) +
          ' <span class="rcount">' + msgs.length + ' msg' + (msgs.length === 1 ? '' : 's') + '</span></div>';
        feed += '<div class="feed">';
        for (const m of msgs) feed += renderMsg(m);
        feed += '</div>';
      }
    }
    document.getElementById("wfeed").innerHTML = feed;
  }

  // The live match map. Same visual grammar as the intro animation - flat
  // discs, white name chips, comet pulses flying rim to rim - but the discs
  // are THIS match's real agents and every pulse is a real email from the
  // feed below. The intro teaches the language; this is the language used.
  const matchMap = (function () {
    const AMBER = "245,158,11", BLUE = "47,84,255", INK = "20,26,48";
    const COLS = ["10,125,68", "147,52,230", "180,83,9"];
    // The tutorial cast's own fractions (NODES / board), so when the intro's
    // stage lands on this rect the seats are where the cast's discs are.
    const SLOTS = [{ x: .112, y: .436 }, { x: .312, y: .75 }, { x: .688, y: .75 }, { x: .888, y: .436 }];
    const MODP = { x: .5, y: .18 };
    let cv = null, ctx = null, names = [], me = null, pulses = [], bumps = {}, raf = 0;
    let scorePops = [], offline = {}, queueing = false;
    function isMax() {
      const el = document.getElementById("wmap");
      return !!(el && el.classList.contains("max"));
    }
    function ensure() {
      if (cv && cv.isConnected) return true;
      cv = document.getElementById("wmap-cv");
      ctx = cv ? cv.getContext("2d") : null;
      return !!ctx;
    }
    function reset() { names = []; pulses = []; bumps = {}; scorePops = []; }
    function setState(match, round) {
      queueing = false;
      const el = document.getElementById("wmap-state");
      if (el) el.textContent = "match #" + match + (round ? " \u00b7 round " + round + "/3" : "");
    }
    function setQueue(len, need, you) {
      queueing = true; me = you;
      const el = document.getElementById("wmap-state");
      if (el) el.textContent = "matchmaking \u00b7 " + Math.min(len, need) + "/" + need + " ready";
      start();
    }
    function setOnline(connected) {
      // A disc goes grey the moment its agent drops off the server; the
      // server gives a reconnect grace before removing it from the match, so
      // grey means "in trouble", gone means gone.
      offline = {};
      if (!connected) return;
      names.forEach(function (n) { if (connected.indexOf(n) < 0) offline[n] = true; });
    }
    function score(delta) {
      scorePops.push({ t0: performance.now(), txt: (delta > 0 ? "+" : "") + delta,
                       col: delta > 0 ? "10,125,68" : "220,38,38" });
      start();
    }
    function setParticipants(list, you) {
      me = you;
      const others = list.filter(function (n) { return n && n !== you && n !== "moderator"; }).sort();
      const want = [you].concat(others).slice(0, 4);
      if (want.join("|") !== names.join("|")) names = want;
    }
    function ghost(w, h, k, r, now) {
      const sl = SLOTS[k];
      return { x: w * sl.x + Math.sin(now * .5 + k * 2.1) * 2,
               y: Math.min(h * sl.y, h - r - 23 - 3) + Math.cos(now * .43 + k * 1.3) * 1.5,
               r: r, ghost: true, ini: "?", col: "154,160,166" };
    }
    function layout(w, h) {
      // Sized so the WHOLE drawing fits the widget. The mini view hides the
      // name labels (initials carry identity) so the discs get the room; the
      // expanded view restores them.
      const maxed = isMax();
      const CHIP = maxed ? 18 : 4, GAP = maxed ? 9 : 4;
      const r = Math.max(10, Math.min(w * .07, h * .14, h * .22 - CHIP - GAP));
      const now = performance.now() / 1000;
      const p = { moderator: { x: w * MODP.x, y: Math.max(h * MODP.y, r + 4), r: r, col: AMBER, ini: "M" } };
      const cast = queueing ? (me ? [me] : []) : names;
      cast.forEach(function (n, k) {
        const sl = SLOTS[k] || SLOTS[3];
        p[n] = { x: w * sl.x + Math.sin(now * .5 + k * 2.1) * 2,
                 y: Math.min(h * sl.y, h - r - CHIP - GAP - 3) + Math.cos(now * .43 + k * 1.3) * 1.5,
                 r: r, col: n === me ? BLUE : COLS[(k + 2) % 3],
                 ini: (n.charAt(0) || "?").toUpperCase(),
                 mine: n === me };
      });
      // In the queue the other three seats are drawn empty: the map says
      // "this is the table, it is waiting for players", and the ghosts turn
      // into the real opponents the moment the match fills them.
      if (queueing) for (let k = cast.length; k < 4; k++) p["\u2205" + k] = ghost(w, h, k, r, now);
      return p;
    }
    function pulse(from, to, kind) {
      if (!from || !to) return;
      pulses.push({ from: from, to: to, kind: kind, t0: performance.now() });
      start();
    }
    function colour(kind) { return kind === "sign" ? "10,125,68" : kind === "bad" ? "220,38,38" : "6,182,212"; }
    function draw() {
      raf = 0;
      const box = document.getElementById("wmap");
      if (!ensure() || !box || box.hidden) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const p = layout(w, h);
      const ids = Object.keys(p);
      // Links THIS agent is on are solid: that is where its mail can fly.
      // Every other link is dashed and quieter, because the traffic on it is
      // private to those agents - the dashes ARE the fog of war.
      for (let a = 0; a < ids.length; a++) for (let b = a + 1; b < ids.length; b++) {
        const mine = ids[a] === me || ids[b] === me;
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = mine ? "rgba(47,84,255,.20)" : "rgba(47,84,255,.09)";
        ctx.setLineDash(mine ? [] : [4, 5]);
        ctx.beginPath(); ctx.moveTo(p[ids[a]].x, p[ids[a]].y); ctx.lineTo(p[ids[b]].x, p[ids[b]].y); ctx.stroke();
      }
      ctx.setLineDash([]);
      const now = performance.now();
      pulses = pulses.filter(function (s) {
        const a = p[s.from], b = p[s.to];
        if (!a || !b) return false;
        const dd = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const dur = Math.max(500, dd * 1.7);
        const k = (now - s.t0) / dur;
        if (k >= 1) { bumps[s.to] = now; return false; }
        if (k <= 0) return true;
        const ux = (b.x - a.x) / dd, uy = (b.y - a.y) / dd;
        const sx = a.x + ux * (a.r + 2), sy = a.y + uy * (a.r + 2);
        const ex = b.x - ux * (b.r + 2), ey = b.y - uy * (b.r + 2);
        const x = sx + (ex - sx) * k, y = sy + (ey - sy) * k;
        const col = colour(s.kind), sh = k > .88 ? (1 - k) / .12 : 1;
        const lag = Math.max(0, k - .08);
        ctx.strokeStyle = "rgba(" + col + ",.45)"; ctx.lineWidth = 2.5; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(sx + (ex - sx) * lag, sy + (ey - sy) * lag); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = "rgba(" + col + ",.16)";
        ctx.beginPath(); ctx.arc(x, y, 9 * sh, 0, 7); ctx.fill();
        ctx.fillStyle = "rgb(" + col + ")";
        ctx.beginPath(); ctx.arc(x, y, 4 * sh, 0, 7); ctx.fill();
        return true;
      });
      // Your score changes rise off your own disc, the way the tutorial and
      // the landing hero pop points. Only yours: rivals' scores are hidden.
      scorePops = scorePops.filter(function (o) {
        const you = p[me];
        if (!you) return false;
        const k = (now - o.t0) / 1400;
        if (k >= 1) return false;
        ctx.font = '700 15px "Space Grotesk", Inter, sans-serif';
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(" + o.col + "," + (1 - k) + ")";
        ctx.fillText(o.txt, you.x, you.y - you.r - 10 - k * 24);
        return true;
      });
      ids.forEach(function (id) {
        const n = p[id];
        let R = n.r;
        if (bumps[id]) {
          const bk = (now - bumps[id]) / 380;
          if (bk < 1) R *= 1 + Math.sin(bk * Math.PI) * .07; else delete bumps[id];
        }
        const gone = !!offline[id];
        const maxed = isMax();
        if (n.ghost) {
          ctx.beginPath(); ctx.arc(n.x, n.y, R, 0, 7);
          ctx.fillStyle = "rgba(255,255,255,.7)"; ctx.fill();
          ctx.setLineDash([5, 5]); ctx.lineWidth = 1.5;
          ctx.strokeStyle = "rgba(154,160,166,.8)"; ctx.stroke(); ctx.setLineDash([]);
          ctx.font = '700 ' + Math.round(R * .74) + 'px "Space Grotesk", Inter, sans-serif';
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillStyle = "rgba(154,160,166,.9)"; ctx.fillText("?", n.x, n.y + 1);
        } else {
        ctx.globalAlpha = gone ? .4 : 1;
        ctx.beginPath(); ctx.arc(n.x, n.y, R, 0, 7);
        ctx.fillStyle = gone ? "rgb(154,160,166)" : "rgb(" + n.col + ")"; ctx.fill();
        ctx.lineWidth = 2.5; ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke();
        ctx.font = '700 ' + Math.round(R * .74) + 'px "Space Grotesk", Inter, sans-serif';
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = "#fff"; ctx.fillText(n.ini, n.x, n.y + 1);
        ctx.globalAlpha = 1;
        }
        if (!maxed) return;   // mini view: initials only, no labels
        // Plain text label - no pill - truncated with a real ellipsis and
        // capped by the spacing to the NEIGHBOURING seat, so two labels can
        // never overlap; clamped into the widget.
        let label = n.ghost ? "waiting" : (id === "moderator" ? "moderator" : id);
        if (gone) label += " \u00b7 offline";
        ctx.font = "600 10px Inter, system-ui, sans-serif";
        const maxTw = id === "moderator" ? w * .3 : w * .205;
        if (ctx.measureText(label).width + 6 > maxTw) {
          while (label.length > 2 &&
                 ctx.measureText(label + "\u2026").width + 6 > maxTw) {
            label = label.slice(0, -1);
          }
          label += "\u2026";
        }
        const tw = ctx.measureText(label).width;
        const lx = Math.max(3 + tw / 2, Math.min(w - 3 - tw / 2, n.x));
        ctx.font = "600 10px Inter, system-ui, sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = n.ghost ? "rgba(154,160,166,.9)"
          : (n.mine ? "rgb(" + BLUE + ")" : "rgba(" + INK + ",.82)");
        ctx.fillText(label, lx, n.y + R + 10);
      });
      start();
    }
    function start() { if (!raf) raf = requestAnimationFrame(draw); }
    return { reset: reset, setParticipants: setParticipants, pulse: pulse,
             start: start, setState: setState, score: score, setOnline: setOnline,
             setQueue: setQueue };
  })();
  // The handover between the intro and the corner map, both directions.
  // Seamless means ONE motion, not two: the tutorial's stage flies to the
  // widget's measured rect while the widget flies the exact inverse - from
  // window-centre at window scale down into its corner - with the same
  // duration and curves. Computed from live geometry, never hardcoded.
  // The widget itself never animates. During flights it is simply dark or
  // lit: dark from the moment the tutorial's discs lift out of its seats
  // (WillOpen), lit again before the overlay's final fade reveals it with
  // the discs already sitting on its seats (WillClose). The tutorial's
  // canvas does ALL the motion; this only manages who owns the pixels.
  function mapLit(on) {
    const el = document.getElementById("wmap");
    if (!el) return;
    // Snap the opacity with transitions suspended, then hand the stylesheet
    // back its transitions (the expand/minimize glide) once applied.
    el.style.transition = "none";
    el.style.opacity = on ? "" : "0";
    void el.offsetWidth;
    el.style.transition = "";
  }
  function setMapMax(on) {
    const el = document.getElementById("wmap");
    if (!el) return;
    el.classList.toggle("max", on);
    const b = document.getElementById("wmap-x");
    if (b) b.innerHTML = on ? MAP_ICO.min : MAP_ICO.expand;
    matchMap.start();
  }
  document.addEventListener("click", function (ev) {
    const b = ev.target && ev.target.closest ? ev.target.closest("#wmap-x") : null;
    if (!b) return;
    const el = document.getElementById("wmap");
    if (!el) return;
    setMapMax(!el.classList.contains("max"));
  });
  // Esc also minimizes - the expanded map behaves like any overlay.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    const el = document.getElementById("wmap");
    if (el && el.classList.contains("max")) setMapMax(false);
  });
  // While a tour runs the map yields the screen: it minimizes for the
  // duration and returns to whatever expansion state it was in before.
  let tourHadMax = false;
  window.egTourWillStart = function () {
    const el = document.getElementById("wmap");
    tourHadMax = !!(el && el.classList.contains("max"));
    if (tourHadMax) setMapMax(false);
  };
  window.egTourDidStop = function () {
    if (tourHadMax) setMapMax(true);
    tourHadMax = false;
  };
  window.tutorialWillOpen = function () { mapLit(false); };
  window.tutorialWillClose = function () { mapLit(true); };
  window.tutorialDidClose = function () { mapLit(true); };
  let mapGame = null, mapBooted = false;
  const mapIds = new Set();

  // Between matches the wait is dead air - fill it with the live top of
  // the board (and your own row when you're outside it). Cached ~30s.
  let mini5At = 0, mini5Html = "";
  async function refreshMini5() {
    if (Date.now() - mini5At < 30000) { paintMini5(); return; }
    mini5At = Date.now();
    try {
      const d = await pull("/api/leaderboard");
      const es = (d && (d.leaderboard || d.entries)) || [];
      if (!es.length) { mini5Html = ""; paintMini5(); return; }
      const top = es.slice(0, 5);
      let rowsH = top.map(function (e) {
        const you = e.agent_id === agent ? " you" : "";
        return '<div class="m5row' + you + '"><span class="m5r">' +
          (e.rank !== null && e.rank !== undefined ? e.rank : "&ndash;") + '</span>' +
          '<span class="m5n">' + esc(e.agent_id) + '</span>' +
          '<span class="m5e">' + e.elo + '</span></div>';
      }).join("");
      if (!top.some(function (e) { return e.agent_id === agent; })) {
        const mine = es.find(function (e) { return e.agent_id === agent; });
        if (mine) rowsH += '<div class="m5row you"><span class="m5r">' +
          (mine.rank !== null && mine.rank !== undefined ? mine.rank : "&ndash;") + '</span>' +
          '<span class="m5n">' + esc(mine.agent_id) + '</span>' +
          '<span class="m5e">' + mine.elo + '</span></div>';
      }
      mini5Html = '<div class="mini5"><div class="m5hd">Top of the board</div>' + rowsH + '</div>';
    } catch (e) { mini5Html = ""; }
    paintMini5();
  }
  function paintMini5() {
    const el = document.getElementById("mini5");
    if (el && el.innerHTML !== mini5Html) el.innerHTML = mini5Html;
  }
  async function tick() {
    // The tutorial's handoff flight runs in a separate script block and needs
    // the handle for the chip it lands as; `agent` is closured in here.
    window.egAgent = agent;
    try {
      const [inbox, sent, qs, pts] = await Promise.all([
        pull("/get_messages/" + encodeURIComponent(agent)),
        pull("/get_sent/" + encodeURIComponent(agent)).catch(() => ({ messages: [] })),
        pull("/queue_status").catch(() => null),
        // Point changes since the last tick. Rides the existing 3s poll, so it
        // adds one small request rather than a new timer.
        pull("/score_events/" + encodeURIComponent(agent) + "?since=" + lastPointTs)
          .catch(() => ({ events: [] })),
      ]);
      if (pts && pts.events && pts.events.length) {
        pts.events.forEach(function (e) {
          if (e.ts > lastPointTs) lastPointTs = e.ts;
          matchMap.score(e.delta);   // the map pops your points off your disc
        });
        pendingPoints = pendingPoints.concat(pts.events);
      }
      // Replace the view with exactly the current fetch. The server scopes
      // inbox to the current game and purges finished games, so this naturally
      // shows only the live match and clears cleanly between matches.
      // Learn the board early this tick so fetchCompletedCount (below) scopes to
      // the right window on the very first match.
      if (qs && qs.nav_board) q.navBoard = qs.nav_board;

      const msgs = (inbox.messages || []).concat(sent.messages || []);
      seen.clear();
      for (const m of msgs) seen.set(m.message_id || JSON.stringify(m), m);

      // Detect match transitions from the messages' game_id. A new non-null
      // game_id means a fresh match just started -> bump the counter, flash a
      // banner, and let the feed reset to it. When the game_id goes away (agent
      // is between matches) we clear curGame so the NEXT match re-triggers the
      // flash even if its id repeats.
      let gid = null;
      for (const m of msgs) { if (m.game_id) { gid = m.game_id; break; } }
      if (gid && gid !== curGame) {
        // New match on screen. Its ordinal = (completed games on the server) + 1,
        // so it's correct regardless of what this tab observed before.
        const transition = everSawMatch;   // a prior match this session -> flash
        curGame = gid;
        fWho = "all";   // counterparty filter is per-match: its agents changed
        everSawMatch = true;
        endedGameId = null; lastResult = null;  // a new game supersedes the old result
        const completed = await fetchCompletedCount();
        matchNum = (completed === null ? Math.max(0, matchNum - 1) : completed) + 1;
        if (transition) flashNewMatch = true;
      } else if (!gid && !msgs.length) {
        if (curGame) endedGameId = curGame;   // remember the game we just left
        curGame = null;   // between matches (keep everSawMatch so the NEXT match flashes)
      }

      // Feed the live match map: every message the feed learns about becomes
      // one pulse between the real agents (the backlog present when the page
      // first loads is primed silently rather than replayed as a swarm).
      if (gid && gid !== mapGame) { mapGame = gid; matchMap.reset(); mapIds.clear(); }
      if (!gid) mapGame = null;
      if (gid) {
        const known = new Set();
        for (const m of msgs) {
          if (m.from && m.from !== "moderator") known.add(m.from);
          if (m.to && m.to !== "moderator") known.add(m.to);
        }
        if (qs && Array.isArray(qs.connected_agents))
          qs.connected_agents.forEach(function (a) { if (a !== "moderator") known.add(a); });
        matchMap.setParticipants(Array.from(known), agent);
        if (qs && qs.connected_agents) matchMap.setOnline(qs.connected_agents);
        // Round comes from the moderator's own ROUND N markers, newest wins.
        let mapRound = null;
        msgs.slice().sort(function (x, y) { return (x.timestamp || 0) - (y.timestamp || 0); })
          .forEach(function (m) {
            if (m.from !== "moderator") return;
            const mt = /\bROUND\s+(\d+)/i.exec((m.subject || "") + " " + (m.body || ""));
            if (mt) mapRound = parseInt(mt[1], 10);
          });
        matchMap.setState(matchNum, mapRound);
        for (const m of msgs) {
          const id = m.message_id || JSON.stringify(m);
          if (mapIds.has(id)) continue;
          mapIds.add(id);
          if (mapBooted) {
            const sig = splitSignature(m.body || "").sig;
            matchMap.pulse(m.from, m.to, sig ? "sign" : "ask");
          }
        }
        matchMap.start();
      }
      if (!gid && qs) {
        // Between matches the map does not vanish: it shows the queue - you,
        // the moderator, and three empty dashed seats - so the corner always
        // reflects what the agent is actually doing.
        matchMap.setQueue(q.len || 0, q.need || 4, agent);
      }
      mapBooted = true;
      const wmapEl = document.getElementById("wmap");
      if (wmapEl && wmapEl.hidden) {
        wmapEl.hidden = false;
        // If the intro is on screen, the map must NOT be lit underneath it:
        // the closing flight's whole premise is that the destination is empty
        // until the discs land (tutorialWillClose lights it at touchdown).
        const tutEl = document.getElementById("tut");
        if (tutEl && !tutEl.hidden) mapLit(false);
        matchMap.start();
      }

      // Pull the just-finished game's final standings from history (durable),
      // so the result shows reliably even though the live game-over mail is purged.
      await refreshResult();

      // Matchmaking status for the pre-match display.
      if (qs) {
        const wasWaiting = q.waiting;
        const waiting = Array.isArray(qs.agents_waiting) && qs.agents_waiting.indexOf(agent) !== -1;
        q = { waiting: waiting, len: qs.queue_length || 0,
              need: qs.num_agents || 4, grace: qs.pre_game_grace_sec || 3,
              navBoard: qs.nav_board || null };
        const bl = document.getElementById("boardlink");
        if (bl) bl.href = boardUrl();   // keep "Leaderboard" pointed at this competitor's board
        if (gid) foundAt = 0;                                  // in a game now
        else if (wasWaiting && !waiting && !msgs.length) foundAt = Date.now();  // just matched
      }
      render();
    } catch (e) {
      if (e.message === "auth") {
        gate("ended");
      }
      // transient errors: keep last render, try again next tick
    }
  }

  function start() {
    seen.clear();
    curGame = null; flashNewMatch = false; everSawMatch = false; matchNum = 0;
    // Point the history link at this agent (its token is recovered from storage
    // on that page, so we don't put the token in the link).
    try {
      const hl = document.getElementById("historylink");
      if (hl) hl.href = "/history?agent=" + encodeURIComponent(agent);
      // Stats needs the agent in the path, so it can only be filled in once we
      // know who is watching - it was previously reachable ONLY from the
      // leaderboard's agent card, which is the page you are least likely to be
      // on when you want it.
      // The token has to ride along: /agent/<id> is server-rendered and the
      // guard reads the token from the query string. Without it the page 403s.
      const sl = document.getElementById("statslink");
      if (sl) sl.href = "/agent/" + encodeURIComponent(agent) +
        (token ? "?token=" + encodeURIComponent(token) : "");
    } catch (e) {}
    // Remember who is watching so the leaderboard can offer a one-click
    // "watch" shortcut on this agent's own row (and only that row). The token
    // is view-only, so this stored copy can never act on the agent's behalf.
    // (Skip locally - there's no token to store.)
    // Record who you're watching so the leaderboard can mark "you" + add shortcuts.
    // Save even without a token (local mode) so the local board's "you" matches the
    // agent you're actually watching, not a stale tokened identity from a prior
    // build/competition session.
    try { localStorage.setItem("emailgame_watch", JSON.stringify({ agent: agent, token: token || "" })); } catch (e) {}
    // Scrub the token from the address bar so it isn't exposed in the URL,
    // browser history, or a screen share. We keep ?agent= for a readable URL and
    // recover the token from storage on reload.
    try {
      const u = new URL(location.href);
      if (u.searchParams.has("token")) {
        u.searchParams.delete("token");
        u.searchParams.set("agent", agent);
        history.replaceState(null, "", u);
      }
    } catch (e) {}
    // Seed prior-play state from the server so returning between matches shows
    // "Between matches" (+ the right count), not the first-match state. Learn the
    // board first so the count reads the right window (history is windowed).
    pull("/queue_status").then(function (qs) {
      if (qs && qs.nav_board) q.navBoard = qs.nav_board;
    }).catch(function () {}).then(function () {
      return fetchCompletedCount();
    }).then(function (n) {
      if (n && n > 0) { everSawMatch = true; if (matchNum === 0) matchNum = n; }
      seedReady = true; render();
    }).catch(function () { seedReady = true; });
    // The replay pill rides the nav pill row rather than sitting on its own
    // line below it (display:contents makes it a peer in the row's flex).
    const nr = document.querySelector(".navrow"), rw = document.getElementById("tut-replay-wrap");
    if (nr && rw) nr.appendChild(rw);
    // The corner map is there from the very first paint - queue ghosts until
    // real data arrives - EXCEPT on a first visit, where the walkthrough owns
    // the reveal (its closing flight lands ON the map).
    try {
      // The tutorial writes its seen-flag under the agent-suffixed key when it
      // can see the agent id and under the bare key when it cannot - honor both.
      const revealNow = function () {
        ensureLayout();
        matchMap.setQueue(0, 4, agent);
        const w0 = document.getElementById("wmap");
        if (w0) { w0.hidden = false; matchMap.start(); }
      };
      if (localStorage.getItem("eg_tut_seen:" + (agent || "")) ||
          localStorage.getItem("eg_tut_seen")) {
        revealNow();
      } else if (window.EG_FIRSTRUN) {
        // Intro seen on the OTHER site (shared first-run state): the
        // walkthrough won't run here, so the map reveals itself instead of
        // waiting for a closing flight that will never come.
        window.EG_FIRSTRUN.ready(function () {
          if (window.EG_FIRSTRUN.flags["tut"]) revealNow();
        });
      }
    } catch (e) {}
    if (timer) clearInterval(timer);
    tick();
    timer = setInterval(tick, 3000);
    refreshBudget();
    // 15s, not 60: the chip should appear as soon as there is anything to
    // show (first spend lands mid-first-round), never "after a reload".
    budgetTimer = setInterval(refreshBudget, 15000);
    // 1s refresher so the matchmaking countdown / "finding" pips update smoothly
    // between the 3s polls. Only re-renders while between matches (cheap, no feed).
    if (cdTimer) clearInterval(cdTimer);
    cdTimer = setInterval(function () { if (!seen.size) render(); }, 1000);
  }

  if (agent && (token || LOCAL)) start(); else gate("welcome");
})();
</script>
""" + BRAND_FOOTER + BRAND_TIP_JS + BRAND_MARGIN_FX + r"""
</body>
</html>"""
    return html.replace("/*__LOCAL__*/false", "true" if local else "false")
