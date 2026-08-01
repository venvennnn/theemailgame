"""Shared brand styling for the competition web pages, aligned with the
landing page (theemailgame.com): Space Grotesk + Inter + JetBrains Mono,
accent #2f54ff, clean cards, and mobile-safe rules.

Usage:
- f-string templates: insert ``{BRAND_OVERRIDE}`` just before </head> and
  ``{BRANDBAR}`` at the top of the body.
- raw-string templates: concatenate, e.g.
  ``r'''...</style>''' + BRAND_OVERRIDE + r'''</head>...'''``.

It is an OVERRIDE sheet (uses !important on the theme bits) so it re-skins a
page's existing CSS without needing to rewrite it.
"""

BRANDBAR = (
    '<a class="brandbar" href="https://theemailgame.com"><svg viewBox="0 4.5 24 16" fill="none" stroke="#2f54ff" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6 6H18C19.6 6 20.4 7.1 20.9 8.7 21.7 11.2 22.6 15 21 17.6 20 19.3 17.7 19.1 16.7 17.2 '
    '15.7 15.3 14.4 14.7 12 14.7 9.6 14.7 8.3 15.3 7.3 17.2 6.3 19.1 4 19.3 3 17.6 1.4 15 2.3 11.2 '
    '3.1 8.7 3.6 7.1 4.4 6 6 6Z"/><path d="M5 6.6 12 12.1 19 6.6"/></svg> The Email Game</a>'
)

