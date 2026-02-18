// YTP - Core: DOM refs, state, routing, player, settings, utils

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
const resolutionBadge = document.getElementById('resolution-badge');
const cancelDownloadBtn = document.getElementById('cancel-download-btn');
const progressRingFill = document.querySelector('.progress-ring-fill');
const downloadPill = document.getElementById('download-pill');
const downloadAction = document.getElementById('download-action');
const downloadGear = document.getElementById('download-gear');
const downloadQualityMenu = document.getElementById('download-quality-menu');

// Related
const relatedVideos = document.getElementById('related-videos');
const relatedLoadMore = document.getElementById('related-load-more');

// Subtitles
const subtitleBtnContainer = document.getElementById('subtitle-btn-container');
const subtitleBtn = document.getElementById('subtitle-btn');
const subtitleMenu = document.getElementById('subtitle-menu');

// ── State ───────────────────────────────────────────────────────────────────

let maxDownloadQuality = 1080;
let autoDownloadThreshold = 0;
let progressInterval = null;
let currentVideoId = null;
let currentVideoChannelId = null;
let dashPlayer = null;

// ── Settings ────────────────────────────────────────────────────────────────

const settingsBtn = document.getElementById('settings-btn');
const settingsMenu = document.getElementById('settings-menu');

settingsBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    settingsMenu.classList.toggle('show');
});

document.addEventListener('click', () => {
    settingsMenu.classList.remove('show');
    downloadQualityMenu.classList.add('hidden');
});

settingsMenu.addEventListener('click', (e) => e.stopPropagation());

// Auto-download threshold
const autoDlChips = document.getElementById('auto-dl-chips');
autoDlChips.addEventListener('click', (e) => {
    if (e.target.classList.contains('chip')) {
        autoDlChips.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
        e.target.classList.add('active');
        const minutes = parseInt(e.target.dataset.value);
        autoDownloadThreshold = minutes * 60;
        localStorage.setItem('autoDownloadMinutes', minutes);
    }
});

const savedMinutes = localStorage.getItem('autoDownloadMinutes');
if (savedMinutes !== null) {
    autoDownloadThreshold = parseInt(savedMinutes) * 60;
    autoDlChips.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.dataset.value === savedMinutes);
    });
}

// Max download quality
const qualityChips = document.getElementById('quality-chips');
qualityChips.addEventListener('click', (e) => {
    if (e.target.classList.contains('chip')) {
        qualityChips.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
        e.target.classList.add('active');
        maxDownloadQuality = parseInt(e.target.dataset.value);
        localStorage.setItem('maxDownloadQuality', maxDownloadQuality);
    }
});

const savedQuality = localStorage.getItem('maxDownloadQuality');
if (savedQuality !== null) {
    maxDownloadQuality = parseInt(savedQuality);
    qualityChips.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.dataset.value === savedQuality);
    });
}

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
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
    if (dashPlayer) {
        dashPlayer.destroy();
        dashPlayer = null;
    }
    cancelDownloadBtn.classList.add('hidden');
    downloadPill.classList.add('hidden');
    downloadQualityMenu.classList.add('hidden');
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
    downloadCancelled = false;
    currentVideoId = videoId;

    videoTitle.textContent = title || 'Loading...';
    videoChannel.textContent = channel || '';
    videoChannel.href = '#';
    videoMeta.textContent = '';
    videoDescription.textContent = '';
    videoDescription.classList.add('hidden');
    resolutionBadge.classList.add('hidden');
    downloadPill.classList.add('hidden');
    downloadQualityMenu.classList.add('hidden');
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

    // Start playback
    try {
        const response = await fetch(`/api/progress/${videoId}`);
        const data = await response.json();

        cancelDownloadBtn.classList.add('hidden');
        downloadPill.classList.add('hidden');

        if (data.status === 'ready') {
            videoPlayer.src = `/api/stream/${videoId}`;
            videoPlayer.play();
        } else {
            dashPlayer = dashjs.MediaPlayer().create();
            dashPlayer.updateSettings({
                streaming: { abr: { maxBitrate: { video: -1 } } },
            });
            dashPlayer.initialize(videoPlayer, `/api/dash/${videoId}?quality=${maxDownloadQuality || 1080}`, true);
        }
    } catch (error) {
        videoTitle.textContent = 'Error: ' + error.message;
        cancelDownloadBtn.classList.add('hidden');
    }
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

videoPlayer.addEventListener('loadedmetadata', () => {
    const h = videoPlayer.videoHeight;
    const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;

    if (h > 0) {
        resolutionBadge.textContent = `${h}p`;
        resolutionBadge.classList.remove('hidden');
    }

    if (!currentVideoId || progressInterval) return;
    if (videoPlayer.src && videoPlayer.src.includes('/api/stream/') && !videoPlayer.src.includes('/api/stream-live/')) {
        return;
    }

    const shouldAutoDownload = autoDownloadThreshold > 0 &&
                               duration > 0 &&
                               duration < autoDownloadThreshold &&
                               !downloadCancelled &&
                               h < maxDownloadQuality;

    if (shouldAutoDownload) {
        startHdDownload(currentVideoId, maxDownloadQuality);
    } else {
        checkDownloadOffer(currentVideoId, h);
    }
});

// Boot — called from index.html after all scripts load
