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

// Related
const relatedVideos = document.getElementById('related-videos');
const relatedLoadMore = document.getElementById('related-load-more');

// Subtitles
const subtitleBtnContainer = document.getElementById('subtitle-btn-container');
const subtitleBtn = document.getElementById('subtitle-btn');
const subtitleMenu = document.getElementById('subtitle-menu');

// ── State ───────────────────────────────────────────────────────────────────

let currentVideoId = null;
let currentVideoChannelId = null;
let dashPlayer = null;
let preferredQuality = parseInt(localStorage.getItem('preferredQuality')) || 1080;
let currentActiveHeight = 0;
// Quality list: [{height, bandwidth, qualityIndex}]
let videoQualities = [];

// ── Quality Selector ────────────────────────────────────────────────────────

function getTargetQuality(heights, preferred) {
    if (heights.includes(preferred)) return preferred;
    const below = heights.filter(h => h <= preferred);
    return below.length > 0 ? Math.max(...below) : Math.min(...heights);
}

function buildQualities() {
    // Single video AdaptationSet — use getBitrateInfoListFor which has qualityIndex
    const bitrateList = dashPlayer.getBitrateInfoListFor('video');
    return (bitrateList || []).map(br => ({
        height: br.height,
        bandwidth: br.bandwidth,
        qualityIndex: br.qualityIndex,
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
            qualityMenu.classList.add('hidden');
            qualityBtn.disabled = true;
            qualityBtn.textContent = `${height}p\u2026`;
        });
    });
}

function switchToQuality(entry) {
    dashPlayer.setQualityFor('video', entry.qualityIndex);
}

qualityBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    qualityMenu.classList.toggle('hidden');
});

qualityMenu.addEventListener('click', (e) => e.stopPropagation());

document.addEventListener('click', () => {
    qualityMenu.classList.add('hidden');
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
    }
}

// ── Player ──────────────────────────────────────────────────────────────────

function stopPlayer() {
    if (dashPlayer) {
        dashPlayer.destroy();
        dashPlayer = null;
    }
    qualitySelector.classList.add('hidden');
    qualityMenu.classList.add('hidden');
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
    qualitySelector.classList.add('hidden');
    relatedVideos.innerHTML = '';

    videoPlayer.dataset.expectedDuration = duration || 0;
    videoPlayer.poster = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

    // Fetch video info
    fetch(`/api/info/${videoId}`)
        .then(r => r.json())
        .then(info => {
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
        })
        .catch(() => {});

    fetchRelatedVideos(videoId);

    // Start DASH playback
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
        videoQualities = buildQualities();
        if (videoQualities.length === 0) return;

        const heights = videoQualities.map(q => q.height);
        const targetHeight = getTargetQuality(heights, preferredQuality);
        const targetEntry = videoQualities.find(q => q.height === targetHeight);

        if (targetEntry) {
            switchToQuality(targetEntry);
            currentActiveHeight = targetHeight;
            qualityBtn.textContent = `${targetHeight}p`;
        }

        populateQualityMenu();
        qualitySelector.classList.remove('hidden');
    });

    dashPlayer.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, (e) => {
        if (e.mediaType !== 'video') return;
        const entry = videoQualities.find(q => q.qualityIndex === e.newQuality);
        if (entry) {
            currentActiveHeight = entry.height;
            qualityBtn.textContent = `${entry.height}p`;
            qualityBtn.disabled = false;
            qualityMenu.querySelectorAll('.quality-option').forEach(opt => {
                opt.classList.toggle('selected', parseInt(opt.dataset.height) === entry.height);
            });
        }
    });
}

// ── Utils ───────────────────────────────────────────────────────────────────

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
    return escaped.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

// ── Event Listeners ─────────────────────────────────────────────────────────

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

videoPlayer.addEventListener('error', () => {
    console.log('Video error:', videoPlayer.error?.message);
});

// For non-DASH fallback: show resolution from video element
videoPlayer.addEventListener('loadedmetadata', () => {
    if (dashPlayer) return;
    const h = videoPlayer.videoHeight;
    if (h > 0) {
        qualityBtn.textContent = `${h}p`;
        qualitySelector.classList.remove('hidden');
        qualityMenu.innerHTML = '';
    }
});

// Boot — called from index.html after all scripts load
