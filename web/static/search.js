// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Search, channel browsing, video grid rendering, queue mode

let currentQuery = '';
let currentChannelId = null;
let currentCursor = null;
let isLoadingMore = false;
let hasMoreResults = true;
let listViewCache = null;
let listViewMode = 'search'; // 'search' | 'channel' | 'channel_playlists'
let _listGeneration = 0; // incremented on every new search/channel/list load to discard stale responses

// Search raw data + filter state
let _searchRawResults = []; // all fetched results (never filtered)
let _searchFilters = { video: true, playlist: true, mix: true };

// Related raw data
let _relatedRawResults = [];

// Channel tabs state
let _channelTab = 'videos'; // 'videos' | 'playlists'

// Queue mode state
let _queue = null; // { title, videos[], currentIndex, playlistId }
let _queueCollapsed = false;

const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !isLoadingMore && hasMoreResults) {
        loadMore();
    }
}, { threshold: 0.1 });


// ── Search ──────────────────────────────────────────────────────────────────

async function searchVideos(query, { pushState = true } = {}) {
    if (!query.trim()) return;

    listViewMode = 'search';
    currentQuery = query;
    currentChannelId = null;
    currentCursor = null;
    hasMoreResults = true;
    _searchRawResults = [];
    const gen = ++_listGeneration;

    const searchUrl = `/results?search_query=${encodeURIComponent(query)}`;
    if (pushState) {
        history.pushState({ view: 'search', query }, '', searchUrl);
    } else {
        history.replaceState({ view: 'search', query }, '', searchUrl);
    }

    showListView();
    searchInput.value = query;
    _removeChannelTabs();
    listHeader.classList.add('hidden');
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    loadMoreObserver.disconnect();
    _removeFilterToggles();
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        if (gen !== _listGeneration) return; // stale
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
            _searchRawResults = data.results;
            _renderSearchFiltered();
            currentCursor = data.cursor;
            hasMoreResults = !!data.cursor;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        if (gen !== _listGeneration) return;
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(error.message)}</p>`;
        hasMoreResults = false;
    }

    if (gen === _listGeneration) loadMoreObserver.observe(loadMoreContainer);
}


// ── Search Filters ──────────────────────────────────────────────────────────

function _hasPlaylists() { return _searchRawResults.some(r => r.type === 'playlist' || r.type === 'mix'); }

function _removeFilterToggles() {
    const existing = document.getElementById('search-filter-toggles');
    if (existing) existing.remove();
}

function _renderFilterToggles() {
    _removeFilterToggles();
    if (!_hasPlaylists()) return;

    const container = document.createElement('div');
    container.id = 'search-filter-toggles';
    container.className = 'filter-toggles';

    // Visibility group
    const group = document.createElement('div');
    group.className = 'filter-group';
    ['video', 'playlist', 'mix'].forEach(type => {
        const btn = document.createElement('button');
        btn.className = `filter-btn${_searchFilters[type] ? ' active' : ''}`;
        btn.textContent = type === 'video' ? 'Videos' : type === 'playlist' ? 'Playlists' : 'Mixes';
        btn.addEventListener('click', () => {
            _searchFilters[type] = !_searchFilters[type];
            btn.classList.toggle('active', _searchFilters[type]);
            _renderSearchFiltered();
        });
        group.appendChild(btn);
    });
    container.appendChild(group);

    videoGrid.parentNode.insertBefore(container, videoGrid);
}

function _applySearchFilters(results) {
    const { video, playlist, mix } = _searchFilters;

    return results.filter(r => {
        if (!r.type) return video;
        if (r.type === 'playlist') return playlist;
        if (r.type === 'mix') return mix;
        return true;
    });
}

function _renderSearchFiltered() {
    const filtered = _applySearchFilters(_searchRawResults);
    _renderFilterToggles();
    videoGrid.innerHTML = filtered.map(r => createVideoCard(r)).join('');
    attachCardListeners(videoGrid);
    if (filtered.length === 0) {
        noResults.classList.remove('hidden');
    } else {
        noResults.classList.add('hidden');
    }
}


// ── Channel ─────────────────────────────────────────────────────────────────

async function loadChannelVideos(channelId, channelName) {
    listViewMode = 'channel';
    _channelTab = 'videos';
    currentChannelId = channelId;
    currentQuery = '';
    currentCursor = null;
    hasMoreResults = true;
    const gen = ++_listGeneration;

    showListView();
    listHeader.classList.remove('hidden');
    listTitle.textContent = channelName || 'Channel';
    clearListBtn.classList.add('hidden');
    _removeFilterToggles();
    _removeChannelTabs();
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    loadMoreObserver.disconnect();
    showLoadingCard(true);

    try {
        // Fetch videos and probe playlists in parallel
        const [response, playlistsResp] = await Promise.all([
            fetch(`/api/channel/${channelId}`),
            fetch(`/api/channel/${channelId}/playlists`).catch(() => null),
        ]);
        if (gen !== _listGeneration) return;
        const data = await response.json();

        if (!response.ok) {
            const msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            throw new Error(msg || 'Failed to load channel');
        }

        // Check if playlists exist
        let hasPlaylists = false;
        if (playlistsResp && playlistsResp.ok) {
            const plData = await playlistsResp.json();
            hasPlaylists = plData.results && plData.results.length > 0;
        }
        const hasVideos = data.results.length > 0;

        showLoadingCard(false);

        if (data.channel) {
            listTitle.textContent = data.channel;
        }

        // Show tabs only if both have content
        if (hasVideos && hasPlaylists) {
            _renderChannelTabs(channelId);
        } else if (!hasVideos && hasPlaylists) {
            // No videos, only playlists — switch to playlists directly
            _channelTab = 'playlists';
            history.replaceState({ view: 'channel', channelId, channelName: listTitle.textContent, tab: 'playlists' }, '', `/channel/${channelId}/playlists`);
            _loadChannelPlaylists(channelId);
            return;
        }

        if (!hasVideos) {
            noResults.classList.remove('hidden');
            hasMoreResults = false;
        } else {
            renderVideos(data.results);
            currentCursor = data.cursor;
            hasMoreResults = !!data.cursor;
            loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
        }
    } catch (error) {
        if (gen !== _listGeneration) return;
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(error.message)}</p>`;
        hasMoreResults = false;
    }

    if (gen === _listGeneration) loadMoreObserver.observe(loadMoreContainer);
}