BRAND_OVERRIDE = """
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <link rel="icon" href="data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'><rect%20width='32'%20height='32'%20rx='7'%20fill='%232f54ff'/><g%20fill='none'%20stroke='%23fff'%20stroke-width='1.6'%20stroke-linecap='round'%20stroke-linejoin='round'%20transform='translate%284%204%29'><path%20d='M6%206H18C19.6%206%2020.4%207.1%2020.9%208.7%2021.7%2011.2%2022.6%2015%2021%2017.6%2020%2019.3%2017.7%2019.1%2016.7%2017.2%2015.7%2015.3%2014.4%2014.7%2012%2014.7%209.6%2014.7%208.3%2015.3%207.3%2017.2%206.3%2019.1%204%2019.3%203%2017.6%201.4%2015%202.3%2011.2%203.1%208.7%203.6%207.1%204.4%206%206%206Z'/><path%20d='M5%206.6%2012%2012.1%2019%206.6'/></g></svg>">
  <style>
    :root {
      --accent-blue:#2f54ff; --accent-blue-d:#2546e6; --ink:#0a0a0b; --ink-2:#3c3f45;
      --muted:#6b7078; --line:#e7e9ec; --line-2:#d9dce0; --bg-soft:#fafafa; --bg-alt:#f4f5f7;
      --th-bg:#eef1fe; --green:#16a34a; --green-bg:#e7f5ee; --tip-bg:#232b3d;
      --card-line:#c6ccd6;
      --shadow:0 1px 2px rgba(10,10,11,.04), 0 8px 30px rgba(10,10,11,.05);
      /* ------------------------------------------------------------------
         SURFACES AND EDGES - the whole palette, in one place.
         These pages had accumulated 11 different border colours and 22
         background colours, mostly near-identical greys that read as sloppy
         rather than as distinctions. Everything below maps onto exactly:
           two edges   - --line (structure), --line-2 (controls)
           two neutral surfaces - white (raised), --surface-2 (inset)
           four semantic tints  - blue / green / amber / red, each a matched
                                  background + border + ink triple
         Add nothing here without deleting something. ------------------- */
      --surface:#ffffff; --surface-2:#f4f6f9;
      --blue-bg:#eef1ff;  --blue-line:#ccd6ff;
      --green-line:#bfe6cf;
      --amber-bg:#fff8e6; --amber-line:#f0dca8; --amber-ink:#7a5c00;
      --red-bg:#fdecea;   --red-line:#f5c6c0;   --red-ink:#c5221f;
      /* Icon shapes, drawn on a 16x16 grid and centred on x=8 by construction.
         Used as CSS masks so they take their colour from the element. */
      --eg-info:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M8 0.7 A7.3 7.3 0 1 1 7.99 0.7 Z M8 2.4 A5.6 5.6 0 1 0 8.01 2.4 Z" fill="black" fill-rule="evenodd"/><circle cx="8" cy="4.75" r="1.02" fill="black"/><rect x="7.05" y="6.65" width="1.9" height="5.1" rx="0.95" fill="black"/></svg>');
      --eg-board:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect x="1.4" y="8.6" width="3.4" height="6" rx="0.7" fill="black"/><rect x="6.3" y="2.6" width="3.4" height="12" rx="0.7" fill="black"/><rect x="11.2" y="6.1" width="3.4" height="8.5" rx="0.7" fill="black"/></svg>');
      --eg-eye:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M8 2.6 C12.1 2.6 15.1 6.3 15.1 8 C15.1 9.7 12.1 13.4 8 13.4 C3.9 13.4 0.9 9.7 0.9 8 C0.9 6.3 3.9 2.6 8 2.6 Z M8 4.5 C6.1 4.5 4.5 6.1 4.5 8 C4.5 9.9 6.1 11.5 8 11.5 C9.9 11.5 11.5 9.9 11.5 8 C11.5 6.1 9.9 4.5 8 4.5 Z" fill="black" fill-rule="evenodd"/><circle cx="8" cy="8" r="1.9" fill="black"/></svg>');
      --eg-stats:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M2 2.2 v10.6 a1 1 0 0 0 1 1 h11" fill="none" stroke="black" stroke-width="1.7" stroke-linecap="round"/><path d="M4.6 10.6 L7.2 7.4 L9.6 9.2 L13.2 4.6" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>');
      --eg-hist:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M8 0.9 A7.1 7.1 0 1 1 7.99 0.9 Z M8 2.7 A5.3 5.3 0 1 0 8.01 2.7 Z" fill="black" fill-rule="evenodd"/><rect x="7.15" y="4.1" width="1.7" height="4.75" rx="0.85" fill="black"/><rect x="7.15" y="7.15" width="4.5" height="1.7" rx="0.85" fill="black"/></svg>');
      --eg-tour:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M8 1.2 A6.8 6.8 0 1 1 7.99 1.2 Z M8 3.1 A4.9 4.9 0 1 0 8.01 3.1 Z" fill="black" fill-rule="evenodd"/><path d="M11.1 4.9 L9.2 9.2 L4.9 11.1 L6.8 6.8 Z" fill="black"/></svg>');
    }
    body { font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif !important;
      /* A faint cool ground so white cards actually lift off the page. */
      background:#f7f8fb !important; color:var(--ink) !important; -webkit-font-smoothing:antialiased; }
    h1,h2,h3,.gate h2,.roundhdr,.result-h { font-family:"Space Grotesk","Inter",sans-serif !important; letter-spacing:-.01em; }
    a, a.plain { color:var(--accent-blue) !important; }
    .brandbar { display:flex; align-items:center; gap:8px; margin:0 0 18px; width:fit-content;
      font-family:"Space Grotesk","Inter",sans-serif; font-weight:700; font-size:1.02rem;
      color:var(--ink) !important; text-decoration:none !important; }
    .brandbar svg { height:16px; width:auto; display:block; }
    table { border:0 !important; border-radius:14px !important;
      box-shadow:0 0 0 1px var(--line), var(--shadow) !important; }
    .gate { border:0 !important; border-radius:14px !important; box-shadow:0 0 0 1px var(--line), var(--shadow) !important; }
    .gate p { color: var(--ink-2) !important; line-height: 1.55; }
    .gate p.sub, .gate .hint { color: var(--muted) !important; }
    .scorebar, .msg { border-color:var(--line) !important; }
    th { background:var(--th-bg) !important; color:#414a6b !important; letter-spacing:.07em !important; }
    th[data-tip] { cursor:help; }
    @media (min-width:641px) { th[data-tip] { text-decoration:underline dotted var(--line-2);
      text-underline-offset:3px; text-decoration-thickness:1px; } }
    @media (max-width:640px) { th[data-tip]::after { content:"?"; display:inline-flex; align-items:center;
      justify-content:center; width:1.15em; height:1.15em; margin-left:.35em; border-radius:50%;
      background:var(--line); color:var(--ink-2); font-size:.62rem; font-weight:700; vertical-align:middle; } }
    .eg-tip { position:absolute; z-index:1000; display:none; max-width:280px; pointer-events:none;
      background:var(--tip-bg); color:#eef0f5; font-size:.78rem; line-height:1.5; font-weight:500;
      padding:.55rem .75rem .55rem .8rem; border-radius:10px;
      box-shadow:0 14px 36px rgba(35,43,61,.34), 0 3px 8px rgba(35,43,61,.22);
      opacity:0; transform:translateY(-4px); transition:opacity .15s ease, transform .15s ease;
      text-transform:none; letter-spacing:normal; text-align:left; white-space:normal;
      overflow-wrap:anywhere; word-break:break-word; }
    .eg-tip strong { color:#fff; }
    .eg-tip::before { content:""; position:absolute; top:-6px; left:var(--tipx,50%); margin-left:-6px;
      border:6px solid transparent; border-top:0; border-bottom-color:var(--tip-bg); }
    .eg-tip.on { opacity:1; transform:none; }
    th, td { border-bottom:1px solid var(--line) !important; }
    td.elo, .sb-score, .result .sb-score { color:var(--accent-blue) !important; font-family:"JetBrains Mono",monospace; }
    td, th { font-variant-numeric:tabular-nums; }
    .btn { background:var(--accent-blue) !important; border-radius:9px !important; }
    .btn:hover { background:var(--accent-blue-d) !important; }
    .chip { border:0 !important; box-shadow:0 0 0 1px var(--line), var(--shadow) !important; }
    /* Dot colour must follow the SAME key as the board: blue (pulsing) = in a
       match, green = connected and waiting. These were previously forced green
       in every state, which silently overrode the watch page's own rules. */
    .chip .dot { background:var(--line-2) !important; }
    .chip.wait .dot, .chip .dot.online { background:var(--green) !important; }
    .chip.live .dot, .chip .dot.ingame { background:var(--accent-blue) !important; }
    /* Same optical fix as the watch chip: inline-block + vertical-align:middle
       centres against the x-height, not the line, so the dot sits low. */
    .chip { display:inline-flex !important; align-items:center !important; gap:.45rem; }
    .chip .dot { margin-right:0 !important; vertical-align:baseline !important; }
    .fchip.on { background:var(--accent-blue) !important; border-color:var(--accent-blue) !important; }
    .card { border:1px solid var(--line) !important; border-radius:12px !important;
      background:var(--surface) !important; box-shadow:var(--shadow) !important; }
    .card .v { font-family:"Space Grotesk","Inter",sans-serif; }
    .match { border:1px solid var(--line) !important; border-radius:10px !important; }
    .match.win { border-color:var(--green-line) !important; background:var(--green-bg) !important; }
    .match.loss { border-color:var(--red-line) !important; background:var(--red-bg) !important; }
    .match.tie { border-color:var(--amber-line) !important; background:var(--amber-bg) !important; }
    .res.win { color:var(--green) !important; }
    /* SENT is blue, RECEIVED is green. Blue is "you" everywhere else on the
       site - your name, your row, your rating, the in-match dot - so your own
       outgoing mail should carry it. This was the other way round, which put
       the brand colour on other agents' messages. */
    .msg.out { border-color:var(--blue-line) !important; background:var(--blue-bg) !important; }
    .msg.inc { border-color:var(--green-line) !important; background:var(--green-bg) !important; }
    .tag.out { background:var(--blue-bg) !important; color:var(--accent-blue) !important; }
    .tag.inc { background:var(--green-bg) !important; color:var(--green) !important; }
    .sig { background:var(--green-bg) !important; color:var(--green) !important; border-color:var(--green-line) !important; }
    .chip .me { color:var(--accent-blue) !important; }
    .mm-pips .pip.on { background:var(--green) !important; }
    .newmatch { background:var(--blue-bg) !important; border-color:var(--blue-line) !important; color:var(--accent-blue) !important; }
    .sb-row.you, .youcard { background:var(--blue-bg) !important; }
    .youcard { border:0 !important; box-shadow:0 0 0 1px var(--line), var(--shadow) !important; }
    /* competition status banner (above the rankings) */
    .status-banner { background:var(--sb-bg); color:var(--sb-fg);
      border:1px solid color-mix(in srgb, var(--sb-accent) 32%, #fff); border-radius:11px; padding:.7rem 1rem;
      margin:0 0 .7rem; font-size:.9rem; line-height:1.5; box-shadow:var(--shadow); }
    .status-banner strong { font-weight:700; }
    .status-banner .icon { vertical-align:-2px; margin-right:.2rem; }
    /* explanation text: readable + structured */
    .legend { color:var(--muted) !important; font-size:.86rem; line-height:1.7; max-width:82ch; }
    dl.deflist { margin:.5rem 0 0; display:grid; grid-template-columns:max-content 1fr;
      gap:.35rem 1rem; font-size:.86rem; color:var(--muted); line-height:1.55; max-width:82ch; }
    dl.deflist dt { font-weight:700; color:var(--ink-2); }
    dl.deflist dd { margin:0; }
    /* consistent page width */
    .wrap { max-width:920px !important; }
    /* online / in-game status dot */
    .statusdot { display:inline-block; width:.5rem; height:.5rem; border-radius:50%;
      background:var(--line-2); vertical-align:middle; flex:none; }
    /* Optical nudge. The row is already flex-centred, but flex centres against
       the full line box - descender space included - while the agent name has
       no descenders in most cases, so its visual centre sits higher and the dot
       reads as low. Half a pixel up puts it on the text's optical centre. */
    td.agent .statusdot, th.agent .statusdot { margin-right:.45rem;
      transform:translateY(-.5px); }
    /* Anything carrying an explanation is hoverable, and gets the themed tip. */
    .statusdot[data-tip], .rank-badge[data-tip] { cursor:help; }
    .dotkey .statusdot { margin-right:.4rem; }
    .statusdot.online { background:var(--green); }
    .statusdot.off { background:transparent !important; animation:none !important; }
    .statusdot.ingame { background:var(--accent-blue);
      box-shadow:0 0 0 0 rgba(47,84,255,.5); animation:egpulse 1.5s infinite; }
    @keyframes egpulse { 0%{box-shadow:0 0 0 0 rgba(47,84,255,.5);} 70%{box-shadow:0 0 0 7px rgba(47,84,255,0);} 100%{box-shadow:0 0 0 0 rgba(47,84,255,0);} }
    /* compact one-row meta bar above the rankings (hint · dots · cross-link) */
    .lb-meta { display:flex; align-items:center; flex-wrap:wrap; gap:.3rem 1rem;
      margin:0 0 .7rem; font-size:.82rem; color:var(--muted); }
    .lb-meta .lb-hint { color:var(--muted); display:inline-flex; align-items:center; gap:.5rem; }
    .lb-meta .lb-nav { display:inline-flex; align-items:center; gap:.45rem; }
    .lb-meta .lb-spacer { flex:1 1 1rem; }
    /* Reusable "there is something here you would not otherwise discover" chip.
       Plain grey sentence fragments at the end of a meta line get skipped, and
       hover-for-detail is exactly the kind of affordance nobody finds by
       accident - so it gets a marker and real contrast instead. */
    .tip { display:inline-flex; align-items:center; gap:.35rem; font-size:.78rem;
      font-weight:600; color:var(--accent-blue); background:rgba(47,84,255,.07);
      border:1px solid var(--blue-line,#ccd6ff); border-radius:999px;
      padding:.22rem .6rem; line-height:1.25; }
    /* The glyph is drawn, not typed: a text "i" never centres in a small circle
       (its side bearings are asymmetric, and italic slant pushes it right), so
       an SVG with a real centre line is the only way to get this exact. */
    /* Cross-page navigation. Same pill as the tour button so the row of
       controls reads as one set - navigation should never look like body-text
       hyperlinks sitting under a heading. */
    .navbtn { display:inline-flex; align-items:center; gap:.4rem; background:#fff;
      border:1px solid var(--line-2); border-radius:999px; color:var(--ink-2) !important;
      font-size:.8rem; font-weight:600; padding:.34rem .82rem; line-height:1.2;
      white-space:nowrap; text-decoration:none !important; cursor:pointer;
      transition:background .15s ease, border-color .15s ease, color .15s ease; }
    .navbtn:hover { background:var(--accent-blue); border-color:var(--accent-blue);
      color:#fff !important; }
    .navbtn:focus-visible { outline:2px solid var(--accent-blue); outline-offset:2px; }
    /* A row of them, with the separators the links used to need removed. */
    .navrow { display:flex; align-items:center; gap:.45rem; flex-wrap:wrap; margin:.1rem 0 1.1rem; }
    /* Each nav button carries the icon for its destination, matching the icons
       on the agent card so the same place looks the same everywhere. Left
       padding runs 1px tighter than right: a leading icon reads heavier than
       the space after the text, so equal padding looks shifted right. */
    .navbtn[class*="i-"] { padding-left:.72rem; padding-right:.82rem; }
    .navbtn[class*="i-"]::before { content:""; width:.9rem; height:.9rem; flex:none;
      align-self:center; background:currentColor;
      -webkit-mask-position:center; mask-position:center;
      -webkit-mask-size:.9rem .9rem; mask-size:.9rem .9rem;
      -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat; }
    .navbtn.i-board::before { -webkit-mask-image:var(--eg-board); mask-image:var(--eg-board); }
    .navbtn.i-eye::before   { -webkit-mask-image:var(--eg-eye);   mask-image:var(--eg-eye); }
    .navbtn.i-hist::before  { -webkit-mask-image:var(--eg-hist);  mask-image:var(--eg-hist); }
    .navbtn.i-stats::before { -webkit-mask-image:var(--eg-stats); mask-image:var(--eg-stats); }
    .tip::before { content:""; width:.95rem; height:.95rem; flex:none; align-self:center;
      background:var(--accent-blue); border-radius:50%;
      -webkit-mask:var(--eg-info) center/.95rem .95rem no-repeat;
      mask:var(--eg-info) center/.95rem .95rem no-repeat; }
    .dotkey { display:flex; align-items:center; gap:.9rem; flex-wrap:wrap;
      color:var(--muted); font-size:.8rem; margin:0; }
    .dotkey .dk { display:inline-flex; align-items:center; }
    /* top-3 row emphasis (medals come from _rank_cell) */
    tr.rank-1 td { background:#fff9e6 !important; }
    tr.rank-2 td { background:#eef1f5 !important; }
    tr.rank-3 td { background:#f9f0e3 !important; }
    tr.rank-1 td.agent .aname { font-weight:800; }
    /* subtle agent-network backdrop behind a header */
    .arena-head { position:relative; }
    .arena-head canvas.arena { position:absolute; inset:0; z-index:0; pointer-events:none; opacity:.55; }
    .arena-head > *:not(canvas) { position:relative; z-index:1; }
    @media (prefers-reduced-motion: reduce) { .arena-head canvas.arena { display:none; } }
    /* shared footer */
    .brand-footer { max-width:920px; margin:42px auto 0; padding:18px 4px 6px;
      border-top:1px solid var(--line); display:flex; flex-wrap:wrap; align-items:center;
      gap:8px 14px; color:var(--muted); font-size:.82rem; }
    .brand-footer a { color:var(--muted) !important; text-decoration:none; }
    .brand-footer a:hover { color:var(--accent-blue) !important; }
    .brand-footer .sep { color:var(--line-2); }
    .brand-footer .bf-spacer { flex:1; }
    .brand-footer span, .brand-footer a { display:inline-flex; align-items:center; gap:6px; }
    .brand-footer .sep { gap:0; }
    .brand-footer svg { width:14px; height:14px; flex:none; }
    .brand-footer .bf-withai { width:18px; height:18px; margin:-2px 0; }
    .brand-footer .bf-ext { width:12px; height:12px; transform:translateY(.5px); }
    /* mobile-safe */
    @media (max-width:640px) {
      body { padding:18px 14px !important; }
      .brandbar { font-size:.95rem; }
      h1 { font-size:1.4rem !important; }
      table { display:block; overflow-x:auto; -webkit-overflow-scrolling:touch; white-space:nowrap; }
      .cards { grid-template-columns:repeat(auto-fit,minmax(120px,1fr)) !important; }
      dl.deflist { grid-template-columns:1fr; gap:.05rem; }
      dl.deflist dt { margin-top:.55rem; }
      .bar { gap:.4rem !important; }
      .brand-footer { font-size:.78rem; }
    }

    /* ====================================================================
       NORMALIZATION. Last in the sheet so it wins, and deliberately blunt:
       every near-duplicate grey in the page CSS collapses onto one of the
       two edge tokens, and every tinted panel onto one of the four semantic
       triples. Grouped by INTENT, not by page, so the same kind of thing
       looks the same everywhere.
       ==================================================================== */

    /* Structural edges: cards, panels, tables, rows, message bodies. */
    .msg, .gate, .card, .result, .roundhdr, .bar, .match, .scorebar,
    .youcard, table, th, td, .newmatch, .sig, .crumbs {
      border-color:var(--line) !important; }
    /* .gate and table are excluded on purpose: they draw their edge as a
       box-shadow ring above, so giving them a border too would double it. */
    .msg, .card, .result, .match, .bar { border-width:1px !important; }

    /* Control edges: anything you click or type into sits one step darker so
       it reads as interactive against a card of the same colour. */
    .fchip, .filters select, .navbtn, .tr-open, select, input, button.navbtn {
      border-color:var(--line-2) !important; }

    /* Raised vs inset. Cards are white; anything nested INSIDE a card that
       needs separating from it is the single inset grey - never a third. */
    .msg, .card, .gate, .match, .fchip, .chip, table { background:var(--surface) !important; }
    /* NOT .filters select: it is a control, styled as a pill below. Listing it
       here applied the `background` SHORTHAND, which resets background-image
       and silently erased its chevron. */
    .bar .chip, .gate code, code { background:var(--surface-2) !important; }
    /* .legend caps at 82ch for readable prose, but a legend that is a flex ROW
       of sentence + tip needs the full column or the tip wraps to its own line
       and stops reading as part of the sentence. */
    .legend-row { max-width:none !important; }

    /* Round headings: "Round 2" is the heading, the count beside it is metadata.
       They were nearly the same size and separated only by a literal space, so
       they read as one run-on phrase ("Round 2 2 msgs" is genuinely confusing
       when both numbers are digits). Baseline-aligned flex row with a real gap,
       and the count drops to a muted, smaller weight so the hierarchy is
       obvious without extra punctuation. */
    .roundhdr { display:flex; align-items:baseline; gap:.55rem; }
    .roundhdr .rcount, .roundhdr .pill { font-size:.72rem !important; font-weight:600 !important;
      color:var(--muted) !important; letter-spacing:.02em; font-variant-numeric:tabular-nums; }
    /* The score pill stays a pill - it is a value, not a label. */
    .roundhdr .pill { background:var(--surface-2); border:1px solid var(--line);
      border-radius:999px; padding:.1rem .5rem; color:var(--ink-2) !important; }

    /* Point change floating off the live indicator, in the same visual language
       as the landing hero's score pops: monospace figure, rises and fades.
       Fixed + pointer-events:none so it can never intercept a click or shift
       the layout it is drawn over. */
    .ptfloat { position:fixed; z-index:150; pointer-events:none;
      transform:translateX(-50%); font-family:"JetBrains Mono",ui-monospace,monospace;
      font-size:.82rem; font-weight:700; white-space:nowrap;
      animation:ptfloat 1.9s ease-out forwards; }
    .ptfloat.up { color:var(--green); }
    .ptfloat.down { color:var(--red-ink); }
    @keyframes ptfloat {
      0%   { opacity:0; transform:translate(-50%, 2px) scale(.85); }
      14%  { opacity:1; transform:translate(-50%, -5px) scale(1); }
      65%  { opacity:1; transform:translate(-50%, -22px) scale(1); }
      100% { opacity:0; transform:translate(-50%, -34px) scale(1); }
    }

    /* History board tabs: same pill vocabulary as the filter chips, so the
       "which set of games am I looking at" control reads like the other
       controls rather than like navigation. */
    .btab { background:var(--surface) !important; border:1px solid var(--line-2) !important;
      color:var(--ink-2) !important; transition:background .15s ease, border-color .15s ease; }
    .btab:hover { border-color:var(--accent-blue) !important; }
    .btab.on { background:var(--accent-blue) !important; border-color:var(--accent-blue) !important;
      color:#fff !important; }

    /* Clickable-row feedback, identical for a stats row and a history match. */
    /* Hover keeps the SAME color, one shade deeper - and the ring follows
       the card's own color. A win card deepens green with a green ring, a
       loss deepens red with a red ring, neutral deepens neutral. Site-wide. */
    .match:hover { background:color-mix(in srgb, #fff 55%, var(--surface-2)) !important;
      box-shadow:0 0 0 2px var(--line-2), var(--shadow) !important; }
    .match.win:hover { background:color-mix(in srgb, var(--green-bg) 72%, var(--green-line)) !important;
      box-shadow:0 0 0 2px var(--green-line), var(--shadow) !important; }
    .match.loss:hover { background:color-mix(in srgb, var(--red-bg) 72%, var(--red-line)) !important;
      box-shadow:0 0 0 2px var(--red-line), var(--shadow) !important; }
    .match.tie:hover { background:color-mix(in srgb, var(--amber-bg) 72%, var(--amber-line)) !important;
      box-shadow:0 0 0 2px var(--amber-line), var(--shadow) !important; }
    .match.left:hover { background:var(--surface-2) !important;
      box-shadow:0 0 0 2px var(--line-2), var(--shadow) !important; }
    tr.rowlink:hover td { background:color-mix(in srgb, #fff 55%, var(--surface-2)) !important; }
    tr.rank-1:hover td, tr.rank-2:hover td, tr.rank-3:hover td {
      background:color-mix(in srgb, var(--amber-bg) 78%, var(--amber-line)) !important; }

    /* The "with <agent>" dropdown is a FILTER, sitting in a row of filter
       chips, so it is styled as one of them rather than as a rounded form
       control: identical height, radius, weight and border to .fchip, a brand
       chevron instead of the platform arrow, and the same blue fill as an
       active chip when a specific agent is selected. Previously it kept the
       proportions of a native select and still read as a browser widget. */
    .filters select {
      -webkit-appearance:none; -moz-appearance:none; appearance:none;
      font:inherit !important; font-size:.82rem !important; font-weight:600 !important;
      color:var(--ink-2) !important; background-color:var(--surface) !important;
      border:1px solid var(--line-2) !important; border-radius:999px !important;
      padding:.3rem 1.85rem .3rem .8rem !important; line-height:1.25 !important;
      height:auto !important; cursor:pointer; text-overflow:ellipsis;
      background-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6.4 L8 10.2 L12 6.4" fill="none" stroke="%232f54ff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/></svg>') !important;
      background-repeat:no-repeat !important;
      background-position:right .6rem center !important;
      background-size:.7rem .7rem !important;
      transition:background-color .15s ease, border-color .15s ease, color .15s ease; }
    .filters select:hover { border-color:var(--accent-blue) !important; }
    .filters select:focus-visible { outline:2px solid var(--accent-blue); outline-offset:2px; }
    /* Filtered to one agent = active, exactly like .fchip.on. */
    .filters select.on {
      background-color:var(--accent-blue) !important; border-color:var(--accent-blue) !important;
      color:#fff !important;
      background-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6.4 L8 10.2 L12 6.4" fill="none" stroke="%23ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/></svg>') !important; }
    .filters select option { color:var(--ink); background:#fff; font-weight:500; }
    /* The label reads as one control with the select it introduces. */
    .filters .flbl { display:inline-flex; align-items:center; gap:.4rem;
      color:var(--muted) !important; font-size:.82rem; }

    /* Semantic tints. Each is a matched background + border + ink triple, so
       "informational blue" is one blue everywhere rather than four. */
    .tag.out, .newmatch, .sb-row.you, .youcard { background:var(--blue-bg) !important; }
    .newmatch { border-color:var(--blue-line) !important; }
    .tag.inc, .sig { background:var(--green-bg) !important; }
    .sig { border-color:var(--green-line) !important; }
    .msg.mod, .result, .tag.mod { background:var(--amber-bg) !important; }
    .result { border-color:var(--amber-line) !important; }
    .result-h, .result .sb-rank, .tag.mod { color:var(--amber-ink) !important; }
    .err { background:var(--red-bg) !important; border-color:var(--red-line) !important;
      color:var(--red-ink) !important; }

    /* Per-item colour = full tinted border + background wash; the direction
       chips carry the label. (Single-edge accent rails are retired.) */
    .msg.mod { border-color:var(--amber-line) !important; background:var(--amber-bg) !important; }
  </style>
"""

