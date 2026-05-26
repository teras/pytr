/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */
/**
 * For You frontend integration.
 *
 * Strict gate: nothing renders unless the bootstrap response includes
 * foryou_available=true. PYTR's existing UI is untouched when the sidecar is
 * down — the contract from .claude/discovery-plan.md.
 */
(() => {
  const STATE = {
    available: false,
    onboardingComplete: false,
    feeds: [],
    activeFeedId: null,
    privacyMode: 'balanced',
    persona: '',
    llmAvailable: false,
    // Infinite-scroll cursor for the currently active For You feed.
    pageOffset: 0,
    pageSize: 30,
    hasMore: false,
    loadingMore: false,
    scrollObserver: null,
    // Per-feed seen-video-id set so we never paint duplicates as the user
    // scrolls. Cleared every time the user switches tabs.
    renderedIds: new Set(),
  };

  async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
    const ct = r.headers.get('content-type') || '';
    return ct.includes('json') ? r.json() : r.text();
  }

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k === 'onclick') e.addEventListener('click', v);
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else if (k.startsWith('aria-') || k === 'role') e.setAttribute(k, v);
      else e[k] = v;
    }
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return e;
  }

  // ── Initialise ──────────────────────────────────────────────────────────

  window.foryouInit = async function (bootData) {
    if (!bootData || !bootData.foryou_available) return;
    STATE.available = true;
    try {
      const [status, settings, feedsResp] = await Promise.all([
        api('GET', '/api/foryou/onboarding/status'),
        api('GET', '/api/foryou/settings'),
        api('GET', '/api/foryou/feeds'),
      ]);
      STATE.onboardingComplete = !!status.complete;
      STATE.privacyMode = settings.privacy_mode || 'balanced';
      STATE.llmAvailable = !!settings.llm_available;
      STATE.persona = settings.persona_text || '';
      STATE.feeds = feedsResp.feeds || [];
    } catch (e) {
      console.warn('foryou init failed:', e);
      STATE.available = false;
      return;
    }
    if (!STATE.onboardingComplete) {
      _renderOnboardingPrompt();
    } else {
      _injectFeedTabs();
    }
    _injectSettingsSection();
    window.addEventListener('message', _onMessage);
  };

  function _onMessage(e) {
    if (e.data === 'foryou-onboarding-complete') {
      STATE.onboardingComplete = true;
      _closeModal();
      // Reload feeds and render tabs.
      api('GET', '/api/foryou/feeds').then((r) => {
        STATE.feeds = r.feeds || [];
        _injectFeedTabs();
      });
    } else if (e.data === 'foryou-onboarding-cancel') {
      _closeModal();
    }
  }

  // ── Onboarding modal ────────────────────────────────────────────────────

  function _renderOnboardingPrompt() {
    const existing = document.getElementById('foryou-onb-banner');
    if (existing) return;
    const banner = el(
      'div',
      { id: 'foryou-onb-banner', class: 'foryou-banner' },
      el('span', {}, 'Καλωσήρθες στο For You — '),
      el('button', { class: 'foryou-onb-btn', onclick: _openOnboarding }, 'Ξεκίνα'),
    );
    const header = document.querySelector('header');
    if (header && header.parentNode) {
      header.parentNode.insertBefore(banner, header.nextSibling);
    } else {
      document.body.prepend(banner);
    }
  }

  function _openOnboarding() {
    const overlay = el('div', { id: 'foryou-modal-overlay', class: 'foryou-modal-overlay' });
    const iframe = el('iframe', {
      src: '/api/foryou/onboarding/ui',
      class: 'foryou-onb-iframe',
      title: 'For You onboarding',
    });
    overlay.appendChild(iframe);
    document.body.appendChild(overlay);
  }

  function _closeModal() {
    document.getElementById('foryou-modal-overlay')?.remove();
    document.getElementById('foryou-onb-banner')?.remove();
  }

  // ── Feed tabs (extra tabs in the home tab bar) ─────────────────────────

  function _injectFeedTabs() {
    const listTabs = document.getElementById('list-tabs');
    if (!listTabs) return;
    // Remove pre-existing For You tabs (idempotent).
    listTabs.querySelectorAll('.list-tab.foryou-tab, .foryou-refresh-all').forEach((b) => b.remove());
    for (const f of STATE.feeds) {
      const btn = el('button', {
        class: 'list-tab foryou-tab',
        dataset: { tab: `foryou:${f.feed_id}`, kind: f.kind },
        onclick: () => _activateFoYouTab(f.feed_id),
      });
      btn.textContent = f.label || f.kind;
      btn.title = `For You · ${f.kind}`;
      listTabs.appendChild(btn);
    }
    // "Refresh all" pill — small, neutral, end of the tab strip.
    const refreshAll = el('button', {
      class: 'foryou-refresh-all',
      title: 'Ξαναφτιάχνει όλα τα feeds στο παρασκήνιο',
      onclick: async (e) => {
        e.target.disabled = true;
        const prev = e.target.textContent;
        e.target.textContent = '⟳ Σε εξέλιξη…';
        try {
          const r = await api('POST', '/api/foryou/feeds/refresh-all');
          e.target.textContent = `Ανανέωση ${r.queued} feeds`;
        } catch {
          e.target.textContent = 'Απέτυχε';
        }
        setTimeout(() => {
          e.target.disabled = false;
          e.target.textContent = prev;
          if (STATE.activeFeedId) _activateFoYouTab(STATE.activeFeedId);
        }, 8000);
      },
    }, '⟳ Ανανέωση όλων');
    listTabs.appendChild(refreshAll);
  }

  async function _activateFoYouTab(feedId) {
    STATE.activeFeedId = feedId;
    STATE.pageOffset = 0;
    STATE.hasMore = false;
    STATE.loadingMore = false;
    STATE.renderedIds = new Set();
    _disconnectScrollObserver();
    document.querySelectorAll('.list-tab').forEach((b) =>
      b.classList.toggle('active', b.dataset.tab === `foryou:${feedId}`),
    );
    const grid = document.getElementById('video-grid');
    if (!grid) return;
    grid.innerHTML = '<p style="opacity:.6;padding:20px;">Φόρτωση…</p>';
    let data;
    try {
      data = await api('GET', `/api/foryou/feeds/${feedId}/items?offset=0&limit=${STATE.pageSize}`);
    } catch (e) {
      _renderEmpty(grid, feedId);
      return;
    }
    if (!data.videos || data.videos.length === 0) {
      _renderEmpty(grid, feedId);
      return;
    }
    STATE.pageOffset = data.videos.length;
    STATE.hasMore = !!data.has_more;
    _renderFeedItems(grid, data, feedId, /*append=*/false);
    _setupScrollObserver(grid, feedId);
  }

  function _disconnectScrollObserver() {
    if (STATE.scrollObserver) {
      STATE.scrollObserver.disconnect();
      STATE.scrollObserver = null;
    }
    document.getElementById('foryou-load-sentinel')?.remove();
  }

  function _setupScrollObserver(grid, feedId) {
    if (!STATE.hasMore) return;
    STATE.scrollObserver = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting || STATE.loadingMore || !STATE.hasMore) return;
      _loadMore(grid, feedId);
    }, { threshold: 0.1, rootMargin: '200px' });
    _reattachSentinel(grid, feedId);
  }

  async function _loadMore(grid, feedId, attempt = 0) {
    STATE.loadingMore = true;
    try {
      const data = await api('GET',
        `/api/foryou/feeds/${feedId}/items?offset=${STATE.pageOffset}&limit=${STATE.pageSize}`);
      if (STATE.activeFeedId !== feedId) return; // user switched tab mid-flight
      if (data.videos && data.videos.length > 0) {
        _renderFeedItems(grid, data, feedId, /*append=*/true);
        STATE.pageOffset += data.videos.length;
      }
      STATE.hasMore = !!data.has_more;
      // Server is fetching more — keep the sentinel alive and poll until items
      // arrive or the feed is genuinely exhausted.
      if (data.pending_expansion && !data.exhausted && attempt < 6) {
        setTimeout(() => _loadMore(grid, feedId, attempt + 1), 2500);
        return;
      }
      if (!STATE.hasMore || data.exhausted) {
        _disconnectScrollObserver();
      } else {
        // Re-attach the sentinel at the new tail so the observer fires again
        // on the next scroll-to-bottom.
        _reattachSentinel(grid, feedId);
      }
    } catch (e) {
      console.warn('foryou load-more failed', e);
    } finally {
      STATE.loadingMore = false;
    }
  }

  function _reattachSentinel(grid, feedId) {
    if (!STATE.scrollObserver) return;
    document.getElementById('foryou-load-sentinel')?.remove();
    const sentinel = el('div', { id: 'foryou-load-sentinel', class: 'foryou-load-sentinel' });
    grid.appendChild(sentinel);
    STATE.scrollObserver.observe(sentinel);
  }

  function _renderEmpty(grid, feedId) {
    grid.innerHTML = `
      <p style="opacity:.7;padding:20px;">
        Δεν υπάρχουν ακόμη videos σε αυτό το feed.
        <button class="foryou-refresh-btn" id="foryou-refresh-now">Δημιουργία τώρα</button>
      </p>`;
    document.getElementById('foryou-refresh-now')?.addEventListener('click', async (ev) => {
      ev.target.disabled = true;
      ev.target.textContent = 'Σε εξέλιξη…';
      await api('POST', `/api/foryou/feeds/${feedId}/refresh`);
      setTimeout(() => _activateFoYouTab(feedId), 8000);
    });
  }

  function _renderFeedItems(grid, data, feedId, append = false) {
    // Reuse PYTR's own card factory so styles, focus-rings, navigation,
    // duration badges, and TV-mode focus traversal all match.
    // Dedupe against what we've already painted in this tab session.
    const fresh = data.videos.filter((v) => v.video_id && !STATE.renderedIds.has(v.video_id));
    fresh.forEach((v) => STATE.renderedIds.add(v.video_id));
    if (fresh.length === 0) return;
    const items = fresh.map((v) => ({
      id: v.video_id,
      title: v.title || '',
      channel: v.channel_name || '',
      thumbnail: v.thumbnail_url || `https://i.ytimg.com/vi/${v.video_id}/hqdefault.jpg`,
      duration: v.duration_seconds || 0,
      duration_str: _fmtDuration(v.duration_seconds),
      is_live: false,
      _why: v.why || '',
    }));

    if (!append) {
      // First paint: wipe placeholder content and start clean.
      grid.innerHTML = '';
    } else {
      // Keep the sentinel out of the way while we insert new cards before it.
      document.getElementById('foryou-load-sentinel')?.remove();
    }

    // Build new cards in a fragment first → one DOM write, no layout thrash.
    const fragment = document.createDocumentFragment();
    const tmp = document.createElement('div');
    tmp.innerHTML = items.map(window.createVideoCard).join('');
    const newCards = [...tmp.children];
    newCards.forEach((card, i) => {
      const v = items[i];
      _decorateCard(card, v);
      fragment.appendChild(card);
    });
    grid.appendChild(fragment);

    if (typeof window.attachCardListeners === 'function') {
      window.attachCardListeners(grid);
    }
    // First-paint banner only — never on append (would jump the scroll).
    if (!append && data.generation_age_sec !== undefined && data.generation_age_sec !== null) {
      const ageMin = Math.round(data.generation_age_sec / 60);
      const banner = el('div', { class: 'foryou-stale' },
        `Δημιουργήθηκε πριν ~${ageMin} λεπτά. `,
        el('button', {
          class: 'foryou-refresh-btn',
          onclick: () => api('POST', `/api/foryou/feeds/${feedId}/refresh`)
            .then(() => alert('Νέα παραγωγή στο background.')),
        }, 'Refresh'));
      grid.prepend(banner);
    }
  }

  function _decorateCard(card, v) {
    const info = card.querySelector('.video-info');
    if (v._why && info) {
      const why = document.createElement('div');
      why.className = 'foryou-why';
      why.textContent = v._why;
      info.appendChild(why);
    }
    const fb = document.createElement('div');
    fb.className = 'foryou-fb';
    fb.innerHTML = '<button title="👍">👍</button><button title="👎">👎</button><button title="Ποτέ ξανά">🚫</button>';
    const [up, down, never] = fb.querySelectorAll('button');
    up.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); _sendFb(v.id, 'thumbs_up'); });
    down.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); _sendFb(v.id, 'thumbs_down'); });
    never.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); _sendFb(v.id, 'never_again'); card.remove(); });
    card.appendChild(fb);
    card.addEventListener('click', () => {
      api('POST', '/api/foryou/impression', { video_id: v.id, feed_kind: 'home', clicked: true }).catch(() => {});
    });
    api('POST', '/api/foryou/impression', { video_id: v.id, feed_kind: 'home' }).catch(() => {});
  }

  function _fmtDuration(seconds) {
    if (!seconds) return '';
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    const m = Math.floor(seconds / 60) % 60;
    const h = Math.floor(seconds / 3600);
    return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${s}` : `${m}:${s}`;
  }

  async function _sendFb(video_id, signal) {
    await api('POST', '/api/foryou/feedback', { video_id, signal });
  }

  // ── Settings → For You section ─────────────────────────────────────────

  function _injectSettingsSection() {
    document.addEventListener('foryou-settings-open', _renderSettingsPanel);
  }

  async function _renderSettingsPanel() {
    let s;
    try { s = await api('GET', '/api/foryou/settings'); }
    catch (e) { return; }
    let m;
    try { m = await api('GET', '/api/foryou/metrics'); }
    catch { m = {}; }
    const host = document.getElementById('foryou-settings-host');
    if (!host) return;
    host.innerHTML = '';
    host.appendChild(_settingsPanel(s, m));
  }

  function _settingsPanel(s, m) {
    const panel = el('div', { class: 'foryou-settings' });
    panel.appendChild(el('h3', {}, 'For You'));
    panel.appendChild(el('div', { class: 'foryou-row' },
      el('strong', {}, 'Status:'),
      ` ${s.llm_available ? 'Ready' : 'No LLM'} · backend=${s.llm_backend}`));
    panel.appendChild(el('div', { class: 'foryou-row' },
      el('strong', {}, 'Privacy mode:'),
      ' ',
      _privacySelect(s.privacy_mode || 'balanced')));
    panel.appendChild(el('div', { class: 'foryou-row' },
      el('strong', {}, 'Hit rate (30d):'),
      ` ${m.hit_rate_30d_pct ?? '—'} %  ·  (7d): ${m.hit_rate_7d_pct ?? '—'} %`));
    panel.appendChild(el('div', { class: 'foryou-row' },
      el('strong', {}, 'Sparsity:'),
      ` ${s.sparsity_state} (richness=${Number(s.signal_richness || 0).toFixed(2)})`));
    // Persona editor.
    const persona = el('textarea', { class: 'foryou-persona', rows: 6 });
    persona.value = s.persona_text || '';
    panel.appendChild(el('div', { class: 'foryou-row' }, el('strong', {}, 'Persona:')));
    panel.appendChild(persona);
    panel.appendChild(el('div', { class: 'foryou-row' },
      el('button', { onclick: async () => {
        await api('PUT', '/api/foryou/persona', { persona_text: persona.value });
        alert('Persona saved.');
      } }, 'Αποθήκευση persona'),
      el('button', { onclick: _openOnboarding, class: 'ghost' }, 'Re-run onboarding'),
    ));
    return panel;
  }

  function _privacySelect(current) {
    const sel = el('select', {});
    for (const m of ['fortress', 'balanced', 'cloud']) {
      const opt = el('option', { value: m }, m);
      if (m === current) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener('change', async () => {
      await api('PUT', '/api/foryou/settings', { privacy_mode: sel.value });
    });
    return sel;
  }

  // ── Public hooks for enhance-list (called by other modules opt-in) ──────

  window.foryouEnhance = async function (surface, context, baseline) {
    if (!STATE.available) return null;
    try {
      return await api('POST', '/api/foryou/enhance-list', { surface, context, baseline });
    } catch {
      return null;
    }
  };

  // Called by PYTR's video page when /api/info returns an error. Idempotent.
  window.foryouReportUnavailable = function (video_id, reason) {
    if (!STATE.available || !video_id) return;
    api('POST', '/api/foryou/report-unavailable', { video_id, reason: String(reason || '').slice(0, 200) })
      .catch(() => {});
  };
})();
