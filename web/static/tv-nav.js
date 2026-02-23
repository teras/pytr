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

    let currentEl = null;
    let playerMode = false;
    let osdTimer = null;
    let osdEl = null;

    function getVideo() {
        return document.getElementById('video-player');
    }

    // ── OSD overlay ─────────────────────────────────────────────────────────
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
        playerMode = false;
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
        // Move focus to first button below player
        const btns = getFocusables().filter(el => el.id !== 'player-container');
        if (btns.length) setFocus(btns[0]);
    }

    function activate() {
        if (!document.body.classList.contains('tv-nav-active')) {
            document.body.classList.add('tv-nav-active');
        }
    }

    function rect(el) {
        return el.getBoundingClientRect();
    }

    // Spatial: find nearest element in direction from current
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

            // Filter by direction
            if (dir === 'left' && ex >= cx - 1) continue;
            if (dir === 'right' && ex <= cx + 1) continue;
            if (dir === 'up' && ey >= cy - 1) continue;
            if (dir === 'down' && ey <= cy + 1) continue;

            // Perpendicular overlap bonus
            let overlap = 0;
            if (dir === 'left' || dir === 'right') {
                overlap = Math.max(0, Math.min(cr.bottom, r.bottom) - Math.max(cr.top, r.top));
            } else {
                overlap = Math.max(0, Math.min(cr.right, r.right) - Math.max(cr.left, r.left));
            }

            const dist = Math.hypot(ex - cx, ey - cy);
            // Strongly prefer overlapping elements (same row/col)
            const score = overlap > 0 ? dist : dist + 100000;

            if (score < bestScore) {
                bestScore = score;
                best = el;
            }
        }
        return best;
    }

    // Check if an option menu is open
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
        if (dir === 'down') {
            const next = idx < items.length - 1 ? items[idx + 1] : items[0];
            setFocus(next);
        } else if (dir === 'up') {
            const prev = idx > 0 ? items[idx - 1] : items[items.length - 1];
            setFocus(prev);
        }
    }

    function closeMenu(menuDef) {
        const menu = document.querySelector(menuDef.menu);
        if (menu) menu.classList.add('hidden');
        const btn = document.querySelector(menuDef.btn);
        if (btn) setFocus(btn);
    }

    // Profile menu handling
    function getOpenProfileMenu() {
        const menu = document.querySelector('.profile-menu');
        return menu && menu.offsetParent ? menu : null;
    }

    function handleBack() {
        // Exit player mode first
        if (playerMode) { exitPlayerMode(); return; }
        // Close open option menu
        const openMenu = getOpenMenu();
        if (openMenu) { closeMenu(openMenu); return; }
        // Close profile menu
        const profMenu = getOpenProfileMenu();
        if (profMenu) {
            const btn = document.getElementById('profile-switcher-btn');
            if (btn) btn.click();
            if (btn) setFocus(btn);
            return;
        }
        // Browser back
        history.back();
    }

    // ── Media keys (always global) ──────────────────────────────────────────
    function handleMediaKey(e) {
        const video = getVideo();
        if (!video || !isVideoView()) return false;

        switch (e.key) {
            case 'MediaPlayPause':
                e.preventDefault();
                video.paused ? video.play() : video.pause();
                showOsd(video.paused ? '⏸' : '▶');
                return true;
            case 'MediaStop':
                e.preventDefault();
                video.pause();
                history.back();
                return true;
            case 'MediaFastForward':
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);
                showOsd('⏩');
                return true;
            case 'MediaRewind':
                e.preventDefault();
                video.currentTime = Math.max(0, video.currentTime - 30);
                showOsd('⏪');
                return true;
            case 'MediaTrackNext':
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);
                showOsd('⏩');
                return true;
            case 'MediaTrackPrevious':
                e.preventDefault();
                video.currentTime = Math.max(0, video.currentTime - 30);
                showOsd('⏪');
                return true;
        }
        return false;
    }

    document.addEventListener('keydown', function (e) {
        // Media keys — always handled first, globally
        if (handleMediaKey(e)) return;

        const tag = document.activeElement?.tagName;
        const isInput = tag === 'INPUT' || tag === 'TEXTAREA';

        // Search input: let keys type, only ArrowDown exits
        if (isInput) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                document.activeElement.blur();
                activate();
                const below = findNearest('down') || getFocusables().find(el => el !== currentEl);
                if (below) setFocus(below);
                return;
            }
            if (e.key === 'Escape') {
                document.activeElement.blur();
                return;
            }
            return;
        }

        // ── Player mode: arrows control video ───────────────────────────────
        if (playerMode) {
            const video = getVideo();
            if (!video) { exitPlayerMode(); return; }

            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                video.currentTime = Math.max(0, video.currentTime - SEEK_SECONDS);
                showOsd('⏪');
                return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                video.currentTime = Math.min(video.duration || 0, video.currentTime + SEEK_SECONDS);
                showOsd('⏩');
                return;
            }
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                exitPlayerMode();
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                video.paused ? video.play() : video.pause();
                showOsd(video.paused ? '⏸' : '▶');
                return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back') {
                e.preventDefault();
                handleBack();
                return;
            }
            return;
        }

        const arrow = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];

        if (arrow) {
            e.preventDefault();
            activate();

            // Menu trap
            const openMenu = getOpenMenu();
            if (openMenu && (arrow === 'up' || arrow === 'down')) {
                navigateMenu(openMenu, arrow);
                return;
            }

            // If no current focus, pick first visible
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
                // Input fields: focus them so user can type
                if (currentEl.tagName === 'INPUT' || currentEl.tagName === 'TEXTAREA') {
                    currentEl.focus();
                    return;
                }
                // Player container: enter player mode
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
                // If we clicked a menu button, focus first option
                for (const m of MENU_SELECTORS) {
                    const btn = document.querySelector(m.btn);
                    if (currentEl === btn) {
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
            e.preventDefault();
            handleBack();
            return;
        }
    });

    // Mouse interop: mousedown clears TV mode
    document.addEventListener('mousedown', function () {
        if (currentEl) {
            currentEl.classList.remove('tv-focus', 'tv-player-mode');
            currentEl = null;
        }
        playerMode = false;
        document.body.classList.remove('tv-nav-active');
    });
})();