_ICO_MARK = ('<svg viewBox="0 4.5 24 16" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M6 6H18C19.6 6 20.4 7.1 20.9 8.7 21.7 11.2 '
    '22.6 15 21 17.6 20 19.3 17.7 19.1 16.7 17.2 15.7 15.3 14.4 14.7 12 14.7 9.6 14.7 8.3 15.3 7.3 17.2 '
    '6.3 19.1 4 19.3 3 17.6 1.4 15 2.3 11.2 3.1 8.7 3.6 7.1 4.4 6 6 6Z"/><path d="M5 6.6 12 12.1 19 6.6"/></svg>')
_ICO_GLOBE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>'
    '<path d="M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18"/></svg>')
_ICO_GH = ('<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 '
    '3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 '
    '18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 '
    '2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 '
    '1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 '
    '2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 '
    '1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222 0 1.606-.014 2.898-.014 3.293 0 '
    '.322.216.694.825.576C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>')
_ICO_WITHAI = ('<svg class="bf-withai" viewBox="3.5 2.9 36 37.2" fill="currentColor"><path d="M13.6342 9.40967L18.0549 '
    '9.41275C18.0568 13.7968 18.0941 18.244 18.0459 22.623C18.2343 22.0477 18.507 21.4854 18.719 20.9181C19.1427 '
    '19.7847 19.589 18.6601 20.025 17.5313C20.186 17.1144 20.4427 16.6657 20.6039 16.2443C21.1315 14.8655 21.6679 '
    '13.4887 22.2005 12.1117C22.5462 11.2177 23.0156 10.3251 23.3214 9.41235L27.7378 9.41474C27.3034 10.1699 '
    '27.2249 10.4826 26.9179 11.2682L26 13.6564C25.5074 14.9356 24.969 16.1138 24.4829 17.3524C23.8441 19.0991 '
    '23.1723 20.7835 22.4299 22.4846C21.7593 24.1541 21.1595 25.8581 20.4692 27.5192C19.6365 29.5231 18.7792 '
    '31.5892 18.0547 33.6328L13.6309 33.6307L13.6198 20.5024C13.3959 21.1463 13.0661 21.7091 12.8305 22.3324C12.093 '
    '24.2836 11.3418 26.203 10.5292 28.1242C9.76725 29.9258 9.13917 31.8206 8.38562 33.6292C6.95027 33.6585 5.4161 '
    '33.6304 3.97266 33.6302L3.97785 9.41149L8.35616 9.41186L8.3575 17.7949C8.35754 19.3755 8.38152 21.0365 '
    '8.34359 22.6099C9.11398 20.8957 9.74888 18.9762 10.458 17.2214L12.7155 11.5894C13.0037 10.8749 13.3754 '
    '10.1089 13.6342 9.40967Z"/><path d="M34.6061 3.32908C36.0396 3.29371 37.578 3.33064 39.0238 3.32292C38.8422 '
    '3.97278 38.544 4.60225 38.3053 5.23492C37.2416 8.05438 36.0168 10.8044 34.9924 13.6416C34.8249 14.1054 '
    '34.5571 14.571 34.3697 15.033L29.6216 26.9171C29.042 28.3879 28.4858 29.9468 27.8925 31.4018L25.3958 '
    '37.6916C25.1174 38.3962 24.7208 38.9451 24.5611 39.7142L20.1055 39.7177C20.9636 37.8428 21.6089 35.9584 '
    '22.3648 34.0508L27.1878 21.9692L31.9049 10.1543C32.1021 9.66356 32.367 9.15818 32.5536 8.67549C32.9935 '
    '7.53745 33.4422 6.40282 33.8699 5.26019C34.1098 4.61945 34.4066 3.9827 34.6061 3.32908Z"/></svg>')

