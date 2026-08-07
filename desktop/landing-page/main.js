/* Mimir landing page — scroll behaviour.
 *
 * No dependencies, nothing loaded from a third party. A page arguing that
 * nothing about you leaves your computer should not be quietly reporting your
 * visit to a CDN.
 *
 * Everything here is decoration. An inline script in <head> adds the `js`
 * class, and only that class hides anything, so if this file never runs the
 * page is a normal visible document rather than a blank one — the classic
 * failure of scroll-reveal sites.
 */

(function () {
  'use strict';

  var root = document.documentElement;

  var reduced =
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* Last line of defence.
   *
   * Every rule that hides anything is scoped to .js, so dropping that class
   * turns the page back into a plain visible document. Called whenever the
   * animation cannot be trusted to run.
   *
   * This is not hypothetical. IntersectionObserver is driven by the rendering
   * lifecycle, and there are real environments where that lifecycle does not
   * tick -- a backgrounded or non-compositing webview, some embedded browsers,
   * some headless renderers. Verified here: in a pane that was not compositing
   * frames, a freshly constructed observer watching an element sitting in the
   * middle of the viewport fired exactly zero times.
   *
   * Without this, that case is a completely blank page. The scroll animation
   * is decoration; being readable is not.
   */
  function abandonAnimation() {
    root.classList.remove('js');
    // The counters start at 0 in the markup so they have something to count
    // from. Dropping the animation without settling them leaves the page
    // stating that a week of Sentry footage is "0 clips", of which "0" are
    // worth opening -- not a missing effect, an actively false claim.
    settleCounters();
  }

  function settleCounters() {
    var pending = document.querySelectorAll('[data-count-to]');
    for (var c = 0; c < pending.length; c++) {
      pending[c].textContent = pending[c].getAttribute('data-count-to');
    }
  }

  /* --------------------------------------------------------- reveals --- */

  var revealables = document.querySelectorAll('[data-reveal]');

  if (reduced || !('IntersectionObserver' in window)) {
    abandonAnimation();
  } else {
    var everFired = false;

    var revealObserver = new IntersectionObserver(
      function (entries) {
        everFired = true;
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-visible');
          // One-shot. Re-animating on the way back up is a tic, not an effect.
          revealObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' }
    );

    for (var j = 0; j < revealables.length; j++) {
      revealObserver.observe(revealables[j]);
    }

    // Something is always on screen at load, so a healthy observer reports
    // back almost immediately. Silence here means it never will.
    window.setTimeout(function () {
      if (!everFired) abandonAnimation();
    }, 1200);
  }

  /* ------------------------------------------------- counting numbers --- */

  function easeOut(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function countUp(el) {
    var target = parseInt(el.getAttribute('data-count-to'), 10);
    if (isNaN(target)) return;

    if (reduced) {
      el.textContent = String(target);
      return;
    }

    var duration = 1100;
    var started = null;

    function frame(now) {
      if (started === null) started = now;
      var progress = Math.min((now - started) / duration, 1);
      el.textContent = String(Math.round(easeOut(progress) * target));
      if (progress < 1) requestAnimationFrame(frame);
    }

    requestAnimationFrame(frame);
  }

  var counters = document.querySelectorAll('[data-count-to]');

  if (reduced || !('IntersectionObserver' in window)) {
    settleCounters();
  } else {
    var countObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          countUp(entry.target);
          countObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.6 }
    );

    for (var m = 0; m < counters.length; m++) {
      countObserver.observe(counters[m]);
    }

    // A number that scrolled past without the observer reporting is still
    // wrong on screen, so settle anything left at zero regardless.
    window.setTimeout(function () {
      for (var n = 0; n < counters.length; n++) {
        if (counters[n].textContent.trim() === '0') {
          counters[n].textContent = counters[n].getAttribute('data-count-to');
        }
      }
    }, 4000);
  }

  /* ------------------------------------------------------ sorting demo --- */

  var buckets = document.querySelector('[data-sort-demo]');

  if (buckets) {
    if (reduced || !('IntersectionObserver' in window)) {
      buckets.classList.add('is-sorting');
    } else {
      var sortObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('is-sorting');
            sortObserver.unobserve(entry.target);
          });
        },
        { threshold: 0.35 }
      );
      sortObserver.observe(buckets);
    }
  }

  /* ----------------------------------------------------- sticky header --- */

  var nav = document.getElementById('nav');

  if (nav) {
    // A sentinel beats a scroll listener: no work on every frame, and it stays
    // correct if the hero's height changes at a breakpoint.
    var sentinel = document.createElement('div');
    sentinel.setAttribute('aria-hidden', 'true');
    sentinel.style.cssText = 'position:absolute;top:0;height:1px;width:1px;';
    document.body.insertBefore(sentinel, document.body.firstChild);

    if ('IntersectionObserver' in window) {
      new IntersectionObserver(
        function (entries) {
          nav.classList.toggle('is-stuck', !entries[0].isIntersecting);
        },
        { threshold: 0 }
      ).observe(sentinel);
    }
  }
})();