// ── Channel Tabs ────────────────────────────────────────────────────────────

function _removeChannelTabs() {
    const existing = document.getElementById('channel-tabs');
    if (existing) existing.remove();
}

function _renderChannelTabs(channelId) {
    _removeChannelTabs();
    const tabs = document.createElement('div');
    tabs.id = 'channel-tabs';
    tabs.className = 'channel-tabs';

    ['videos', 'playlists'].forEach(tab => {
        const btn = document.createElement('button');
        btn.className = `channel-tab${_channelTab === tab ? ' active' : ''}`;
        btn.textContent = tab === 'videos' ? 'Videos' : 'Playlists';
        btn.addEventListener('click', () => {
            if (_channelTab === tab) return;
            _channelTab = tab;
            const channelName = listTitle.textContent;
            const url = tab === 'playlists' ? `/channel/${channelId}/playlists` : `/channel/${channelId}`;
            history.pushState({ view: 'channel', channelId, channelName, tab }, '', url);
            tabs.querySelectorAll('.channel-tab').forEach(b => b.classList.toggle('active', b === btn));
            if (tab === 'videos') {
                loadChannelVideos(channelId, channelName);
            } else {
                _loadChannelPlaylists(channelId);
            }
        });
        tabs.appendChild(btn);
    });

    // Insert after list header
    listHeader.insertAdjacentElement('afterend', tabs);
}

