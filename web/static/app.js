// YouTube Web App

// DOM Elements - Views
const listView = document.getElementById('list-view');
const videoView = document.getElementById('video-view');
const listHeader = document.getElementById('list-header');
const listTitle = document.getElementById('list-title');

// DOM Elements - Search
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const videoGrid = document.getElementById('video-grid');
const noResults = document.getElementById('no-results');
const loadMoreContainer = document.getElementById('load-more-container');

// DOM Elements - Video Page
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

// DOM Elements - Related
const relatedVideos = document.getElementById('related-videos');
const relatedLoadMore = document.getElementById('related-load-more');

// DOM Elements - Subtitles
const subtitleBtnContainer = document.getElementById('subtitle-btn-container');
const subtitleBtn = document.getElementById('subtitle-btn');
const subtitleMenu = document.getElementById('subtitle-menu');

// State
let maxDownloadQuality = 1080;
let currentQuery = '';
let currentChannelId = null;
let currentCount = 10;
const BATCH_SIZE = 10;
let progressInterval = null;
let isLoadingMore = false;
let hasMoreResults = true;
let currentVideoId = null;
let currentVideoChannelId = null;
let autoDownloadThreshold = 0;
let loadedVideoIds = new Set();
let listViewCache = null; // Cache list view for back navigation
let listViewMode = 'search'; // 'search' or 'channel'
let dashPlayer = null;
let downloadCancelled = false;
let availableQualities = [];
let selectedDownloadQuality = 0;
let subtitleTracks = [];  // [{lang, label, auto}]
let failedSubtitles = new Set(); // "videoId:lang" — avoid re-downloading known failures

// Settings dropdown
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

settingsMenu.addEventListener('click', (e) => {
    e.stopPropagation();
});

// Auto-download settings
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

// Max quality setting
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

// ===================
// ROUTING
// ===================

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
    // Cache current list view
    listViewCache = {
        mode: listViewMode,
        query: currentQuery,
        channelId: currentChannelId,
        html: videoGrid.innerHTML,
        loadedIds: new Set(loadedVideoIds),
        headerVisible: !listHeader.classList.contains('hidden'),
        headerTitle: listTitle.textContent
    };

    // Update URL
    const url = `/watch?v=${videoId}`;
    history.pushState({ view: 'video', videoId, title, channel, duration }, '', url);

    // Show video page
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

function restoreListCache() {
    if (listViewCache) {
        listViewMode = listViewCache.mode;
        currentQuery = listViewCache.query;
        currentChannelId = listViewCache.channelId;
        searchInput.value = currentQuery || '';
        videoGrid.innerHTML = listViewCache.html;
        loadedVideoIds = listViewCache.loadedIds;

        if (listViewCache.headerVisible) {
            listHeader.classList.remove('hidden');
            listTitle.textContent = listViewCache.headerTitle;
        } else {
            listHeader.classList.add('hidden');
        }

        attachCardListeners(videoGrid);
    }
}

// Handle browser back/forward
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

// Handle initial page load
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

// ===================
// SEARCH
// ===================

const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !isLoadingMore && hasMoreResults) {
        if (listViewMode === 'search' && currentQuery) {
            loadMoreSearch();
        } else if (listViewMode === 'channel' && currentChannelId) {
            loadMoreChannel();
        }
    }
}, { threshold: 0.1 });

async function searchVideos(query) {
    if (!query.trim()) return;

    listViewMode = 'search';
    currentQuery = query;
    currentChannelId = null;
    currentCount = BATCH_SIZE;
    hasMoreResults = true;
    loadedVideoIds.clear();

    // Update URL if not already on search
    if (window.location.pathname !== '/') {
        history.pushState({ view: 'search' }, '', '/');
    }

    showListView();
    listHeader.classList.add('hidden');
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    showLoadingCard(true);

    await fetchSearchVideos(true);
    loadMoreObserver.observe(loadMoreContainer);
}

async function loadMoreSearch() {
    if (isLoadingMore || !hasMoreResults) return;

    isLoadingMore = true;
    currentCount += BATCH_SIZE;
    showLoadingCard(true);
    await fetchSearchVideos(false);
    isLoadingMore = false;
}

async function fetchSearchVideos(isNewSearch) {
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(currentQuery)}&count=${currentCount}`);
        const data = await response.json();

        if (!response.ok) {
            const msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            throw new Error(msg || 'Search failed');
        }

        showLoadingCard(false);

        if (data.results.length === 0) {
            noResults.classList.remove('hidden');
            hasMoreResults = false;
        } else {
            const newVideos = data.results.filter(v => !loadedVideoIds.has(v.id));

            if (isNewSearch) {
                renderVideos(data.results);
                data.results.forEach(v => loadedVideoIds.add(v.id));
            } else {
                appendVideos(newVideos);
                newVideos.forEach(v => loadedVideoIds.add(v.id));
            }

            hasMoreResults = newVideos.length > 0;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${error.message}</p>`;
        hasMoreResults = false;
    }
}

