/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */

// TV Remote / D-pad spatial navigation — Core
(function () {
    const _tv = window._tv = {};

    // Stubs replaced by tv-overlays.js
    _tv.showTop = _tv.hideTop = _tv.hideBottom = _tv.showNextRow = _tv.hideBottomRow = _tv.resetOverlayState = () => {};
    _tv.isTopOpen = _tv.isBottomOpen = () => false;
    _tv.activeOverlay = () => null;
    _tv.navigateTopOverlay = () => 'exit';
    _tv.navigateBottomOverlay = () => false;

    const FOCUSABLE = '.video-card, .related-card, .queue-item, .player-btn, .filter-btn, .list-tab, .channel-tab, .profile-card, #search-input, #search-btn, #logo-link, #profile-switcher-btn, #player-container, .quality-option, .audio-option, .subtitle-option, .profile-menu-item, .queue-toggle-area';

    const MENU_SELECTORS = [
        { menu: '#quality-menu', btn: '#quality-btn' },
        { menu: '#audio-menu', btn: '#audio-btn' },
        { menu: '#subtitle-menu', btn: '#subtitle-btn' },
    ];

    const SEEK_SECONDS = 10;
    const OSD_TIMEOUT = 1200;

    let currentEl = null;
    let playerMode = false;
    let osdTimer = null;
    let osdEl = null;

    // ── Persistence & Toggle ───────────────────────────────────────────────
    const TV_KEY = 'tv-mode';

    function isTvActive() {
        return document.body.classList.contains('tv-nav-active');
    }

    function toggleTvMode() {
        const entering = !isTvActive();
        if (entering) {
            document.body.classList.add('tv-nav-active');
            localStorage.setItem(TV_KEY, '1');
            if (isVideoView()) enterPlayerMode();
        } else {
            document.body.classList.remove('tv-nav-active');
            localStorage.removeItem(TV_KEY);
            if (currentEl) {
                currentEl.classList.remove('tv-focus', 'tv-player-mode');
                currentEl = null;
            }
            playerMode = false;
            _tv.hideTop(); _tv.hideBottom();
            const pc = document.getElementById('player-container');
            if (pc) pc.classList.remove('tv-player-mode');
        }
    }

    // Expose for profile menu
    window.toggleTvMode = toggleTvMode;

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

    // ── Back handler ─────────────────────────────────────────────────────────
    function handleBack() {
        if (_tv.isTopOpen()) { _tv.hideTop(); return; }
        if (_tv.isBottomOpen()) { _tv.hideBottom(); enterPlayerMode(); return; }
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
                showOsd(video.paused ? '\u23F8' : '\u25B6'); return true;
            case 'MediaStop':
                e.preventDefault(); video.pause(); history.back(); return true;
            case 'MediaFastForward': case 'MediaTrackNext':
                e.preventDefault(); video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);
                showOsd('\u23E9'); return true;
            case 'MediaRewind': case 'MediaTrackPrevious':
                e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 30);
                showOsd('\u23EA'); return true;
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
                if (_tv.isTopOpen()) _tv.hideTop();
                return;
            }
            if (e.key === 'ArrowDown' && isTvActive()) {
                e.preventDefault(); document.activeElement.blur();
                if (_tv.isTopOpen()) _tv.hideTop();
                else {
                    const below = findNearest('down') || getFocusables().find(el => el !== currentEl);
                    if (below) setFocus(below);
                }
                return;
            }
            return; // all other keys → type normally
        }

        const arrow = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];

        // Not in TV mode → let browser handle everything except Escape
        if (!isTvActive()) {
            if (e.key === 'Escape') {
                // Escape closes menus even in desktop mode
                const openMenu = getOpenMenu();
                if (openMenu) { e.preventDefault(); closeMenu(openMenu); }
            }
            return;
        }

        // ── Escape in TV mode: exit TV if nothing else to close ──────────
        if (e.key === 'Escape') {
            e.preventDefault();
            if (_tv.isTopOpen()) { _tv.hideTop(); return; }
            if (_tv.isBottomOpen()) { _tv.hideBottom(); enterPlayerMode(); return; }
            const openMenu = getOpenMenu();
            if (openMenu) { closeMenu(openMenu); return; }
            const profMenu = getOpenProfileMenu();
            if (profMenu) {
                const btn = document.getElementById('profile-switcher-btn');
                if (btn) { btn.click(); setFocus(btn); }
                return;
            }
            // Nothing open → exit TV mode
            toggleTvMode();
            return;
        }

        // ── Overlay mode: unified navigation ─────────────────────────────
        const which = _tv.activeOverlay();
        if (which) {
            if (arrow) {
                e.preventDefault();
                if (which === 'bottom') {
                    // Row-aware navigation for bottom overlay
                    const handled = _tv.navigateBottomOverlay(arrow);
                    if (!handled) {
                        // exit bottom overlay → player mode
                        _tv.hideBottom();
                        enterPlayerMode();
                    }
                } else {
                    const result = _tv.navigateTopOverlay(arrow);
                    if (result === 'exit') _tv.hideTop();
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
                showOsd('\u23EA'); return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + SEEK_SECONDS);
                showOsd('\u23E9'); return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (isTvActive() && isVideoView()) {
                    if (_tv.isBottomOpen()) _tv.hideBottom();
                    else _tv.showTop(false);
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (isTvActive() && isVideoView()) {
                    if (_tv.isTopOpen()) _tv.hideTop();
                    else _tv.showNextRow();
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                video.paused ? video.play() : video.pause();
                showOsd(video.paused ? '\u23F8' : '\u25B6'); return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
                e.preventDefault(); handleBack(); return;
            }
            return;
        }

        // ── Normal spatial navigation (TV mode active, not in player mode) ─
        if (arrow) {
            e.preventDefault();

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
                        showOsd(video.paused ? '\u23F8' : '\u25B6');
                    }
                    return;
                }
                currentEl.click();
            }
            return;
        }

        if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
            e.preventDefault(); handleBack(); return;
        }
    });

    // ── Auto player mode on video view ───────────────────────────────────
    window.addEventListener('video-changed', () => {
        if (!isTvActive()) return;
        _tv.hideTop(); _tv.hideBottom();
        _tv.resetOverlayState();
        enterPlayerMode();
        _tv.showTop(true);
    });

    // Clean up overlays when leaving video view
    const vvEl = document.getElementById('video-view');
    if (vvEl) {
        new MutationObserver(() => {
            if (!isVideoView()) {
                _tv.hideTop(); _tv.hideBottom();
            }
        }).observe(vvEl, { attributes: true, attributeFilter: ['class'] });
    }

    window.addEventListener('popstate', function () {
        _tv.hideTop(); _tv.hideBottom();
    });

    // ── Restore TV mode from localStorage on page load ───────────────────────
    if (localStorage.getItem(TV_KEY) === '1') {
        document.body.classList.add('tv-nav-active');
    }

    // ── Namespace exports ────────────────────────────────────────────────────
    _tv.setFocus = setFocus;
    _tv.getCurrentEl = () => currentEl;
    _tv.enterPlayerMode = enterPlayerMode;
    _tv.isVideoView = isVideoView;
    _tv.isTvActive = isTvActive;
    _tv.toggleTvMode = toggleTvMode;
})();