async function loadChannelPlaylists(channelId, channelName) {
    _channelTab = 'playlists';
    currentChannelId = channelId;
    currentQuery = '';

    showListView();
    listHeader.classList.remove('hidden');
    listTitle.textContent = channelName || 'Channel';
    clearListBtn.classList.add('hidden');
    _removeFilterToggles();
    _removeChannelTabs();

    _loadChannelPlaylists(channelId);

    // Probe videos in background to decide whether to show tabs
    const gen = _listGeneration;
    fetch(`/api/channel/${channelId}`).then(r => r.ok ? r.json() : null).then(data => {
        if (gen !== _listGeneration) return;
        if (data && data.results && data.results.length > 0) {
            if (data.channel) listTitle.textContent = data.channel;
            _renderChannelTabs(channelId);
        }
    }).catch(() => {});
}

async function _loadChannelPlaylists(channelId) {
    listViewMode = 'channel_playlists';
    currentCursor = null;
    hasMoreResults = true;
    const gen = ++_listGeneration;

    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    loadMoreObserver.disconnect();
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/channel/${channelId}/playlists`);
        if (gen !== _listGeneration) return;
        const data = await response.json();

        if (!response.ok) throw new Error('Failed to load playlists');

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
        if (gen !== _listGeneration) return;
        showLoadingCard(false);
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(error.message)}</p>`;
        hasMoreResults = false;
    }

    if (gen === _listGeneration) loadMoreObserver.observe(loadMoreContainer);
}


// ── Load More (shared) ──────────────────────────────────────────────────────

async function loadMore() {
    if (isLoadingMore || !hasMoreResults || !currentCursor) return;
    isLoadingMore = true;
    const gen = _listGeneration; // capture, don't increment
    showLoadingCard(true);

    try {
        const response = await fetch(`/api/more?cursor=${encodeURIComponent(currentCursor)}`);
        if (gen !== _listGeneration) { isLoadingMore = false; return; } // stale
        const data = await response.json();

        showLoadingCard(false);

        if (!response.ok) {
            throw new Error('Load more failed');
        }

        if (listViewMode === 'search') {
            _searchRawResults = _searchRawResults.concat(data.results);
            const filtered = _applySearchFilters(data.results);
            appendVideos(filtered);
        } else {
            appendVideos(data.results);
        }
        currentCursor = data.cursor;
        hasMoreResults = !!data.cursor && data.results.length > 0;
        loadMoreContainer.classList.toggle('hidden', !hasMoreResults);
    } catch (error) {
        if (gen !== _listGeneration) { isLoadingMore = false; return; }
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

function formatDuration(seconds) {
    if (!seconds || seconds <= 0) return '';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
}

function createVideoCard(item) {
    // Playlist/mix card
    if (item.type === 'playlist' || item.type === 'mix') {
        const badgeClass = item.type === 'playlist' ? 'badge-playlist' : 'badge-mix';
        const badgeLabel = item.type === 'playlist' ? 'Playlist' : 'Mix';
        const countBadge = item.video_count ? `<span class="video-count">${escapeHtml(item.video_count)}</span>` : '';
        const firstVid = item.first_video_id || item.id;
        const plId = item.playlist_id || item.id;
        return `<a class="video-card" href="/watch?v=${escapeAttr(firstVid)}&list=${escapeAttr(plId)}" data-id="${escapeAttr(firstVid)}" data-title="${escapeAttr(item.title)}" data-channel="${escapeAttr(item.channel || '')}" data-duration="0" data-playlist-id="${escapeAttr(plId)}" data-item-type="${escapeAttr(item.type)}">
            <div class="thumbnail-container">
                <img src="${escapeAttr(item.thumbnail)}" alt="${escapeHtml(item.title)}" loading="lazy">
                ${countBadge}
                <span class="${badgeClass}">${badgeLabel}</span>
            </div>
            <div class="video-info">
                <h3 class="video-title">${escapeHtml(item.title)}</h3>
                <p class="channel">${escapeHtml(item.channel || '')}</p>
            </div>
        </a>`;
    }

    // Regular video card
    const meta = item.published ? `<span class="video-published">${escapeHtml(item.published)}</span>`
               : '';
    const durationBadge = item.is_live ? '<span class="duration live">LIVE</span>'
                        : item.duration_str ? `<span class="duration">${escapeHtml(item.duration_str)}</span>` : '';
    return `<a class="video-card" href="/watch?v=${escapeAttr(item.id)}" data-id="${escapeAttr(item.id)}" data-title="${escapeAttr(item.title)}" data-channel="${escapeAttr(item.channel || '')}" data-duration="${item.duration || 0}">
        <div class="thumbnail-container">
            <img src="${escapeAttr(item.thumbnail)}" alt="${escapeHtml(item.title)}" loading="lazy">
            ${durationBadge}
        </div>
        <div class="video-info">
            <h3 class="video-title">${escapeHtml(item.title)}</h3>
            <p class="channel">${escapeHtml(item.channel)}${meta ? ' \u00b7 ' : ''}${meta}</p>
        </div>
    </a>`;
}

function attachCardListeners(container) {
    container.querySelectorAll('.video-card:not([data-attached])').forEach(card => {
        card.dataset.attached = 'true';
        const playlistId = card.dataset.playlistId;
        const itemType = card.dataset.itemType;
        if (playlistId && (itemType === 'playlist' || itemType === 'mix')) {
            card.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
                e.preventDefault();
                const videoId = card.dataset.id;
                const title = card.dataset.title;
                const channel = card.dataset.channel;
                _startQueue(videoId, title, channel, playlistId);
            });
        } else {
            card.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
                e.preventDefault();
                navigateToVideo(
                    card.dataset.id,
                    card.dataset.title,
                    card.dataset.channel,
                    parseInt(card.dataset.duration) || 0
                );
            });
        }
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
    attachCardListeners(videoGrid);
}


