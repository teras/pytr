// YTP - Core: DOM refs, state, routing, player, quality selector, utils

// ── DOM Elements ────────────────────────────────────────────────────────────

// Views
const listView = document.getElementById('list-view');
const videoView = document.getElementById('video-view');
const listHeader = document.getElementById('list-header');
const listTitle = document.getElementById('list-title');

// Search
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const videoGrid = document.getElementById('video-grid');
const noResults = document.getElementById('no-results');
const loadMoreContainer = document.getElementById('load-more-container');

// Video Page
const playerContainer = document.getElementById('player-container');
const videoPlayer = document.getElementById('video-player');
const videoTitle = document.getElementById('video-title');
const videoChannel = document.getElementById('video-channel');
const videoMeta = document.getElementById('video-meta');
const videoDescription = document.getElementById('video-description');

// Quality selector
const qualitySelector = document.getElementById('quality-selector');
const qualityBtn = document.getElementById('quality-btn');
const qualityMenu = document.getElementById('quality-menu');

// Audio selector
const audioBtnContainer = document.getElementById('audio-btn-container');
const audioBtn = document.getElementById('audio-btn');
const audioMenu = document.getElementById('audio-menu');

// Related
const relatedVideos = document.getElementById('related-videos');

// Subtitles
const subtitleBtnContainer = document.getElementById('subtitle-btn-container');
const subtitleBtn = document.getElementById('subtitle-btn');
const subtitleMenu = document.getElementById('subtitle-menu');

// ── State ───────────────────────────────────────────────────────────────────

let currentVideoId = null;
let currentVideoChannelId = null;
let dashPlayer = null;
let hlsPlayer = null;
let currentPlayerType = null; // 'dash' | 'hls'
let currentAudioLang = null; // current HLS audio language
let hlsAudioTracks = []; // [{lang, default}]
let preferredQuality = parseInt(localStorage.getItem('preferredQuality')) || 1080;
let currentActiveHeight = 0;
// Quality list: [{height, bandwidth, qualityIndex}]
let videoQualities = [];
let pendingSeek = null; // {time, play} — set during audio language switch
let currentHlsManifestUrl = null; // base HLS manifest URL for multi-audio videos

// ── Quality Selector ────────────────────────────────────────────────────────

function getTargetQuality(heights, preferred) {
    if (heights.includes(preferred)) return preferred;
    const below = heights.filter(h => h <= preferred);
    return below.length > 0 ? Math.max(...below) : Math.min(...heights);
}

function buildQualitiesDash() {
    const bitrateList = dashPlayer.getBitrateInfoListFor('video');
    return (bitrateList || []).map(br => ({
        height: br.height,
        bandwidth: br.bandwidth,
        qualityIndex: br.qualityIndex,
    })).sort((a, b) => a.height - b.height);
}

function buildQualitiesHls() {
    return (hlsPlayer.levels || []).map((level, idx) => ({
        height: level.height,
        bandwidth: level.bitrate || level.bandwidth || 0,
        qualityIndex: idx,
    })).sort((a, b) => a.height - b.height);
}

function populateQualityMenu() {
    qualityMenu.innerHTML = [...videoQualities].reverse().map(q => {
        const active = q.height === currentActiveHeight ? ' selected' : '';
        return `<div class="quality-option${active}" data-height="${q.height}">
            <span>${q.height}p</span>
        </div>`;
    }).join('');

    qualityMenu.querySelectorAll('.quality-option').forEach(opt => {
        opt.addEventListener('click', (e) => {
            e.stopPropagation();
            const height = parseInt(opt.dataset.height);
            const entry = videoQualities.find(q => q.height === height);
            if (!entry || qualityBtn.disabled) return;
            switchToQuality(entry);
            preferredQuality = height;
            localStorage.setItem('preferredQuality', height);
            if (typeof savePreference === 'function') savePreference('quality', height);
            qualityMenu.classList.add('hidden');
            if (currentPlayerType === 'dash') {
                qualityBtn.disabled = true;
                qualityBtn.textContent = `\ud83c\udfac ${height}p\u2026`;
            } else {
                currentActiveHeight = height;
                qualityBtn.textContent = `\ud83c\udfac ${height}p`;
                qualityMenu.querySelectorAll('.quality-option').forEach(o => {
                    o.classList.toggle('selected', parseInt(o.dataset.height) === height);
                });
            }
        });
    });
}

