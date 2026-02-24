// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// YTP - Core: DOM refs, state, routing, player, quality selector, utils

// ── DOM Elements ────────────────────────────────────────────────────────────

// Views
const listView = document.getElementById('list-view');
const videoView = document.getElementById('video-view');
const listHeader = document.getElementById('list-header');
const listTitle = document.getElementById('list-title');
const clearListBtn = document.getElementById('clear-list-btn');
const listTabs = document.getElementById('list-tabs');

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

// Timestamp click handler (delegated)
videoDescription.addEventListener('click', (e) => {
    const link = e.target.closest('.timestamp-link');
    if (!link) return;
    e.preventDefault();
    const time = parseFloat(link.dataset.time);
    videoPlayer.currentTime = time;
    if (videoPlayer.paused) videoPlayer.play();
});

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
let isLiveStream = false; // true when playing a live stream
let liveRetried = false; // true after one recovery attempt
let _dashAutoRefreshed = false; // prevent DASH error → playVideo loop

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
                updateQualityHighlight(height);
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

function updateQualityHighlight(height) {
    currentActiveHeight = height;
    qualityBtn.textContent = `\ud83c\udfac ${height}p`;
    qualityBtn.disabled = false;
    qualityMenu.querySelectorAll('.quality-option').forEach(opt => {
        opt.classList.toggle('selected', parseInt(opt.dataset.height) === height);
    });
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
        const isDefault = track.default && track.lang !== 'original' ? ' (original)' : '';
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
    subtitleMenu.classList.add('hidden');
    const pm = document.getElementById('profile-menu');
    if (pm) pm.classList.add('hidden');
});

// ── Routing ─────────────────────────────────────────────────────────────────

function showListView() {
    listView.classList.remove('hidden');
    videoView.classList.add('hidden');
    stopPlayer();
    // Close queue when leaving video view
    if (typeof _closeQueue === 'function') _closeQueue();
    // Reset list UI — specific views (history/favorites) re-show what they need
    listTabs.classList.add('hidden');
    listTitle.classList.remove('hidden');
    _removeFavFilterToggles();
    if (typeof _removeFollowButton === 'function') _removeFollowButton();
}

function showVideoView() {
    listView.classList.add('hidden');
    videoView.classList.remove('hidden');
}

function navigateToVideo(videoId, title, channel, duration) {
    cacheListView();
    history.pushState({ view: 'video', videoId, title, channel, duration }, '', `/watch?v=${videoId}`);
    document.title = title ? `${title} - YTP` : 'YTP';
    showVideoView();
    playVideo(videoId, title, channel, duration);
}

function navigateToChannel(channelId, channelName) {
    history.pushState({ view: 'channel', channelId, channelName }, '', `/channel/${channelId}`);
    document.title = channelName ? `${channelName} - YTP` : 'YTP';
    showListView();
    loadChannelVideos(channelId, channelName);
}

const _handleCache = {}; // @handle → UCXXXX

async function resolveHandleAndLoad(handle, tab) {
    try {
        let channelId = _handleCache[handle];
        if (!channelId) {
            const resp = await fetch(`/api/resolve-handle/${encodeURIComponent(handle)}`);
            if (!resp.ok) throw new Error('Channel not found');
            const data = await resp.json();
            channelId = data.channel_id;
            _handleCache[handle] = channelId;
        }
        // Keep the @handle URL visible
        const url = tab === 'playlists' ? `/@${handle}/playlists` : `/@${handle}`;
        history.replaceState({ view: 'channel', channelId, channelName: '', tab, handle }, '', url);
        if (tab === 'playlists') {
            loadChannelPlaylists(channelId, '');
        } else {
            loadChannelVideos(channelId, '');
        }
    } catch (e) {
        document.getElementById('video-grid').innerHTML = `<p class="error">Channel @${escapeHtml(handle)} not found</p>`;
    }
}

window.addEventListener('pagehide', () => {
    videoPlayer.pause();
});