// ===================
// CHANNEL
// ===================

async function loadChannelVideos(channelId, channelName) {
    listViewMode = 'channel';
    currentChannelId = channelId;
    currentQuery = '';
    currentCount = BATCH_SIZE;
    hasMoreResults = true;
    loadedVideoIds.clear();

    showListView();
    listHeader.classList.remove('hidden');
    listTitle.textContent = channelName || 'Channel';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    showLoadingCard(true);

    await fetchChannelVideos(true);
    loadMoreObserver.observe(loadMoreContainer);
}

async function loadMoreChannel() {
    if (isLoadingMore || !hasMoreResults) return;

    isLoadingMore = true;
    currentCount += BATCH_SIZE;
    showLoadingCard(true);
    await fetchChannelVideos(false);
    isLoadingMore = false;
}

async function fetchChannelVideos(isNewLoad) {
    try {
        const response = await fetch(`/api/channel/${currentChannelId}?count=${currentCount}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to load channel');
        }

        showLoadingCard(false);

        // Update header with channel name
        if (data.channel) {
            listTitle.textContent = data.channel;
        }

        if (data.results.length === 0) {
            noResults.classList.remove('hidden');
            hasMoreResults = false;
        } else {
            const newVideos = data.results.filter(v => !loadedVideoIds.has(v.id));

            if (isNewLoad) {
                renderVideos(data.results);
                data.results.forEach(v => loadedVideoIds.add(v.id));
            } else {
                appendVideos(newVideos);
                newVideos.forEach(v => loadedVideoIds.add(v.id));
            }

            hasMoreResults = newVideos.length > 0;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${error.message}</p>`;
        hasMoreResults = false;
    }
}

// ===================
// VIDEO GRID
// ===================

function showLoadingCard(show) {
    const existingLoader = document.getElementById('loading-card');
    if (existingLoader) existingLoader.remove();

    if (show) {
        const loadingCard = document.createElement('div');
        loadingCard.id = 'loading-card';
        loadingCard.className = 'video-card loading-card';
        loadingCard.innerHTML = `
            <div class="thumbnail-container">
                <div class="loading-spinner"></div>
            </div>
            <div class="video-info">
                <div class="skeleton-text"></div>
                <div class="skeleton-text short"></div>
            </div>
        `;
        videoGrid.appendChild(loadingCard);
    }
}

function createVideoCard(video) {
    return `<div class="video-card" data-id="${video.id}" data-title="${escapeAttr(video.title)}" data-channel="${escapeAttr(video.channel)}" data-duration="${video.duration}">
        <div class="thumbnail-container">
            <img src="${video.thumbnail}" alt="${escapeHtml(video.title)}" loading="lazy">
            <span class="duration">${video.duration_str}</span>
        </div>
        <div class="video-info">
            <h3 class="video-title">${escapeHtml(video.title)}</h3>
            <p class="channel">${escapeHtml(video.channel)}</p>
        </div>
    </div>`;
}

function attachCardListeners(container) {
    container.querySelectorAll('.video-card:not([data-attached])').forEach(card => {
        card.dataset.attached = 'true';
        card.addEventListener('click', () => navigateToVideo(
            card.dataset.id,
            card.dataset.title,
            card.dataset.channel,
            parseInt(card.dataset.duration) || 0
        ));
    });
}

function renderVideos(videos) {
    videoGrid.innerHTML = videos.map(createVideoCard).join('');
    attachCardListeners(videoGrid);
}

function appendVideos(videos) {
    if (videos.length === 0) return;
    videoGrid.insertAdjacentHTML('beforeend', videos.map(createVideoCard).join(''));
    attachCardListeners(videoGrid);
}

// ===================
// SUBTITLES
// ===================

function loadSubtitleTracks(videoId, tracks) {
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
    subtitleTracks = tracks || [];

    if (subtitleTracks.length === 0) {
        subtitleBtnContainer.classList.add('hidden');
        return;
    }

    subtitleBtnContainer.classList.remove('hidden');
    applySubtitlePreference();
}

function applySubtitlePreference() {
    const saved = localStorage.getItem('subtitle_lang');
    if (!saved || saved === 'off') {
        updateSubtitleBtn(null);
        return;
    }

    // Don't retry if we know this download failed
    if (failedSubtitles.has(`${currentVideoId}:${saved}`)) {
        updateSubtitleBtn(null);
        return;
    }

    // If a <track> for this language is already in the DOM, just re-enable it
    for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        const tt = videoPlayer.textTracks[i];
        if (tt.language === saved) {
            tt.mode = 'showing';
            updateSubtitleBtn(saved);
            return;
        }
    }

    // No track in DOM yet — find in available list and download
    const track = subtitleTracks.find(t => t.lang === saved)
               || subtitleTracks.find(t => t.lang.startsWith(saved + '-'));

    if (track) {
        activateTrack(track);
    } else {
        updateSubtitleBtn(null);
    }
}

