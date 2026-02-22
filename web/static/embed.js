// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// YTP Embed — minimal standalone player for /embed/, /v/, /shorts/, /live/

(function () {
    'use strict';

    const video = document.getElementById('video');
    const qualityBtn = document.getElementById('quality-btn');
    const qualityMenu = document.getElementById('quality-menu');
    const subtitleBtn = document.getElementById('subtitle-btn');
    const subtitleMenu = document.getElementById('subtitle-menu');
    const errorMsg = document.getElementById('error-msg');

    let dashPlayer = null;
    let hlsPlayer = null;
    let playerType = null; // 'dash' | 'hls'
    let videoQualities = [];
    let currentHeight = 0;
    let preferredQuality = 1080;
    let subtitleTracks = [];
    let activeLang = null;

    // ── Parse video ID from URL path ─────────────────────────────────────────

    const path = window.location.pathname;
    const match = path.match(/^\/(embed|v|shorts|live)\/([^/?#]+)/);
    if (!match) {
        showError('Invalid video URL');
        return;
    }
    const videoId = match[2];

    // ── Helpers ──────────────────────────────────────────────────────────────

    const _langNames = new Intl.DisplayNames(['en'], { type: 'language' });
    function langName(code) {
        try { return _langNames.of(code); }
        catch { return code.toUpperCase(); }
    }

    function showError(msg) {
        errorMsg.textContent = msg;
        errorMsg.style.display = 'block';
    }

    function getTargetQuality(heights, preferred) {
        if (heights.includes(preferred)) return preferred;
        const below = heights.filter(h => h <= preferred);
        return below.length > 0 ? Math.max(...below) : Math.min(...heights);
    }

    // ── Quality ─────────────────────────────────────────────────────────────

    function buildQualitiesDash() {
        return (dashPlayer.getBitrateInfoListFor('video') || []).map(br => ({
            height: br.height, bandwidth: br.bandwidth, qualityIndex: br.qualityIndex,
        })).sort((a, b) => a.height - b.height);
    }

    function buildQualitiesHls() {
        return (hlsPlayer.levels || []).map((level, idx) => ({
            height: level.height, bandwidth: level.bitrate || level.bandwidth || 0, qualityIndex: idx,
        })).sort((a, b) => a.height - b.height);
    }

    function switchToQuality(entry) {
        if (playerType === 'dash') dashPlayer.setQualityFor('video', entry.qualityIndex);
        else if (playerType === 'hls') hlsPlayer.currentLevel = entry.qualityIndex;
    }

    function updateQualityHighlight(height) {
        currentHeight = height;
        qualityBtn.textContent = height + 'p';
        qualityBtn.disabled = false;
        qualityMenu.querySelectorAll('.embed-menu-item').forEach(opt => {
            opt.classList.toggle('selected', parseInt(opt.dataset.height) === height);
        });
    }

    function populateQualityMenu() {
        qualityMenu.innerHTML = [...videoQualities].reverse().map(q => {
            const sel = q.height === currentHeight ? ' selected' : '';
            return `<div class="embed-menu-item${sel}" data-height="${q.height}">${q.height}p</div>`;
        }).join('');

        qualityMenu.querySelectorAll('.embed-menu-item').forEach(opt => {
            opt.addEventListener('click', e => {
                e.stopPropagation();
                const height = parseInt(opt.dataset.height);
                const entry = videoQualities.find(q => q.height === height);
                if (!entry) return;
                switchToQuality(entry);
                preferredQuality = height;
                qualityMenu.classList.remove('open');
                if (playerType === 'dash') {
                    qualityBtn.disabled = true;
                    qualityBtn.textContent = height + 'p\u2026';
                } else {
                    updateQualityHighlight(height);
                }
            });
        });
    }

    qualityBtn.addEventListener('click', e => {
        e.stopPropagation();
        subtitleMenu.classList.remove('open');
        qualityMenu.classList.toggle('open');
    });

    // ── Subtitles ───────────────────────────────────────────────────────────

    function loadSubtitles(tracks) {
        subtitleTracks = tracks || [];
        if (subtitleTracks.length === 0) {
            subtitleBtn.style.display = 'none';
            return;
        }
        subtitleBtn.style.display = '';
        subtitleBtn.textContent = '\uD83D\uDCAC Off';
    }

    function renderSubtitleMenu() {
        const items = [{ lang: null, label: 'Off' }, ...subtitleTracks.sort((a, b) => (a.label || '').localeCompare(b.label || ''))];
        subtitleMenu.innerHTML = items.map(t => {
            const sel = t.lang === activeLang ? ' selected' : '';
            return `<div class="embed-menu-item${sel}" data-lang="${t.lang || ''}">${t.label || 'Off'}</div>`;
        }).join('');

        subtitleMenu.querySelectorAll('.embed-menu-item').forEach(opt => {
            opt.addEventListener('click', e => {
                e.stopPropagation();
                const lang = opt.dataset.lang || null;
                selectSubtitle(lang);
                subtitleMenu.classList.remove('open');
            });
        });
    }

    function selectSubtitle(lang) {
        [...video.querySelectorAll('track')].forEach(t => t.remove());
        activeLang = lang;

        if (!lang) {
            subtitleBtn.textContent = '\uD83D\uDCAC Off';
            return;
        }

        const track = subtitleTracks.find(t => t.lang === lang);
        if (!track) return;

        const el = document.createElement('track');
        el.kind = 'subtitles';
        el.srclang = track.lang;
        el.label = track.label;
        el.src = `/api/subtitle/${videoId}?lang=${encodeURIComponent(track.lang)}`;

        el.addEventListener('load', () => {
            subtitleBtn.textContent = '\uD83D\uDCAC ' + langName(track.lang);
        });
        el.addEventListener('error', () => {
            subtitleBtn.textContent = '\uD83D\uDCAC Off';
            activeLang = null;
        });

        video.appendChild(el);
        subtitleBtn.textContent = '\uD83D\uDCAC ' + langName(track.lang) + '\u2026';

        const activate = e => {
            if (e.track.language === track.lang) {
                e.track.mode = 'showing';
                video.textTracks.removeEventListener('addtrack', activate);
            }
        };
        video.textTracks.addEventListener('addtrack', activate);
        for (let i = 0; i < video.textTracks.length; i++) {
            if (video.textTracks[i].language === track.lang) {
                video.textTracks[i].mode = 'showing';
                video.textTracks.removeEventListener('addtrack', activate);
                break;
            }
        }
    }

    subtitleBtn.addEventListener('click', e => {
        e.stopPropagation();
        qualityMenu.classList.remove('open');
        renderSubtitleMenu();
        subtitleMenu.classList.toggle('open');
    });

    // Close menus on outside click
    document.addEventListener('click', () => {
        qualityMenu.classList.remove('open');
        subtitleMenu.classList.remove('open');
    });

    // ── DASH Player ─────────────────────────────────────────────────────────

    function startDash() {
        playerType = 'dash';
        dashPlayer = dashjs.MediaPlayer().create();
        dashPlayer.updateSettings({
            streaming: {
                buffer: { fastSwitchEnabled: true, flushBufferAtTrackSwitch: true },
                abr: { autoSwitchBitrate: { video: false } },
            },
        });
        dashPlayer.initialize(video, `/api/dash/${videoId}`, true);

        dashPlayer.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
            videoQualities = buildQualitiesDash();
            if (videoQualities.length === 0) return;
            const heights = videoQualities.map(q => q.height);
            const target = getTargetQuality(heights, preferredQuality);
            const entry = videoQualities.find(q => q.height === target);
            if (entry) { switchToQuality(entry); updateQualityHighlight(target); }
            populateQualityMenu();
        });

        dashPlayer.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, e => {
            if (e.mediaType !== 'video') return;
            const entry = videoQualities.find(q => q.qualityIndex === e.newQuality);
            if (entry) updateQualityHighlight(entry.height);
        });
    }

    // ── HLS Player ──────────────────────────────────────────────────────────

    function startHls(manifestUrl, live) {
        playerType = 'hls';
        const cfg = live ? { liveSyncDurationCount: 3 } : { maxBufferLength: 30, maxMaxBufferLength: 60 };
        hlsPlayer = new Hls(cfg);
        hlsPlayer.attachMedia(video);
        hlsPlayer.loadSource(manifestUrl);

        hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => {
            videoQualities = buildQualitiesHls();
            if (videoQualities.length === 0) return;
            const heights = videoQualities.map(q => q.height);
            const target = getTargetQuality(heights, preferredQuality);
            const entry = videoQualities.find(q => q.height === target);
            if (entry) { hlsPlayer.currentLevel = entry.qualityIndex; updateQualityHighlight(target); }
            populateQualityMenu();
            video.play();
        });

        hlsPlayer.on(Hls.Events.LEVEL_SWITCHED, (event, data) => {
            const level = hlsPlayer.levels[data.level];
            if (level) {
                const entry = videoQualities.find(q => q.height === level.height);
                if (entry) updateQualityHighlight(entry.height);
            }
        });

        hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
            if (data.fatal) {
                hlsPlayer.destroy();
                hlsPlayer = null;
                if (!live) startDash();
            }
        });
    }

    // ── Init ────────────────────────────────────────────────────────────────

    qualityBtn.textContent = '\u2014';
    qualityBtn.disabled = true;

    fetch(`/api/info/${videoId}`)
        .then(r => { if (!r.ok) throw new Error('Video not found'); return r.json(); })
        .then(info => {
            document.title = info.title || 'YTP';
            video.poster = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
            loadSubtitles(info.subtitle_tracks || []);

            if (info.is_live && Hls.isSupported()) {
                startHls(`/api/hls/master/${videoId}?live=1`, true);
            } else {
                startDash();
            }
        })
        .catch(err => showError(err.message));
})();
