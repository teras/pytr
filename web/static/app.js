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
const qualityBadge = document.getElementById('quality-badge');
const downloadOffer = document.getElementById('download-offer');
const downloadButtons = document.getElementById('download-buttons');
const cancelDownloadBtn = document.getElementById('cancel-download-btn');

let downloadQualityLabel = 'HD';

let currentQuery = '';
let currentCount = 10;
const BATCH_SIZE = 10;
let progressInterval = null;
let isLoadingMore = false;
let hasMoreResults = true;
let currentVideoId = null;
const AUTO_DOWNLOAD_THRESHOLD = 15 * 60; // 15 minutes in seconds

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

let hlsPlayer = null;

async function playVideo(videoId, title, channel, duration) {
    // Stop any previous progress polling
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }

    // Destroy previous HLS player
    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }

    // Reset cancel flag for new video
    downloadCancelled = false;

    videoTitle.textContent = title || 'Loading...';
    videoMeta.textContent = channel || '';
    qualityBadge.textContent = '...';

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
        const response = await fetch(`/api/progress/${videoId}`);
        const data = await response.json();

        downloadProgress.classList.add('hidden');

        if (data.status === 'ready') {
            // HD already cached - play from file
            videoPlayer.src = `/api/stream/${videoId}`;
            videoPlayer.play();
        } else {
            // Try HLS first (best quality), fallback to direct stream
            if (Hls.isSupported()) {
                hlsPlayer = new Hls();
                hlsPlayer.loadSource(`/api/hls/${videoId}`);
                hlsPlayer.attachMedia(videoPlayer);
                hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => {
                    videoPlayer.play();
                });
                hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        console.log('HLS failed, falling back to direct stream');
                        hlsPlayer.destroy();
                        hlsPlayer = null;
                        videoPlayer.src = `/api/stream-live/${videoId}`;
                        videoPlayer.play();
                    }
                });
            } else {
                // Fallback for browsers without HLS support
                videoPlayer.src = `/api/stream-live/${videoId}`;
                videoPlayer.play();
            }

            // Store for later quality check
            currentVideoId = videoId;
            downloadOffer.classList.add('hidden');
            // Download check happens in loadedmetadata when we know actual quality
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
    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }
    playerContainer.classList.add('hidden');
    downloadProgress.classList.add('hidden');
    downloadOffer.classList.add('hidden');
    qualityBadge.textContent = '';
    currentVideoId = null;
    videoPlayer.pause();
    videoPlayer.removeAttribute('src');
    videoPlayer.load();
}

function startHdDownload(videoId, quality = 0) {
    downloadProgress.classList.remove('hidden');
    downloadOffer.classList.add('hidden');
    progressFill.style.width = '0%';
    downloadQualityLabel = quality ? `${quality}p` : 'HD';
    progressText.textContent = `Downloading ${downloadQualityLabel}...`;

    // Start HD download
    const url = quality ? `/api/play/${videoId}?quality=${quality}` : `/api/play/${videoId}`;
    fetch(url);

    // Poll for HD completion
    progressInterval = setInterval(async () => {
        try {
            const prog = await fetch(`/api/progress/${videoId}`);
            const progData = await prog.json();

            progressFill.style.width = `${progData.progress}%`;

            if (progData.status === 'finished' || progData.status === 'ready') {
                clearInterval(progressInterval);
                progressInterval = null;
                progressText.textContent = `${downloadQualityLabel} ready! Switching...`;

                // Switch to HD
                const currentTime = videoPlayer.currentTime;
                const wasPlaying = !videoPlayer.paused;

                if (hlsPlayer) {
                    hlsPlayer.destroy();
                    hlsPlayer = null;
                }

                // Pause first, then switch source
                videoPlayer.pause();
                videoPlayer.src = `/api/stream/${videoId}`;

                // Wait for new source to load before seeking
                videoPlayer.onloadedmetadata = () => {
                    videoPlayer.currentTime = currentTime;
                    if (wasPlaying) videoPlayer.play();
                    videoPlayer.onloadedmetadata = null;
                };
                videoPlayer.load();

                setTimeout(() => downloadProgress.classList.add('hidden'), 1500);
            } else if (progData.status === 'error' || progData.status === 'cancelled') {
                progressText.textContent = progData.status === 'cancelled' ? 'Cancelled' : `${downloadQualityLabel} unavailable`;
                clearInterval(progressInterval);
                progressInterval = null;
                setTimeout(() => downloadProgress.classList.add('hidden'), 1500);
            } else {
                progressText.textContent = `Downloading ${downloadQualityLabel}: ${progData.message || '...'}`;
            }
        } catch (e) {
            // Ignore progress errors
        }
    }, 500);
}