function activateTrack(trackInfo) {
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());

    const el = document.createElement('track');
    el.kind = 'subtitles';
    el.srclang = trackInfo.lang;
    el.label = trackInfo.label;
    el.src = `/api/subtitle/${currentVideoId}?lang=${encodeURIComponent(trackInfo.lang)}`;

    el.addEventListener('load', () => {
        updateSubtitleBtn(trackInfo.lang);  // remove loading indicator
    });
    el.addEventListener('error', () => {
        failedSubtitles.add(`${currentVideoId}:${trackInfo.lang}`);
        if (localStorage.getItem('subtitle_lang') === trackInfo.lang) {
            localStorage.setItem('subtitle_lang', 'off');
        }
        subtitleBtn.textContent = 'CC';
        subtitleBtn.classList.remove('active', 'loading');
    });

    videoPlayer.appendChild(el);

    // Show loading state
    subtitleBtn.textContent = `CC: ${trackInfo.lang.toUpperCase()} …`;
    subtitleBtn.classList.add('active');

    // Set mode to showing once the track is registered
    const activate = (e) => {
        if (e.track.language === trackInfo.lang) {
            e.track.mode = 'showing';
            videoPlayer.textTracks.removeEventListener('addtrack', activate);
        }
    };
    videoPlayer.textTracks.addEventListener('addtrack', activate);
    // Fallback: try immediately in case addtrack already fired
    for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        if (videoPlayer.textTracks[i].language === trackInfo.lang) {
            videoPlayer.textTracks[i].mode = 'showing';
            videoPlayer.textTracks.removeEventListener('addtrack', activate);
            break;
        }
    }
}

function updateSubtitleBtn(activeLang) {
    if (activeLang) {
        subtitleBtn.textContent = `CC: ${activeLang.toUpperCase()}`;
        subtitleBtn.classList.add('active');
    } else {
        subtitleBtn.textContent = 'CC';
        subtitleBtn.classList.remove('active');
    }
}

function renderSubtitleMenu() {
    const saved = localStorage.getItem('subtitle_lang');
    const activeLang = (saved && saved !== 'off') ? saved : null;
    const allItems = [{ lang: null, label: 'Off', auto: false }, ...subtitleTracks];

    // Put active item at the top (if not already 'Off')
    let items;
    if (activeLang) {
        const activeItem = allItems.find(t => t.lang === activeLang);
        items = activeItem
            ? [activeItem, ...allItems.filter(t => t.lang !== activeLang)]
            : allItems;
    } else {
        items = allItems;
    }

    subtitleMenu.innerHTML = items.map(t => {
        const isActive = t.lang === activeLang;
        return `<div class="subtitle-option${isActive ? ' selected' : ''}" data-lang="${t.lang || ''}">
            ${escapeHtml(t.label || 'Off')}
        </div>`;
    }).join('');

    subtitleMenu.querySelectorAll('.subtitle-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const lang = opt.dataset.lang || null;
            selectSubtitle(lang);
            subtitleMenu.classList.add('hidden');
        });
    });
}

function getActiveSubtitleLang() {
    for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        if (videoPlayer.textTracks[i].mode === 'showing') {
            return videoPlayer.textTracks[i].language;
        }
    }
    return null;
}

function selectSubtitle(lang) {
    localStorage.setItem('subtitle_lang', lang || 'off');
    if (!lang) {
        [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
        updateSubtitleBtn(null);
        return;
    }
    const track = subtitleTracks.find(t => t.lang === lang);
    if (track) activateTrack(track);
}

subtitleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    renderSubtitleMenu();
    subtitleMenu.classList.toggle('hidden');
});

subtitleMenu.addEventListener('click', (e) => e.stopPropagation());

document.addEventListener('click', () => subtitleMenu.classList.add('hidden'));

