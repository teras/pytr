/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */

// TV Remote / D-pad spatial navigation
(function () {
    const FOCUSABLE = '.video-card, .related-card, .queue-item, .player-btn, .filter-btn, .list-tab, .channel-tab, .profile-card, #search-input, #search-btn, #logo-link, #profile-switcher-btn, #player-container, .quality-option, .audio-option, .subtitle-option, .profile-menu-item, .queue-toggle-area';

    const MENU_SELECTORS = [
        { menu: '#quality-menu', items: '.quality-option', btn: '#quality-btn' },
        { menu: '#audio-menu', items: '.audio-option', btn: '#audio-btn' },
        { menu: '#subtitle-menu', items: '.subtitle-option', btn: '#subtitle-btn' },
    ];

    const SEEK_SECONDS = 10;
    const OSD_TIMEOUT = 1200;
    const TOP_OVERLAY_AUTO_HIDE = 3000;

    // All focusable items inside overlays
    const OVERLAY_FOCUSABLE = '.tv-overlay-item';

    let currentEl = null;
    let playerMode = false;
    let osdTimer = null;
    let osdEl = null;

    // Overlays (top and bottom) share the same lifecycle
    let topOverlay = null;
    let topOverlayTimer = null;
    let bottomOverlay = null;
    let bottomHideTimer = null;

    // Auto-show tracking
    let _autoShowDone = null;

    function getVideo() {
        return document.getElementById('video-player');
    }

    // ── OSD ──────────────────────────────────────────────────────────────────
    function ensureOsd() {
        if (osdEl) return osdEl;
        osdEl = document.createElement('div');
        osdEl.className = 'tv-osd';
        osdEl.innerHTML = '<div class="tv-osd-icon"></div><div class="tv-osd-bar"><div class="tv-osd-progress"></div></div><div class="tv-osd-time"></div>';
        document.getElementById('player-container')?.appendChild(osdEl);
        return osdEl;
    }

    function fmt(s) {
        if (!s || !isFinite(s)) return '0:00';
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = Math.floor(s % 60);
        return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}` : `${m}:${sec.toString().padStart(2, '0')}`;
    }

    function showOsd(icon) {
        const video = getVideo();
        if (!video) return;
        const osd = ensureOsd();
        const pct = video.duration ? (video.currentTime / video.duration) * 100 : 0;
        osd.querySelector('.tv-osd-icon').textContent = icon;
        osd.querySelector('.tv-osd-progress').style.width = pct + '%';
        osd.querySelector('.tv-osd-time').textContent = `${fmt(video.currentTime)} / ${fmt(video.duration)}`;
        osd.classList.add('visible');
        clearTimeout(osdTimer);
        osdTimer = setTimeout(() => osd.classList.remove('visible'), OSD_TIMEOUT);
    }

    // ── Generic overlay helpers ──────────────────────────────────────────────
    // Both top & bottom overlays use the same show/hide/navigate pattern.
    // Focusable items inside overlays get class "tv-overlay-item".

    function showOverlay(el, focusFirst) {
        // Double-rAF for CSS transition from initial state
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (el && el.parentNode) {
                el.classList.add('visible');
                if (focusFirst !== false) {
                    const first = el.querySelector(OVERLAY_FOCUSABLE);
                    if (first) setFocus(first);
                }
            }
        }));
    }

    function getOverlayItems(overlay) {
        return overlay ? [...overlay.querySelectorAll(OVERLAY_FOCUSABLE)] : [];
    }

    // Navigate within an overlay: left/right move sequentially, up/down move
    // between rows. At the edge, return 'exit' to let caller close.
    function navigateOverlay(overlay, dir) {
        const items = getOverlayItems(overlay);
        if (!items.length) return 'exit';
        const idx = items.indexOf(currentEl);
        if (idx === -1) { setFocus(items[0]); return 'handled'; }

        if (dir === 'left') {
            if (idx > 0) { setFocus(items[idx - 1]); items[idx - 1].scrollIntoView({ block: 'nearest', inline: 'center' }); }
            return 'handled';
        }
        if (dir === 'right') {
            if (idx < items.length - 1) { setFocus(items[idx + 1]); items[idx + 1].scrollIntoView({ block: 'nearest', inline: 'center' }); }
            return 'handled';
        }

        // Up/down: find items in different rows (different Y position)
        const curRect = currentEl.getBoundingClientRect();
        const curY = Math.round(curRect.top);

        if (dir === 'down') {
            const below = items.filter(el => Math.round(el.getBoundingClientRect().top) > curY + 5);
            if (below.length) { setFocus(below[0]); return 'handled'; }
            return 'exit';
        }
        if (dir === 'up') {
            const above = items.filter(el => Math.round(el.getBoundingClientRect().top) < curY - 5);
            if (above.length) { setFocus(above[above.length - 1]); return 'handled'; }
            return 'exit';
        }
        return 'handled';
    }

    function isOverlayOpen(overlay) {
        return overlay && overlay.classList.contains('visible');
    }

    function activeOverlay() {
        // Return whichever overlay currently contains focus
        if (isOverlayOpen(topOverlay) && topOverlay.contains(currentEl)) return 'top';
        if (isOverlayOpen(bottomOverlay) && bottomOverlay.contains(currentEl)) return 'bottom';
        return null;
    }

    // ── Top overlay (search + info + buttons) ────────────────────────────────
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
        if (origSearch?.value) searchInput.value = origSearch.value;
        const searchBtn = document.createElement('button');
        searchBtn.textContent = '🔍';
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

        searchRow.appendChild(searchInput);
        searchRow.appendChild(searchBtn);

        // Row 2: buttons left, meta right
        const infoRow = document.createElement('div');
        infoRow.className = 'tv-top-info-row';

        const btnContainer = document.createElement('div');
        btnContainer.className = 'tv-top-buttons';

        // Clone visible action buttons
        const btnSources = [
            { el: document.getElementById('favorite-btn'), container: null },
            { el: document.getElementById('subtitle-btn'), container: document.getElementById('subtitle-btn-container') },
            { el: document.getElementById('audio-btn'), container: document.getElementById('audio-btn-container') },
            { el: document.getElementById('quality-btn'), container: document.getElementById('quality-selector') },
        ];
        for (const { el, container } of btnSources) {
            if (!el) continue;
            if (container && container.classList.contains('hidden')) continue;
            if (!container && el.classList.contains('hidden')) continue;
            const clone = el.cloneNode(true);
            clone.removeAttribute('id');
            clone.classList.add('tv-overlay-item');
            clone.addEventListener('click', () => el.click());
            btnContainer.appendChild(clone);
        }

        // Meta (right side, not focusable)
        const metaDiv = document.createElement('div');
        metaDiv.className = 'tv-top-meta';
        const titleText = document.getElementById('video-title')?.textContent;
        const channelText = document.getElementById('video-channel')?.textContent;
        const metaText = document.getElementById('video-meta')?.textContent;
        if (titleText) metaDiv.innerHTML += `<div class="tv-top-title">${titleText}</div>`;
        if (channelText) metaDiv.innerHTML += `<div class="tv-top-channel">${channelText}</div>`;
        if (metaText) metaDiv.innerHTML += `<div class="tv-top-views">${metaText}</div>`;

        infoRow.appendChild(btnContainer);
        infoRow.appendChild(metaDiv);
        topOverlay.appendChild(searchRow);
        topOverlay.appendChild(infoRow);
        document.body.appendChild(topOverlay);
    }

    function showTop(autoHide) {
        buildTopOverlay();
        clearTimeout(topOverlayTimer);
        if (autoHide) {
            showOverlay(topOverlay, false); // visual only, don't steal focus
            topOverlayTimer = setTimeout(() => hideTop(), TOP_OVERLAY_AUTO_HIDE);
        } else {
            requestAnimationFrame(() => requestAnimationFrame(() => {
                if (!topOverlay) return;
                topOverlay.classList.add('visible');
                const input = topOverlay.querySelector('input');
                if (input) setFocus(input);
            }));
        }
    }

    function hideTop() {
        clearTimeout(topOverlayTimer);
        if (!topOverlay) return;
        topOverlay.classList.remove('visible');
        const input = topOverlay.querySelector('input');
        if (input && document.activeElement === input) input.blur();
        const ref = topOverlay;
        setTimeout(() => { if (ref.parentNode) ref.remove(); }, 300);
        topOverlay = null;
    }

    // ── Bottom overlay (related + queue) ─────────────────────────────────────
    function buildBottomOverlay() {
        // Cancel pending hide
        if (bottomOverlay) {
            clearTimeout(bottomHideTimer);
            bottomOverlay.remove();
            bottomOverlay = null;
        }

        bottomOverlay = document.createElement('div');
        bottomOverlay.className = 'tv-related-overlay';

        // Queue strip
        const queue = typeof window._getQueue === 'function' ? window._getQueue() : null;
        if (queue?.videos?.length) {
            bottomOverlay.insertAdjacentHTML('beforeend', `<h3>${queue.title || 'Queue'}</h3>`);
            const strip = document.createElement('div');
            strip.className = 'tv-related-strip';
            queue.videos.forEach((v, i) => {
                const card = document.createElement('div');
                card.className = 'tv-overlay-item related-card tv-queue-card' + (i === queue.currentIndex ? ' active' : '');
                card.innerHTML = `<div class="thumbnail-container"><img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" loading="lazy"></div><div class="related-info"><div class="related-title">${v.title || ''}</div><div class="related-channel">${v.channel || ''}</div></div>`;
                card.addEventListener('click', () => {
                    if (typeof window._playQueueItem === 'function') window._playQueueItem(i);
                    hideBottom();
                });
                strip.appendChild(card);
            });
            bottomOverlay.appendChild(strip);
        }

        // Related videos strip
        const sidebar = document.querySelector('.video-sidebar');
        const relCards = sidebar ? sidebar.querySelectorAll('.related-card') : [];
        if (relCards.length) {
            bottomOverlay.insertAdjacentHTML('beforeend', '<h3>Related</h3>');
            const strip = document.createElement('div');
            strip.className = 'tv-related-strip';
            relCards.forEach(card => {
                const clone = card.cloneNode(true);
                clone.classList.remove('tv-focus');
                clone.classList.add('tv-overlay-item');
                clone.addEventListener('click', () => { card.click(); hideBottom(); });
                strip.appendChild(clone);
            });
            bottomOverlay.appendChild(strip);
        }

        if (!bottomOverlay.querySelector(OVERLAY_FOCUSABLE)) {
            // Nothing to show
            bottomOverlay = null;
            return;
        }

        document.body.appendChild(bottomOverlay);
    }

    function showBottom() {
        buildBottomOverlay();
        if (!bottomOverlay) return;
        showOverlay(bottomOverlay);
    }

    function hideBottom() {
        clearTimeout(bottomHideTimer);
        if (!bottomOverlay) return;
        bottomOverlay.classList.remove('visible');
        const ref = bottomOverlay;
        bottomHideTimer = setTimeout(() => { if (ref.parentNode) ref.remove(); }, 300);
        bottomOverlay = null;
    }

    // ── Core focus management ────────────────────────────────────────────────
    function isVideoView() {
        const vv = document.getElementById('video-view');
        return vv && !vv.classList.contains('hidden');
    }

    function isVisible(el) {
        if (!el.offsetParent && el.id !== 'search-input') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    function getFocusables() {
        return [...document.querySelectorAll(FOCUSABLE)].filter(isVisible);
    }

    function setFocus(el) {
        if (currentEl) currentEl.classList.remove('tv-focus');
        // playerMode is NOT touched here — it persists until explicitly exited
        currentEl = el;
        if (el) {
            el.classList.add('tv-focus');
            el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    function enterPlayerMode() {
        playerMode = true;
        if (currentEl) currentEl.classList.remove('tv-focus');
        const pc = document.getElementById('player-container');
        if (pc) {
            currentEl = pc;
            pc.classList.add('tv-focus', 'tv-player-mode');
        }
    }

    function exitPlayerMode() {
        playerMode = false;
        const pc = document.getElementById('player-container');
        if (pc) pc.classList.remove('tv-player-mode');
        const btns = getFocusables().filter(el => el.id !== 'player-container');
        if (btns.length) setFocus(btns[0]);
    }

    function activate() {
        if (!document.body.classList.contains('tv-nav-active')) {
            document.body.classList.add('tv-nav-active');
            // First arrow key on video view → enter player mode
            if (isVideoView()) enterPlayerMode();
        }
    }

    function rect(el) { return el.getBoundingClientRect(); }

    function findNearest(dir) {
        const all = getFocusables().filter(e => e !== currentEl);
        if (!all.length) return null;
        const cr = currentEl ? rect(currentEl) : { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 };
        const cx = (cr.left + cr.right) / 2;
        const cy = (cr.top + cr.bottom) / 2;

        let best = null, bestScore = Infinity;
        for (const el of all) {
            const r = rect(el);
            const ex = (r.left + r.right) / 2;
            const ey = (r.top + r.bottom) / 2;
            if (dir === 'left' && ex >= cx - 1) continue;
            if (dir === 'right' && ex <= cx + 1) continue;
            if (dir === 'up' && ey >= cy - 1) continue;
            if (dir === 'down' && ey <= cy + 1) continue;

            let overlap = 0;
            if (dir === 'left' || dir === 'right') {
                overlap = Math.max(0, Math.min(cr.bottom, r.bottom) - Math.max(cr.top, r.top));
            } else {
                overlap = Math.max(0, Math.min(cr.right, r.right) - Math.max(cr.left, r.left));
            }

            const dist = Math.hypot(ex - cx, ey - cy);
            const score = overlap > 0 ? dist : dist + 100000;
            if (score < bestScore) { bestScore = score; best = el; }
        }
        return best;
    }

    // ── Menus ────────────────────────────────────────────────────────────────
    function getOpenMenu() {
        for (const m of MENU_SELECTORS) {
            const menu = document.querySelector(m.menu);
            if (menu && !menu.classList.contains('hidden')) return m;
        }
        return null;
    }

    function navigateMenu(menuDef, dir) {
        const items = [...document.querySelectorAll(menuDef.items)].filter(isVisible);
        if (!items.length) return;
        const idx = items.indexOf(currentEl);
        if (dir === 'down') setFocus(idx < items.length - 1 ? items[idx + 1] : items[0]);
        else if (dir === 'up') setFocus(idx > 0 ? items[idx - 1] : items[items.length - 1]);
    }

    function closeMenu(menuDef) {
        const menu = document.querySelector(menuDef.menu);
        if (menu) menu.classList.add('hidden');
        const btn = document.querySelector(menuDef.btn);
        if (btn) setFocus(btn);
    }

    function getOpenProfileMenu() {
        const menu = document.querySelector('.profile-menu');
        return menu && menu.offsetParent ? menu : null;
    }

    // ── Auto player mode on video view ───────────────────────────────────────
    function tryAutoPlayerMode() {
        if (!document.body.classList.contains('tv-nav-active') || !isVideoView()) return;
        const videoId = new URLSearchParams(window.location.search).get('v');
        if (_autoShowDone === videoId) return;
        _autoShowDone = videoId;
        enterPlayerMode();
        showTop(true);
    }

    const vvEl = document.getElementById('video-view');
    if (vvEl) {
        new MutationObserver(() => {
            if (document.body.classList.contains('tv-nav-active') && isVideoView()) {
                tryAutoPlayerMode();
            }
            if (!isVideoView()) {
                hideTop(); hideBottom(); _autoShowDone = null;
            }
        }).observe(vvEl, { attributes: true, attributeFilter: ['class'] });
    }

    // ── Back handler ─────────────────────────────────────────────────────────
    function handleBack() {
        if (isOverlayOpen(topOverlay)) { hideTop(); return; }
        if (isOverlayOpen(bottomOverlay)) { hideBottom(); return; }
        if (playerMode) { exitPlayerMode(); history.back(); return; }
        const openMenu = getOpenMenu();
        if (openMenu) { closeMenu(openMenu); return; }
        const profMenu = getOpenProfileMenu();
        if (profMenu) {
            const btn = document.getElementById('profile-switcher-btn');
            if (btn) { btn.click(); setFocus(btn); }
            return;
        }
        history.back();
    }

    // ── Media keys ───────────────────────────────────────────────────────────
    function handleMediaKey(e) {
        const video = getVideo();
        if (!video || !isVideoView()) return false;
        switch (e.key) {
            case 'MediaPlayPause':
                e.preventDefault(); video.paused ? video.play() : video.pause();
                showOsd(video.paused ? '⏸' : '▶'); return true;
            case 'MediaStop':
                e.preventDefault(); video.pause(); history.back(); return true;
            case 'MediaFastForward': case 'MediaTrackNext':
                e.preventDefault(); video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);
                showOsd('⏩'); return true;
            case 'MediaRewind': case 'MediaTrackPrevious':
                e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 30);
                showOsd('⏪'); return true;
        }
        return false;
    }

    // ── Main keydown handler ─────────────────────────────────────────────────
    document.addEventListener('keydown', function (e) {
        if (handleMediaKey(e)) return;

        const tag = document.activeElement?.tagName;
        const isInput = tag === 'INPUT' || tag === 'TEXTAREA';

        // Typing mode: let keys through, Escape/ArrowDown exits
        if (isInput) {
            if (e.key === 'Escape') {
                e.preventDefault(); document.activeElement.blur();
                if (isOverlayOpen(topOverlay)) hideTop();
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault(); document.activeElement.blur();
                if (isOverlayOpen(topOverlay)) hideTop();
                else {
                    activate();
                    const below = findNearest('down') || getFocusables().find(el => el !== currentEl);
                    if (below) setFocus(below);
                }
                return;
            }
            return; // all other keys → type normally
        }

        const arrow = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];

        // ── Overlay mode: unified navigation ─────────────────────────────
        const which = activeOverlay();
        if (which) {
            const overlay = which === 'top' ? topOverlay : bottomOverlay;
            if (arrow) {
                e.preventDefault();
                const result = navigateOverlay(overlay, arrow);
                if (result === 'exit') {
                    if (which === 'top') hideTop(); else hideBottom();
                }
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                if (currentEl?.tagName === 'INPUT') {
                    currentEl.focus(); // start typing
                } else if (currentEl) {
                    currentEl.click();
                }
                return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
                e.preventDefault(); handleBack(); return;
            }
            return;
        }

        // ── Player mode: arrows control video ────────────────────────────
        if (playerMode) {
            const video = getVideo();
            if (!video) { exitPlayerMode(); return; }

            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                video.currentTime = Math.max(0, video.currentTime - SEEK_SECONDS);
                showOsd('⏪'); return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + SEEK_SECONDS);
                showOsd('⏩'); return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (document.body.classList.contains('tv-nav-active') && isVideoView()) {
                    showTop(false);
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (document.body.classList.contains('tv-nav-active') && isVideoView()) {
                    showBottom();
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                video.paused ? video.play() : video.pause();
                showOsd(video.paused ? '⏸' : '▶'); return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
                e.preventDefault(); handleBack(); return;
            }
            return;
        }

        // ── Normal spatial navigation ────────────────────────────────────
        if (arrow) {
            e.preventDefault();
            activate();

            const openMenu = getOpenMenu();
            if (openMenu && (arrow === 'up' || arrow === 'down')) {
                navigateMenu(openMenu, arrow); return;
            }

            if (!currentEl || !isVisible(currentEl)) {
                const first = getFocusables()[0];
                if (first) setFocus(first);
                return;
            }

            const next = findNearest(arrow);
            if (next) setFocus(next);
            return;
        }

        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (currentEl) {
                if (currentEl.tagName === 'INPUT' || currentEl.tagName === 'TEXTAREA') {
                    currentEl.focus(); return;
                }
                if (currentEl.id === 'player-container') {
                    enterPlayerMode();
                    const video = getVideo();
                    if (video) {
                        video.paused ? video.play() : video.pause();
                        showOsd(video.paused ? '⏸' : '▶');
                    }
                    return;
                }
                currentEl.click();
                for (const m of MENU_SELECTORS) {
                    if (currentEl === document.querySelector(m.btn)) {
                        setTimeout(() => {
                            const menu = document.querySelector(m.menu);
                            if (menu && !menu.classList.contains('hidden')) {
                                const first = menu.querySelector(m.items);
                                if (first) setFocus(first);
                            }
                        }, 50);
                        break;
                    }
                }
            }
            return;
        }

        if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
            e.preventDefault(); handleBack(); return;
        }
    });

    // Mouse: exit TV mode
    document.addEventListener('mousedown', function () {
        if (currentEl) {
            currentEl.classList.remove('tv-focus', 'tv-player-mode');
            currentEl = null;
        }
        playerMode = false;
        hideTop(); hideBottom();
        document.body.classList.remove('tv-nav-active');
    });

    window.addEventListener('popstate', function () {
        hideTop(); hideBottom(); _autoShowDone = null;
    });
})();
