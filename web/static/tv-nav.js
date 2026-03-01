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

    const FOCUSABLE = '.video-card, .related-card, .queue-item, .player-btn, .filter-btn, .list-tab, .channel-tab, .profile-card, #search-input, #search-btn, #logo-link, #profile-switcher-btn, #player-container, .quality-option, .audio-option, .subtitle-option, .summarize-option, .profile-menu-item, .profile-menu-profile, .queue-toggle-area, .tv-top-home-btn';

    const MENU_SELECTORS = [
        { menu: '#quality-menu', btn: '#quality-btn' },
        { menu: '#audio-menu', btn: '#audio-btn' },
        { menu: '#subtitle-menu', btn: '#subtitle-btn' },
    ];

    const SEEK_STEPS = [10, 10, 10, 10, 10, 20, 20, 30, 30, 60];
    const SEEK_REPEAT_MS = 500;

    let currentEl = null;
    let playerMode = false;
    let _osdTimer = null;
    let _seekCount = 0;
    let _seekDir = null;
    let _seekResetTimer = null;

    function progressiveSeek(video, dir) {
        if (_seekDir !== dir) { _seekCount = 0; _seekDir = dir; }
        const step = SEEK_STEPS[Math.min(_seekCount, SEEK_STEPS.length - 1)];
        _seekCount++;
        if (_seekResetTimer) clearTimeout(_seekResetTimer);
        _seekResetTimer = setTimeout(() => { _seekCount = 0; _seekDir = null; }, SEEK_REPEAT_MS);
        if (dir === 'right') {
            video.currentTime = Math.min(video.duration || 0, video.currentTime + step);
        } else {
            video.currentTime = Math.max(0, video.currentTime - step);
        }
    }

    // ── Persistence & Toggle ───────────────────────────────────────────────
    const TV_KEY = 'tv-mode';

    function isTvActive() {
        return document.body.classList.contains('tv-nav-active');
    }

    function isTvLocked() {
        const v = localStorage.getItem(TV_KEY);
        return v && v !== 'desktop';
    }

    function toggleTvMode() {
        if (isTvLocked()) return;
        const entering = !isTvActive();
        if (entering) {
            document.body.classList.add('tv-nav-active');
            localStorage.setItem(TV_KEY, 'desktop');
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

    function closeProfileMenu() {
        const btn = document.getElementById('profile-switcher-btn');
        if (btn) { btn.click(); setFocus(btn); }
    }

    // ── Back handler ─────────────────────────────────────────────────────────
    function handleBack() {
        if (_tv.isTopOpen()) { _tv.hideTop(); enterPlayerMode(); return; }
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
                showOsd(); return true;
            case 'MediaStop':
                e.preventDefault(); video.pause(); history.back(); return true;
            case 'MediaFastForward': case 'MediaTrackNext':
                e.preventDefault(); video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);
                showOsd(); return true;
            case 'MediaRewind': case 'MediaTrackPrevious':
                e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 30);
                showOsd(); return true;
        }
        return false;
    }

    // ── Main keydown handler ─────────────────────────────────────────────────
    document.addEventListener('keydown', function (e) {
        if (handleMediaKey(e)) return;

        const tag = document.activeElement && document.activeElement.tagName;
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

        // Not in TV mode → handle seek/play keys on video, Escape for menus
        if (!isTvActive()) {
            if (e.key === 'Escape') {
                const openMenu = getOpenMenu();
                if (openMenu) { e.preventDefault(); closeMenu(openMenu); }
            }
            if (isVideoView()) {
                const video = getVideo();
                if (video) {
                    if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        progressiveSeek(video, 'left');
                        showOsd(); return;
                    }
                    if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        progressiveSeek(video, 'right');
                        showOsd(); return;
                    }
                    if (e.key === ' ' || e.key === 'Enter') {
                        e.preventDefault();
                        video.paused ? video.play() : video.pause();
                        showOsd(); return;
                    }
                }
            }
            return;
        }

        // ── Escape in TV mode: exit TV if nothing else to close ──────────
        if (e.key === 'Escape') {
            e.preventDefault();
            if (_tv.isTopOpen()) { _tv.hideTop(); enterPlayerMode(); return; }
            if (_tv.isBottomOpen()) { _tv.hideBottom(); enterPlayerMode(); return; }
            const openMenu = getOpenMenu();
            if (openMenu) { closeMenu(openMenu); return; }
            const profMenu = getOpenProfileMenu();
            if (profMenu) {
                const btn = document.getElementById('profile-switcher-btn');
                if (btn) { btn.click(); setFocus(btn); }
                return;
            }
            // Nothing open → exit TV mode (only if not a real TV client)
            if (!isTvLocked()) toggleTvMode();
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
                    if (result === 'exit') { _tv.hideTop(); enterPlayerMode(); }
                }
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                if (currentEl && currentEl.tagName === 'INPUT') {
                    currentEl.focus(); // start typing
                } else if (currentEl) {
                    currentEl.click();
                }
                return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back' || e.keyCode === 461) {
                e.preventDefault(); handleBack(); return;
            }
            return;
        }

        // ── Player mode: arrows control video ────────────────────────────
        if (playerMode) {
            const video = getVideo();
            if (!video) { exitPlayerMode(); return; }

            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (isTvActive() && isVideoView()) {
                    _tv.showTop(false);
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (isTvActive() && isVideoView()) {
                    _tv.hideTop();
                    _tv.showNextRow();
                } else { exitPlayerMode(); }
                return;
            }
            if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back' || e.keyCode === 461) {
                e.preventDefault(); handleBack(); return;
            }

            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                progressiveSeek(video, 'left');
                showOsd();
                return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                progressiveSeek(video, 'right');
                showOsd();
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                video.paused ? video.play() : video.pause();
                showOsd();
                return;
            }
            return;
        }

        // ── Profile menu navigation (constrained) ──────────────────────
        const profMenu = getOpenProfileMenu();
        if (profMenu && arrow) {
            e.preventDefault();
            const items = [...profMenu.querySelectorAll('.profile-menu-profile, .profile-menu-item')].filter(isVisible);
            const idx = items.indexOf(currentEl);
            if (arrow === 'up') {
                if (idx > 0) { setFocus(items[idx - 1]); }
                else { closeProfileMenu(); }
            } else if (arrow === 'down') {
                if (idx === -1) { if (items.length) setFocus(items[0]); }
                else if (idx < items.length - 1) { setFocus(items[idx + 1]); }
                else { closeProfileMenu(); }
            } else {
                // left/right close profile menu
                closeProfileMenu();
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
                    if (video) { video.paused ? video.play() : video.pause(); showOsd(); }
                    return;
                }
                currentEl.click();
            }
            return;
        }

        if (e.key === 'Backspace' || e.key === 'BrowserBack' || e.key === 'XF86Back' || e.keyCode === 461) {
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

    // Clean up overlays when leaving video view + toggle body class for TV header
    const vvEl = document.getElementById('video-view');
    if (vvEl) {
        new MutationObserver(() => {
            const inVideo = !vvEl.classList.contains('hidden');
            document.body.classList.toggle('tv-video-active', inVideo);
            if (!inVideo) {
                _tv.hideTop(); _tv.hideBottom();
            }
        }).observe(vvEl, { attributes: true, attributeFilter: ['class'] });
    }

    window.addEventListener('popstate', function () {
        _tv.hideTop(); _tv.hideBottom();
    });

    // ── Restore TV mode from localStorage on page load ───────────────────────
    if (localStorage.getItem(TV_KEY)) {
        document.body.classList.add('tv-nav-active');
    }

    // ── OSD (custom on-screen display) ──────────────────────────────────────
    function updateOsd() {
        const video = getVideo();
        const osd = document.getElementById('tv-osd');
        if (!video || !osd) return;
        const cur = video.currentTime || 0;
        const dur = video.duration || 0;
        document.getElementById('osd-current').innerHTML = formatTime(cur, dur);
        document.getElementById('osd-total').textContent = formatTime(dur);
        const pct = dur > 0 ? (cur / dur) * 100 : 0;
        document.getElementById('osd-progress').style.width = pct + '%';
        const icon = document.getElementById('osd-play-icon');
        if (icon) icon.innerHTML = video.paused
            ? svgIcon(SVG_PLAY, null, 18)
            : svgIcon(SVG_PAUSE, null, 18);
    }

    function isOsdPopupOpen() {
        const vp = document.getElementById('osd-volume-popup');
        return vp && vp.classList.contains('open');
    }

    function showOsd() {
        const osd = document.getElementById('tv-osd');
        if (!osd) return;
        updateOsd();
        osd.classList.add('visible');
        if (_osdTimer) clearTimeout(_osdTimer);
        if (!isOsdPopupOpen()) {
            _osdTimer = setTimeout(() => {
                if (!isOsdPopupOpen()) {
                    osd.classList.remove('visible');
                }
                _osdTimer = null;
            }, 2000);
        }
        // notify SponsorBlock to refresh markers
        if (typeof window.refreshSbMarkers === 'function') window.refreshSbMarkers();
    }

    function hideOsd() {
        const osd = document.getElementById('tv-osd');
        if (osd) osd.classList.remove('visible');
        if (_osdTimer) { clearTimeout(_osdTimer); _osdTimer = null; }
    }

    // Click-to-seek on progress bar
    const osdBar = document.getElementById('osd-bar');
    if (osdBar) {
        osdBar.addEventListener('click', function (e) {
            const video = getVideo();
            if (!video || !video.duration) return;
            const rect = osdBar.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            video.currentTime = pct * video.duration;
            showOsd();
        });
    }

    // ── Volume control ────────────────────────────────────────────────────
    const VOL_ICONS = {
        loud: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
        mid:  '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
        low:  '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>',
        mute: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>'
    };
    let _savedVolume = 1;

    function volIcon(level) {
        const svg = document.getElementById('osd-vol-icon');
        if (svg) svg.innerHTML = VOL_ICONS[level];
    }

    function updateVolIcon(v) {
        if (v === 0) volIcon('mute');
        else if (v < 0.33) volIcon('low');
        else if (v < 0.66) volIcon('mid');
        else volIcon('loud');
    }

    const volSlider = document.getElementById('osd-volume-slider');
    const volBtn = document.getElementById('osd-volume-btn');
    const volPopup = document.getElementById('osd-volume-popup');
    const volWrap = document.querySelector('.osd-volume-wrap');

    // Show/hide volume popup on hover with grace period
    let _volLeaveTimer = null;
    if (volWrap && volPopup) {
        volWrap.addEventListener('mouseenter', () => {
            if (_volLeaveTimer) { clearTimeout(_volLeaveTimer); _volLeaveTimer = null; }
            volPopup.classList.add('open');
            showOsd();
        });
        volWrap.addEventListener('mouseleave', () => {
            _volLeaveTimer = setTimeout(() => {
                volPopup.classList.remove('open');
                _volLeaveTimer = null;
                showOsd();
            }, 300);
        });
    }

    if (volSlider) {
        volSlider.addEventListener('input', () => {
            const video = getVideo();
            if (!video) return;
            const val = parseInt(volSlider.value, 10) / 100;
            video.volume = val;
            video.muted = val === 0;
            updateVolIcon(val);
            showOsd();
        });
    }
    if (volBtn) {
        volBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const video = getVideo();
            if (!video) return;
            if (video.muted || video.volume === 0) {
                video.muted = false;
                video.volume = _savedVolume || 0.5;
                if (volSlider) volSlider.value = Math.round(video.volume * 100);
                updateVolIcon(video.volume);
            } else {
                _savedVolume = video.volume;
                video.muted = true;
                if (volSlider) volSlider.value = 0;
                volIcon('mute');
            }
            showOsd();
        });
    }

    // Sync slider when video loads
    const _videoEl = getVideo();
    if (_videoEl) {
        _videoEl.addEventListener('volumechange', () => {
            const v = _videoEl.muted ? 0 : _videoEl.volume;
            if (volSlider) volSlider.value = Math.round(v * 100);
            updateVolIcon(v);
        });
    }

    // ── Mode buttons (theater / normal / fullscreen / exit-fs) ─────────
    const modeBtns = document.querySelectorAll('.osd-mode-btn');
    let _preFsMode = 'normal';

    function updateModeButtons() {
        const isFs = !!document.fullscreenElement;
        const isTheater = document.body.classList.contains('theater-mode');
        modeBtns.forEach(btn => {
            const a = btn.dataset.action;
            if (isFs) {
                btn.style.display = a === 'exit-fs' ? '' : 'none';
            } else if (isTheater) {
                btn.style.display = (a === 'normal' || a === 'fullscreen') ? '' : 'none';
            } else {
                btn.style.display = (a === 'theater' || a === 'fullscreen') ? '' : 'none';
            }
        });
    }

    updateModeButtons();

    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement) {
            if (_preFsMode === 'theater') {
                document.body.classList.add('theater-mode');
            } else {
                document.body.classList.remove('theater-mode');
            }
        }
        updateModeButtons();
    });

    modeBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = btn.dataset.action;
            const pc = document.getElementById('player-container');
            if (action === 'normal') {
                document.body.classList.remove('theater-mode');
            } else if (action === 'theater') {
                document.body.classList.add('theater-mode');
            } else if (action === 'fullscreen') {
                _preFsMode = document.body.classList.contains('theater-mode') ? 'theater' : 'normal';
                if (pc) pc.requestFullscreen().catch(() => {});
            } else if (action === 'exit-fs') {
                if (document.fullscreenElement) document.exitFullscreen();
            }
            updateModeButtons();
            showOsd();
        });
    });

    // Mouse move on player-container → show OSD
    const pcEl = document.getElementById('player-container');
    if (pcEl) {
        pcEl.addEventListener('mousemove', () => {
            if (isVideoView()) showOsd();
        });
        // Click on video toggles play/pause + shows OSD
        pcEl.addEventListener('click', (e) => {
            if (e.target.closest('.tv-osd-bar') || e.target.closest('.sb-toast') || e.target.closest('.osd-right-controls')) return;
            const video = getVideo();
            if (!video || !isVideoView()) return;
            video.paused ? video.play() : video.pause();
            showOsd();
        });
    }

    // Update OSD progress while visible
    const videoEl = getVideo();
    if (videoEl) {
        videoEl.addEventListener('timeupdate', () => {
            const osd = document.getElementById('tv-osd');
            if (osd && osd.classList.contains('visible')) updateOsd();
        });
    }

    // ── Namespace exports ────────────────────────────────────────────────────
    _tv.setFocus = setFocus;
    _tv.getCurrentEl = () => currentEl;
    _tv.enterPlayerMode = enterPlayerMode;
    _tv.isVideoView = isVideoView;
    _tv.isTvActive = isTvActive;
    _tv.isTvLocked = isTvLocked;
    _tv.toggleTvMode = toggleTvMode;
    _tv.showOsd = showOsd;
    _tv.hideOsd = hideOsd;

    // Expose setFocus globally for profile auto-focus
    window._tvSetFocus = setFocus;
})();
