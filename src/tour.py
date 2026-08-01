"""Shared guided-tour widget for the competitor-facing pages.

One spotlight + card walkthrough, used by the leaderboard, watch, history and
agent-stats pages so they all behave identically. Each page supplies its own
steps; a step whose target element is missing is skipped, so a tour never
spotlights empty space.

Pages chain together with ``?tour=1``: arriving with that flag auto-starts the
tour and the last step can hand off to the next page. The flag is stripped from
the URL as soon as the tour starts, which matters because the leaderboard
meta-refreshes every 120s - otherwise every refresh would restart the tour.

Usage in a page template:
    from src.tour import TOUR_CSS, TOUR_HTML, tour_js
    ... f"<style>{TOUR_CSS}</style>"
    ... TOUR_HTML
    ... f"<script>{tour_js(STEPS, next_url='/history')}</script>"
where STEPS is a list of {"sel", "title", "body"} dicts. ``body`` may contain
<b> tags.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

__all__ = ["TOUR_CSS", "TOUR_HTML", "TOUR_LINK", "tour_js"]

TOUR_CSS = """
  /* ease-out only - never a bezier that undershoots or overshoots its ends, so
     the cutout closes on the target from one side. Suppressed entirely while a
     tour-driven scroll is running (.tr-moving), where the cutout is positioned
     every frame and a transition would make it trail the content. */
  .tr-spot { position:fixed; z-index:200; border-radius:12px; pointer-events:none; display:none;
             box-shadow:0 0 0 9999px rgba(10,10,11,.38);
             transition:top .3s ease-out, left .3s ease-out, width .3s ease-out, height .3s ease-out; }
  .tr-moving .tr-spot, .tr-moving .tr-card { transition:none !important; }
  .tr-on .tr-spot { display:block; }
  /* overflow-y:auto with a JS-set max-height, as a last resort only: the card
     scrolls when it cannot fit the SCREEN, not merely when it cannot fit beside
     its target - there it is allowed to overlay instead. */
  .tr-card { position:fixed; z-index:201; width:min(360px, calc(100vw - 32px)); background:#fff;
             border-radius:14px; box-shadow:0 18px 50px rgba(10,10,11,.28); padding:17px 19px 15px;
             display:none; color:#0a0a0b; overflow-y:auto; overscroll-behavior:contain;
             transition:top .3s ease-out, left .3s ease-out; }
  .tr-on .tr-card { display:block; }
  .tr-step { font-size:.66rem; letter-spacing:.13em; text-transform:uppercase; color:#2f54ff; font-weight:700; }
  .tr-card h3 { font-size:1.05rem; margin:.3rem 0 .35rem; }
  .tr-card p { margin:0; font-size:.89rem; line-height:1.55; color:#3c3f45; }
  .tr-nav { display:flex; align-items:center; gap:8px; margin-top:14px; }
  .tr-dots { display:flex; gap:5px; margin-right:auto; }
  .tr-dot { width:6px; height:6px; border-radius:50%; background:#d9dce0; }
  .tr-dot.on { background:#2f54ff; }
  .tr-btn { font:inherit; font-size:.85rem; border-radius:8px; padding:7px 14px; cursor:pointer;
            border:1px solid var(--line-2,#d9dce0); background:#fff; color:var(--ink,#0a0a0b); }
  .tr-btn.pri { background:var(--accent-blue,#2f54ff); border-color:var(--accent-blue,#2f54ff); color:#fff; }
  .tr-skip { position:fixed; z-index:201; top:16px; right:16px; display:none; background:rgba(255,255,255,.94);
             border:0; border-radius:8px; padding:8px 14px; font:inherit; font-size:.83rem;
             cursor:pointer; color:#0a0a0b; }
  .tr-on .tr-skip { display:block; }
  /* A real button, not an underlined link: this is an action, and it has to
     look identical on every page (the leaderboard previously styled its own,
     which left it as a raw browser button once that CSS was removed). */
  .tr-open { display:inline-flex; align-items:center; gap:.4rem; background:#fff;
             border:1px solid var(--line-2,#d9dce0); border-radius:999px; color:var(--accent-blue,#2f54ff); font:inherit;
             font-size:.8rem; font-weight:600; cursor:pointer; padding:.34rem .8rem;
             line-height:1.2; white-space:nowrap; text-decoration:none;
             transition:background .15s ease, border-color .15s ease, color .15s ease; }
  /* A compass, not an arrow: this starts a guided walkthrough, it does not
     advance or navigate onward. Drawn as a mask so it inherits the text colour
     on hover. The left padding is 1px tighter than the right because a leading
     icon reads as heavier than the space after the text - equal padding makes
     the pair look shifted right even when it is geometrically centred. */
  .tr-open { padding-left:.72rem; padding-right:.82rem; }
  .tr-open::before { content:""; width:.92rem; height:.92rem; flex:none; align-self:center;
             background:currentColor;
             -webkit-mask:var(--eg-tour) center/.92rem .92rem no-repeat;
             mask:var(--eg-tour) center/.92rem .92rem no-repeat; }
  .tr-open:hover { background:var(--accent-blue,#2f54ff); border-color:var(--accent-blue,#2f54ff); color:#fff; }
  .tr-open:focus-visible { outline:2px solid #2f54ff; outline-offset:2px; }
  @media (prefers-reduced-motion: reduce){ .tr-spot,.tr-card{ transition:none; } }
  @media (max-width:640px){ .tr-card{ left:16px!important; right:16px; width:auto; top:auto!important; bottom:16px; } }
"""

TOUR_HTML = """
  <div class="tr-spot" id="trSpot" aria-hidden="true"></div>
  <button class="tr-skip" id="trSkip" type="button">Skip tour</button>
  <div class="tr-card" id="trCard" role="dialog" aria-modal="true" aria-live="polite">
    <div class="tr-step" id="trStep"></div>
    <h3 id="trTitle"></h3>
    <p id="trBody"></p>
    <div class="tr-nav">
      <div class="tr-dots" id="trDots"></div>
      <button class="tr-btn" id="trPrev" type="button">Back</button>
      <button class="tr-btn pri" id="trNext" type="button">Next</button>
    </div>
  </div>
"""

# Drop this anywhere a page wants a manual trigger.
#
# Deliberately NOT "Take a tour": the number of steps is only known when the
# tour opens, because steps whose target is missing are dropped. The watch page
# is three steps during a match but one between them; history is two with a
# match open and one on the list. "Take a tour" promises a walkthrough that
# sometimes turns out to be a single card - and a button that renamed itself as
# the page changed would be worse. This wording is accurate at any length.
TOUR_LINK = '<button class="tr-open" id="trOpen" type="button">How this page works</button>'

_JS = """
(function () {
  var RAW = %(steps)s, PAGE_KEY = %(page_key)s, AUTO = %(auto)s;
  // Resolve each step's target. A step may list several selectors (first
  // VISIBLE one wins) and may ask to spotlight the whole containing section
  // rather than just the heading it points at. Anything missing or hidden is
  // dropped, so a tour never highlights empty space - e.g. the "your agent"
  // card only exists once the viewer has opened their own watch link.
  function vis(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    // offsetParent is null for position:fixed elements even when perfectly
    // visible (the match map), so fixed elements pass on their rect alone.
    var fixed = getComputedStyle(el).position === 'fixed';
    return (el.offsetParent !== null || fixed) && r.width > 4 && r.height > 4;
  }
  function resolve(s) {
    var list = s.sel.split('|');
    for (var k = 0; k < list.length; k++) {
      var el = document.querySelector(list[k].trim());
      if (vis(el)) return s.sec ? (el.closest('section') || el) : el;
    }
    return null;
  }
  var STEPS = [];

  // Sticky and fixed elements stay pinned to an edge as the page scrolls, so a
  // target scrolled flush to the top lands UNDERNEATH them - on the leaderboard
  // the sticky "You" card sat over the very row a step was pointing at. Treat
  // the bands they occupy as unusable, so both the scroll target and the card
  // are placed in the space that is actually visible.
  //
  // Collected once when the tour opens (cheap enough, and the set does not
  // change mid-tour); the tour's own chrome is excluded.
  var occluders = [];
  function findOccluders() {
    occluders = [];
    var all = document.body.querySelectorAll('*');
    for (var k = 0; k < all.length; k++) {
      var el = all[k];
      if (el === spot || el === card || el === skip || card.contains(el)) continue;
      var cs = getComputedStyle(el);
      if (cs.position !== 'sticky' && cs.position !== 'fixed') continue;
      // Only genuine pinned BANDS occlude: full-width bars a card must stay
      // clear of. Decorations that cover the whole screen but catch nothing
      // (the margin-ambience canvas) and corner widgets (the match map) are
      // not bands - counting them swallowed the entire viewport and pushed
      // every card off screen.
      if (cs.pointerEvents === 'none') continue;
      if (el.offsetWidth < window.innerWidth * 0.6) continue;
      occluders.push(el);
    }
  }
  // Height of the pinned band at an edge. Uses the element's own offset rather
  // than its current rect: a sticky element is only AT its offset once scrolled
  // to, and we need the band it will occupy after the move, not before it.
  function inset(side, target) {
    var px = 0;
    for (var k = 0; k < occluders.length; k++) {
      var el = occluders[k];
      // A step that points AT a sticky element must not treat it as something
      // to avoid - step 4 highlights the buttons inside the sticky "You" card,
      // and excluding its own band would make the target unreachable.
      if (target && (el === target || el.contains(target) || target.contains(el))) continue;
      if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') continue;
      var cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      var off = parseFloat(cs[side]);
      if (isNaN(off)) continue;                 // not anchored to this edge
      px = Math.max(px, off + el.offsetHeight);
    }
    return px;
  }

  var spot = document.getElementById('trSpot'), card = document.getElementById('trCard'),
      skip = document.getElementById('trSkip'), dots = document.getElementById('trDots'),
      eS = document.getElementById('trStep'), eT = document.getElementById('trTitle'),
      eB = document.getElementById('trBody'), prev = document.getElementById('trPrev'),
      next = document.getElementById('trNext');
  if (!spot || !card) return;
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  var i = 0, running = false;

  var dotEls = [];
  // Steps are chosen when the tour OPENS, not when the script runs. Panels like
  // #app are empty until their data arrives, so filtering at load time would
  // throw away every step and leave the trigger button dead.
  // A step with no selector is deliberate: some things are worth saying and have
  // nowhere sensible to point, so they get a centred card and no cutout.
  function build() {
    STEPS = RAW.filter(function (s) { return !s.sel || resolve(s); });
    dots.innerHTML = '';
    STEPS.forEach(function () {
      var d = document.createElement('span'); d.className = 'tr-dot'; dots.appendChild(d);
    });
    dotEls = Array.prototype.slice.call(dots.children);
    return STEPS.length > 0;
  }

  function centreCard() {
    if (window.innerWidth <= 640) return;
    var cw = card.offsetWidth || 360, ch = card.offsetHeight || 180;
    card.style.top = Math.max(8, (window.innerHeight - ch) / 2) + 'px';
    card.style.left = Math.max(8, (window.innerWidth - cw) / 2) + 'px';
  }

  function place() {
    var s = STEPS[i];
    // Re-resolve every time: the board replaces #live-root on each refresh, so
    // a reference cached at load can go stale and measure as a 0x0 box in the
    // corner. Looking it up fresh also lets a step point at something that
    // only appears later.
    var el = s.sel ? resolve(s) : null;
    if (!el) {                       // no target: dim the page, centre the card
      spot.style.width = '0px'; spot.style.height = '0px';
      spot.style.top = '-80px'; spot.style.left = '-80px';
      centreCard();
      return;
    }
    var r = el.getBoundingClientRect(), pad = 8;
    // Clip the padded rect to the viewport rather than clamping only its size.
    // Clamping the height alone meant a target taller than the screen (or one
    // scrolled partly off it) produced a cutout that started at the element's
    // top edge but ran to the bottom of the WINDOW, so the spotlight had a real
    // top edge and an arbitrary bottom one. Intersecting instead keeps both
    // edges on the element whenever they are on screen.
    var vh = window.innerHeight, vw = window.innerWidth, gap = 14, edge = 8;
    // Clip to the band the pinned headers/footers leave free, so the cutout
    // never extends under one and the card is never placed beneath one.
    var vTop = inset('top', el), vBot = vh - inset('bottom', el);
    var top = Math.max(vTop + 6, r.top - pad), left = Math.max(6, r.left - pad);
    var right = Math.min(vw - 6, r.right + pad);
    var bottom = Math.min(vBot - 6, r.bottom + pad);

    // The spotlight is never resized to make room for anything: shrinking it was
    // worse than the problem it solved, collapsing the cutout to a sliver over a
    // target that was perfectly visible. The CARD moves instead.
    var w = Math.max(0, right - left), h = Math.max(0, bottom - top);
    spot.style.top = top + 'px'; spot.style.left = left + 'px';
    spot.style.width = w + 'px'; spot.style.height = h + 'px';

    if (vw <= 640) return;                 // card is pinned to the bottom by CSS

    // Measure the card at its natural height. It is only capped when it cannot
    // fit the screen at all - shrinking it to squeeze beside a big target just
    // trades one unreadable thing for another.
    card.style.maxHeight = Math.max(140, vBot - vTop - edge * 2) + 'px';
    var ch = card.offsetHeight || 180, cw = card.offsetWidth || 360;

    // Prefer below, then above. When the target is big enough that neither side
    // can hold the card, let the card sit over it: a target filling the screen
    // is not something you lose track of, and covering part of it is better
    // than a cramped or clipped explanation. targetScrollY already avoids this
    // whenever a scroll position exists that fits both.
    var roomBelow = vBot - bottom - gap - edge;
    var roomAbove = top - vTop - gap - edge;
    var ct;
    if (roomBelow >= ch)      ct = bottom + gap;
    else if (roomAbove >= ch) ct = top - gap - ch;
    else                      ct = vTop + (vBot - vTop - ch) / 2;   // overlay, centred
    ct = Math.min(Math.max(vTop + edge, ct), Math.max(vTop + edge, vBot - ch - edge));
    card.style.top = ct + 'px';
    card.style.left = Math.min(Math.max(edge, left), Math.max(edge, vw - cw - edge)) + 'px';
  }
  // Animate to the target, decelerating into it, never travelling past it.
  //
  // We drive the scroll ourselves rather than using scrollIntoView({behavior:
  // 'smooth'}): its easing is the browser's to choose, it can drift past the
  // mark on long jumps, and it gives no signal to keep the cutout in step - so
  // the spotlight had to be re-placed blindly for a fixed 700ms and could
  // settle late. Here the position is eased with easeOutCubic, which is
  // strictly increasing and reaches its destination exactly once, so the target
  // is approached from one side and never passed. The destination is clamped to
  // the document's scroll range first, so there is nothing beyond it to reach.
  var anim = null;

  // The NEAREST scroll position at which the whole target and the whole card are
  // both on screen.
  //
  // Not the position that centres them: centring moves the page further than it
  // needs to, and the goal is only that both are fully visible. So we solve for
  // the feasible range of scroll offsets - separately for "card below target"
  // and "card above target" - and take the closest point in either to where the
  // page already is. Nothing gets clipped and nothing gets squeezed, because the
  // fit is guaranteed before the move rather than patched up afterwards.
  function targetScrollY(el) {
    var vh = window.innerHeight, pad = 8, gap = 14, edge = 8;
    var r = el.getBoundingClientRect();
    var now = window.scrollY;
    var elTop = now + r.top - pad, elBot = now + r.bottom + pad;
    var elH = elBot - elTop;
    // Usable band, in viewport coordinates: what is left after the pinned
    // headers and footers take their share.
    var vTop = inset('top', el) + edge, vBot = vh - inset('bottom', el) - edge;
    var avail = vBot - vTop;

    // Measure the card at its natural height; place() may cap it later.
    var prevMax = card.style.maxHeight;
    card.style.maxHeight = 'none';
    var ch = (window.innerWidth <= 640) ? 0 : (card.offsetHeight || 180) + gap;
    card.style.maxHeight = prevMax;

    var max = Math.max(0, document.documentElement.scrollHeight - vh);
    var clamp = function (y) { return Math.max(0, Math.min(y, max)); };

    // Target alone cannot fit with the card anywhere: show as much of it as
    // possible from the top of the usable band and let place() do its best.
    if (elH + ch > avail) return clamp(elTop - vTop);

    // Feasible scroll ranges. "y" is the scroll offset, so the usable band spans
    // [y + vTop, y + vBot]; every constraint below is "stays inside that band".
    function nearestIn(lo, hi) {                 // closest point of [lo, hi] to now
      if (lo > hi) return null;                  // no feasible position this way
      return Math.min(Math.max(now, lo), hi);
    }
    var below = nearestIn(elBot + ch - vBot, elTop - vTop);
    var above = nearestIn(elBot - vBot, elTop - ch - vTop);

    var best = null;
    [below, above].forEach(function (y) {
      if (y === null) return;
      if (best === null || Math.abs(y - now) < Math.abs(best - now)) best = y;
    });
    return clamp(best === null ? elTop - vTop : best);
  }

  function animateScrollTo(to, onFrame) {
    if (anim) cancelAnimationFrame(anim);
    clearTimeout(recentre);
    var from = window.scrollY, delta = to - from;
    if (reduce || Math.abs(delta) < 2) {      // already there, or motion is unwanted
      window.scrollTo(0, to);
      if (onFrame) onFrame();
      return;
    }
    // Distance-proportional, within sane bounds: a short hop should not take as
    // long as a full-page jump. Paced so the movement is legible - you should be
    // able to follow where the page is taking you, not just notice it arrived.
    var dur = Math.min(1100, Math.max(520, Math.abs(delta) * 1.25));
    var t0 = null;
    document.documentElement.classList.add('tr-moving');
    (function frame(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);     // easeOutCubic: monotonic, ends at exactly 1
      window.scrollTo(0, from + delta * eased);
      if (onFrame) onFrame();                 // cutout tracks the content every frame
      if (p < 1) { anim = requestAnimationFrame(frame); }
      else { anim = null; document.documentElement.classList.remove('tr-moving'); if (onFrame) onFrame(); }
    })(performance.now());
  }
  function scrollToEl(el) {
    // A fixed element is wherever it is regardless of scroll - the document-
    // coordinate math would chase a phantom position. Just place around it.
    if (getComputedStyle(el).position === 'fixed') { place(); return; }
    animateScrollTo(targetScrollY(el), place);
  }
  function show(n) {
    i = Math.max(0, Math.min(STEPS.length - 1, n));
    var s = STEPS[i], solo = STEPS.length === 1;
    // A one-step tour is not a sequence: no "Step 1 of 1", no progress dots.
    eS.style.display = solo ? 'none' : '';
    dots.style.display = solo ? 'none' : '';
    if (!solo) eS.textContent = 'Step ' + (i + 1) + ' of ' + STEPS.length;
    eT.textContent = s.title; eB.innerHTML = s.body;
    prev.style.visibility = i === 0 ? 'hidden' : 'visible';
    next.textContent = solo ? 'Got it' : ((i === STEPS.length - 1) ? 'Done' : 'Next');
    // "Skip tour" is meaningless when there is nothing to skip past - a single
    // card is a note, not a walkthrough, so its dismiss reads as a dismiss.
    skip.textContent = solo ? 'Close' : 'Skip tour';
    // On the last step there is nothing left to skip past - "Done" is right
    // there - so the skip leaves, and stepping back brings it back.
    skip.style.display = (i === STEPS.length - 1) ? 'none' : '';
    dotEls.forEach(function (d, k) { d.classList.toggle('on', k === i); });
    // Pages can style around the current target (the watch page keeps its map
    // translucent during other steps and solid during its own).
    try { document.documentElement.setAttribute('data-tour-sel', s.sel || ''); } catch (e) {}
    var el = s.sel ? resolve(s) : null;
    if (el) scrollToEl(el); else place();
    // One more pass next frame: web fonts and images can resize the target
    // between the jump and the first paint, which would leave the cutout a few
    // pixels off. Cheap, and it runs once rather than for 700ms.
    requestAnimationFrame(place);
  }
  var originY = null;                        // where the reader was before we moved them
  function start() {
    if (running) return;
    findOccluders();
    if (!build()) return;
    running = true;
    // Pages can react to the tour taking the screen (the watch page minimizes
    // its match map for the duration, and restores it after).
    try { if (window.egTourWillStart) window.egTourWillStart(); } catch (e) {}
    originY = window.scrollY;
    document.documentElement.classList.add('tr-on');
    show(0);
  }
  function stop() {
    running = false;
    try { document.documentElement.removeAttribute('data-tour-sel'); } catch (e) {}
    try { if (window.egTourDidStop) window.egTourDidStop(); } catch (e) {}
    if (anim) { cancelAnimationFrame(anim); anim = null; }   // never keep scrolling a closed tour
    clearTimeout(recentre);
    document.documentElement.classList.remove('tr-on');
    document.documentElement.classList.remove('tr-moving');
    // Put the reader back where they were. The tour scrolled the page for its
    // own purposes; ending it should not leave them somewhere they never chose
    // to be, halfway down a board they were reading the top of.
    if (originY !== null) {
      var back = originY;
      originY = null;
      animateScrollTo(Math.max(0, Math.min(back,
        Math.max(0, document.documentElement.scrollHeight - window.innerHeight))), null);
    }
  }
  function finish() { stop(); }

  next.addEventListener('click', function () { (i === STEPS.length - 1) ? finish() : show(i + 1); });
  prev.addEventListener('click', function () { show(i - 1); });
  skip.addEventListener('click', stop);
  document.addEventListener('keydown', function (e) {
    if (!running) return;
    if (e.key === 'Escape') stop();
    else if (e.key === 'ArrowRight') { e.preventDefault(); (i === STEPS.length - 1) ? finish() : show(i + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); show(i - 1); }
  });
  window.addEventListener('resize', function () { if (running) place(); });

  // Scrolling away from the highlighted element leaves the tour describing
  // something you cannot see. Bring it back - but only once scrolling has
  // STOPPED, and only when the target has actually left the comfortable middle
  // of the screen, so this never fights a deliberate scroll mid-gesture.
  var recentre = null;
  function backToTarget() {
    if (!running) return;
    var s = STEPS[i];
    if (!s || !s.sel) return;                 // a no-target step has nothing to return to
    var el = resolve(s);
    if (!el) return;
    var r = el.getBoundingClientRect(), vh = window.innerHeight;
    var gone = r.bottom < vh * 0.15 || r.top > vh * 0.85;
    if (gone) scrollToEl(el);
  }
  window.addEventListener('scroll', function () {
    if (!running) return;
    place();                                  // keep the cutout glued while scrolling
    // Ignore the scroll events our OWN animation generates. Arming the timer on
    // them meant that as soon as a move finished, a second move could fire from
    // its own tail - which is what made the page appear to travel in steps.
    if (anim) return;
    clearTimeout(recentre);
    recentre = setTimeout(backToTarget, 350);
  }, { passive: true });

  var t = document.getElementById('trOpen');
  if (t) t.addEventListener('click', start);

  // Auto-start the FIRST time someone lands on this page, then never again.
  // Marked as seen the moment it opens, not on completion, so that a page which
  // meta-refreshes (the leaderboard does, every 120s) cannot relaunch the tour
  // mid-read. ?tour=1 forces it open again for testing and is stripped from the
  // URL immediately for the same reason.
  var forced = /[?&]tour=1(&|$)/.test(window.location.search);
  if (forced) {
    try {
      var u = new URL(window.location.href);
      u.searchParams.delete('tour');
      history.replaceState(null, '', u.pathname + (u.search || '') + u.hash);
    } catch (e) {}
  }
  var KEY = 'emailgame_tour_' + PAGE_KEY;
  function tourSeen() {
    try { if (localStorage.getItem(KEY) === '1') return true; } catch (e) {}
    // Seen on the OTHER environment (local sandbox vs hosted): the shared
    // first-run state carries it across origins.
    try {
      if (window.EG_FIRSTRUN && window.EG_FIRSTRUN.flags['tour_' + PAGE_KEY]) return true;
    } catch (e) {}
    return false;
  }
  function tourLaunch() {
    try { localStorage.setItem(KEY, '1'); } catch (e) {}
    try { if (window.EG_FIRSTRUN) window.EG_FIRSTRUN.mark('tour_' + PAGE_KEY); } catch (e) {}
    // Retry a few times: these pages fetch their content, so the first attempt
    // can land before there is anything to point at.
    var tries = 0;
    (function attempt() {
      // The full-screen walkthrough owns first contact: while it is on
      // screen, wait - a tour card floating over the intro (and its Skip
      // stealing the corner) is two guides talking at once.
      var tut = document.getElementById('tut');
      if (tut && !tut.hidden) { setTimeout(attempt, 900); return; }
      start();
      if (!running && ++tries < 6) setTimeout(attempt, 700);
    })();
  }
  if (forced) {
    tourLaunch();
  } else if (AUTO) {
    // Decide only once the shared first-run state has answered (or its
    // timeout fired) - never before, or the suppression could race the tour.
    var tourGo = function () { if (!tourSeen()) tourLaunch(); };
    if (window.EG_FIRSTRUN) window.EG_FIRSTRUN.ready(tourGo); else tourGo();
  }
})();
"""


def tour_js(steps: List[Dict[str, str]], page_key: str, auto: bool = True) -> str:
    """Return the tour script for a page.

    ``steps``     list of {"sel", "title", "body"}; ``body`` may contain <b>.
    ``page_key``  short id for this page ("board", "watch", ...). Each page
                  auto-runs its tour once, tracked separately under that key,
                  so arriving somewhere new always explains itself while
                  nothing ever repeats.
    ``auto``      False = never auto-run; the tour opens only from its
                  "How this page works" button (or ?tour=1). For pages whose
                  first contact is owned by the interactive walkthrough.
    """
    return FIRSTRUN_JS + (_JS % {
        "steps": json.dumps(steps),
        "page_key": json.dumps(page_key),
        "auto": json.dumps(bool(auto)),
    })


# Shared first-run state: which intros/tours this human has already seen,
# fetched from the server so "once ever" holds across the local sandbox and
# the hosted game (browser storage cannot cross origins - third-party storage
# is partitioned). localStorage stays as the instant, offline fallback; the
# server fetch only ADDS suppression, and a 1.5s timeout guarantees a first
# visit is never left waiting for it.
FIRSTRUN_JS = r"""
if (!window.EG_FIRSTRUN) (function () {
  var agent = "";
  try { agent = new URLSearchParams(location.search).get("agent") || ""; } catch (e) {}
  var F = { flags: {}, _r: false, _cbs: [] };
  F.ready = function (cb) { if (F._r) { try { cb(); } catch (e) {} } else F._cbs.push(cb); };
  function done() {
    if (F._r) return;
    F._r = true;
    for (var i = 0; i < F._cbs.length; i++) { try { F._cbs[i](); } catch (e) {} }
  }
  F.mark = function (flag) {
    F.flags[flag] = true;
    try {
      fetch("/firstrun_flags", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agent, flag: flag }) });
    } catch (e) {}
  };
  window.EG_FIRSTRUN = F;
  try {
    fetch("/firstrun_flags/" + encodeURIComponent(agent || "-"))
      .then(function (r) { return r.json(); })
      .then(function (j) { F.flags = (j && j.flags) || {}; done(); })
      .catch(done);
  } catch (e) { done(); }
  setTimeout(done, 1500);
})();
"""