window.addEventListener('popstate', (e) => {
    if (e.state?.view === 'video') {
        document.title = e.state.title ? `${e.state.title} - YTP` : 'YTP';
        showVideoView();
        playVideo(e.state.videoId, e.state.title, e.state.channel, e.state.duration);
        if (e.state.playlistId) {
            if (_queue?.playlistId === e.state.playlistId) {
                const idx = _queue.videos.findIndex(v => v.id === e.state.videoId);
                if (idx !== -1) _queue.currentIndex = idx;
                _renderQueue();
            } else {
                _loadQueue(e.state.videoId, e.state.playlistId);
            }
        }
    } else if (e.state?.view === 'channel') {
        document.title = e.state.channelName ? `${e.state.channelName} - YTP` : 'YTP';
        showListView();
        if (e.state.tab === 'playlists') {
            loadChannelPlaylists(e.state.channelId, e.state.channelName);
        } else {
            loadChannelVideos(e.state.channelId, e.state.channelName);
        }
    } else if (e.state?.view === 'search') {
        document.title = e.state.query ? `${e.state.query} - YTP` : 'YTP';
        showListView();
        if (listViewCache && listViewCache.query === e.state.query) {
            restoreListCache();
        } else if (e.state.query) {
            searchVideos(e.state.query, { pushState: false });
        }
    } else {
        document.title = 'YTP';
        if (e.state?.view === 'history') {
            showListView();
            loadHistory();
        } else if (e.state?.view === 'favorites') {
            showListView();
            loadFavorites();
        } else if (e.state?.view === 'channels') {
            showListView();
            loadChannels();
        } else {
            // Default (home) = remembered tab
            showListView();
            loadHomeTab();
        }
    }
});

function handleInitialRoute() {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);

    if (path === '/watch' && params.get('v')) {
        const videoId = params.get('v');
        const listId = params.get('list');
        const url = listId ? `/watch?v=${videoId}&list=${listId}` : `/watch?v=${videoId}`;
        history.replaceState({ view: 'video', videoId, title: '', channel: '', duration: 0, playlistId: listId || undefined }, '', url);
        showVideoView();
        playVideo(videoId, '', '', 0);
        if (listId) _loadQueue(videoId, listId);
    } else if (path.startsWith('/@')) {
        const rest = path.slice(2); // remove /@
        const isPlaylists = rest.endsWith('/playlists');
        const handle = isPlaylists ? rest.slice(0, -'/playlists'.length) : rest;
        const tab = isPlaylists ? 'playlists' : 'videos';
        showListView();
        resolveHandleAndLoad(handle, tab);
    } else if (path.startsWith('/channel/')) {
        const rest = path.slice('/channel/'.length);
        const isPlaylists = rest.endsWith('/playlists');
        const channelId = isPlaylists ? rest.slice(0, -'/playlists'.length) : rest;
        const tab = isPlaylists ? 'playlists' : 'videos';
        history.replaceState({ view: 'channel', channelId, channelName: '', tab }, '', path);
        showListView();
        if (isPlaylists) {
            loadChannelPlaylists(channelId, '');
        } else {
            loadChannelVideos(channelId, '');
        }
    } else if (path === '/results') {
        const query = params.get('search_query');
        if (query) {
            showListView();
            searchVideos(query, { pushState: false });
        } else {
            history.replaceState({ view: 'history' }, '', '/');
            showListView();
            loadHistory();
        }
    } else if (path === '/history') {
        history.replaceState({ view: 'history' }, '', '/');
        showListView();
        loadHistory();
    } else if (path === '/favorites') {
        history.replaceState({ view: 'favorites' }, '', '/');
        showListView();
        loadFavorites();
    } else if (path === '/channels') {
        history.replaceState({ view: 'channels' }, '', '/');
        showListView();
        loadChannels();
    } else {
        // Home page = remembered tab
        showListView();
        loadHomeTab();
    }
}