_ICO_EXT = ('<svg class="bf-ext" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="M19 5l-8 8"/>'
    '<path d="M18 14v4a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 18V8a1.5 1.5 0 0 1 1.5-1.5H10"/></svg>')

BRAND_FOOTER = (
    '<footer class="brand-footer">'
    '<span>' + _ICO_MARK + 'The Email Game</span><span class="sep">&middot;</span>'
    '<a href="https://theemailgame.com" target="_blank" rel="noopener">' + _ICO_EXT + 'theemailgame.com</a>'
    # No GitHub link. The starter repo is public so competitors can clone it,
    # but nothing we host should route people to it: these pages are reached
    # DURING play, and the repo is the build-time artefact. The landing page
    # gates its own repo links until the build window opens (Aug 1) for the same
    # reason - so a stale previous-competition repo is never the thing someone
    # finds first.
    '<span class="bf-spacer"></span><span>' + _ICO_WITHAI + 'A WithAI product</span></footer>'
)

# Subtle agent-network backdrop (drifting nodes + message pulses), used behind a
# header inside a .arena-head container. Tasteful: few nodes, low opacity, gated
# off for reduced-motion.
BRAND_ARENA_CANVAS = '<canvas class="arena" aria-hidden="true"></canvas>'

# The landing page's ambient network, playing in the side margins of every
# competitor-facing page: drifting dots, faint links, the cyan comet pulse.
# One full-viewport canvas UNDER the content column; the dots live only in
# the strips beside .wrap, so text never gains a busy background. Skipped
# for reduced-motion users and on screens with no real margin.
BRAND_MARGIN_FX = """
<canvas id="eg-mfx" aria-hidden="true" style="position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.55"></canvas>
<style>.wrap{position:relative;z-index:1}.arena-head canvas.arena{display:none}</style>
<script>
(function(){
  // The header arena with its window EXPANDED: one animation whose play
  // region is the union of the header band and both side margins, touching
  // at the seams - so the same dots drift out from behind the header and
  // travel down the margins. Identical constants, identical .55 canvas
  // opacity, and a hard clip to the region, so nothing can differ and
  // nothing can appear under the page contents.
  if(!matchMedia('(prefers-reduced-motion: no-preference)').matches) return;
  var cv=document.getElementById('eg-mfx'); if(!cv) return;
  var ctx=cv.getContext('2d'); if(!ctx) return;
  var dpr=Math.min(window.devicePixelRatio||1,2),W=0,H=0,nodes=[],pulses=[],frame=0,LINK=120;
  function rnd(a,b){return a+Math.random()*(b-a);}
  function rects(){
    var out=[],wr=document.querySelector('.wrap'),r=wr?wr.getBoundingClientRect():null;
    if(r){
      if(r.left>34)out.push({x0:0,y0:0,x1:r.left,y1:H});
      if(W-r.right>34)out.push({x0:r.right,y0:0,x1:W,y1:H});
      var hd=document.querySelector('.arena-head');
      if(hd){
        var hr=hd.getBoundingClientRect();
        var y0=Math.max(0,hr.top),y1=Math.min(H,hr.bottom);
        if(y1-y0>40)out.push({x0:r.left,y0:y0,x1:r.right,y1:y1});
      }
    }
    return out;
  }
  function inside(rs,x,y){
    for(var i=0;i<rs.length;i++){
      var z=rs[i];
      if(x>=z.x0&&x<=z.x1&&y>=z.y0&&y<=z.y1)return true;
    }
    return false;
  }
  function build(){
    var rs=rects();nodes=[];pulses=[];
    if(!rs.length)return;
    var area=0;
    for(var i=0;i<rs.length;i++)area+=(rs[i].x1-rs[i].x0)*(rs[i].y1-rs[i].y0);
    var n=Math.max(14,Math.min(64,Math.round(area/9000)));   // denser than the old head band
    for(var i=0;i<n;i++){
      var z=rs[(Math.random()*rs.length)|0];
      nodes.push({x:rnd(z.x0,z.x1),y:rnd(z.y0,z.y1),vx:rnd(-0.3,0.3),vy:rnd(-0.3,0.3)});
    }
  }
  function spawnPulse(){
    if(nodes.length<2)return;
    var a=nodes[(Math.random()*nodes.length)|0],c=null,cd=LINK;
    for(var i=0;i<nodes.length;i++){
      if(nodes[i]===a)continue;
      var d=Math.hypot(nodes[i].x-a.x,nodes[i].y-a.y);
      if(d<cd){cd=d;c=nodes[i];}
    }
    if(c)pulses.push({a:a,b:c,t:0});
  }
  function tick(){
    frame++;
    ctx.clearRect(0,0,W,H);
    var rs=rects();
    if(!rs.length){requestAnimationFrame(tick);return;}
    for(var i=0;i<nodes.length;i++){
      var o=nodes[i],nx=o.x+o.vx,ny=o.y+o.vy;
      if(inside(rs,nx,ny)){o.x=nx;o.y=ny;}
      else if(inside(rs,nx,o.y)){o.vy*=-1;o.x=nx;}
      else if(inside(rs,o.x,ny)){o.vx*=-1;o.y=ny;}
      else{
        o.vx*=-1;o.vy*=-1;
        if(!inside(rs,o.x,o.y)){
          var z=rs[(Math.random()*rs.length)|0];
          o.x=rnd(z.x0,z.x1);o.y=rnd(z.y0,z.y1);
        }
      }
    }
    ctx.save();
    ctx.beginPath();
    for(var i=0;i<rs.length;i++)ctx.rect(rs[i].x0,rs[i].y0,rs[i].x1-rs[i].x0,rs[i].y1-rs[i].y0);
    ctx.clip();
    for(var i=0;i<nodes.length;i++)for(var j=i+1;j<nodes.length;j++){
      var d=Math.hypot(nodes[i].x-nodes[j].x,nodes[i].y-nodes[j].y);
      if(d<LINK){
        ctx.strokeStyle='rgba(47,84,255,'+(0.26*(1-d/LINK)).toFixed(3)+')';
        ctx.lineWidth=1;
        ctx.beginPath();ctx.moveTo(nodes[i].x,nodes[i].y);ctx.lineTo(nodes[j].x,nodes[j].y);ctx.stroke();
      }
    }
    for(var i=0;i<nodes.length;i++){
      ctx.fillStyle='rgba(47,84,255,.7)';
      ctx.beginPath();ctx.arc(nodes[i].x,nodes[i].y,1.6,0,7);ctx.fill();
    }
    for(var k=pulses.length-1;k>=0;k--){
      var pu=pulses[k];pu.t+=.03;
      if(pu.t>=1){pulses.splice(k,1);continue;}
      ctx.fillStyle='rgba(6,182,212,.9)';
      ctx.beginPath();
      ctx.arc(pu.a.x+(pu.b.x-pu.a.x)*pu.t,pu.a.y+(pu.b.y-pu.a.y)*pu.t,2.3,0,7);
      ctx.fill();
    }
    if(frame%34===0)spawnPulse();
    ctx.restore();
    requestAnimationFrame(tick);
  }
  function resize(){
    W=window.innerWidth;H=window.innerHeight;
    // Explicit CSS size, exactly like the header's resize(): a canvas is a
    // REPLACED element, so inset:0 does not stretch it - without this line
    // it displays at its attribute size (W*dpr), which on hiDPI screens
    // rendered everything dpr-times too big and past the margins.
    cv.style.width=W+'px';cv.style.height=H+'px';
    cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);
    build();
  }
  resize();
  requestAnimationFrame(tick);
  var t;window.addEventListener('resize',function(){clearTimeout(t);t=setTimeout(resize,200);});
  window.addEventListener('load',function(){resize();});
})();
</script>"""