function switchToQuality(entry) {
    if (currentPlayerType === 'dash') {
        dashPlayer.setQualityFor('video', entry.qualityIndex);
    } else if (currentPlayerType === 'hls') {
        hlsPlayer.currentLevel = entry.qualityIndex;
    }
}

qualityBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    qualityMenu.classList.toggle('hidden');
    audioMenu.classList.add('hidden');
});

qualityMenu.addEventListener('click', (e) => e.stopPropagation());

// ── Audio Selector ──────────────────────────────────────────────────────────

audioBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    audioMenu.classList.toggle('hidden');
    qualityMenu.classList.add('hidden');
});

audioMenu.addEventListener('click', (e) => e.stopPropagation());

function populateAudioMenu(tracks, currentLang) {
    audioMenu.innerHTML = tracks.map(track => {
        const selected = track.lang === currentLang ? ' selected' : '';
        const label = track.lang === 'original' ? 'Original' : langName(track.lang);
        const isDefault = track.default ? ' (original)' : '';
        return `<div class="audio-option${selected}" data-lang="${escapeAttr(track.lang)}">
            <span>${label}${isDefault}</span>
        </div>`;
    }).join('');

    audioMenu.querySelectorAll('.audio-option').forEach(opt => {
        opt.addEventListener('click', (e) => {
            e.stopPropagation();
            const lang = opt.dataset.lang;
            if (lang === currentAudioLang) {
                audioMenu.classList.add('hidden');
                return;
            }
            switchAudioLanguage(lang);
            audioMenu.classList.add('hidden');
        });
    });
}

function switchAudioLanguage(lang) {
    if (!currentVideoId) return;

    // Save current position and play state
    const currentTime = videoPlayer.currentTime;
    const wasPlaying = !videoPlayer.paused;
    pendingSeek = { time: currentTime, play: wasPlaying };

    // Remove existing subtitle tracks so they get recreated fresh after player switch
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());

    // Update state and UI
    currentAudioLang = lang;
    audioBtn.textContent = lang === 'original' ? '\ud83d\udd0a Original' : `\ud83d\udd0a ${langName(lang)}`;
    audioMenu.querySelectorAll('.audio-option').forEach(o => {
        o.classList.toggle('selected', o.dataset.lang === lang);
    });

    if (lang === 'original') {
        // Switch back to DASH (restores full quality, up to 4K)
        if (hlsPlayer) { hlsPlayer.destroy(); hlsPlayer = null; }
        startDashPlayer(currentVideoId);
    } else {
        // Switch to HLS with selected audio (max 1080p)
        if (dashPlayer) { dashPlayer.destroy(); dashPlayer = null; }
        if (hlsPlayer) { hlsPlayer.destroy(); hlsPlayer = null; }
        const manifestUrl = `/api/hls/master/${currentVideoId}?audio=${encodeURIComponent(lang)}`;
        startHlsPlayer(currentVideoId, manifestUrl);
    }
}

document.addEventListener('click', () => {
    qualityMenu.classList.add('hidden');
    audioMenu.classList.add('hidden');
});

// ── Routing ─────────────────────────────────────────────────────────────────

function showListView() {
    listView.classList.remove('hidden');
    videoView.classList.add('hidden');
    stopPlayer();
}

function showVideoView() {
    listView.classList.add('hidden');
    videoView.classList.remove('hidden');
}

function navigateToVideo(videoId, title, channel, duration) {
    cacheListView();
    history.pushState({ view: 'video', videoId, title, channel, duration }, '', `/watch?v=${videoId}`);
    showVideoView();
    playVideo(videoId, title, channel, duration);
}

function navigateToChannel(channelId, channelName) {
    history.pushState({ view: 'channel', channelId, channelName }, '', `/channel/${channelId}`);
    showListView();
    loadChannelVideos(channelId, channelName);
}

function navigateToSearch() {
    history.pushState({ view: 'search' }, '', '/');
    showListView();
    restoreListCache();
}