// ── Related Videos ──────────────────────────────────────────────────────────

async function fetchRelatedVideos(videoId) {
    try {
        relatedVideos.innerHTML = '<div class="loading-more"><div class="loading-spinner"></div></div>';

        const response = await fetch(`/api/related/${videoId}`);
        const data = await response.json();

        _relatedRawResults = data.results || [];
        _renderRelatedFiltered();
    } catch (error) {
        relatedVideos.innerHTML = '<p style="color: #ff4444; font-size: 14px;">Failed to load related videos</p>';
    }
}

function _renderRelatedFiltered() {
    relatedVideos.innerHTML = '';

    const results = _relatedRawResults;
    if (results.length > 0) {
        results.forEach(video => {
            relatedVideos.insertAdjacentHTML('beforeend', createRelatedCard(video));
        });
        attachRelatedListeners();
    } else {
        relatedVideos.innerHTML = '<p style="color: #717171; font-size: 14px;">No related videos found</p>';
    }
}

function createRelatedCard(video) {
    const isMixOrPlaylist = video.type === 'mix' || video.type === 'playlist';
    let badge;
    if (isMixOrPlaylist) {
        const label = video.type === 'playlist' ? 'Playlist' : 'Mix';
        const countBadge = video.video_count ? `<span class="video-count">${escapeHtml(video.video_count)}</span>` : '';
        badge = `${countBadge}<span class="badge-${escapeAttr(video.type)}">${label}</span>`;
    } else {
        badge = video.duration_str ? `<span class="duration">${escapeHtml(video.duration_str)}</span>` : '';
    }

    const dataAttrs = isMixOrPlaylist
        ? `data-id="${escapeAttr(video.first_video_id || video.id)}" data-playlist-id="${escapeAttr(video.playlist_id || video.id)}" data-item-type="${escapeAttr(video.type)}"`
        : `data-id="${escapeAttr(video.id)}"`;

    const vid = isMixOrPlaylist ? (video.first_video_id || video.id) : video.id;
    const relHref = isMixOrPlaylist ? `/watch?v=${escapeAttr(vid)}&list=${escapeAttr(video.playlist_id || video.id)}` : `/watch?v=${escapeAttr(vid)}`;
    return `<a class="related-card" href="${relHref}" ${dataAttrs} data-title="${escapeAttr(video.title)}" data-channel="${escapeAttr(video.channel || '')}" data-duration="0">
        <div class="thumbnail-container">
            <img src="${escapeAttr(video.thumbnail)}" alt="${escapeHtml(video.title)}" loading="lazy">
            ${badge}
        </div>
        <div class="related-info">
            <div class="related-title">${escapeHtml(video.title)}</div>
            ${video.channel ? `<div class="related-channel">${escapeHtml(video.channel)}</div>` : ''}
        </div>
    </a>`;
}

