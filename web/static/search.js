// Search, channel browsing, and video grid rendering

let currentQuery = '';
let currentChannelId = null;
let currentCursor = null;
let isLoadingMore = false;
let hasMoreResults = true;
let listViewCache = null;
let listViewMode = 'search'; // 'search' or 'channel'

const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !isLoadingMore && hasMoreResults) {
        loadMore();
    }
}, { threshold: 0.1 });


// ── Search ──────────────────────────────────────────────────────────────────

async function searchVideos(query) {
    if (!query.trim()) return;

    listViewMode = 'search';
    currentQuery = query;
    currentChannelId = null;
    currentCursor = null;
    hasMoreResults = true;

    if (window.location.pathname !== '/') {
        history.pushState({ view: 'search' }, '', '/');
    }

    showListView();
    listHeader.classList.add('hidden');
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
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
            currentCursor = data.cursor;
            hasMoreResults = !!data.cursor;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(error.message)}</p>`;
        hasMoreResults = false;
    }

    loadMoreObserver.observe(loadMoreContainer);
}


// ── Channel ─────────────────────────────────────────────────────────────────

async function loadChannelVideos(channelId, channelName) {
    listViewMode = 'channel';
    currentChannelId = channelId;
    currentQuery = '';
    currentCursor = null;
    hasMoreResults = true;

    showListView();
    listHeader.classList.remove('hidden');
    listTitle.textContent = channelName || 'Channel';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/channel/${channelId}`);
        const data = await response.json();

        if (!response.ok) {
            const msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            throw new Error(msg || 'Failed to load channel');
        }

        showLoadingCard(false);

        if (data.channel) {
            listTitle.textContent = data.channel;
        }

        if (data.results.length === 0) {
            noResults.classList.remove('hidden');
            hasMoreResults = false;
        } else {
            renderVideos(data.results);
            currentCursor = data.cursor;
            hasMoreResults = !!data.cursor;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(error.message)}</p>`;
        hasMoreResults = false;
    }

    loadMoreObserver.observe(loadMoreContainer);
}


// ── Load More (shared) ──────────────────────────────────────────────────────

async function loadMore() {
    if (isLoadingMore || !hasMoreResults || !currentCursor) return;
    isLoadingMore = true;
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/more?cursor=${encodeURIComponent(currentCursor)}`);
        const data = await response.json();

        showLoadingCard(false);

        if (!response.ok) {
            throw new Error('Load more failed');
        }

        appendVideos(data.results);
        currentCursor = data.cursor;
        hasMoreResults = !!data.cursor && data.results.length > 0;
        loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
    } catch (error) {
        showLoadingCard(false);
        console.error('Load more error:', error);
        hasMoreResults = false;
        loadMoreContainer.classList.add('hidden');
    }

    isLoadingMore = false;
}


// ── Video Grid ──────────────────────────────────────────────────────────────

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
    const fragment = document.createElement('div');
    fragment.innerHTML = videos.map(createVideoCard).join('');
    const newCards = [...fragment.children];
    newCards.forEach(card => videoGrid.appendChild(card));
    // Only attach listeners to the newly added cards (not the entire grid)
    newCards.forEach(card => {
        card.dataset.attached = 'true';
        card.addEventListener('click', () => navigateToVideo(
            card.dataset.id,
            card.dataset.title,
            card.dataset.channel,
            parseInt(card.dataset.duration) || 0
        ));
    });
}


// ── Related Videos ──────────────────────────────────────────────────────────

async function fetchRelatedVideos(videoId) {
    try {
        relatedVideos.innerHTML = '<div class="loading-more"><div class="loading-spinner"></div></div>';

        const response = await fetch(`/api/related/${videoId}`);
        const data = await response.json();

        relatedVideos.innerHTML = '';

        if (data.results && data.results.length > 0) {
            data.results.forEach(video => {
                relatedVideos.insertAdjacentHTML('beforeend', createRelatedCard(video));
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
            history.pushState({ view: 'video', videoId, title, channel, duration: 0 }, '', `/watch?v=${videoId}`);
            playVideo(videoId, title, channel, 0);
        });
    });
}


// ── List View Cache ─────────────────────────────────────────────────────────

function cacheListView() {
    listViewCache = {
        mode: listViewMode,
        query: currentQuery,
        channelId: currentChannelId,
        cursor: currentCursor,
        html: videoGrid.innerHTML,
        headerVisible: !listHeader.classList.contains('hidden'),
        headerTitle: listTitle.textContent
    };
}

function restoreListCache() {
    if (listViewCache) {
        listViewMode = listViewCache.mode;
        currentQuery = listViewCache.query;
        currentChannelId = listViewCache.channelId;
        currentCursor = listViewCache.cursor;
        hasMoreResults = !!listViewCache.cursor;
        searchInput.value = currentQuery || '';
        videoGrid.innerHTML = listViewCache.html;

        if (listViewCache.headerVisible) {
            listHeader.classList.remove('hidden');
            listTitle.textContent = listViewCache.headerTitle;
        } else {
            listHeader.classList.add('hidden');
        }

        attachCardListeners(videoGrid);

        if (hasMoreResults) {
            loadMoreContainer.classList.remove('hidden');
            loadMoreObserver.observe(loadMoreContainer);
        }
    }
}