window.addEventListener('popstate', (e) => {
    if (e.state?.view === 'video') {
        showVideoView();
        playVideo(e.state.videoId, e.state.title, e.state.channel, e.state.duration);
    } else if (e.state?.view === 'channel') {
        showListView();
        loadChannelVideos(e.state.channelId, e.state.channelName);
    } else if (e.state?.view === 'history') {
        showListView();
        loadHistory();
    } else if (e.state?.view === 'favorites') {
        showListView();
        loadFavorites();
    } else {
        showListView();
        restoreListCache();
    }
});

function handleInitialRoute() {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);

    if (path === '/watch' && params.get('v')) {
        showVideoView();
        playVideo(params.get('v'), '', '', 0);
    } else if (path.startsWith('/channel/')) {
        const channelId = path.split('/channel/')[1];
        showListView();
        loadChannelVideos(channelId, '');
    } else if (path === '/history') {
        showListView();
        loadHistory();
    } else if (path === '/favorites') {
        showListView();
        loadFavorites();
    }
}

async function loadHistory() {
    listHeader.classList.remove('hidden');
    listTitle.textContent = 'Watch History';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');

    try {
        const resp = await fetch('/api/profiles/history?limit=50');
        if (!resp.ok) throw new Error('Failed to load history');
        const items = await resp.json();
        if (items.length === 0) {
            noResults.classList.remove('hidden');
        } else {
            renderVideos(items.map(item => ({
                id: item.video_id,
                title: item.title,
                channel: item.channel,
                thumbnail: item.thumbnail || `https://img.youtube.com/vi/${item.video_id}/hqdefault.jpg`,
                duration: item.duration,
                duration_str: item.duration_str || '',
            })));
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

async function loadFavorites() {
    listHeader.classList.remove('hidden');
    listTitle.textContent = 'Favorites';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');

    try {
        const resp = await fetch('/api/profiles/favorites?limit=50');
        if (!resp.ok) throw new Error('Failed to load favorites');
        const items = await resp.json();
        if (items.length === 0) {
            noResults.classList.remove('hidden');
        } else {
            renderVideos(items.map(item => ({
                id: item.video_id,
                title: item.title,
                channel: item.channel,
                thumbnail: item.thumbnail || `https://img.youtube.com/vi/${item.video_id}/hqdefault.jpg`,
                duration: item.duration,
                duration_str: item.duration_str || '',
            })));
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

// ── Player ──────────────────────────────────────────────────────────────────

function stopPlayer() {
    savePosition();
    if (positionSaveTimer) {
        clearTimeout(positionSaveTimer);
        positionSaveTimer = null;
    }
    if (dashPlayer) {
        dashPlayer.destroy();
        dashPlayer = null;
    }
    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }
    currentPlayerType = null;
    currentAudioLang = null;
    hlsAudioTracks = [];
    pendingSeek = null;
    currentHlsManifestUrl = null;
    qualitySelector.classList.add('hidden');
    qualityMenu.classList.add('hidden');
    audioBtnContainer.classList.add('hidden');
    audioMenu.classList.add('hidden');
    currentActiveHeight = 0;
    videoQualities = [];
    subtitleBtnContainer.classList.add('hidden');
    subtitleTracks = [];
    failedSubtitles.clear();
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
    currentVideoId = null;
    currentVideoChannelId = null;
    videoPlayer.pause();
    videoPlayer.removeAttribute('src');
    videoPlayer.removeAttribute('poster');
    videoPlayer.load();
}

async function playVideo(videoId, title, channel, duration) {
    stopPlayer();
    currentVideoId = videoId;

    videoTitle.textContent = title || 'Loading...';
    videoChannel.textContent = channel || '';
    videoChannel.href = '#';
    videoMeta.textContent = '';
    videoDescription.textContent = '';
    videoDescription.classList.add('hidden');
    qualitySelector.classList.remove('hidden');
    qualityBtn.textContent = '\ud83c\udfac \u2014';
    qualityBtn.disabled = true;
    audioBtnContainer.classList.add('hidden');
    relatedVideos.innerHTML = '';

    videoPlayer.dataset.expectedDuration = duration || 0;
    videoPlayer.poster = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

    // Favorite button
    const favBtn = document.getElementById('favorite-btn');
    if (favBtn) {
        if (typeof currentProfile !== 'undefined' && currentProfile) {
            favBtn.classList.remove('hidden');
            favBtn.dataset.favorited = 'false';
            favBtn.textContent = '\u2606 Save';
            favBtn.classList.remove('favorited');
            if (typeof checkFavoriteStatus === 'function') checkFavoriteStatus(videoId);
        } else {
            favBtn.classList.add('hidden');
        }
    }

    // Fetch video info — determines player type
    try {
        const resp = await fetch(`/api/info/${videoId}`);
        const info = await resp.json();

        videoTitle.textContent = info.title || title;
        videoChannel.textContent = info.channel || channel;

        if (info.channel_id) {
            currentVideoChannelId = info.channel_id;
            videoChannel.href = `/channel/${info.channel_id}`;
            videoChannel.onclick = (e) => {
                e.preventDefault();
                navigateToChannel(info.channel_id, info.channel);
            };
        }

        const metaParts = [];
        if (info.upload_date) metaParts.push(`\ud83d\udcc5 ${info.upload_date}`);
        if (info.views) metaParts.push(`\ud83d\udc41 ${info.views}`);
        if (info.likes) metaParts.push(`\ud83d\udc4d ${info.likes}`);
        videoMeta.textContent = metaParts.join('  \u2022  ');

        if (info.description) {
            videoDescription.innerHTML = linkifyText(info.description);
            videoDescription.classList.remove('hidden');
        }

        loadSubtitleTracks(videoId, info.subtitle_tracks || []);

        // Always start with DASH (full quality, up to 4K)
        startDashPlayer(videoId);

        // If multi-audio available, show audio selector (HLS used only on language switch)
        if (info.has_multi_audio && info.hls_manifest_url && Hls.isSupported()) {
            currentHlsManifestUrl = info.hls_manifest_url;
            try {
                const audioResp = await fetch(`/api/hls/audio-tracks/${videoId}`);
                const data = await audioResp.json();
                hlsAudioTracks = data.audio_tracks || [];
                if (hlsAudioTracks.length > 1) {
                    audioBtnContainer.classList.remove('hidden');
                    audioBtn.textContent = '\ud83d\udd0a Original';
                    currentAudioLang = 'original';
                    populateAudioMenu(hlsAudioTracks, 'original');
                }
            } catch {}
        }
    } catch (err) {
        console.error('Info fetch failed, falling back to DASH:', err);
        startDashPlayer(videoId);
    }

    fetchRelatedVideos(videoId);
}

function startDashPlayer(videoId) {
    currentPlayerType = 'dash';
    dashPlayer = dashjs.MediaPlayer().create();
    dashPlayer.updateSettings({
        streaming: {
            buffer: {
                fastSwitchEnabled: true,
                flushBufferAtTrackSwitch: true,
            },
            abr: { autoSwitchBitrate: { video: false } },
        },
    });
    dashPlayer.initialize(videoPlayer, `/api/dash/${videoId}`, true);

    dashPlayer.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
        videoQualities = buildQualitiesDash();
        if (videoQualities.length === 0) return;

        const heights = videoQualities.map(q => q.height);
        const targetHeight = getTargetQuality(heights, preferredQuality);
        const targetEntry = videoQualities.find(q => q.height === targetHeight);

        if (targetEntry) {
            switchToQuality(targetEntry);
            currentActiveHeight = targetHeight;
            qualityBtn.textContent = `\ud83c\udfac ${targetHeight}p`;
            qualityBtn.disabled = false;
        }

        populateQualityMenu();

        // Restore position: pendingSeek (from audio switch) takes priority
        if (pendingSeek) {
            videoPlayer.currentTime = pendingSeek.time;
            if (pendingSeek.play) videoPlayer.play();
            applySubtitlePreference();
            pendingSeek = null;
        } else {
            restorePosition(videoId);
        }
    });

    dashPlayer.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, (e) => {
        if (e.mediaType !== 'video') return;
        const entry = videoQualities.find(q => q.qualityIndex === e.newQuality);
        if (entry) {
            currentActiveHeight = entry.height;
            qualityBtn.textContent = `\ud83c\udfac ${entry.height}p`;
            qualityBtn.disabled = false;
            qualityMenu.querySelectorAll('.quality-option').forEach(opt => {
                opt.classList.toggle('selected', parseInt(opt.dataset.height) === entry.height);
            });
        }
    });
}

function startHlsPlayer(videoId, manifestUrl) {
    currentPlayerType = 'hls';

    hlsPlayer = new Hls({ maxBufferLength: 30, maxMaxBufferLength: 60 });
    hlsPlayer.attachMedia(videoPlayer);
    hlsPlayer.loadSource(manifestUrl);

    hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => {
        videoQualities = buildQualitiesHls();
        if (videoQualities.length === 0) return;

        const heights = videoQualities.map(q => q.height);
        const targetHeight = getTargetQuality(heights, preferredQuality);
        const targetEntry = videoQualities.find(q => q.height === targetHeight);

        if (targetEntry) {
            hlsPlayer.currentLevel = targetEntry.qualityIndex;
            currentActiveHeight = targetHeight;
            qualityBtn.textContent = `\ud83c\udfac ${targetHeight}p`;
            qualityBtn.disabled = false;
        }

        populateQualityMenu();

        // Restore position: pendingSeek (from audio switch) takes priority
        if (pendingSeek) {
            videoPlayer.currentTime = pendingSeek.time;
            if (pendingSeek.play) videoPlayer.play();
            applySubtitlePreference();
            pendingSeek = null;
        } else {
            videoPlayer.play();
            restorePosition(videoId);
        }
    });

    hlsPlayer.on(Hls.Events.LEVEL_SWITCHED, (event, data) => {
        const level = hlsPlayer.levels[data.level];
        if (level) {
            const entry = videoQualities.find(q => q.height === level.height);
            if (entry) {
                currentActiveHeight = entry.height;
                qualityBtn.textContent = `\ud83c\udfac ${entry.height}p`;
                qualityBtn.disabled = false;
                qualityMenu.querySelectorAll('.quality-option').forEach(opt => {
                    opt.classList.toggle('selected', parseInt(opt.dataset.height) === entry.height);
                });
            }
        }
    });

    hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
            console.error('HLS fatal error, falling back to DASH:', data);
            hlsPlayer.destroy();
            hlsPlayer = null;
            startDashPlayer(videoId);
        }
    });
}