BRAND_ARENA_JS = """<script>
(function(){
  if(!matchMedia('(prefers-reduced-motion: no-preference)').matches) return;
  var host=document.querySelector('.arena-head'); if(!host) return;
  var cv=host.querySelector('canvas.arena'); if(!cv) return;
  var ctx=cv.getContext('2d'); if(!ctx) return;
  var dpr=Math.min(window.devicePixelRatio||1,2),W=0,H=0,nodes=[],pulses=[],frame=0,LINK=120;
  function rnd(a,b){return a+Math.random()*(b-a);}
  function resize(){var r=host.getBoundingClientRect(); if(r.width<2||r.height<2)return false;
    W=r.width;H=r.height;cv.style.width=W+'px';cv.style.height=H+'px';
    cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);return true;}
  function build(){nodes=[];var n=Math.max(6,Math.min(14,Math.round(W/70)));
    for(var i=0;i<n;i++)nodes.push({x:rnd(0,W),y:rnd(0,H),vx:rnd(-.3,.3),vy:rnd(-.3,.3)});}
  function spawn(){if(nodes.length<2)return;var a=nodes[(Math.random()*nodes.length)|0],c=null,cd=LINK;
    for(var i=0;i<nodes.length;i++){if(nodes[i]===a)continue;var d=Math.hypot(nodes[i].x-a.x,nodes[i].y-a.y);if(d<cd){cd=d;c=nodes[i];}}
    if(c)pulses.push({a:a,b:c,t:0});}
  function tick(){frame++;ctx.clearRect(0,0,W,H);
    for(var i=0;i<nodes.length;i++){var o=nodes[i];o.x+=o.vx;o.y+=o.vy;if(o.x<0||o.x>W)o.vx*=-1;if(o.y<0||o.y>H)o.vy*=-1;}
    for(var i=0;i<nodes.length;i++)for(var j=i+1;j<nodes.length;j++){var d=Math.hypot(nodes[i].x-nodes[j].x,nodes[i].y-nodes[j].y);
      if(d<LINK){ctx.strokeStyle='rgba(47,84,255,'+(0.26*(1-d/LINK))+')';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(nodes[i].x,nodes[i].y);ctx.lineTo(nodes[j].x,nodes[j].y);ctx.stroke();}}
    for(var i=0;i<nodes.length;i++){ctx.fillStyle='rgba(47,84,255,.7)';ctx.beginPath();ctx.arc(nodes[i].x,nodes[i].y,1.6,0,7);ctx.fill();}
    for(var p=pulses.length-1;p>=0;p--){var pu=pulses[p];pu.t+=.03;if(pu.t>=1){pulses.splice(p,1);continue;}
      ctx.fillStyle='rgba(6,182,212,.9)';ctx.beginPath();ctx.arc(pu.a.x+(pu.b.x-pu.a.x)*pu.t,pu.a.y+(pu.b.y-pu.a.y)*pu.t,2.3,0,7);ctx.fill();}
    if(frame%34===0)spawn();
    requestAnimationFrame(tick);}
  if(resize()){build();requestAnimationFrame(tick);
    var t;window.addEventListener('resize',function(){clearTimeout(t);t=setTimeout(function(){if(resize())build();},200);});}
})();
</script>"""

