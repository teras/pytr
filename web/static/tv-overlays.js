/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */

// TV Remote / D-pad spatial navigation — Overlays
(function () {
    const _tv = window._tv;

    const OVERLAY_FOCUSABLE = '.tv-overlay-item';
    const TOP_OVERLAY_AUTO_HIDE = 5000;

    let topOverlay = null;
    let topOverlayTimer = null;
    let topRefreshInterval = null;
    let bottomOverlay = null;
    let bottomHideTimer = null;
    let topIsAutoHide = false;
    let bottomRows = [];
    let bottomRowIdx = 0;
    let bottomRowCursorIdx = {}; // defIdx → last focused item index
    let _channelDataCache = {};
    let _activeRowDefs = [];
    let _abortControllers = [];

    // ── Generic overlay helpers ──────────────────────────────────────────────

    function showOverlay(el, focusFirst) {
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (el && el.parentNode) {
                el.classList.add('visible');
                if (focusFirst !== false) {
                    const first = el.querySelector(OVERLAY_FOCUSABLE);
                    if (first) _tv.setFocus(first);
                }
            }
        }));
    }

    function getOverlayItems(overlay) {
        if (!overlay) return [];
        return [...overlay.querySelectorAll(OVERLAY_FOCUSABLE)].filter(el => {
            const dropdown = el.closest('.tv-top-dropdown');
            return !dropdown || !dropdown.classList.contains('hidden');
        });
    }

    function navigateOverlay(overlay, dir) {
        const items = getOverlayItems(overlay);
        if (!items.length) return 'exit';
        const cur = _tv.getCurrentEl();
        const idx = items.indexOf(cur);
        if (idx === -1) { _tv.setFocus(items[0]); return 'handled'; }

        if (dir === 'left') {
            if (idx > 0) { _tv.setFocus(items[idx - 1]); items[idx - 1].scrollIntoView({ block: 'nearest', inline: 'center' }); }
            return 'handled';
        }
        if (dir === 'right') {
            if (idx < items.length - 1) { _tv.setFocus(items[idx + 1]); items[idx + 1].scrollIntoView({ block: 'nearest', inline: 'center' }); }
            return 'handled';
        }

        const curRect = cur.getBoundingClientRect();
        const curY = Math.round(curRect.top);

        if (dir === 'down') {
            const below = items.filter(el => Math.round(el.getBoundingClientRect().top) > curY + 5);
            if (below.length) { _tv.setFocus(below[0]); return 'handled'; }
            return 'exit';
        }
        if (dir === 'up') {
            const above = items.filter(el => Math.round(el.getBoundingClientRect().top) < curY - 5);
            if (above.length) { _tv.setFocus(above[above.length - 1]); return 'handled'; }
            return 'exit';
        }
        return 'handled';
    }

    function _getRowDefIdx(row) { return row.dataset.defIdx; }

    function _focusRowItem(row, preferIdx) {
        const items = [...row.querySelectorAll(OVERLAY_FOCUSABLE)];
        if (!items.length) return;
        const target = items[Math.min(preferIdx, items.length - 1)];
        _tv.setFocus(target);
        target.scrollIntoView({ block: 'nearest', inline: 'center' });
    }

    function navigateBottomOverlay(dir) {
        if (!bottomRows.length) return false;
        const cur = _tv.getCurrentEl();
        const curRow = bottomRows.find(r => r.contains(cur));
        if (!curRow) { _focusRowItem(bottomRows[0], bottomRowCursorIdx[_getRowDefIdx(bottomRows[0])] || 0); return true; }
        const rowItems = [...curRow.querySelectorAll(OVERLAY_FOCUSABLE)];
        const idx = rowItems.indexOf(cur);
        if (idx >= 0) bottomRowCursorIdx[_getRowDefIdx(curRow)] = idx;

        if (dir === 'left') {
            if (idx > 0) { _tv.setFocus(rowItems[idx - 1]); rowItems[idx - 1].scrollIntoView({ block: 'nearest', inline: 'center' }); }
            return true;
        }
        if (dir === 'right') {
            if (idx < rowItems.length - 1) {
                _tv.setFocus(rowItems[idx + 1]);
                rowItems[idx + 1].scrollIntoView({ block: 'nearest', inline: 'center' });
            } else {
                maybeLoadMoreInRow(curRow);
            }
            return true;
        }
        if (dir === 'down') {
            const rowIdx = bottomRows.indexOf(curRow);
            showNextRow();
            if (rowIdx < bottomRows.length - 1) {
                const nextRow = bottomRows[rowIdx + 1];
                _focusRowItem(nextRow, bottomRowCursorIdx[_getRowDefIdx(nextRow)] || 0);
            }
            return true;
        }
        if (dir === 'up') {
            const rowIdx = bottomRows.indexOf(curRow);
            if (rowIdx > 0) {
                const prevRow = bottomRows[rowIdx - 1];
                _focusRowItem(prevRow, bottomRowCursorIdx[_getRowDefIdx(prevRow)] || 0);
            }
            return hideBottomRow();
        }
        return true;
    }

    function isOverlayOpen(overlay) {
        return overlay && overlay.classList.contains('visible');
    }

    function activeOverlay() {
        if (isOverlayOpen(topOverlay) && !topIsAutoHide) return 'top';
        if (bottomOverlay && bottomOverlay.querySelector(OVERLAY_FOCUSABLE)) {
            const cur = _tv.getCurrentEl();
            if (isOverlayOpen(bottomOverlay) || (cur && bottomOverlay.contains(cur))) return 'bottom';
        }
        return null;
    }

    // ── Top overlay (search + info + buttons) ────────────────────────────────

    function appendActionButtons(btnContainer) {
        const btnSources = [
            { el: document.getElementById('favorite-btn'), container: null, menu: null },
            { el: document.getElementById('subtitle-btn'), container: document.getElementById('subtitle-btn-container'), menu: '.subtitle-menu', optClass: '.subtitle-option' },
            { el: document.getElementById('audio-btn'), container: document.getElementById('audio-btn-container'), menu: '.audio-menu', optClass: '.audio-option' },
            { el: document.getElementById('quality-btn'), container: document.getElementById('quality-selector'), menu: '.quality-menu', optClass: '.quality-option' },
        ];
        for (const { el, container, menu, optClass } of btnSources) {
            if (!el) continue;
            if (container && container.classList.contains('hidden')) continue;
            if (!container && el.classList.contains('hidden')) continue;

            const clone = el.cloneNode(true);
            clone.removeAttribute('id');
            clone.classList.add('tv-overlay-item');

            if (menu) {
                const wrapper = document.createElement('div');
                wrapper.className = 'tv-top-menu-wrapper';
                wrapper.appendChild(clone);
                const menuEl = container.querySelector(menu);
                if (menuEl) {
                    const dropdown = document.createElement('div');
                    dropdown.className = 'tv-top-dropdown hidden';

                    function populateDropdown() {
                        dropdown.innerHTML = '';
                        // Trigger the original button to populate the menu
                        el.click();
                        menuEl.querySelectorAll(optClass).forEach(opt => {
                            const optClone = opt.cloneNode(true);
                            optClone.classList.add('tv-overlay-item');
                            optClone.addEventListener('click', (e) => {
                                e.stopPropagation();
                                opt.click();
                                dropdown.classList.add('hidden');
                                setTimeout(() => { clone.textContent = el.textContent; }, 100);
                            });
                            dropdown.appendChild(optClone);
                        });
                        // Hide the original menu that el.click() opened
                        menuEl.classList.add('hidden');
                    }

                    clone.addEventListener('click', (e) => {
                        e.stopPropagation();
                        if (topOverlay) topOverlay.querySelectorAll('.tv-top-dropdown').forEach(d => {
                            if (d !== dropdown) d.classList.add('hidden');
                        });
                        const wasHidden = dropdown.classList.contains('hidden');
                        if (wasHidden) populateDropdown();
                        dropdown.classList.toggle('hidden');
                        // Auto-focus first item when opening
                        if (wasHidden) {
                            const firstItem = dropdown.querySelector('.tv-overlay-item');
                            if (firstItem) _tv.setFocus(firstItem);
                        }
                    });
                    wrapper.appendChild(dropdown);
                }
                btnContainer.appendChild(wrapper);
            } else {
                clone.addEventListener('click', () => {
                    el.click();
                    setTimeout(() => {
                        clone.textContent = el.textContent;
                        if (el.classList.contains('favorited')) clone.classList.add('favorited');
                        else clone.classList.remove('favorited');
                    }, 100);
                });
                btnContainer.appendChild(clone);
            }
        }

    }

    function buildTopOverlay() {
        if (topOverlay) { topOverlay.remove(); topOverlay = null; }

        topOverlay = document.createElement('div');
        topOverlay.className = 'tv-top-overlay';

        // Row 1: Search
        const searchRow = document.createElement('div');
        searchRow.className = 'tv-top-search';
        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.placeholder = 'Search...';
        searchInput.className = 'tv-overlay-item';
        const origSearch = document.getElementById('search-input');
        if (origSearch && origSearch.value) searchInput.value = origSearch.value;
        const searchBtn = document.createElement('button');
        searchBtn.textContent = '\u{1F50D}';
        searchBtn.className = 'tv-overlay-item tv-top-search-btn';

        const doSearch = () => {
            const q = searchInput.value.trim();
            if (!q) return;
            hideTop();
            if (origSearch) origSearch.value = q;
            if (typeof searchVideos === 'function') searchVideos(q);
        };
        searchBtn.addEventListener('click', doSearch);
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); doSearch(); }
            else if (e.key === 'Escape') { e.preventDefault(); searchInput.blur(); hideTop(); }
            e.stopPropagation(); // don't let TV nav intercept typing
        });

        const homeBtn = document.createElement('button');
        homeBtn.className = 'tv-overlay-item tv-top-home-btn';
        homeBtn.innerHTML = '<img src="/static/pytr.svg" alt="Home">';
        homeBtn.addEventListener('click', () => {
            hideTop();
            document.getElementById('logo-link').click();
        });

        searchRow.appendChild(homeBtn);
        searchRow.appendChild(searchInput);
        searchRow.appendChild(searchBtn);

        // Row 2: buttons left, meta right
        const infoRow = document.createElement('div');
        infoRow.className = 'tv-top-info-row';

        const btnContainer = document.createElement('div');
        btnContainer.className = 'tv-top-buttons';
        appendActionButtons(btnContainer);

        // Click anywhere on overlay closes open dropdowns
        topOverlay.addEventListener('click', () => {
            topOverlay.querySelectorAll('.tv-top-dropdown').forEach(d => d.classList.add('hidden'));
        });

        // Meta (right side, not focusable)
        const metaDiv = document.createElement('div');
        metaDiv.className = 'tv-top-meta';
        fillMeta(metaDiv);

        infoRow.appendChild(btnContainer);
        infoRow.appendChild(metaDiv);
        topOverlay.appendChild(searchRow);
        topOverlay.appendChild(infoRow);
        document.body.appendChild(topOverlay);
    }

    function addMetaLine(parent, cls, text) {
        if (!text) return;
        const div = document.createElement('div');
        div.className = cls;
        div.textContent = text;
        parent.appendChild(div);
    }

    function fillMeta(metaDiv) {
        metaDiv.innerHTML = '';
        var _el;
        _el = document.getElementById('video-title'); addMetaLine(metaDiv, 'tv-top-title', _el ? _el.textContent : '');
        _el = document.getElementById('video-channel'); addMetaLine(metaDiv, 'tv-top-channel', _el ? _el.textContent : '');
        _el = document.getElementById('video-meta'); addMetaLine(metaDiv, 'tv-top-views', _el ? _el.textContent : '');
    }

    function refreshTopOverlayMeta() {
        if (!topOverlay) return;
        const metaDiv = topOverlay.querySelector('.tv-top-meta');
        if (metaDiv) fillMeta(metaDiv);
    }

    function showTop(autoHide) {
        topIsAutoHide = !!autoHide;
        buildTopOverlay();
        clearTimeout(topOverlayTimer);
        clearInterval(topRefreshInterval);
        if (autoHide) {
            showOverlay(topOverlay, false);
            topRefreshInterval = setInterval(() => refreshTopOverlayMeta(), 500);
            topOverlayTimer = setTimeout(() => hideTop(), TOP_OVERLAY_AUTO_HIDE);
        } else {
            requestAnimationFrame(() => requestAnimationFrame(() => {
                if (!topOverlay) return;
                topOverlay.classList.add('visible');
                const input = topOverlay.querySelector('input');
                if (input) _tv.setFocus(input);
            }));
        }
    }

    function hideTop() {
        topIsAutoHide = false;
        clearTimeout(topOverlayTimer);
        clearInterval(topRefreshInterval);
        topRefreshInterval = null;
        if (!topOverlay) return;
        topOverlay.classList.remove('visible');
        const input = topOverlay.querySelector('input');
        if (input && document.activeElement === input) input.blur();
        const ref = topOverlay;
        setTimeout(() => { if (ref.parentNode) ref.remove(); }, 300);
        topOverlay = null;
    }

    // ── Bottom overlay (generator-based multi-row) ─────────────────────────

    function _waitForEvent(eventName, signal) {
        return new Promise((resolve, reject) => {
            if (signal && signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
            const onReady = () => { if (signal) signal.removeEventListener('abort', onAbort); resolve(); };
            const onAbort = () => { window.removeEventListener(eventName, onReady); reject(new DOMException('Aborted', 'AbortError')); };
            window.addEventListener(eventName, onReady, { once: true });
            if (signal) signal.addEventListener('abort', onAbort, { once: true });
        });
    }

    async function _waitForChannelId(signal) {
        const chId = typeof currentVideoChannelId !== 'undefined' ? currentVideoChannelId : null;
        if (chId) return chId;
        await _waitForEvent('channel-id-ready', signal);
        return typeof currentVideoChannelId !== 'undefined' ? currentVideoChannelId : null;
    }

    function _cardsFromApiData(results, cursor) {
        const cards = results.map(item => createOverlayCard(item));
        return { cards, cursor: cursor || null };
    }

    function _getAllRowDefs() {
        return [
            {
                label: 'Queue',
                active: () => {
                    const queue = typeof window._getQueue === 'function' ? window._getQueue() : null;
                    if (queue && queue.videos && queue.videos.length) return true;
                    return new URLSearchParams(location.search).has('list');
                },
                generate: async (signal) => {
                    let queue = typeof window._getQueue === 'function' ? window._getQueue() : null;
                    if (!queue || !queue.videos || !queue.videos.length) {
                        await _waitForEvent('queue-ready', signal);
                        queue = typeof window._getQueue === 'function' ? window._getQueue() : null;
                    }
                    if (!queue || !queue.videos || !queue.videos.length) return null;
                    const cards = queue.videos.map((v, i) => {
                        const card = document.createElement('div');
                        card.className = 'tv-overlay-item related-card tv-queue-card' + (i === queue.currentIndex ? ' active' : '');
                        card.innerHTML = `<div class="thumbnail-container"><img src="https://i.ytimg.com/vi/${encodeURIComponent(v.id)}/mqdefault.jpg" loading="lazy"></div><div class="related-info"><div class="related-title">${escapeHtml(v.title || '')}</div><div class="related-channel">${escapeHtml(v.channel || '')}</div></div>`;
                        card.addEventListener('click', () => {
                            if (typeof window._playQueueItem === 'function') window._playQueueItem(i);
                            hideBottom();
                        });
                        return card;
                    });
                    return { cards, label: queue.title || 'Queue' };
                },
            },
            {
                label: 'Related',
                active: () => true,
                generate: async (signal) => {
                    let results = typeof window._getRelatedResults === 'function' ? window._getRelatedResults() : [];
                    if (!results.length) {
                        await _waitForEvent('related-ready', signal);
                        results = typeof window._getRelatedResults === 'function' ? window._getRelatedResults() : [];
                    }
                    if (!results.length) return null;
                    return _cardsFromApiData(results);
                },
            },
            {
                label: 'Channel Playlists',
                active: () => true,
                generate: async (signal) => {
                    const chId = await _waitForChannelId(signal);
                    if (!chId) return null;
                    if (_channelDataCache['playlists_' + chId]) {
                        const cached = _channelDataCache['playlists_' + chId];
                        return _cardsFromApiData(cached.results, cached.cursor);
                    }
                    const resp = await fetch(`/api/channel/${encodeURIComponent(chId)}/playlists`, { signal });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    if (!data.results || !data.results.length) return null;
                    _channelDataCache['playlists_' + chId] = data;
                    return _cardsFromApiData(data.results, data.cursor);
                },
            },
            {
                label: 'More from channel',
                active: () => true,
                generate: async (signal) => {
                    const chId = await _waitForChannelId(signal);
                    if (!chId) return null;
                    if (_channelDataCache['videos_' + chId]) {
                        const cached = _channelDataCache['videos_' + chId];
                        return _cardsFromApiData(cached.results, cached.cursor);
                    }
                    const resp = await fetch(`/api/channel/${encodeURIComponent(chId)}`, { signal });
                    if (!resp.ok) return null;
                    const data = await resp.json();
                    if (!data.results || !data.results.length) return null;
                    _channelDataCache['videos_' + chId] = data;
                    return _cardsFromApiData(data.results, data.cursor);
                },
            },
        ];
    }

    function _computeActiveRows() {
        _activeRowDefs = _getAllRowDefs().filter(def => def.active());
        bottomRowIdx = 0;
    }

    function createOverlayCard(item) {
        const card = document.createElement('div');
        card.className = 'tv-overlay-item related-card';
        const isPlaylist = item.type === 'playlist' || item.type === 'mix';
        let badge = '';
        if (isPlaylist) {
            const label = item.type === 'playlist' ? 'Playlist' : 'Mix';
            badge = (item.video_count ? `<span class="video-count">${escapeHtml(String(item.video_count))}</span>` : '') +
                    `<span class="badge-${item.type === 'mix' ? 'mix' : 'playlist'}">${label}</span>`;
        } else if (item.duration_str) {
            badge = `<span class="duration">${escapeHtml(item.duration_str)}</span>`;
        }
        const thumb = item.thumbnail || `https://i.ytimg.com/vi/${encodeURIComponent(item.id || '')}/mqdefault.jpg`;
        card.innerHTML = `<div class="thumbnail-container"><img src="${escapeAttr(thumb)}" loading="lazy">${badge}</div><div class="related-info"><div class="related-title">${escapeHtml(item.title || '')}</div>${item.channel ? `<div class="related-channel">${escapeHtml(item.channel)}</div>` : ''}</div>`;
        card.addEventListener('click', (e) => {
            e.preventDefault();
            hideBottom();
            if (isPlaylist) {
                const vid = item.first_video_id || item.id;
                const plId = item.playlist_id || item.id;
                if (typeof window._startQueue === 'function') window._startQueue(vid, item.title, item.channel || '', plId);
                else navigateToVideo(vid, item.title, item.channel || '', 0);
            } else {
                if (typeof window._closeQueue === 'function') window._closeQueue();
                navigateToVideo(item.id, item.title, item.channel || '', item.duration || 0);
            }
        });
        return card;
    }

    function buildRowEl(label) {
        const row = document.createElement('div');
        row.className = 'tv-bottom-row';
        const h = document.createElement('h3');
        h.textContent = label;
        row.appendChild(h);
        const strip = document.createElement('div');
        strip.className = 'tv-related-strip';
        row.appendChild(strip);
        return row;
    }

    function ensureBottomOverlay() {
        if (bottomOverlay) return;
        bottomOverlay = document.createElement('div');
        bottomOverlay.className = 'tv-related-overlay';
        document.body.appendChild(bottomOverlay);
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (bottomOverlay) bottomOverlay.classList.add('visible');
        }));
    }

    function buildPlaceholderRow(label) {
        const row = document.createElement('div');
        row.className = 'tv-bottom-row tv-row-placeholder';
        const h = document.createElement('h3');
        h.textContent = label;
        row.appendChild(h);
        const strip = document.createElement('div');
        strip.className = 'tv-related-strip';
        for (let i = 0; i < 1; i++) {
            const card = document.createElement('div');
            card.className = 'tv-overlay-item related-card tv-skeleton-card';
            card.innerHTML = '<div class="thumbnail-container"><div class="skeleton-text" style="width:100%;height:100%;margin:0;border-radius:0"></div></div><div class="related-info"><div class="skeleton-text"></div><div class="skeleton-text short"></div></div>';
            strip.appendChild(card);
        }
        row.appendChild(strip);
        return row;
    }

    function showNextRow() {
        if (!_activeRowDefs.length) _computeActiveRows();
        if (bottomRowIdx >= _activeRowDefs.length) return;

        const def = _activeRowDefs[bottomRowIdx];
        const defIdx = bottomRowIdx;
        bottomRowIdx++;

        const controller = new AbortController();
        _abortControllers[defIdx] = controller;

        const placeholder = buildPlaceholderRow(def.label);
        placeholder.dataset.defIdx = String(defIdx);
        placeholder.dataset.pending = '1';
        ensureBottomOverlay();
        bottomOverlay.appendChild(placeholder);
        bottomRows.push(placeholder);
        requestAnimationFrame(() => requestAnimationFrame(() => placeholder.classList.add('visible')));
        if (!bottomOverlay.contains(_tv.getCurrentEl())) {
            const first = placeholder.querySelector(OVERLAY_FOCUSABLE);
            if (first) _tv.setFocus(first);
        }
        _resolveRow(def, placeholder, controller.signal);
    }

    function _removeFailedRow(placeholder) {
        const hadFocus = placeholder.contains(_tv.getCurrentEl());
        const pIdx = bottomRows.indexOf(placeholder);
        if (pIdx !== -1) bottomRows.splice(pIdx, 1);
        if (placeholder.parentNode) {
            placeholder.classList.remove('visible');
            setTimeout(() => { if (placeholder.parentNode) placeholder.remove(); }, 300);
        }
        if (hadFocus) {
            // Move focus to nearest remaining row, or back to player
            for (let i = bottomRows.length - 1; i >= 0; i--) {
                const item = bottomRows[i].querySelector(OVERLAY_FOCUSABLE);
                if (item) { _tv.setFocus(item); return; }
            }
            _tv.enterPlayerMode();
        }
    }

    async function _resolveRow(def, placeholder, signal) {
        try {
            const result = await def.generate(signal);

            if ((signal && signal.aborted) || !placeholder.parentNode) return;

            if (!result || !result.cards || !result.cards.length) {
                _removeFailedRow(placeholder);
                return;
            }

            const label = result.label || def.label;
            const row = buildRowEl(label);
            const strip = row.querySelector('.tv-related-strip');
            result.cards.forEach(card => strip.appendChild(card));
            if (result.cursor) row.dataset.cursor = result.cursor;

            placeholder.innerHTML = '';
            while (row.firstChild) placeholder.appendChild(row.firstChild);
            delete placeholder.dataset.pending;
            if (row.dataset.cursor) placeholder.dataset.cursor = row.dataset.cursor;

            if (bottomOverlay && !bottomOverlay.contains(_tv.getCurrentEl())) {
                _focusRowItem(placeholder, bottomRowCursorIdx[placeholder.dataset.defIdx] || 0);
            }
        } catch (e) {
            if (e && e.name === 'AbortError') return;
            _removeFailedRow(placeholder);
        }
    }

    function hideBottomRow() {
        if (!bottomRows.length) return false;
        const row = bottomRows.pop();
        const defIdx = row.dataset.defIdx;
        if (defIdx != null) {
            const idx = parseInt(defIdx);
            if (_abortControllers[idx]) { _abortControllers[idx].abort(); _abortControllers[idx] = null; }
            bottomRowIdx = idx;
        }
        row.classList.remove('visible');
        setTimeout(() => { if (row.parentNode) row.remove(); }, 300);
        if (!bottomRows.length) {
            bottomRowIdx = 0;
            hideBottom();
            return false;
        }
        return true;
    }

    async function maybeLoadMoreInRow(row) {
        const cursor = row && row.dataset.cursor;
        if (!cursor || row.dataset.loading) return;
        row.dataset.loading = '1';
        try {
            const resp = await fetch(`/api/more?cursor=${encodeURIComponent(cursor)}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data.results || !data.results.length) { delete row.dataset.cursor; return; }
            const strip = row.querySelector('.tv-related-strip');
            const firstNew = createOverlayCard(data.results[0]);
            strip.appendChild(firstNew);
            data.results.slice(1).forEach(item => strip.appendChild(createOverlayCard(item)));
            if (data.cursor) row.dataset.cursor = data.cursor;
            else delete row.dataset.cursor;
            _tv.setFocus(firstNew);
            firstNew.scrollIntoView({ block: 'nearest', inline: 'center' });
        } finally {
            delete row.dataset.loading;
        }
    }

    function hideBottom() {
        clearTimeout(bottomHideTimer);
        _abortControllers.forEach(c => { if (c) c.abort(); });
        _abortControllers = [];
        _activeRowDefs = [];
        if (!bottomOverlay) return;
        bottomOverlay.classList.remove('visible');
        const ref = bottomOverlay;
        bottomHideTimer = setTimeout(() => { if (ref.parentNode) ref.remove(); }, 300);
        bottomOverlay = null;
        bottomRows = [];
        bottomRowIdx = 0;
        bottomRowCursorIdx = {};
    }

    function resetOverlayState() {
        _channelDataCache = {};
        _computeActiveRows();
    }

    // ── Dropdown navigation (vertical menus inside top overlay) ─────────────

    function closeDropdown(dropdown) {
        dropdown.classList.add('hidden');
        const wrapper = dropdown.closest('.tv-top-menu-wrapper');
        const btn = wrapper ? wrapper.querySelector('.tv-overlay-item') : null;
        if (btn) _tv.setFocus(btn);
    }

    function navigateDropdown(dropdown, dir) {
        const items = [...dropdown.querySelectorAll('.tv-overlay-item')];
        if (!items.length) { closeDropdown(dropdown); return 'handled'; }
        const cur = _tv.getCurrentEl();
        const idx = items.indexOf(cur);
        if (dir === 'up') {
            if (idx <= 0) { closeDropdown(dropdown); }
            else { _tv.setFocus(items[idx - 1]); }
            return 'handled';
        }
        if (dir === 'down') {
            if (idx >= items.length - 1) { closeDropdown(dropdown); }
            else { _tv.setFocus(items[idx + 1]); }
            return 'handled';
        }
        // left/right close dropdown
        closeDropdown(dropdown);
        return 'handled';
    }

    function navigateTopOverlayWrapped(dir) {
        const cur = _tv.getCurrentEl();
        if (cur) {
            const dropdown = cur.closest('.tv-top-dropdown');
            if (dropdown && !dropdown.classList.contains('hidden')) {
                return navigateDropdown(dropdown, dir);
            }
        }
        return navigateOverlay(topOverlay, dir);
    }

    // ── Namespace exports ────────────────────────────────────────────────────
    _tv.showTop = showTop;
    _tv.hideTop = hideTop;
    _tv.isTopOpen = () => isOverlayOpen(topOverlay);
    _tv.navigateTopOverlay = navigateTopOverlayWrapped;
    _tv.showNextRow = showNextRow;
    _tv.hideBottom = hideBottom;
    _tv.hideBottomRow = hideBottomRow;
    _tv.isBottomOpen = () => isOverlayOpen(bottomOverlay);
    _tv.navigateBottomOverlay = navigateBottomOverlay;
    _tv.activeOverlay = activeOverlay;
    _tv.resetOverlayState = resetOverlayState;
})();