// ── Utils ───────────────────────────────────────────────────────────────────

const _langNames = new Intl.DisplayNames(['en'], { type: 'language' });
function langName(code) {
    try { return _langNames.of(code); }
    catch { return code.toUpperCase(); }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function linkifyText(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
        const href = match.replace(/&quot;/g, '%22').replace(/&#39;/g, '%27').replace(/&amp;/g, '&');
        return `<a href="${href}" target="_blank" rel="noopener">${match}</a>`;
    });
}

// ── Playback Position ───────────────────────────────────────────────────────

let positionSaveTimer = null;

function savePosition() {
    if (typeof savePositionToAPI === 'function') {
        savePositionToAPI();
    }
}

function restorePosition(videoId) {
    if (typeof restorePositionFromAPI === 'function') {
        restorePositionFromAPI(videoId);
    }
}

videoPlayer.addEventListener('timeupdate', () => {
    if (!positionSaveTimer) {
        positionSaveTimer = setTimeout(() => {
            savePosition();
            positionSaveTimer = null;
        }, 5000);
    }
});

videoPlayer.addEventListener('ended', () => {
    if (currentVideoId) {
        // Save position 0 to mark as watched
        if (typeof savePositionToAPI === 'function' && typeof currentProfile !== 'undefined' && currentProfile) {
            fetch('/api/profiles/position', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video_id: currentVideoId, position: 0 }),
            }).catch(() => {});
        }
    }
});


// ── Event Listeners ─────────────────────────────────────────────────────────

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

videoPlayer.addEventListener('error', () => {
    console.log('Video error:', videoPlayer.error?.message);
});

// For non-DASH/HLS fallback: show resolution from video element
videoPlayer.addEventListener('loadedmetadata', () => {
    if (dashPlayer || hlsPlayer) return;
    const h = videoPlayer.videoHeight;
    if (h > 0) {
        qualityBtn.textContent = `\ud83c\udfac ${h}p`;
        qualityBtn.disabled = false;
        qualityMenu.innerHTML = '';
    }
});

// Boot — called from index.html after all scripts load