# Custom styled tooltips for any element with a data-tip attribute (e.g. table
# column headers). Desktop: hover. Touch: tap (a "?" badge marks them on small
# screens via CSS). One reused bubble appended to <body> so it is never clipped
# by a table's overflow.
BRAND_TIP_JS = """<script>
(function(){
  var tip;
  function ensure(){ if(!tip){ tip=document.createElement('div'); tip.className='eg-tip'; document.body.appendChild(tip);} return tip; }
  function show(el){
    var t=el.getAttribute('data-tip'); if(!t) return;
    var e=ensure(); e.textContent=t; e.dataset.for=t; e.style.display='block'; e.style.left='0px'; e.style.top='0px';
    var r=el.getBoundingClientRect(), w=e.offsetWidth, vw=document.documentElement.clientWidth;
    var cx=r.left+window.scrollX+r.width/2, left=cx-w/2;
    left=Math.max(window.scrollX+8, Math.min(left, window.scrollX+vw-w-8));
    e.style.left=left+'px'; e.style.top=(r.bottom+window.scrollY+10)+'px';
    e.style.setProperty('--tipx', (cx-left)+'px');
    requestAnimationFrame(function(){ e.classList.add('on'); });
  }
  var cur=null;
  function hide(){ cur=null; if(tip){ tip.classList.remove('on'); tip.style.display='none'; tip.dataset.for=''; } }
  function closestTip(t){ return (t && t.closest) ? t.closest('[data-tip]') : null; }
  // Event delegation, so elements tagged with data-tip AFTER load (e.g. names
  // that turn out to be truncated) get tooltips too, with no re-wiring.
  document.addEventListener('mouseover', function(e){ var el=closestTip(e.target); if(el && el!==cur){ cur=el; show(el); } });
  document.addEventListener('mouseout',  function(e){ var el=closestTip(e.target); if(el && el===cur){ hide(); } });
  document.addEventListener('click',     function(e){ var el=closestTip(e.target);
    if(el){ if(cur===el){ hide(); } else { cur=el; show(el); } } else { hide(); } });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
})();
</script>"""

