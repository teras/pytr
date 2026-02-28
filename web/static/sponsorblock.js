// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// SponsorBlock: auto-skip segments, toast, highlight, TV OSD markers

(function () {
    const CATEGORY_COLORS = {
        sponsor: '#00d400',
        intro: '#00ffff',
        outro: '#0202ed',
        selfpromo: '#ffff00',
        interaction: '#cc00ff',
        preview: '#008fd6',
        filler: '#7300FF',
        music_offtopic: '#ff9900',
    };

    const CATEGORY_LABELS = {
        sponsor: 'Sponsor',
        intro: 'Intro',
        outro: 'Outro',
        selfpromo: 'Self-promotion',
        interaction: 'Interaction',
        preview: 'Preview',
        filler: 'Filler',
        music_offtopic: 'Non-music',
    };

    const DEFAULT_ON = ['sponsor', 'intro', 'outro', 'selfpromo', 'interaction'];

    let _segments = [];
    let _highlight = null;
    let _videoId = null;
    let _skipTimer = null;
    let _toastTimer = null;
    let _toastEl = null;
    let _prefs = null; // {enabled: bool, categories: string[]}
    let _seekedAt = 0; // performance.now() of last USER seek (not auto-skip)
    let _isAutoSeek = false; // flag to distinguish auto-skip seeks from user seeks

    // ── Preferences ──────────────────────────────────────────────────────────

    function _loadPrefs() {
        if (typeof currentProfile !== 'undefined' && currentProfile && currentProfile.sb_prefs) {
            try {
                const p = typeof currentProfile.sb_prefs === 'string'
                    ? JSON.parse(currentProfile.sb_prefs)
                    : currentProfile.sb_prefs;
                if (typeof p.enabled === 'boolean') {
                    _prefs = p;
                    return;
                }
            } catch (e) {}
        }
        _prefs = { enabled: true, categories: [...DEFAULT_ON] };
    }

    function _isCategoryEnabled(cat) {
        if (!_prefs) _loadPrefs();
        return _prefs.enabled && _prefs.categories.includes(cat);
    }

    // ── Init & fetch ─────────────────────────────────────────────────────────

    async function initSponsorBlock(videoId) {
        resetSponsorBlock();
        _videoId = videoId;
        _loadPrefs();
        if (!_prefs.enabled) return;

        try {
            const resp = await fetch(`/api/sponsorblock/${videoId}`);
            if (!resp.ok) return;
            if (_videoId !== videoId) return;
            const data = await resp.json();
            _segments = data.segments || [];
            _highlight = data.highlight || null;
        } catch (e) {}
    }

    function resetSponsorBlock() {
        _segments = [];
        _highlight = null;
        _videoId = null;
        _seekedAt = 0;
        _isAutoSeek = false;
        if (_skipTimer) { clearTimeout(_skipTimer); _skipTimer = null; }
        _dismissToast();
    }

    // ── Skip logic ───────────────────────────────────────────────────────────

    function _scheduleSkip() {
        if (_skipTimer) { clearTimeout(_skipTimer); _skipTimer = null; }
        const video = document.getElementById('video-player');
        if (!video || !_segments.length || !video.duration) return;

        const now = video.currentTime;
        let nearest = null;
        let minDelay = Infinity;

        for (const seg of _segments) {
            if (!_isCategoryEnabled(seg.category)) continue;
            const start = seg.segment[0];
            const end = seg.segment[1];
            if (now >= end) continue;
            if (now >= start - 0.5 && now < end) {
                if (performance.now() - _seekedAt < 1500) continue;
                _doSkip(video, seg);
                return;
            }
            const delay = (start - now) * 1000;
            if (delay > 0 && delay < minDelay) {
                minDelay = delay;
                nearest = seg;
            }
        }

        if (nearest && minDelay < 7200000) {
            _skipTimer = setTimeout(() => {
                if (_videoId && !video.paused) {
                    const cur = video.currentTime;
                    if (cur >= nearest.segment[0] - 0.5 && cur < nearest.segment[1]) {
                        _doSkip(video, nearest);
                    } else {
                        _scheduleSkip();
                    }
                }
            }, Math.max(minDelay - 200, 0));
        }
    }

    function _doSkip(video, seg) {
        if (seg.actionType === 'mute') {
            video.muted = true;
            const checkUnmute = () => {
                if (video.currentTime >= seg.segment[1]) {
                    video.muted = false;
                    video.removeEventListener('timeupdate', checkUnmute);
                }
            };
            video.addEventListener('timeupdate', checkUnmute);
            _scheduleSkip();
            return;
        }
        const skipFrom = video.currentTime;
        _isAutoSeek = true;
        video.currentTime = seg.segment[1];
        _showToast(seg.category, skipFrom);
        _scheduleSkip();
    }

    // ── Toast ────────────────────────────────────────────────────────────────

    function _showToast(category, undoPosition) {
        _dismissToast();
        const container = document.getElementById('player-container');
        if (!container) return;

        _toastEl = document.createElement('div');
        _toastEl.className = 'sb-toast';
        const label = CATEGORY_LABELS[category] || category;
        const color = CATEGORY_COLORS[category] || '#888';
        _toastEl.innerHTML = `<span class="sb-toast-dot" style="background:${color}"></span><span>Skipped: ${label}</span><button class="sb-toast-undo">Undo</button>`;
        container.appendChild(_toastEl);

        // Trigger reflow for animation
        _toastEl.offsetHeight;
        _toastEl.classList.add('sb-toast-visible');

        _toastEl.querySelector('.sb-toast-undo').addEventListener('click', () => {
            const video = document.getElementById('video-player');
            if (video) video.currentTime = undoPosition;
            _dismissToast();
        });

        _toastTimer = setTimeout(() => _dismissToast(), 4000);
    }

    function _dismissToast() {
        if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
        if (_toastEl) {
            _toastEl.classList.remove('sb-toast-visible');
            const el = _toastEl;
            setTimeout(() => el.remove(), 300);
            _toastEl = null;
        }
    }

    // ── Highlight ────────────────────────────────────────────────────────────

    function getSponsorBlockHighlight() {
        return _highlight ? _highlight.timestamp : null;
    }

    function seekToHighlight() {
        const ts = getSponsorBlockHighlight();
        if (ts != null && ts > 5) {
            const video = document.getElementById('video-player');
            if (video) video.currentTime = ts;
        }
    }

    // ── Timeupdate hook ──────────────────────────────────────────────────────

    function checkSponsorBlock(currentTime) {
        if (!_skipTimer && _segments.length && _prefs && _prefs.enabled) {
            _scheduleSkip();
        }
    }

    // ── Event listeners ──────────────────────────────────────────────────────

    const video = document.getElementById('video-player');
    if (video) {
        video.addEventListener('seeking', () => {
            if (_isAutoSeek) {
                _isAutoSeek = false;
            } else {
                _seekedAt = performance.now();
                if (_skipTimer) { clearTimeout(_skipTimer); _skipTimer = null; }
            }
        });
        video.addEventListener('play', () => {
            if (_segments.length) _scheduleSkip();
        });
    }

    // ── Settings UI ──────────────────────────────────────────────────────────

    function buildSponsorBlockSettings() {
        if (!_prefs) _loadPrefs();
        const cats = Object.keys(CATEGORY_COLORS);
        const rows = cats.map(cat => {
            const checked = _prefs.categories.includes(cat) ? 'checked' : '';
            const label = CATEGORY_LABELS[cat] || cat;
            const color = CATEGORY_COLORS[cat];
            return `<label class="sb-cat-row">
                <input type="checkbox" data-cat="${cat}" ${checked} ${!_prefs.enabled ? 'disabled' : ''}>
                <span class="sb-cat-dot" style="background:${color}"></span>
                ${label}
            </label>`;
        }).join('');

        return `<div class="sb-settings">
            <label class="sb-toggle-row">
                <input type="checkbox" id="sb-enabled" ${_prefs.enabled ? 'checked' : ''}>
                <strong>SponsorBlock</strong>
            </label>
            <div class="sb-categories" id="sb-categories">
                ${rows}
            </div>
        </div>`;
    }

    function attachSponsorBlockSettingsListeners(container) {
        const toggle = container.querySelector('#sb-enabled');
        const catBoxes = container.querySelectorAll('[data-cat]');

        toggle.addEventListener('change', () => {
            catBoxes.forEach(cb => cb.disabled = !toggle.checked);
            _savePrefs(toggle.checked, _getSelectedCats(catBoxes));
        });

        catBoxes.forEach(cb => {
            cb.addEventListener('change', () => {
                _savePrefs(toggle.checked, _getSelectedCats(catBoxes));
            });
        });
    }

    function _getSelectedCats(catBoxes) {
        const cats = [];
        catBoxes.forEach(cb => { if (cb.checked) cats.push(cb.dataset.cat); });
        return cats;
    }

    async function _savePrefs(enabled, categories) {
        _prefs = { enabled, categories };
        if (typeof currentProfile !== 'undefined' && currentProfile) {
            currentProfile.sb_prefs = JSON.stringify(_prefs);
        }
        try {
            await fetch('/api/profiles/preferences/sponsorblock', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled, categories }),
            });
        } catch (e) {}
    }

    // ── OSD Markers ─────────────────────────────────────────────────────────

    function _renderOsdMarkers() {
        _clearOsdMarkers();
        const bar = document.getElementById('osd-bar');
        const video = document.getElementById('video-player');
        if (!bar || !video || !video.duration || !_segments.length) return;
        const dur = video.duration;
        for (const seg of _segments) {
            if (!_isCategoryEnabled(seg.category)) continue;
            const start = seg.segment[0];
            const end = seg.segment[1];
            const left = (start / dur) * 100;
            const width = ((end - start) / dur) * 100;
            const marker = document.createElement('div');
            marker.className = 'sb-osd-marker';
            marker.style.left = left + '%';
            marker.style.width = Math.max(width, 0.3) + '%';
            marker.style.background = CATEGORY_COLORS[seg.category] || '#888';
            bar.appendChild(marker);
        }
    }

    function _clearOsdMarkers() {
        const bar = document.getElementById('osd-bar');
        if (!bar) return;
        bar.querySelectorAll('.sb-osd-marker').forEach(el => el.remove());
    }

    function refreshSbMarkers() {
        if (_segments.length && _prefs && _prefs.enabled) _renderOsdMarkers();
    }

    // ── Public API ───────────────────────────────────────────────────────────

    window.initSponsorBlock = initSponsorBlock;
    window.resetSponsorBlock = resetSponsorBlock;
    window.checkSponsorBlock = checkSponsorBlock;
    window.getSponsorBlockHighlight = getSponsorBlockHighlight;
    window.seekToHighlight = seekToHighlight;
    window.buildSponsorBlockSettings = buildSponsorBlockSettings;
    window.attachSponsorBlockSettingsListeners = attachSponsorBlockSettingsListeners;
    window.refreshSbMarkers = refreshSbMarkers;

})();