async function checkDownloadOffer(videoId, currentHeight) {
    try {
        const response = await fetch(`/api/formats/${videoId}`);
        const data = await response.json();

        // Filter options better than current streaming quality
        const betterOptions = data.options.filter(opt => opt.height > currentHeight);

        if (betterOptions.length > 0) {
            downloadOffer.classList.remove('hidden');
            downloadButtons.innerHTML = betterOptions.map(opt => {
                const sizeInfo = opt.size_str ? ` <span class="size">(${opt.size_str})</span>` : '';
                return `<button class="download-btn" data-quality="${opt.height}">${opt.label}${sizeInfo}</button>`;
            }).join('');

            // Add click handlers
            downloadButtons.querySelectorAll('.download-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const quality = parseInt(btn.dataset.quality);
                    btn.disabled = true;
                    btn.textContent = 'Starting...';
                    startHdDownload(currentVideoId, quality);
                });
            });
        }
    } catch (e) {
        // Ignore errors
    }
}

// Cancel download handler
let downloadCancelled = false;

cancelDownloadBtn.addEventListener('click', async () => {
    if (currentVideoId) {
        downloadCancelled = true;
        await fetch(`/api/cancel/${currentVideoId}`, { method: 'POST' });
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
        progressText.textContent = 'Cancelled';
        setTimeout(() => {
            downloadProgress.classList.add('hidden');
            // Re-show download options (even for short videos after cancel)
            const h = videoPlayer.videoHeight;
            if (currentVideoId) {
                checkDownloadOffer(currentVideoId, h);
            }
        }, 1000);
    }
});


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
    // Don't show errors to user - they're usually transient
    // Just log for debugging
    console.log('Video error:', videoPlayer.error?.message);
});

// Show video quality when metadata loads + smart download check
videoPlayer.addEventListener('loadedmetadata', () => {
    const h = videoPlayer.videoHeight;
    const duration = parseInt(videoPlayer.dataset.expectedDuration) || 0;

    // Update quality badge
    if (h >= 1080) {
        qualityBadge.textContent = '1080p';
        qualityBadge.style.backgroundColor = '#065fd4';
    } else if (h >= 720) {
        qualityBadge.textContent = '720p';
        qualityBadge.style.backgroundColor = '#065fd4';
    } else if (h >= 480) {
        qualityBadge.textContent = '480p';
        qualityBadge.style.backgroundColor = '#606060';
    } else if (h >= 360) {
        qualityBadge.textContent = '360p';
        qualityBadge.style.backgroundColor = '#606060';
    } else if (h > 0) {
        qualityBadge.textContent = `${h}p`;
        qualityBadge.style.backgroundColor = '#606060';
    }

    // Smart download based on actual streaming quality
    // Skip if already playing downloaded file or download in progress
    if (!currentVideoId || progressInterval) return;
    if (videoPlayer.src && videoPlayer.src.includes('/api/stream/') && !videoPlayer.src.includes('/api/stream-live/')) {
        return; // Already playing downloaded file
    }

    if (duration > 0 && duration < AUTO_DOWNLOAD_THRESHOLD && !downloadCancelled) {
        // Short video: auto-download HD if streaming quality is low
        if (h < 1080) {
            startHdDownload(currentVideoId);
        }
    } else if (duration >= AUTO_DOWNLOAD_THRESHOLD || downloadCancelled) {
        // Long video or after cancel: offer download if better quality available
        checkDownloadOffer(currentVideoId, h);
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