# Re-skin sheet for the host-only tools (dashboard, logs viewer), whose markup
# uses its own class names. Maps their bespoke colors (#007bff / #28a745 /
# #2c3e50 / #3498db / dark headers) onto the brand palette + fonts. Insert before
# </head>; pair with BRANDBAR in the .header and BRAND_FOOTER before </body>.
BRAND_TOOL_OVERRIDE = """
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <link rel="icon" href="data:image/svg+xml,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'><rect%20width='32'%20height='32'%20rx='7'%20fill='%232f54ff'/><g%20fill='none'%20stroke='%23fff'%20stroke-width='1.6'%20stroke-linecap='round'%20stroke-linejoin='round'%20transform='translate%284%204%29'><path%20d='M6%206H18C19.6%206%2020.4%207.1%2020.9%208.7%2021.7%2011.2%2022.6%2015%2021%2017.6%2020%2019.3%2017.7%2019.1%2016.7%2017.2%2015.7%2015.3%2014.4%2014.7%2012%2014.7%209.6%2014.7%208.3%2015.3%207.3%2017.2%206.3%2019.1%204%2019.3%203%2017.6%201.4%2015%202.3%2011.2%203.1%208.7%203.6%207.1%204.4%206%206%206Z'/><path%20d='M5%206.6%2012%2012.1%2019%206.6'/></g></svg>">
  <style>
    :root{--blue:#2f54ff;--blue-d:#2546e6;--ink:#0a0a0b;--ink2:#3c3f45;--muted:#6b7078;
      --line:#e7e9ec;--card-line:#c6ccd6;--green:#16a34a;--bg-alt:#f4f5f7;--th-bg:#eef1fe;
      --shadow:0 1px 2px rgba(10,10,11,.04),0 8px 30px rgba(10,10,11,.05);}
    body{font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif !important;
      background:#fff !important; color:var(--ink) !important; -webkit-font-smoothing:antialiased;}
    h1,h2,h3{font-family:"Space Grotesk","Inter",sans-serif !important; letter-spacing:-.01em;}
    a{color:var(--blue);}
    .header{background:var(--ink) !important; border-radius:0 0 14px 14px !important;}
    .header h1,.header p{color:#fff !important;}
    .brandbar{display:flex;align-items:center;gap:8px;color:#fff;
      font-family:"Space Grotesk","Inter",sans-serif;font-weight:700;font-size:1rem;margin-bottom:8px;}
    .brandbar svg{height:18px;width:auto;stroke:#fff;}
    .stats,.filter-section,.main-messages,.agent-column,.no-messages,.session-card,.session-overview{
      border:1px solid var(--line) !important; box-shadow:var(--shadow) !important;
      border-radius:12px !important; background:#fff !important;}
    .filter-tab.active{border-bottom-color:var(--blue) !important; color:var(--blue) !important;}
    .filter-btn.active{background:var(--blue) !important; border-color:var(--blue) !important; color:#fff !important;}
    .agent-column-header{background:var(--blue) !important;}
    .agent-column.agent2 .agent-column-header{background:var(--green) !important;}
    .compare-btn{background:var(--blue) !important;} .compare-btn:hover{background:var(--blue-d) !important;}
    .message.highlight{border-color:var(--blue) !important; background:#f4f6ff !important;}
    .message.moderator{border-color:#e3b6ba !important; background:#fdf5f5 !important;}
    .to,.status-sent{color:var(--blue) !important;}
    .status-delivered,.status-active,.score{color:var(--green) !important;}
    .from{color:#c4444f !important;}
    .agent{border:1px solid var(--line) !important; border-radius:10px !important; background:var(--bg-alt) !important;}
    .body{background:var(--bg-alt) !important; font-family:"JetBrains Mono",monospace !important;}
    .session-card{border:1px solid var(--line) !important; background:var(--bg-alt) !important;}
    .session-title{color:var(--ink) !important;}
    .back-btn{background:var(--blue) !important; border-radius:8px !important;}
    .back-btn:hover{background:var(--blue-d) !important;}
    .brand-footer{max-width:1100px;margin:42px auto 0;padding:18px 4px 6px;border-top:1px solid var(--line);
      display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;color:var(--muted);font-size:.82rem;}
    .brand-footer span,.brand-footer a{display:inline-flex;align-items:center;gap:6px;}
    .brand-footer a{color:var(--muted) !important;text-decoration:none;}
    .brand-footer a:hover{color:var(--blue) !important;}
    .brand-footer .sep{color:var(--line);gap:0;}
    .brand-footer .bf-spacer{flex:1;}
    .brand-footer svg{width:14px;height:14px;flex:none;}
    .brand-footer .bf-withai{width:18px;height:18px;margin:-2px 0;}
    .brand-footer .bf-ext{width:12px;height:12px;}
  </style>
"""


