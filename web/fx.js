'use strict';
/* Decorative layer only: living map background, hero word rotator, example chips, card spotlight,
   button ripples, header shadow and the "working" timer. It never touches plan data or the network,
   and every part is guarded so a failure here cannot break planning. Motion is skipped entirely
   when the user prefers reduced motion. */
(function () {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fine = matchMedia('(pointer: fine)').matches;
  const $ = id => document.getElementById(id);
  const guard = fn => { try { fn(); } catch (e) { /* decoration must never break the app */ } };

  /* ---- topographic background: nested wobbly contour rings around a few "hills" ---- */
  guard(function topo() {
    const svg = $('topo'); if (!svg) return;
    const NS = 'http://www.w3.org/2000/svg', W = 1200, H = 800;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const hills = [[210, 190, 12], [930, 560, 13], [1040, 150, 8]];
    for (const [cx, cy, rings] of hills) {
      const g = document.createElementNS(NS, 'g'); g.setAttribute('class', 'hill');
      const ph = [rnd() * 6.28, rnd() * 6.28, rnd() * 6.28], am = [0.1 + rnd() * 0.07, 0.07 + rnd() * 0.05, 0.04 + rnd() * 0.04];
      for (let k = 1; k <= rings; k++) {
        const base = 26 + k * 30, pts = [];
        for (let a = 0; a <= 72; a++) {
          const t = a / 72 * 6.2832, grow = 1 + k * 0.012;
          const r = base * (1 + grow * (am[0] * Math.sin(2 * t + ph[0] + k * 0.12) + am[1] * Math.sin(3 * t + ph[1]) + am[2] * Math.sin(5 * t + ph[2] - k * 0.1)));
          pts.push((cx + r * Math.cos(t) * 1.25).toFixed(1) + ' ' + (cy + r * Math.sin(t)).toFixed(1));
        }
        const path = document.createElementNS(NS, 'path');
        path.setAttribute('d', 'M' + pts.join('L') + 'Z');
        if (k % 4 === 0) path.setAttribute('style', 'stroke-width:2;opacity:.8');
        g.appendChild(path);
      }
      svg.appendChild(g);
    }
    if (reduce || !fine) return;
    let raf = 0, nx = 0, ny = 0;
    addEventListener('pointermove', e => {
      nx = (e.clientX / innerWidth - 0.5) * -26; ny = (e.clientY / innerHeight - 0.5) * -18;
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; svg.style.setProperty('--px', nx.toFixed(1) + 'px'); svg.style.setProperty('--py', ny.toFixed(1) + 'px'); });
    }, { passive: true });
  });

  /* ---- hero: rotating promise word (the accessible text stays "checked") ---- */
  guard(function rotator() {
    const el = $('rot'); if (!el || reduce) return;
    const words = ['checked', 'budget-aware', 'rain-aware', 'on-time', 'realistic'];
    let i = 0;
    setInterval(() => {
      if (document.hidden || !el.offsetParent) return;
      el.classList.add('out');
      setTimeout(() => {
        i = (i + 1) % words.length; el.textContent = words[i];
        el.classList.remove('out'); el.classList.add('in');
        requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('in')));
      }, 360);
    }, 3200);
  });

  /* ---- example request chips fill the textarea ---- */
  guard(function examples() {
    const box = $('examples'), req = $('req'); if (!box || !req) return;
    box.addEventListener('click', e => {
      const b = e.target.closest('button.eg'); if (!b) return;
      req.value = b.dataset.text || '';
      req.dispatchEvent(new Event('input', { bubbles: true }));
      req.focus({ preventScroll: true });
      if (!reduce) { req.animate([{ boxShadow: '0 0 0 0 rgba(14,116,144,.5)' }, { boxShadow: '0 0 0 8px rgba(14,116,144,0)' }], { duration: 550 }); }
    });
  });

  /* ---- card spotlight follows the pointer ---- */
  guard(function spotlight() {
    if (reduce || !fine) return;
    let raf = 0, last = null;
    document.addEventListener('pointermove', e => {
      const card = e.target.closest && e.target.closest('.card'); if (!card) return;
      last = [card, e.clientX, e.clientY];
      if (!raf) raf = requestAnimationFrame(() => {
        raf = 0; const [c, x, y] = last, r = c.getBoundingClientRect();
        c.style.setProperty('--mx', (x - r.left) + 'px'); c.style.setProperty('--my', (y - r.top) + 'px');
      });
    }, { passive: true });
  });

  /* ---- button ripple ---- */
  guard(function ripples() {
    if (reduce) return;
    document.addEventListener('pointerdown', e => {
      const b = e.target.closest && e.target.closest('button'); if (!b || b.disabled || b.classList.contains('icon')) return;
      const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2;
      const s = document.createElement('span'); s.className = 'ripple';
      s.style.cssText = 'width:' + d + 'px;height:' + d + 'px;left:' + (e.clientX - r.left - d / 2) + 'px;top:' + (e.clientY - r.top - d / 2) + 'px';
      b.appendChild(s); s.addEventListener('animationend', () => s.remove());
    });
  });

  /* ---- sticky header gains a hairline once the page scrolls ---- */
  guard(function header() {
    const t = $('top'); if (!t) return;
    const on = () => t.classList.toggle('scrolled', scrollY > 6);
    addEventListener('scroll', on, { passive: true }); on();
  });

  /* ---- working state: spinning compass + elapsed timer ---- */
  let timer = 0;
  window.fx = {
    working(on) {
      guard(() => {
        document.body.classList.toggle('busy', !!on);
        clearInterval(timer);
        const out = $('workTimer'); if (!on || !out) return;
        const t0 = Date.now(); out.textContent = '0 s';
        timer = setInterval(() => {
          const w = $('working');
          if (!w || w.classList.contains('hide')) { clearInterval(timer); document.body.classList.remove('busy'); return; }
          out.textContent = Math.round((Date.now() - t0) / 1000) + ' s';
        }, 500);
      });
    }
  };
})();