// ===================
// VIDEO PLAYER
// ===================

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
    // Reset state
    stopPlayer();
    downloadCancelled = false;
    currentVideoId = videoId;

    // Reset UI
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

    // Store duration
    videoPlayer.dataset.expectedDuration = duration || 0;

    // Show thumbnail as poster while stream loads
    videoPlayer.poster = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

    // Fetch video info
    fetch(`/api/info/${videoId}`)
        .then(r => r.json())
        .then(info => {
            videoTitle.textContent = info.title || title;
            videoChannel.textContent = info.channel || channel;

            // Make channel clickable
            if (info.channel_id) {
                currentVideoChannelId = info.channel_id;
                videoChannel.href = `/channel/${info.channel_id}`;
                videoChannel.onclick = (e) => {
                    e.preventDefault();
                    navigateToChannel(info.channel_id, info.channel);
                };
            }

            const metaParts = [];
            if (info.upload_date) metaParts.push(`📅 ${info.upload_date}`);
            if (info.views) metaParts.push(`👁 ${info.views}`);
            if (info.likes) metaParts.push(`👍 ${info.likes}`);
            videoMeta.textContent = metaParts.join('  •  ');

            // Description
            if (info.description) {
                videoDescription.innerHTML = linkifyText(info.description);
                videoDescription.classList.remove('hidden');
            }

            // Subtitles
            loadSubtitleTracks(videoId, info.subtitle_tracks || []);
        })
        .catch(() => {});

    // Fetch related videos
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
            // Use dash.js for DASH adaptive streaming (up to 4K, full seeking)
            dashPlayer = dashjs.MediaPlayer().create();
            dashPlayer.updateSettings({
                streaming: {
                    abr: {
                        maxBitrate: { video: -1 },  // Let quality be controlled by manifest
                    },
                },
            });
            dashPlayer.initialize(videoPlayer, `/api/dash/${videoId}?quality=${maxDownloadQuality || 1080}`, true);
        }
    } catch (error) {
        videoTitle.textContent = 'Error: ' + error.message;
        cancelDownloadBtn.classList.add('hidden');
    }
}

// ===================
// RELATED VIDEOS
// ===================

async function fetchRelatedVideos(videoId) {
    try {
        relatedVideos.innerHTML = '<div class="loading-more"><div class="loading-spinner"></div></div>';

        const response = await fetch(`/api/related/${videoId}`);
        const data = await response.json();

        relatedVideos.innerHTML = '';

        if (data.results && data.results.length > 0) {
            data.results.forEach(video => {
                const card = createRelatedCard(video);
                relatedVideos.insertAdjacentHTML('beforeend', card);
            });
            attachRelatedListeners();
        } else {
            relatedVideos.innerHTML = '<p style="color: #717171; font-size: 14px;">No related videos found</p>';
        }
    } catch (error) {
        relatedVideos.innerHTML = '<p style="color: #ff4444; font-size: 14px;">Failed to load related videos</p>';
    }
}

function createRelatedCard(video) {
    return `<div class="related-card" data-id="${video.id}" data-title="${escapeAttr(video.title)}" data-channel="${escapeAttr(video.channel || '')}" data-duration="0">
        <div class="thumbnail-container">
            <img src="${video.thumbnail}" alt="${escapeHtml(video.title)}" loading="lazy">
            ${video.duration_str ? `<span class="duration">${video.duration_str}</span>` : ''}
        </div>
        <div class="related-info">
            <div class="related-title">${escapeHtml(video.title)}</div>
            ${video.channel ? `<div class="related-channel">${escapeHtml(video.channel)}</div>` : ''}
        </div>
    </div>`;
}

function attachRelatedListeners() {
    relatedVideos.querySelectorAll('.related-card:not([data-attached])').forEach(card => {
        card.dataset.attached = 'true';
        card.addEventListener('click', () => {
            const videoId = card.dataset.id;
            const title = card.dataset.title;
            const channel = card.dataset.channel;

            const url = `/watch?v=${videoId}`;
            history.pushState({ view: 'video', videoId, title, channel, duration: 0 }, '', url);
            playVideo(videoId, title, channel, 0);
        });
    });
}

// ===================
// DOWNLOAD
// ===================

const PROGRESS_CIRCUMFERENCE = 62.83;

function setProgress(percent) {
    const offset = PROGRESS_CIRCUMFERENCE - (percent / 100) * PROGRESS_CIRCUMFERENCE;
    progressRingFill.style.strokeDashoffset = offset;
}