async function loadListPage(endpoint, title, {showClear = false, removable = false, clearEndpoint = '', clearPrompt = '', keepTabs = false} = {}) {
    if (typeof _removeChannelTabs === 'function') _removeChannelTabs();
    if (typeof _removeFilterToggles === 'function') _removeFilterToggles();
    listHeader.classList.remove('hidden');
    if (!keepTabs) {
        listTabs.classList.add('hidden');
        listTitle.classList.remove('hidden');
    }
    listTitle.textContent = title;
    clearListBtn.classList.toggle('hidden', !showClear);
    clearListBtn.textContent = `Clear ${title.toLowerCase()}`;
    _clearListEndpoint = clearEndpoint;
    _clearListPrompt = clearPrompt;
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    // Bump generation to discard any in-flight search/channel/loadMore responses
    _listGeneration++;
    loadMoreObserver.disconnect();
    searchInput.value = '';

    try {
        const resp = await fetch(endpoint);
        if (!resp.ok) throw new Error(`Failed to load ${title.toLowerCase()}`);
        const items = await resp.json();
        if (items.length === 0) {
            noResults.classList.remove('hidden');
            clearListBtn.classList.add('hidden');
        } else {
            renderVideos(items.map(item => ({
                id: item.video_id,
                title: item.title,
                channel: item.channel,
                thumbnail: item.thumbnail || `https://img.youtube.com/vi/${item.video_id}/hqdefault.jpg`,
                duration: item.duration,
                duration_str: item.duration_str || '',
            })));
            if (removable) {
                const removeEndpoint = clearEndpoint; // e.g. /api/profiles/history or /api/profiles/favorites
                videoGrid.querySelectorAll('.video-card').forEach(card => {
                    const btn = document.createElement('button');
                    btn.className = 'remove-entry-btn';
                    btn.title = 'Remove';
                    btn.textContent = '\u00d7';
                    btn.addEventListener('click', async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        const resp = await fetch(`${removeEndpoint}/${card.dataset.id}`, {method: 'DELETE'});
                        if (resp.ok) {
                            card.remove();
                            if (!videoGrid.querySelector('.video-card')) {
                                noResults.classList.remove('hidden');
                                clearListBtn.classList.add('hidden');
                            }
                        }
                    });
                    card.style.position = 'relative';
                    card.appendChild(btn);
                });
            }
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

let _favFilters = { video: true, playlist: true, mix: true };

async function _probeHomeTabs() {
    const [hResp, fResp, cResp] = await Promise.all([
        fetch('/api/profiles/history?limit=1').then(r => r.ok ? r.json() : []),
        fetch('/api/profiles/favorites?limit=1').then(r => r.ok ? r.json() : []),
        fetch('/api/profiles/channels').then(r => r.ok ? r.json() : []),
    ]);
    return {
        history: hResp.length > 0,
        favorites: fResp.length > 0,
        channels: cResp.length > 0,
    };
}

function _updateTabVisibility(counts) {
    listTabs.querySelectorAll('.list-tab').forEach(btn => {
        btn.classList.toggle('hidden', !counts[btn.dataset.tab]);
    });
}

function loadHistoryOrFavorites(tab, skipProbe = false) {
    localStorage.setItem('lastHomeTab', tab);
    listTabs.classList.remove('hidden');
    listTitle.classList.add('hidden');

    listTabs.querySelectorAll('.list-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
        if (!skipProbe) btn.classList.remove('hidden');
    });

    // Attach tab click listeners (replace to avoid duplicates)
    listTabs.querySelectorAll('.list-tab').forEach(btn => {
        btn.onclick = () => {
            if (btn.dataset.tab === tab) return;
            const newTab = btn.dataset.tab;
            history.replaceState({ view: newTab }, '', '/');
            loadHistoryOrFavorites(newTab, true);
        };
    });

    // Probe counts and hide empty tabs — redirect if active tab is empty
    if (!skipProbe) {
        _probeHomeTabs().then(counts => {
            _updateTabVisibility(counts);
            // If active tab is empty, switch to one that has content
            if (!counts[tab]) {
                const fallback = ['history', 'favorites', 'channels'].find(t => counts[t]);
                if (fallback) {
                    history.replaceState({ view: fallback }, '', '/');
                    loadHistoryOrFavorites(fallback, true);
                }
                // If nothing has content, current tab stays visible (shows "no results")
            }
        });
    }

    _loadTabContent(tab);
}

function _loadTabContent(tab) {
    if (tab === 'favorites') {
        _favFilters = { video: true, playlist: true, mix: true };
        return loadFavoritesPage();
    } else if (tab === 'channels') {
        return loadChannelsPage();
    } else {
        return loadListPage('/api/profiles/history?limit=50', 'Watch History', {showClear: true, removable: true, clearEndpoint: '/api/profiles/history', clearPrompt: 'Clear all watch history?', keepTabs: true});
    }
}

function _removeFavFilterToggles() {
    const existing = document.getElementById('fav-filter-toggles');
    if (existing) existing.remove();
}

function _renderFavFilterToggles(items) {
    _removeFavFilterToggles();
    // Only show filters if there's a mix of types
    const hasVideos = items.some(i => !i.item_type || i.item_type === 'video' || i.item_type === 'live');
    const hasPlaylists = items.some(i => i.item_type === 'playlist');
    const hasMixes = items.some(i => i.item_type === 'mix');
    const typeCount = [hasVideos, hasPlaylists, hasMixes].filter(Boolean).length;
    if (typeCount < 2) return;

    const container = document.createElement('div');
    container.id = 'fav-filter-toggles';
    container.className = 'filter-toggles';

    const group = document.createElement('div');
    group.className = 'filter-group';

    [
        { key: 'video', label: 'Videos', show: hasVideos },
        { key: 'playlist', label: 'Playlists', show: hasPlaylists },
        { key: 'mix', label: 'Mixes', show: hasMixes },
    ].forEach(({ key, label, show }) => {
        if (!show) return;
        const btn = document.createElement('button');
        btn.className = `filter-btn${_favFilters[key] ? ' active' : ''}`;
        btn.textContent = label;
        btn.addEventListener('click', () => {
            _favFilters[key] = !_favFilters[key];
            btn.classList.toggle('active', _favFilters[key]);
            _applyFavFilters(items);
        });
        group.appendChild(btn);
    });

    container.appendChild(group);
    videoGrid.parentNode.insertBefore(container, videoGrid);
}

function _applyFavFilters(allItems) {
    const filtered = allItems.filter(i => {
        const t = i.item_type || 'video';
        return _favFilters[t === 'live' ? 'video' : t];
    });
    if (filtered.length === 0) {
        videoGrid.innerHTML = '';
        noResults.classList.remove('hidden');
    } else {
        noResults.classList.add('hidden');
        _renderFavoriteCards(filtered);
    }
}

async function loadFavoritesPage() {
    if (typeof _removeChannelTabs === 'function') _removeChannelTabs();
    if (typeof _removeFilterToggles === 'function') _removeFilterToggles();
    listHeader.classList.remove('hidden');
    listTabs.classList.remove('hidden');
    listTitle.classList.add('hidden');
    clearListBtn.classList.remove('hidden');
    clearListBtn.textContent = 'Clear favorites';
    _clearListEndpoint = '/api/profiles/favorites';
    _clearListPrompt = 'Clear all favorites?';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    _listGeneration++;
    loadMoreObserver.disconnect();
    searchInput.value = '';

    try {
        // Always fetch all items first to determine which filter buttons to show
        const allResp = await fetch('/api/profiles/favorites?limit=200');
        if (!allResp.ok) throw new Error('Failed to load favorites');
        const allItems = await allResp.json();

        // Render filter toggles based on all items
        _renderFavFilterToggles(allItems);

        // Apply client-side filter
        const items = allItems.filter(i => { const t = i.item_type || 'video'; return _favFilters[t === 'live' ? 'video' : t]; });

        if (items.length === 0) {
            noResults.classList.remove('hidden');
            if (allItems.length === 0) clearListBtn.classList.add('hidden');
        } else {
            _renderFavoriteCards(items);
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

function _renderFavoriteCards(items) {
    videoGrid.innerHTML = items.map(item => {
        const itemType = item.item_type || 'video';
        if (itemType === 'playlist' || itemType === 'mix') {
            return createVideoCard({
                id: item.first_video_id || item.video_id,
                title: item.title,
                channel: item.channel,
                thumbnail: item.thumbnail || `https://img.youtube.com/vi/${item.first_video_id || item.video_id}/hqdefault.jpg`,
                type: itemType,
                playlist_id: item.playlist_id || item.video_id,
                first_video_id: item.first_video_id,
                video_count: item.video_count || '',
            });
        }
        const isLive = itemType === 'live';
        return createVideoCard({
            id: item.video_id,
            title: item.title,
            channel: item.channel,
            thumbnail: item.thumbnail || `https://img.youtube.com/vi/${item.video_id}/hqdefault.jpg`,
            duration: item.duration,
            duration_str: item.duration_str || '',
            is_live: isLive,
        });
    }).join('');
    attachCardListeners(videoGrid);

    // Add remove buttons — use video_id (which is PL*/RD* for playlists)
    videoGrid.querySelectorAll('.video-card').forEach((card, idx) => {
        const item = items[idx];
        const btn = document.createElement('button');
        btn.className = 'remove-entry-btn';
        btn.title = 'Remove';
        btn.textContent = '\u00d7';
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const deleteId = item.video_id;
            const resp = await fetch(`/api/profiles/favorites/${encodeURIComponent(deleteId)}`, { method: 'DELETE' });
            if (resp.ok) {
                card.remove();
                if (!videoGrid.querySelector('.video-card')) {
                    noResults.classList.remove('hidden');
                    clearListBtn.classList.add('hidden');
                }
            }
        });
        card.style.position = 'relative';
        card.appendChild(btn);
    });
}

async function loadChannelsPage() {
    if (typeof _removeChannelTabs === 'function') _removeChannelTabs();
    if (typeof _removeFilterToggles === 'function') _removeFilterToggles();
    _removeFavFilterToggles();
    listHeader.classList.remove('hidden');
    listTabs.classList.remove('hidden');
    listTitle.classList.add('hidden');
    clearListBtn.classList.remove('hidden');
    clearListBtn.textContent = 'Clear channels';
    _clearListEndpoint = '/api/profiles/channels';
    _clearListPrompt = 'Clear all channels?';
    videoGrid.innerHTML = '';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    _listGeneration++;
    loadMoreObserver.disconnect();
    searchInput.value = '';

    try {
        const resp = await fetch('/api/profiles/channels');
        if (!resp.ok) throw new Error('Failed to load channels');
        const items = await resp.json();

        if (items.length === 0) {
            noResults.classList.remove('hidden');
            clearListBtn.classList.add('hidden');
        } else {
            videoGrid.innerHTML = items.map(ch => {
                const avatar = ch.avatar_url
                    ? `<img src="${escapeAttr(ch.avatar_url)}" alt="${escapeHtml(ch.channel_name)}" loading="lazy">`
                    : `<div class="channel-card-placeholder">${escapeHtml(ch.channel_name.charAt(0).toUpperCase())}</div>`;
                return `<a class="video-card channel-card" href="/channel/${escapeAttr(ch.channel_id)}" data-channel-id="${escapeAttr(ch.channel_id)}" data-channel-name="${escapeAttr(ch.channel_name)}">
                    <div class="thumbnail-container channel-avatar-container">
                        ${avatar}
                    </div>
                    <div class="video-info">
                        <h3 class="video-title">${escapeHtml(ch.channel_name)}</h3>
                    </div>
                </a>`;
            }).join('');

            // Attach click listeners
            videoGrid.querySelectorAll('.channel-card').forEach(card => {
                card.addEventListener('click', (e) => {
                    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
                    e.preventDefault();
                    navigateToChannel(card.dataset.channelId, card.dataset.channelName);
                });

                // Remove button
                const btn = document.createElement('button');
                btn.className = 'remove-entry-btn';
                btn.title = 'Unfollow';
                btn.textContent = '\u00d7';
                btn.addEventListener('click', async (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    const resp = await fetch(`/api/profiles/channels/${card.dataset.channelId}`, {method: 'DELETE'});
                    if (resp.ok) {
                        card.remove();
                        if (!videoGrid.querySelector('.channel-card')) {
                            noResults.classList.remove('hidden');
                            clearListBtn.classList.add('hidden');
                        }
                    }
                });
                card.style.position = 'relative';
                card.appendChild(btn);
            });
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

function loadHistory() { return loadHistoryOrFavorites('history'); }
function loadFavorites() { return loadHistoryOrFavorites('favorites'); }
function loadChannels() { return loadHistoryOrFavorites('channels'); }

function loadHomeTab() {
    const tab = localStorage.getItem('lastHomeTab') || 'history';
    history.replaceState({ view: tab }, '', '/');
    loadHistoryOrFavorites(tab);
}

let _clearListEndpoint = '';
let _clearListPrompt = '';

clearListBtn.addEventListener('click', async () => {
    if (!_clearListEndpoint || !await nativeConfirm(_clearListPrompt)) return;
    try {
        const resp = await fetch(_clearListEndpoint, {method: 'DELETE'});
        if (!resp.ok) throw new Error('Failed to clear');
        videoGrid.innerHTML = '';
        noResults.classList.remove('hidden');
        clearListBtn.classList.add('hidden');
    } catch (err) {
        nativeAlert(err.message);
    }
});

// ── Player ──────────────────────────────────────────────────────────────────

function showPlayerError(title, message) {
    videoTitle.textContent = title || 'Video unavailable';
    qualitySelector.classList.add('hidden');
    const overlay = document.createElement('div');
    overlay.className = 'player-error-overlay';
    overlay.innerHTML = `<div class="player-error-icon">!</div><p>${escapeHtml(message || 'This video is currently unavailable.')}</p><button class="player-error-retry">Retry</button>`;
    overlay.querySelector('.player-error-retry').addEventListener('click', () => {
        const vid = new URLSearchParams(window.location.search).get('v');
        if (vid) playVideo(vid);
    });
    playerContainer.appendChild(overlay);
}

function stopPlayer() {
    const errOverlay = playerContainer.querySelector('.player-error-overlay');
    if (errOverlay) errOverlay.remove();
    if (!isLiveStream) savePosition();
    if (positionSaveTimer) {
        clearTimeout(positionSaveTimer);
        positionSaveTimer = null;
    }
    videoPlayer.pause();
    if (dashPlayer) {
        try { dashPlayer.destroy(); } catch(e) { console.warn('dash destroy error:', e); }
        dashPlayer = null;
    }
    if (hlsPlayer) {
        try { hlsPlayer.destroy(); } catch(e) { console.warn('hls destroy error:', e); }
        hlsPlayer = null;
    }
    currentPlayerType = null;
    currentAudioLang = null;
    hlsAudioTracks = [];
    pendingSeek = null;
    qualitySelector.classList.add('hidden');
    qualityMenu.classList.add('hidden');
    audioBtnContainer.classList.add('hidden');
    audioMenu.classList.add('hidden');
    currentActiveHeight = 0;
    videoQualities = [];
    isLiveStream = false;
    liveRetried = false;
    const liveBadge = document.getElementById('live-badge');
    if (liveBadge) liveBadge.remove();
    subtitleBtnContainer.classList.add('hidden');
    subtitleTracks = [];
    failedSubtitles.clear();
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
    currentVideoId = null;
    currentVideoChannelId = null;
    videoPlayer.removeAttribute('src');
    videoPlayer.removeAttribute('poster');
    videoPlayer.load();
}

async function playVideo(videoId, title, channel, duration) {
    stopPlayer();
    currentVideoId = videoId;

    videoTitle.textContent = title || '';
    videoChannel.textContent = channel || '';
    videoChannel.href = '#';
    videoMeta.textContent = '';
    window.dispatchEvent(new Event('video-changed'));
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

        if (!resp.ok) {
            showPlayerError(title, info.message || info.detail);
            fetchRelatedVideos(videoId);
            return;
        }

        videoTitle.textContent = info.title || title;
        document.title = (info.title || title) ? `${info.title || title} - YTP` : 'YTP';
        videoChannel.textContent = info.channel || channel;

        if (info.channel_id) {
            currentVideoChannelId = info.channel_id;
            window.dispatchEvent(new Event('channel-id-ready'));
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

        if (info.is_live && Hls.isSupported()) {
            // Live stream: use HLS (DASH requires fixed duration)
            isLiveStream = true;
            const badge = document.createElement('span');
            badge.id = 'live-badge';
            badge.className = 'live-edge';
            badge.textContent = 'LIVE';
            badge.title = 'Click to jump to live';
            badge.addEventListener('click', seekToLiveEdge);
            document.querySelector('.video-title-row').appendChild(badge);
            startHlsPlayer(videoId, `/api/hls/master/${videoId}?live=1`, true);
        } else {
            // Regular video: start with DASH (full quality, up to 4K)
            startDashPlayer(videoId);

            // If multi-audio available, show audio selector (HLS used only on language switch)
            if (info.has_multi_audio && info.hls_manifest_url && Hls.isSupported()) {
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
        }
    } catch (err) {
        console.error('Info fetch failed:', err);
        showPlayerError(title);
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
            retryAttempts: { MPD: 0 },
        },
    });
    dashPlayer.initialize(videoPlayer, `/api/dash/${videoId}`, true);

    dashPlayer.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
        _dashAutoRefreshed = false;
        videoQualities = buildQualitiesDash();
        if (videoQualities.length === 0) return;

        const heights = videoQualities.map(q => q.height);
        const targetHeight = getTargetQuality(heights, preferredQuality);
        const targetEntry = videoQualities.find(q => q.height === targetHeight);

        if (targetEntry) {
            switchToQuality(targetEntry);
            updateQualityHighlight(targetHeight);
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
            updateQualityHighlight(entry.height);
        }
    });

    dashPlayer.on(dashjs.MediaPlayer.events.ERROR, (e) => {
        if (!e.error || _dashAutoRefreshed) return;
        _dashAutoRefreshed = true;
        console.warn('DASH error, auto-refreshing session');
        savePosition();
        playVideo(videoId, videoTitle.textContent, videoChannel.textContent, videoPlayer.dataset.expectedDuration);
    });
}

function startHlsPlayer(videoId, manifestUrl, live = false) {
    currentPlayerType = 'hls';

    const hlsConfig = live
        ? { liveSyncDurationCount: 3 }
        : { maxBufferLength: 30, maxMaxBufferLength: 60 };
    hlsPlayer = new Hls(hlsConfig);
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
            updateQualityHighlight(targetHeight);
        }

        populateQualityMenu();

        if (live) {
            // Live: just start playing at live edge, no position restore
            liveRetried = false; // successful load, allow future recovery
            videoPlayer.play();
        } else if (pendingSeek) {
            // Restore position: pendingSeek (from audio switch) takes priority
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
                updateQualityHighlight(entry.height);
            }
        }
    });

    hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
            hlsPlayer.destroy();
            hlsPlayer = null;
            if (live && !liveRetried) {
                // Live stream: likely expired URLs — reload with fresh manifest
                console.warn('HLS live error, reloading manifest:', data.type);
                liveRetried = true;
                startHlsPlayer(videoId, manifestUrl, true);
            } else if (!live) {
                console.error('HLS fatal error, falling back to DASH:', data);
                startDashPlayer(videoId);
            }
        }
    });
}