function attachRelatedListeners() {
    relatedVideos.querySelectorAll('.related-card:not([data-attached])').forEach(card => {
        card.dataset.attached = 'true';
        const playlistId = card.dataset.playlistId;
        const itemType = card.dataset.itemType;
        if (playlistId && (itemType === 'playlist' || itemType === 'mix')) {
            card.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
                e.preventDefault();
                const videoId = card.dataset.id;
                const title = card.dataset.title;
                const channel = card.dataset.channel;
                _startQueue(videoId, title, channel, playlistId);
            });
        } else {
            card.addEventListener('click', (e) => {
                if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
                e.preventDefault();
                const videoId = card.dataset.id;
                const title = card.dataset.title;
                const channel = card.dataset.channel;
                _closeQueue();
                history.pushState({ view: 'video', videoId, title, channel, duration: 0 }, '', `/watch?v=${videoId}`);
                playVideo(videoId, title, channel, 0);
            });
        }
    });
}


// ── Queue Mode ──────────────────────────────────────────────────────────────

const queueSection = document.getElementById('queue-section');
const queueTitle = document.getElementById('queue-title');
const queuePosition = document.getElementById('queue-position');
const queueList = document.getElementById('queue-list');
const queueToggleArea = document.getElementById('queue-toggle-area');
const queueHideIndicator = document.getElementById('queue-hide-indicator');
const queueCloseArea = document.getElementById('queue-close-area');

async function _startQueue(videoId, playlistTitle, channel, playlistId) {
    // Navigate to the video (don't pass playlist title as video title)
    cacheListView();
    history.pushState({ view: 'video', videoId, title: '', channel: '', duration: 0, playlistId }, '', `/watch?v=${videoId}&list=${playlistId}`);
    showVideoView();
    playVideo(videoId, '', '', 0);
    _loadQueue(videoId, playlistId);
}

async function _loadQueue(videoId, playlistId) {
    try {
        const resp = await fetch(`/api/playlist-contents?video_id=${videoId}&playlist_id=${encodeURIComponent(playlistId)}`);
        if (currentVideoId !== videoId) return; // user navigated away
        const data = await resp.json();

        if (data.videos && data.videos.length > 0) {
            _queue = {
                title: data.title || 'Queue',
                videos: data.videos,
                currentIndex: data.videos.findIndex(v => v.id === videoId),
                playlistId: playlistId,
            };
            if (_queue.currentIndex === -1) _queue.currentIndex = 0;
            _queueCollapsed = false;
            _renderQueue();
            // Re-check favorite status now that queue is loaded
            if (typeof checkFavoriteStatus === 'function') checkFavoriteStatus(videoId);
        }
    } catch (err) {
        console.error('Failed to fetch playlist contents:', err);
        queueTitle.textContent = 'Queue unavailable';
        queueSection.classList.remove('hidden');
        queueList.innerHTML = '<div style="padding: 12px; color: #ff4444; font-size: 13px;">Failed to load playlist</div>';
        setTimeout(() => { if (!_queue) queueSection.classList.add('hidden'); }, 4000);
    }
}

