// YouTube Web App

const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const videoGrid = document.getElementById('video-grid');
const playerContainer = document.getElementById('player-container');
const videoPlayer = document.getElementById('video-player');
const videoTitle = document.getElementById('video-title');
const videoMeta = document.getElementById('video-meta');
const closePlayerBtn = document.getElementById('close-player');
const noResults = document.getElementById('no-results');
const loadMoreContainer = document.getElementById('load-more-container');
const downloadProgress = document.getElementById('download-progress');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');

let currentQuery = '';
let currentCount = 10;
const BATCH_SIZE = 10;
let progressInterval = null;
let isLoadingMore = false;
let hasMoreResults = true;

// Infinite scroll - load more when sentinel becomes visible
const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !isLoadingMore && hasMoreResults && currentQuery) {
        loadMore();
    }
}, { threshold: 0.1 });

async function searchVideos(query) {
    if (!query.trim()) return;

    currentQuery = query;
    currentCount = BATCH_SIZE;
    hasMoreResults = true;

    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    showLoadingCard(true);

    await fetchVideos();

    // Start observing for infinite scroll
    loadMoreObserver.observe(loadMoreContainer);
}

async function loadMore() {
    if (isLoadingMore || !hasMoreResults) return;

    isLoadingMore = true;
    currentCount += BATCH_SIZE;
    showLoadingCard(true);
    await fetchVideos();
    isLoadingMore = false;
}

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

async function fetchVideos() {
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
            renderVideos(data.results);
            // Check if we got fewer results than requested = no more results
            hasMoreResults = data.results.length >= currentCount;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${error.message}</p>`;
        hasMoreResults = false;
    }
}

function renderVideos(videos) {
    videoGrid.innerHTML = videos.map(video => `
        <div class="video-card" data-id="${video.id}" data-title="${escapeAttr(video.title)}" data-channel="${escapeAttr(video.channel)}" data-duration="${video.duration}">
            <div class="thumbnail-container">
                <img src="${video.thumbnail}" alt="${escapeHtml(video.title)}" loading="lazy">
                <span class="duration">${video.duration_str}</span>
            </div>
            <div class="video-info">
                <h3 class="video-title">${escapeHtml(video.title)}</h3>
                <p class="channel">${escapeHtml(video.channel)}</p>
            </div>
        </div>
    `).join('');

    document.querySelectorAll('.video-card').forEach(card => {
        card.addEventListener('click', () => playVideo(
            card.dataset.id,
            card.dataset.title,
            card.dataset.channel,
            parseInt(card.dataset.duration) || 0
        ));
    });
}

async function playVideo(videoId, title, channel, duration) {
    // Stop any previous progress polling
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }

    videoTitle.textContent = title || 'Loading...';
    videoMeta.textContent = channel || '';

    // Fetch extra info in background
    fetch(`/api/info/${videoId}`)
        .then(r => r.json())
        .then(info => {
            const parts = [info.channel || channel];
            if (info.upload_date) parts.push(info.upload_date);
            if (info.views) parts.push(`${info.views} views`);
            if (info.likes) parts.push(`${info.likes} likes`);
            videoMeta.textContent = parts.join(' • ');
        })
        .catch(() => {});
    playerContainer.classList.remove('hidden');

    // Store duration to set on video when metadata loads
    videoPlayer.dataset.expectedDuration = duration || 0;
    playerContainer.scrollIntoView({ behavior: 'smooth' });

    try {
        // Check if HD is already cached
        const response = await fetch(`/api/play/${videoId}`);
        const data = await response.json();

        if (data.status === 'ready') {
            // HD already cached - play immediately
            downloadProgress.classList.add('hidden');
            videoPlayer.src = data.url;
            videoPlayer.play();
        } else {
            // Start 720p live stream immediately
            downloadProgress.classList.remove('hidden');
            progressFill.style.width = '0%';
            progressText.textContent = 'Streaming 720p, downloading HD...';

            videoPlayer.src = `/api/stream-live/${videoId}`;
            videoPlayer.play();

            // HD download started in background by /api/play call
            // Poll for HD completion
            progressInterval = setInterval(async () => {
                try {
                    const prog = await fetch(`/api/progress/${videoId}`);
                    const progData = await prog.json();

                    progressFill.style.width = `${progData.progress}%`;

                    if (progData.status === 'finished' || progData.status === 'ready') {
                        clearInterval(progressInterval);
                        progressInterval = null;
                        progressText.textContent = 'HD ready! Switching...';

                        // Switch to HD
                        const currentTime = videoPlayer.currentTime;
                        const wasPlaying = !videoPlayer.paused;

                        videoPlayer.src = `/api/stream/${videoId}`;
                        videoPlayer.currentTime = currentTime;
                        if (wasPlaying) videoPlayer.play();

                        setTimeout(() => downloadProgress.classList.add('hidden'), 1500);
                    } else if (progData.status === 'error') {
                        // HD failed, but 720p still playing - just hide progress
                        progressText.textContent = 'HD unavailable, using 720p';
                        clearInterval(progressInterval);
                        progressInterval = null;
                        setTimeout(() => downloadProgress.classList.add('hidden'), 2000);
                    } else {
                        progressText.textContent = `Streaming 720p | HD: ${progData.message || 'downloading...'}`;
                    }
                } catch (e) {
                    // Ignore progress errors
                }
            }, 500);
        }
    } catch (error) {
        videoTitle.textContent = 'Error: ' + error.message;
        downloadProgress.classList.add('hidden');
    }
}

function hidePlayer() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
    playerContainer.classList.add('hidden');
    downloadProgress.classList.add('hidden');
    videoPlayer.pause();
    videoPlayer.removeAttribute('src');
    videoPlayer.load();
}


function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));
closePlayerBtn.addEventListener('click', hidePlayer);

videoPlayer.addEventListener('error', () => {
    if (videoPlayer.src && !playerContainer.classList.contains('hidden')) {
        videoTitle.textContent = 'Error loading video';
    }
});

// Update time display during playback
videoPlayer.addEventListener('timeupdate', () => {
    const current = videoPlayer.currentTime;
    const expected = parseInt(videoPlayer.dataset.expectedDuration) || 0;
    // Always use expected duration since browser doesn't know it during streaming
    const total = expected > 0 ? expected : (isFinite(videoPlayer.duration) ? videoPlayer.duration : 0);

    if (total > 0) {
        const formatTime = (t) => {
            const h = Math.floor(t / 3600);
            const m = Math.floor((t % 3600) / 60);
            const s = Math.floor(t % 60);
            return h > 0 ? `${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}` : `${m}:${s.toString().padStart(2,'0')}`;
        };
        document.getElementById('time-display').textContent = `${formatTime(current)} / ${formatTime(total)}`;
    }
});