function seekToLiveEdge() {
    if (!hlsPlayer || !isLiveStream) return;
    videoPlayer.currentTime = hlsPlayer.liveSyncPosition || videoPlayer.duration;
    videoPlayer.play();
}

function updateLiveBadge() {
    const badge = document.getElementById('live-badge');
    if (!badge || !hlsPlayer || !isLiveStream) return;
    const livePos = hlsPlayer.liveSyncPosition || videoPlayer.duration;
    const behind = livePos - videoPlayer.currentTime;
    const atEdge = behind < 15; // within ~15s of live edge
    badge.classList.toggle('live-edge', atEdge);
    badge.classList.toggle('live-behind', !atEdge);
}

// ── Utils ───────────────────────────────────────────────────────────────────

const _langNames = new Intl.DisplayNames(['en'], { type: 'language' });
function langName(code) {
    try { return _langNames.of(code); }
    catch { return code.toUpperCase(); }
}

const _escapeDiv = document.createElement('div');
function escapeHtml(text) {
    _escapeDiv.textContent = text;
    return _escapeDiv.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function linkifyText(text) {
    const escaped = escapeHtml(text);
    // First linkify URLs
    let result = escaped.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
        const href = match.replace(/&quot;/g, '%22').replace(/&#39;/g, '%27').replace(/&amp;/g, '&');
        return `<a href="${href}" target="_blank" rel="noopener">${match}</a>`;
    });
    // Then parse timestamps (0:00, 1:23, 1:23:45) — but not inside <a> tags
    result = result.replace(/(?:<a[^>]*>.*?<\/a>)|(?:^|\s|\()(\d{1,2}:\d{2}(?::\d{2})?)\b/g, (full, ts) => {
        if (!ts) return full; // skip <a> tag matches
        const parts = ts.split(':').map(Number);
        const seconds = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
        const prefix = full.slice(0, full.indexOf(ts));
        return `${prefix}<a href="#" class="timestamp-link" data-time="${seconds}">${ts}</a>`;
    });
    return result;
}

// ── Native Modals (replace browser alert/confirm) ──────────────────────────

function showModal(message, {confirm: isConfirm = false} = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'pin-modal';
        overlay.innerHTML = `
            <div class="pin-modal-content" style="max-width:360px">
                <p style="margin-bottom:20px;font-size:15px;line-height:1.5">${escapeHtml(message)}</p>
                <div class="pin-actions">
                    ${isConfirm ? '<button class="pin-cancel">Cancel</button>' : ''}
                    <button class="pin-submit">OK</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const cancelBtn = overlay.querySelector('.pin-cancel');
        if (cancelBtn) cancelBtn.focus(); else overlay.querySelector('.pin-submit').focus();
        // Delay removal by one frame to prevent click-through to modals underneath
        overlay.querySelector('.pin-submit').addEventListener('click', () => { overlay.remove(); resolve(true); });
        if (cancelBtn) cancelBtn.addEventListener('click', () => { overlay.remove(); resolve(false); });
        overlay.addEventListener('click', (e) => { if (e.target === overlay) { overlay.remove(); resolve(isConfirm ? false : true); } });
        overlay.addEventListener('keydown', (e) => { if (e.key === 'Escape') { overlay.remove(); resolve(isConfirm ? false : true); } });
    });
}

function nativeAlert(message) { return showModal(message); }
function nativeConfirm(message) { return showModal(message, {confirm: true}); }

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
    if (isLiveStream) { updateLiveBadge(); return; }
    if (!positionSaveTimer) {
        positionSaveTimer = setTimeout(() => {
            savePosition();
            positionSaveTimer = null;
        }, 5000);
    }
});

// ── Event Listeners ─────────────────────────────────────────────────────────

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

document.getElementById('logo-link').addEventListener('click', (e) => {
    e.preventDefault();
    history.pushState({ view: 'home' }, '', '/');
    document.title = 'YTP';
    showListView();
    loadHomeTab();
});

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