function _renderQueue() {
    if (!_queue) {
        queueSection.classList.add('hidden');
        return;
    }

    queueSection.classList.remove('hidden');
    queueTitle.textContent = _queue.title;
    queuePosition.textContent = `${_queue.currentIndex + 1}/${_queue.videos.length}`;

    queueList.classList.toggle('collapsed', _queueCollapsed);
    queueHideIndicator.innerHTML = _queueCollapsed ? '&#9650;' : '&#9660;';

    queueList.innerHTML = _queue.videos.map((v, i) => {
        const active = i === _queue.currentIndex ? ' active' : '';
        return `<div class="queue-item${active}" data-index="${i}" data-id="${escapeAttr(v.id)}">
            <span class="queue-item-index">${i + 1}</span>
            <div class="queue-item-thumb">
                <img src="${escapeAttr(v.thumbnail)}" alt="" loading="lazy">
            </div>
            <div class="queue-item-info">
                <div class="queue-item-title">${escapeHtml(v.title)}</div>
                ${v.channel ? `<div class="queue-item-channel">${escapeHtml(v.channel)}</div>` : ''}
            </div>
        </div>`;
    }).join('');

    // Attach click listeners
    queueList.querySelectorAll('.queue-item').forEach(item => {
        item.addEventListener('click', () => {
            const idx = parseInt(item.dataset.index);
            _playQueueItem(idx);
        });
    });

    // Scroll active item into view
    const activeItem = queueList.querySelector('.queue-item.active');
    if (activeItem) {
        activeItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
}

function _playQueueItem(index) {
    if (!_queue || index < 0 || index >= _queue.videos.length) return;
    _queue.currentIndex = index;
    const v = _queue.videos[index];
    history.pushState({ view: 'video', videoId: v.id, title: v.title, channel: v.channel, duration: 0, playlistId: _queue.playlistId }, '', `/watch?v=${v.id}&list=${_queue.playlistId}`);
    playVideo(v.id, v.title, v.channel, 0);
    _renderQueue();
}

function _advanceQueue() {
    if (!_queue) return;
    // Only auto-advance if the current video is actually the queue's current item
    const expectedId = _queue.videos[_queue.currentIndex]?.id;
    if (currentVideoId !== expectedId) return;
    const nextIndex = _queue.currentIndex + 1;
    if (nextIndex < _queue.videos.length) {
        _playQueueItem(nextIndex);
    } else {
        // Queue finished
        queuePosition.textContent = 'Finished';
    }
}

function _closeQueue() {
    _queue = null;
    queueSection.classList.add('hidden');
}

queueToggleArea.addEventListener('click', () => {
    _queueCollapsed = !_queueCollapsed;
    queueList.classList.toggle('collapsed', _queueCollapsed);
    queueHideIndicator.innerHTML = _queueCollapsed ? '&#9650;' : '&#9660;';
});

queueCloseArea.addEventListener('click', () => {
    _closeQueue();
});

// Expose queue state for other modules (e.g. profiles.js)
window._getQueue = () => _queue;

// Auto-advance: listen for video ended
videoPlayer.addEventListener('ended', () => {
    if (_queue) {
        _advanceQueue();
    }
});


// ── List View Cache ─────────────────────────────────────────────────────────

function cacheListView() {
    listViewCache = {
        mode: listViewMode,
        query: currentQuery,
        channelId: currentChannelId,
        cursor: currentCursor,
        html: videoGrid.innerHTML,
        headerVisible: !listHeader.classList.contains('hidden'),
        headerTitle: listTitle.textContent,
        searchRawResults: _searchRawResults,
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

        // Restore filter toggles and channel tabs
        if (listViewMode === 'search' && listViewCache.searchRawResults?.length > 0) {
            _searchRawResults = listViewCache.searchRawResults;
            _renderFilterToggles();
        } else if (listViewMode === 'channel' || listViewMode === 'channel_playlists') {
            _renderChannelTabs(currentChannelId);
        }
    }
}