function startHdDownload(videoId, quality = 0) {
    cancelDownloadBtn.classList.remove('hidden');
    downloadPill.classList.add('hidden');
    downloadQualityMenu.classList.add('hidden');
    setProgress(0);

    const url = quality ? `/api/play/${videoId}?quality=${quality}` : `/api/play/${videoId}`;
    fetch(url);

    progressInterval = setInterval(async () => {
        try {
            const prog = await fetch(`/api/progress/${videoId}`);
            const progData = await prog.json();

            setProgress(progData.progress);

            if (progData.status === 'finished' || progData.status === 'ready') {
                clearInterval(progressInterval);
                progressInterval = null;
                setProgress(100);

                const currentTime = videoPlayer.currentTime;
                const wasPlaying = !videoPlayer.paused;

                if (dashPlayer) {
                    dashPlayer.destroy();
                    dashPlayer = null;
                }

                videoPlayer.pause();
                videoPlayer.src = `/api/stream/${videoId}`;

                videoPlayer.onloadedmetadata = () => {
                    videoPlayer.currentTime = currentTime;
                    if (wasPlaying) videoPlayer.play();
                    videoPlayer.onloadedmetadata = null;
                    // Re-apply subtitle preference after src change
                    applySubtitlePreference();
                };
                videoPlayer.load();

                setTimeout(() => cancelDownloadBtn.classList.add('hidden'), 800);
            } else if (progData.status === 'error' || progData.status === 'cancelled') {
                clearInterval(progressInterval);
                progressInterval = null;
                setTimeout(() => cancelDownloadBtn.classList.add('hidden'), 500);
            }
        } catch (e) {
            // Ignore
        }
    }, 500);
}

async function checkDownloadOffer(videoId, currentHeight) {
    try {
        const response = await fetch(`/api/formats/${videoId}`);
        const data = await response.json();

        const betterOptions = data.options.filter(opt => opt.height > currentHeight);

        if (betterOptions.length > 0) {
            availableQualities = betterOptions;

            let targetQuality = betterOptions.find(opt => opt.height >= maxDownloadQuality);
            if (!targetQuality) {
                targetQuality = betterOptions[betterOptions.length - 1];
            }
            selectedDownloadQuality = targetQuality.height;

            const sizeText = targetQuality.size_str ? ` (${targetQuality.size_str})` : '';
            downloadAction.textContent = `Download${sizeText}`;

            updateQualityMenu();
            downloadPill.classList.remove('hidden');
        }
    } catch (e) {
        // Ignore
    }
}

function updateQualityMenu() {
    downloadQualityMenu.innerHTML = [...availableQualities].reverse().map(opt => {
        const sizeInfo = opt.size_str ? `<span class="size">${opt.size_str}</span>` : '';
        const selected = opt.height === selectedDownloadQuality ? ' selected' : '';
        return `<div class="quality-option${selected}" data-quality="${opt.height}">
            <span>${opt.label}</span>
            ${sizeInfo}
        </div>`;
    }).join('');

    downloadQualityMenu.querySelectorAll('.quality-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const quality = parseInt(opt.dataset.quality);
            selectedDownloadQuality = quality;
            const selected = availableQualities.find(q => q.height === quality);
            const sizeText = selected?.size_str ? ` (${selected.size_str})` : '';
            downloadAction.textContent = `Download${sizeText}`;
            updateQualityMenu();
            downloadQualityMenu.classList.add('hidden');
        });
    });
}

downloadAction.addEventListener('click', () => {
    if (currentVideoId && selectedDownloadQuality > 0) {
        startHdDownload(currentVideoId, selectedDownloadQuality);
    }
});

downloadGear.addEventListener('click', (e) => {
    e.stopPropagation();
    downloadQualityMenu.classList.toggle('hidden');
});

downloadQualityMenu.addEventListener('click', (e) => {
    e.stopPropagation();
});

cancelDownloadBtn.addEventListener('click', async () => {
    if (currentVideoId) {
        downloadCancelled = true;
        await fetch(`/api/cancel/${currentVideoId}`, { method: 'POST' });
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
        cancelDownloadBtn.classList.add('hidden');
        if (availableQualities.length > 0) {
            downloadPill.classList.remove('hidden');
        }
    }
});

// ===================
// UTILS
// ===================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function linkifyText(text) {
    // Escape HTML first
    const escaped = escapeHtml(text);
    // Convert URLs to links
    const urlRegex = /(https?:\/\/[^\s<]+)/g;
    return escaped.replace(urlRegex, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

// ===================
// EVENT LISTENERS
// ===================

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

videoPlayer.addEventListener('error', () => {
    console.log('Video error:', videoPlayer.error?.message);
});

videoPlayer.addEventListener('loadedmetadata', () => {
    const h = videoPlayer.videoHeight;
    const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;

    // Show resolution badge
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

// Handle initial route on page load
handleInitialRoute();