# Celebratory winner modal (shown on the competition leaderboard once the comp has
# ended). CSS + JS are static; the modal markup (with the winner) is built per-render
# in leaderboard.py and only emitted when ended. JS no-ops if the modal is absent.
BRAND_WINNER_CSS = """
  <style>
    .win-modal { position:fixed; inset:0; z-index:1000; display:flex; align-items:center;
      justify-content:center; padding:20px; background:rgba(12,14,22,.55);
      -webkit-backdrop-filter:blur(4px); backdrop-filter:blur(4px);
      opacity:0; transition:opacity .25s ease; }
    .win-modal.on { opacity:1; }
    .win-confetti { position:fixed; inset:0; width:100%; height:100%; pointer-events:none; z-index:1; }
    .win-card { position:relative; z-index:2; width:100%; max-width:640px;
      background:var(--surface,#fff); color:var(--ink,#0a0a0b); border-radius:18px;
      padding:26px 30px 30px; box-shadow:0 30px 80px rgba(10,10,11,.35), 0 4px 14px rgba(10,10,11,.18);
      opacity:0; transform:scale(.92) translateY(16px);
      transition:transform .42s cubic-bezier(.2,.9,.3,1.25), opacity .3s ease; }
    .win-modal.on .win-card { opacity:1; transform:none; }
    .win-head { display:flex; align-items:center; gap:14px; margin-bottom:18px; }
    .win-trophy svg { width:42px; height:42px; stroke:var(--accent-blue); }
    .win-eyebrow { font-size:.7rem; font-weight:800; letter-spacing:.22em;
      text-transform:uppercase; color:var(--accent-blue); }
    .win-sub { font-size:.85rem; color:var(--muted,#6b7078); margin-top:2px; }
    .podium { display:flex; align-items:flex-end; gap:14px; }
    .pod { flex:1; min-width:0; text-align:center; opacity:0; transform:translateY(18px);
      transition:opacity .4s ease, transform .45s cubic-bezier(.2,.9,.3,1.2); }
    .win-modal.on .pod { opacity:1; transform:none; }
    .win-modal.on .pod.p2 { transition-delay:.25s; }
    .win-modal.on .pod.p1 { transition-delay:.55s; }
    .win-modal.on .pod.p3 { transition-delay:.05s; }
    .pod-medal svg, .pod-medal { width:34px; height:34px; margin:0 auto 4px; }
    .pod-medal.gold { color:#f5b301; } .pod-medal.silver { color:#9aa3b0; }
    .pod-medal.bronze { color:#cd7f32; }
    .pod-place { font-size:.68rem; font-weight:800; letter-spacing:.14em;
      text-transform:uppercase; color:var(--muted,#6b7078); }
    .pod-name { font-family:"Space Grotesk","Inter",sans-serif; font-weight:700;
      font-size:1.05rem; margin:2px 0; overflow:hidden; text-overflow:ellipsis;
      white-space:nowrap; }
    .pod.p1 .pod-name { font-size:1.3rem; }
    .pod-stats { font-family:"JetBrains Mono",ui-monospace,monospace; font-size:.72rem;
      color:var(--muted,#6b7078); margin-bottom:10px; }
    .pod-bar { border-radius:10px 10px 0 0; display:flex; align-items:flex-start;
      justify-content:center; padding-top:8px; color:#fff;
      font-family:"Space Grotesk","Inter",sans-serif; font-weight:700; font-size:1.1rem;
      transform-origin:bottom; transform:scaleY(0); transition:transform .5s cubic-bezier(.2,.8,.3,1.1); }
    .win-modal.on .pod-bar { transform:none; }
    .win-modal.on .p2 .pod-bar { transition-delay:.35s; }
    .win-modal.on .p1 .pod-bar { transition-delay:.65s; }
    .win-modal.on .p3 .pod-bar { transition-delay:.15s; }
    .p1 .pod-bar { height:110px; background:linear-gradient(180deg,#f5b301,#d99a00); }
    .p2 .pod-bar { height:78px; background:linear-gradient(180deg,#a8b0bc,#8b95a3); }
    .p3 .pod-bar { height:58px; background:linear-gradient(180deg,#cd7f32,#b06a25); }
    .win-note { margin:20px auto 0; max-width:480px; text-align:center;
      font-size:.8rem; color:var(--muted,#6b7078); line-height:1.5; }
    .win-note code { font-family:"JetBrains Mono",ui-monospace,monospace;
      font-size:.74rem; background:var(--surface-2,#f4f6f9); padding:.1rem .35rem;
      border-radius:5px; }
    .win-close { display:block; margin:12px auto 0;
      background:var(--accent-blue); color:#fff; border:0; border-radius:9px;
      padding:.55rem 1.1rem; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; }
    .win-close:hover { background:var(--accent-blue-d,#2546e6); }
    @media (max-width:560px) { .podium { gap:8px; } .pod-name { font-size:.9rem; }
      .pod.p1 .pod-name { font-size:1.05rem; } }
    @media (prefers-reduced-motion: reduce) {
      .win-modal, .win-card, .pod, .pod-bar { transition:none !important; }
      .win-card, .pod { opacity:1; transform:none; } .pod-bar { transform:none; }
      .win-confetti { display:none; }
    }
  </style>
"""

BRAND_WINNER_JS = """<script>
(function(){
  var m = document.getElementById('winModal');
  if(!m) return;
  if(sessionStorage.getItem('eg_win_seen') === '1'){ m.parentNode && m.parentNode.removeChild(m); return; }
  requestAnimationFrame(function(){ m.classList.add('on'); });
  // lightweight canvas confetti (no library)
  var cv = document.getElementById('winConfetti');
  if(cv && matchMedia('(prefers-reduced-motion: no-preference)').matches){
    var ctx = cv.getContext('2d'), W, H, parts = [],
        COLORS = ['#2f54ff','#16a34a','#f5b301','#8b3dff','#ff4d6d','#06b6d4'];
    function size(){ W = cv.width = window.innerWidth; H = cv.height = window.innerHeight; }
    size(); window.addEventListener('resize', size);
    for(var i=0;i<150;i++) parts.push({
      x: Math.random()*W, y: -20 - Math.random()*H*0.6,
      r: 4 + Math.random()*5, c: COLORS[i % COLORS.length],
      vy: 2 + Math.random()*3.5, vx: -1.4 + Math.random()*2.8,
      rot: Math.random()*6.28, vr: -0.25 + Math.random()*0.5 });
    var t0 = null, dur = 4500;
    function tick(ts){
      if(!t0) t0 = ts; var el = ts - t0; ctx.clearRect(0,0,W,H);
      for(var i=0;i<parts.length;i++){ var p = parts[i];
        p.x += p.vx; p.y += p.vy; p.vy += 0.03; p.rot += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.c; ctx.fillRect(-p.r/2, -p.r/2, p.r, p.r*0.62); ctx.restore(); }
      if(el < dur) requestAnimationFrame(tick); else ctx.clearRect(0,0,W,H);
    }
    requestAnimationFrame(tick);
  }
  function close(){ try{ sessionStorage.setItem('eg_win_seen','1'); }catch(e){}
    m.classList.remove('on'); setTimeout(function(){ m.parentNode && m.parentNode.removeChild(m); }, 320); }
  var btn = document.getElementById('winClose'); if(btn) btn.addEventListener('click', close);
  m.addEventListener('click', function(e){ if(e.target === m) close(); });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape') close(); });
})();
</script>"""
