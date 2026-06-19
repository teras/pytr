// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// PYTR — OSD (on-screen display) for video player, shared by main app and embed

/** Format seconds as M:SS, MM:SS, H:MM:SS etc.
 *  Pass refSec (e.g. duration) to pad current time to match its width.
 *  Returns HTML — use innerHTML to set. Hidden padding uses same chars
 *  as the duration format for pixel-perfect width matching. */
function formatTime(s, refS) {
    if (!isFinite(s)) return '0:00';
    s = Math.floor(s);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const natural = h > 0
        ? h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0')
        : m + ':' + String(sec).padStart(2, '0');
    if (refS === undefined || !isFinite(refS)) return natural;
    const ref = formatTime(Math.floor(refS));
    if (natural.length >= ref.length) return natural;
    const pad = ref.substring(0, ref.length - natural.length);
    return '<span class="time-pad">' + pad + '</span>' + natural;
}

// ── OSD setup ────────────────────────────────────────────────────────────────

(function () {
    const _osd = window._osd = {};
    let _osdTimer = null;
    let _getVideo = null;
    let _isVideoView = null;
    let _containerId = null;

    /**
     * Initialize the OSD for a given context.
     * @param {object} config
     * @param {function} config.getVideo   — returns the <video> element
     * @param {function} config.isVideoView — returns true when video view is active
     * @param {string}   config.containerId — ID of the player container element
     */
    _osd.init = function (config) {
        _getVideo = config.getVideo;
        _isVideoView = config.isVideoView;
        _containerId = config.containerId;

        setupSeekBar();
        setupVolume();
        setupModeButtons();
        setupMouseInteraction();
        setupTimeUpdate();
        setupOrientationFullscreen();
    };

    function setupOrientationFullscreen() {
        // Touch-device only. On HTTPS/localhost the page can also be installed
        // as a PWA — in standalone mode the body class below already produces
        // a clean fullscreen because there's no browser URL bar to hide. On
        // plain HTTP we still apply the CSS so the player at least fills the
        // viewport (the URL bar persists but page chrome is gone), and the
        // click handler in setupMouseInteraction tries to upgrade to real
        // fullscreen on the next tap.
        if (!window.matchMedia('(pointer: coarse)').matches) return;
        const mql = window.matchMedia('(orientation: landscape)');
        const apply = () => {
            const wantFs = mql.matches && _isVideoView && _isVideoView();
            document.body.classList.toggle('landscape-fullscreen', wantFs);
            if (!wantFs && document.fullscreenElement) {
                document.exitFullscreen().catch(() => {});
            } else if (wantFs && !document.fullscreenElement) {
                // Try once; succeeds only within the 5s sticky-activation
                // window of the most recent gesture.
                const pc = document.getElementById(_containerId);
                if (pc) pc.requestFullscreen().catch(() => {});
            }
        };
        mql.addEventListener('change', apply);
        document.addEventListener('pytr:videoview', apply);
        apply();
    }

    // Exported so tv-overlays.js can reuse it
    function _getChapterAt(time) {
        const chapters = window.currentChapters;
        if (!chapters || !chapters.length) return null;
        for (let i = chapters.length - 1; i >= 0; i--) {
            if (time >= chapters[i].start_time) return chapters[i];
        }
        return null;
    }
    window._getChapterAt = _getChapterAt;

    function updateOsd() {
        const video = _getVideo();
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
        // Update current chapter name
        const chapterEl = document.getElementById('osd-chapter');
        if (chapterEl) {
            const ch = _getChapterAt(cur);
            chapterEl.textContent = ch ? ch.title : '';
        }
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
        // Cancel any pending hide timer
        if (_osdTimer) { clearTimeout(_osdTimer); _osdTimer = null; }
        // Only auto-hide if not pinned (pinned = always visible)
        if (!isOsdPopupOpen() && !osd.classList.contains('osd-pinned')) {
            _osdTimer = setTimeout(() => {
                if (!isOsdPopupOpen() && !osd.classList.contains('osd-pinned')) {
                    osd.classList.remove('visible');
                }
                _osdTimer = null;
            }, 2000);
        }
        // notify SponsorBlock to refresh markers
        if (typeof window.refreshSbMarkers === 'function') window.refreshSbMarkers();
        refreshChapterMarkers();
    }

    function hideOsd() {
        const osd = document.getElementById('tv-osd');
        if (!osd) return;
        // Don't hide if pinned
        if (osd.classList.contains('osd-pinned')) return;
        osd.classList.remove('visible');
        if (_osdTimer) { clearTimeout(_osdTimer); _osdTimer = null; }
    }

    // ── Storyboard preview ──────────────────────────────────────────────

    let _sbData = null;       // {width, height, rows, columns, interval, count}
    let _sbImages = {};       // index -> Image (preloaded sprites)

    _osd.setStoryboard = function (videoId, data) {
        _sbData = data;
        _sbImages = {};
        if (data) {
            for (let i = 0; i < data.count; i++) {
                const img = new Image();
                img.src = `/api/storyboard/${videoId}/${i}`;
                _sbImages[i] = img;
            }
        }
    };

    function _getPreviewForTime(time) {
        if (!_sbData) return null;
        const sb = _sbData;
        const tilesPerSheet = sb.rows * sb.columns;
        const tileIndex = Math.floor(time / sb.interval);
        const sheetIndex = Math.floor(tileIndex / tilesPerSheet);
        const posInSheet = tileIndex % tilesPerSheet;
        const col = posInSheet % sb.columns;
        const row = Math.floor(posInSheet / sb.columns);
        return { sheetIndex, col, row };
    }

    function _tooltipHtml(time) {
        const ch = _getChapterAt(time);
        return ch
            ? '<span class="osd-seek-chapter">' + escapeHtml(ch.title) + '</span><br>' + formatTime(time)
            : formatTime(time);
    }

    function _showPreview(previewEl, pct, time) {
        if (!_sbData || !previewEl) return;
        const info = _getPreviewForTime(time);
        if (!info) return;
        const sb = _sbData;
        const img = _sbImages[info.sheetIndex];
        if (!img) { previewEl.style.display = 'none'; return; }

        previewEl.style.display = 'block';
        previewEl.style.width = sb.width + 'px';
        previewEl.style.height = sb.height + 'px';
        previewEl.style.backgroundImage = 'url(' + img.src + ')';
        previewEl.style.backgroundPosition = '-' + (info.col * sb.width) + 'px -' + (info.row * sb.height) + 'px';
        // Clamp to bar edges
        const bar = previewEl.parentElement;
        if (bar) {
            const barW = bar.offsetWidth;
            const half = sb.width / 2;
            const px = pct * barW;
            const left = Math.max(0, Math.min(barW - sb.width, px - half));
            previewEl.style.left = left + 'px';
        } else {
            previewEl.style.left = 'calc(' + (pct * 100) + '% - ' + (sb.width / 2) + 'px)';
        }
    }

    function _hidePreview(previewEl) {
        if (previewEl) previewEl.style.display = 'none';
    }

    // Expose for TV mode seeking
    _osd.showPreviewAtTime = function (time) {
        const previewEl = document.getElementById('osd-preview');
        const video = _getVideo();
        if (!video || !video.duration) return;
        const pct = time / video.duration;
        _showPreview(previewEl, pct, time);
        // Also update the tooltip
        const tooltip = document.getElementById('osd-seek-tooltip');
        if (tooltip) {
            tooltip.innerHTML = _tooltipHtml(time);
            tooltip.style.left = (pct * 100) + '%';
            tooltip.style.display = 'block';
        }
    };

    _osd.hidePreview = function () {
        _hidePreview(document.getElementById('osd-preview'));
        const tooltip = document.getElementById('osd-seek-tooltip');
        if (tooltip) { tooltip.innerHTML = ''; tooltip.style.display = ''; }
    };

    // ── Seek freeze overlay ─────────────────────────────────────────────
    // While seeking, cover the player with the storyboard tile of the TARGET
    // time (where we're going), scaled to fill, until the new frame is ready.
    // App wires this to the video's seeking/seeked events. No-op without a
    // storyboard. Segments are same-origin, so the sprite loads fine.
    function _seekFreezeEl() {
        let ov = document.getElementById('seek-freeze-overlay');
        if (!ov) {
            const host = document.getElementById(_containerId || 'player-container');
            if (!host) return null;
            ov = document.createElement('div');
            ov.id = 'seek-freeze-overlay';
            ov.className = 'seek-freeze-overlay';
            host.appendChild(ov);
        }
        return ov;
    }

    _osd.showSeekFreeze = function (time) {
        if (!_sbData) return;
        const info = _getPreviewForTime(time);
        if (!info) return;
        const img = _sbImages[info.sheetIndex];
        if (!img) return;
        const ov = _seekFreezeEl();
        if (!ov) return;
        const sb = _sbData;
        let W = ov.clientWidth, H = ov.clientHeight;
        if (!W || !H) { const v = _getVideo(); if (v) { W = v.clientWidth; H = v.clientHeight; } }
        if (!W || !H) return;
        // Scale the whole sprite sheet so one tile fills the overlay, then offset
        // to the target tile.
        ov.style.backgroundImage = 'url(' + img.src + ')';
        ov.style.backgroundSize = (sb.columns * W) + 'px ' + (sb.rows * H) + 'px';
        ov.style.backgroundPosition = '-' + (info.col * W) + 'px -' + (info.row * H) + 'px';
        ov.style.display = 'block';
    };

    _osd.hideSeekFreeze = function () {
        const ov = document.getElementById('seek-freeze-overlay');
        if (ov) ov.style.display = 'none';
    };

    // ── Click-to-seek on progress bar ────────────────────────────────────

    function setupSeekBar() {
        const osdBar = document.getElementById('osd-bar');
        if (!osdBar) return;

        const tooltip = document.getElementById('osd-seek-tooltip');
        const previewEl = document.getElementById('osd-preview');

        function barPct(e) {
            const rect = osdBar.getBoundingClientRect();
            return Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        }

        osdBar.addEventListener('click', function (e) {
            const video = _getVideo();
            if (!video || !video.duration) return;
            video.currentTime = barPct(e) * video.duration;
            showOsd();
        });

        if (tooltip) {
            osdBar.addEventListener('mousemove', function (e) {
                const video = _getVideo();
                if (!video || !video.duration) return;
                const pct = barPct(e);
                const time = pct * video.duration;
                tooltip.innerHTML = _tooltipHtml(time);
                tooltip.style.left = (pct * 100) + '%';
                _showPreview(previewEl, pct, time);
            });
            osdBar.addEventListener('mouseleave', function () {
                tooltip.innerHTML = '';
                _hidePreview(previewEl);
            });
        }
    }

    // ── Volume control ───────────────────────────────────────────────────

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

    function setupVolume() {
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
                const video = _getVideo();
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
                const video = _getVideo();
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
        const videoEl = _getVideo();
        if (videoEl) {
            videoEl.addEventListener('volumechange', () => {
                const v = videoEl.muted ? 0 : videoEl.volume;
                if (volSlider) volSlider.value = Math.round(v * 100);
                updateVolIcon(v);
            });
        }
    }

    // ── Mode buttons (theater / normal / fullscreen / exit-fs) ────────────

    let _preFsMode = 'normal';

    function updateModeButtons() {
        const modeBtns = document.querySelectorAll('.osd-mode-btn');
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

    function setupModeButtons() {
        const modeBtns = document.querySelectorAll('.osd-mode-btn');
        updateModeButtons();

        document.addEventListener('theater-mode-changed', updateModeButtons);

        document.addEventListener('fullscreenchange', () => {
            if (!document.fullscreenElement) {
                if (_preFsMode === 'theater') {
                    document.body.classList.add('theater-mode');
                    const pc = document.getElementById(_containerId);
                    if (pc) pc.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
                const pc = document.getElementById(_containerId);
                if (action === 'normal') {
                    document.body.classList.remove('theater-mode');
                } else if (action === 'theater') {
                    document.body.classList.add('theater-mode');
                    if (pc) pc.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
    }

    // ── Mouse interaction ────────────────────────────────────────────────

    function setupMouseInteraction() {
        const pcEl = document.getElementById(_containerId);
        if (!pcEl) return;

        // Mouse move on container → show OSD (throttled)
        let _mouseMoveThrottle = 0;
        pcEl.addEventListener('mousemove', () => {
            if (!_isVideoView()) return;
            const now = Date.now();
            if (now - _mouseMoveThrottle < 100) return;
            _mouseMoveThrottle = now;
            showOsd();
        });

        // Click on video toggles play/pause + shows OSD.
        // Exception: when in landscape pseudo-fullscreen but not yet in real
        // fullscreen, the tap is "consumed" to upgrade into real fullscreen
        // (which needs the gesture). Without this, the tap would pause the
        // video without ever hiding the browser URL bar.
        pcEl.addEventListener('click', (e) => {
            if (e.target.closest('.tv-osd-bar') || e.target.closest('.sb-toast') || e.target.closest('.osd-right-controls')) return;
            const video = _getVideo();
            if (!video || !_isVideoView()) return;
            if (typeof currentPlayerType !== 'undefined' && currentPlayerType === null) return;
            if (document.body.classList.contains('landscape-fullscreen') && !document.fullscreenElement) {
                pcEl.requestFullscreen().catch(() => {});
                showOsd();
                return;
            }
            video.paused ? video.play() : video.pause();
            showOsd();
        });
    }

    // ── Time update → update OSD when visible ────────────────────────────

    function setupTimeUpdate() {
        const videoEl = _getVideo();
        if (!videoEl) return;
        videoEl.addEventListener('timeupdate', () => {
            const osd = document.getElementById('tv-osd');
            if (osd && osd.classList.contains('visible')) updateOsd();
        });
    }

    // ── Chapter markers on progress bar ─────────────────────────────────

    let _chaptersRendered = false;

    function renderChapterMarkers() {
        clearChapterMarkers();
        const bar = document.getElementById('osd-bar');
        const video = _getVideo();
        const chapters = window.currentChapters;
        if (!bar || !video || !video.duration || !chapters || chapters.length < 2) return;
        const dur = video.duration;
        // Add divider lines between chapters (skip the first one at 0:00)
        for (let i = 1; i < chapters.length; i++) {
            const pct = (chapters[i].start_time / dur) * 100;
            const div = document.createElement('div');
            div.className = 'chapter-marker';
            div.style.left = pct + '%';
            bar.appendChild(div);
        }
        _chaptersRendered = true;
    }

    function clearChapterMarkers() {
        const bar = document.getElementById('osd-bar');
        if (!bar) return;
        bar.querySelectorAll('.chapter-marker').forEach(el => el.remove());
        _chaptersRendered = false;
    }

    function refreshChapterMarkers() {
        const chapters = window.currentChapters;
        if (chapters && chapters.length >= 2 && !_chaptersRendered) renderChapterMarkers();
    }

    // ── Exports ──────────────────────────────────────────────────────────

    _osd.showOsd = showOsd;
    _osd.hideOsd = hideOsd;
    _osd.updateOsd = updateOsd;
    _osd.renderChapterMarkers = renderChapterMarkers;
    _osd.clearChapterMarkers = clearChapterMarkers;
})();

// Global aliases (used by tv-nav.js, sponsorblock.js, etc.)
function showOsd() { window._osd.showOsd(); }
function hideOsd() { window._osd.hideOsd(); }
