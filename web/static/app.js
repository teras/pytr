// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// PYTR - Core: DOM refs, state, routing, player, quality selector, utils

// ── Utilities ───────────────────────────────────────────────────────────────

/** Append ?cookies=<mode> (or &cookies=<mode>) to a URL based on localStorage */
function appendCookieParam(url) {
    const mode = (typeof getCookieMode === 'function') ? getCookieMode() : (localStorage.getItem('cookieMode') || 'auto');
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}cookies=${mode}`;
}

/** Set best available YouTube poster: maxresdefault → sddefault → hqdefault */
function setBestPoster(videoEl, videoId) {
    const base = `https://img.youtube.com/vi/${videoId}`;
    videoEl.poster = `${base}/hqdefault.jpg`; // immediate low-res
    const img = new Image();
    img.onload = function() {
        // YouTube returns a 120x90 placeholder when maxresdefault doesn't exist
        if (img.naturalWidth > 120) {
            videoEl.poster = img.src;
        } else {
            // try sddefault (640x480) as middle ground
            const sd = new Image();
            sd.onload = function() {
                if (sd.naturalWidth > 120) videoEl.poster = sd.src;
            };
            sd.src = `${base}/sddefault.jpg`;
        }
    };
    img.src = `${base}/maxresdefault.jpg`;
}

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

// Internal PYTR link click handler (delegated) — YouTube URLs in descriptions
videoDescription.addEventListener('click', (e) => {
    const link = e.target.closest('a[data-internal="1"]');
    if (!link) return;
    e.preventDefault();
    // Push the internal URL and let handleInitialRoute parse all params
    history.pushState(null, '', link.getAttribute('href'));
    handleInitialRoute();
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
let pendingSeek = null; // {time, play} — set during audio language switch or t= param
let isLiveStream = false; // true when playing a live stream
let liveRetried = false; // true after one recovery attempt
let _dashAutoRefreshed = false; // prevent DASH error → playVideo loop

// Parse YouTube t= param: "120", "2m30s", "1h2m30s"
function parseYouTubeTime(t) {
    if (!t) return 0;
    if (/^\d+$/.test(t)) return parseInt(t, 10);
    let sec = 0;
    const h = t.match(/(\d+)h/), m = t.match(/(\d+)m/), s = t.match(/(\d+)s/);
    if (h) sec += parseInt(h[1], 10) * 3600;
    if (m) sec += parseInt(m[1], 10) * 60;
    if (s) sec += parseInt(s[1], 10);
    return sec;
}

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
    const wasOpen = !qualityMenu.classList.contains('hidden');
    closeAllMenus();
    if (!wasOpen) qualityMenu.classList.remove('hidden');
});

qualityMenu.addEventListener('click', (e) => e.stopPropagation());

// ── Audio Selector ──────────────────────────────────────────────────────────

audioBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasOpen = !audioMenu.classList.contains('hidden');
    closeAllMenus();
    if (!wasOpen) audioMenu.classList.remove('hidden');
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
        const manifestUrl = appendCookieParam(`/api/hls/master/${currentVideoId}?audio=${encodeURIComponent(lang)}`);
        startHlsPlayer(currentVideoId, manifestUrl);
    }
}

function closeAllMenus() {
    qualityMenu.classList.add('hidden');
    audioMenu.classList.add('hidden');
    subtitleMenu.classList.add('hidden');
    document.getElementById('summarize-menu').classList.add('hidden');
    const pm = document.getElementById('profile-menu');
    if (pm) pm.classList.add('hidden');
}

document.addEventListener('click', closeAllMenus);

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
    // Remote mode: send command to target instead of playing locally
    if (typeof _remoteMode !== 'undefined' && _remoteMode && typeof _pairedDeviceName !== 'undefined' && _pairedDeviceName) {
        if (typeof _remotePlayVideo === 'function') {
            _remotePlayVideo(videoId, title, channel, duration);
            return;
        }
    }
    cacheListView();
    history.pushState({ view: 'video', videoId, title, channel, duration }, '', `/watch?v=${videoId}`);
    document.title = title ? `${title} - PYTR` : 'PYTR';
    showVideoView();
    playVideo(videoId, title, channel, duration);
}

function navigateToChannel(channelId, channelName) {
    history.pushState({ view: 'channel', channelId, channelName }, '', `/channel/${channelId}`);
    document.title = channelName ? `${channelName} - PYTR` : 'PYTR';
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
    if (e.state && e.state.view === 'video') {
        document.title = e.state.title ? `${e.state.title} - PYTR` : 'PYTR';
        showVideoView();
        playVideo(e.state.videoId, e.state.title, e.state.channel, e.state.duration);
        if (e.state.playlistId) {
            if (_queue && _queue.playlistId === e.state.playlistId) {
                const idx = _queue.videos.findIndex(v => v.id === e.state.videoId);
                if (idx !== -1) _queue.currentIndex = idx;
                _renderQueue();
            } else {
                _loadQueue(e.state.videoId, e.state.playlistId);
            }
        }
    } else if (e.state && e.state.view === 'channel') {
        document.title = e.state.channelName ? `${e.state.channelName} - PYTR` : 'PYTR';
        showListView();
        if (e.state.tab === 'playlists') {
            loadChannelPlaylists(e.state.channelId, e.state.channelName);
        } else {
            loadChannelVideos(e.state.channelId, e.state.channelName);
        }
    } else if (e.state && e.state.view === 'search') {
        document.title = e.state.query ? `${e.state.query} - PYTR` : 'PYTR';
        showListView();
        if (listViewCache && listViewCache.query === e.state.query) {
            restoreListCache();
        } else if (e.state.query) {
            searchVideos(e.state.query, { pushState: false });
        }
    } else {
        document.title = 'PYTR';
        if (e.state && e.state.view === 'history') {
            showListView();
            loadHistory();
        } else if (e.state && e.state.view === 'favorites') {
            showListView();
            loadFavorites();
        } else if (e.state && e.state.view === 'channels') {
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
        const startTime = parseYouTubeTime(params.get('t') || params.get('start'));
        const index = parseInt(params.get('index'), 10);
        const url = listId ? `/watch?v=${videoId}&list=${listId}` : `/watch?v=${videoId}`;
        history.replaceState({ view: 'video', videoId, title: '', channel: '', duration: 0, playlistId: listId || undefined }, '', url);
        showVideoView();
        if (listId && index > 0) {
            // Have a playlist index — load queue first, then play the correct video
            _loadQueue(videoId, listId);
            window.addEventListener('queue-ready', () => {
                if (_queue && _queue.videos[index]) {
                    const target = _queue.videos[index];
                    _queue.currentIndex = index;
                    _renderQueue();
                    playVideo(target.id, target.title || '', '', 0, startTime);
                    history.replaceState({ view: 'video', videoId: target.id, title: target.title || '', channel: '', duration: 0, playlistId: listId }, '', `/watch?v=${target.id}&list=${listId}`);
                } else {
                    // Index out of range — fall back to the video from the URL
                    playVideo(videoId, '', '', 0, startTime);
                }
            }, { once: true });
        } else {
            playVideo(videoId, '', '', 0, startTime);
            if (listId) _loadQueue(videoId, listId);
        }
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
    if (typeof resetSponsorBlock === 'function') resetSponsorBlock();
    currentVideoId = null;
    currentVideoChannelId = null;
    videoPlayer.removeAttribute('src');
    videoPlayer.removeAttribute('poster');
    videoPlayer.load();
}

async function playVideo(videoId, title, channel, duration, startTime) {
    stopPlayer();
    currentVideoId = videoId;
    if (startTime > 0) pendingSeek = { time: startTime, play: true };

    videoTitle.textContent = title || '';
    videoChannel.textContent = channel || '';
    videoChannel.href = '#';
    const ytLink = document.getElementById('youtube-link');
    if (ytLink) ytLink.href = `https://www.youtube.com/watch?v=${videoId}`;
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
    // Try maxresdefault (1280x720 HD, 16:9) with fallback chain
    setBestPoster(videoPlayer, videoId);

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
        const resp = await fetch(appendCookieParam(`/api/info/${videoId}`));
        const info = await resp.json();

        if (!resp.ok) {
            showPlayerError(title, info.message || info.detail);
            fetchRelatedVideos(videoId);
            return;
        }

        videoTitle.textContent = info.title || title;
        document.title = (info.title || title) ? `${info.title || title} - PYTR` : 'PYTR';
        videoChannel.textContent = info.channel || channel;
        if (info.channel_followers) {
            videoChannel.textContent += `\u2003\u2003\ud83d\udc65 ${info.channel_followers}`;
        }

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
        updateSummarizeVisibility();

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
            startHlsPlayer(videoId, appendCookieParam(`/api/hls/master/${videoId}?live=1`), true);
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
                } catch(e) {}
            }
        }
        if (typeof initSponsorBlock === 'function') initSponsorBlock(videoId);
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
    dashPlayer.initialize(videoPlayer, appendCookieParam(`/api/dash/${videoId}`), true);

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

const _langNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'language' }) : null;
function langName(code) {
    try { return _langNames ? _langNames.of(code) : code.toUpperCase(); }
    catch(e) { return code.toUpperCase(); }
}

// escapeHtml, escapeAttr, linkifyText, showModal, nativeAlert, nativeConfirm → utilities.js

// ── Playback Position ───────────────────────────────────────────────────────

function savePosition() {
    // Send state via WebSocket — server saves position to DB
    if (_wsConnected && currentVideoId) {
        _broadcastPlayerState();
    }
}

function restorePosition(videoId) {
    if (typeof restorePositionFromAPI === 'function') {
        restorePositionFromAPI(videoId);
    }
}

videoPlayer.addEventListener('timeupdate', () => {
    if (isLiveStream) { updateLiveBadge(); return; }
    if (typeof checkSponsorBlock === 'function') checkSponsorBlock(videoPlayer.currentTime);
    // Position saving is handled by _broadcastPlayerState (throttled 1x/sec via WS)
});

// ── Event Listeners ─────────────────────────────────────────────────────────

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

document.getElementById('logo-link').addEventListener('click', (e) => {
    e.preventDefault();
    history.pushState({ view: 'home' }, '', '/');
    document.title = 'PYTR';
    showListView();
    loadHomeTab();
});

videoPlayer.addEventListener('error', () => {
    console.log('Video error:', videoPlayer.error && videoPlayer.error.message);
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

// ── Summarize Button ────────────────────────────────────────────────────────

const summarizeBtnContainer = document.getElementById('summarize-btn-container');
const summarizeBtn = document.getElementById('summarize-btn');
const summarizeMenu = document.getElementById('summarize-menu');

function parseVTT(vttText) {
    const lines = vttText
        .replace(/^WEBVTT.*$/m, '')
        .replace(/Kind:.*$/gm, '')
        .replace(/Language:.*$/gm, '')
        .replace(/^\d+$/gm, '')
        .replace(/\d{2}:\d{2}[:\.][\d.]+ --> \d{2}:\d{2}[:\.][\d.]+.*$/gm, '')
        .replace(/<[^>]+>/g, '')
        .split('\n')
        .map(l => l.trim())
        .filter(Boolean);
    // Deduplicate consecutive identical lines (YouTube karaoke overlap)
    const deduped = [];
    for (const line of lines) {
        if (deduped.length === 0 || line !== deduped[deduped.length - 1]) {
            deduped.push(line);
        }
    }
    return deduped.join('\n');
}

function getBestSubtitleLang() {
    if (!subtitleTracks || subtitleTracks.length === 0) return null;
    // Prefer active subtitle
    const saved = localStorage.getItem('subtitle_lang');
    if (saved && saved !== 'off') {
        const match = subtitleTracks.find(t => t.lang === saved);
        if (match) return match.lang;
    }
    // Prefer manual English over auto
    const manualEn = subtitleTracks.find(t => (t.lang === 'en' || t.lang.startsWith('en-')) && !t.auto);
    if (manualEn) return manualEn.lang;
    const autoEn = subtitleTracks.find(t => (t.lang === 'en' || t.lang.startsWith('en-')));
    if (autoEn) return autoEn.lang;
    // Prefer any manual track
    const manual = subtitleTracks.find(t => !t.auto);
    if (manual) return manual.lang;
    return subtitleTracks[0].lang;
}

async function getSummarizePrompt() {
    const lang = getBestSubtitleLang();
    if (!lang || !currentVideoId) return null;
    try {
        const resp = await fetch(`/api/subtitle/${currentVideoId}?lang=${encodeURIComponent(lang)}`);
        if (!resp.ok) return null;
        const vtt = await resp.text();
        const text = parseVTT(vtt);
        if (!text) return null;
        return 'Summarize this: ' + text;
    } catch (e) {
        return null;
    }
}

function updateSummarizeVisibility() {
    const show = !document.body.classList.contains('tv-nav-active') && subtitleTracks && subtitleTracks.length > 0;
    summarizeBtnContainer.classList.toggle('hidden', !show);
}


const summarizeOptions = [
    { label: '📋 Copy to Clipboard', url: null },
    { label: '🤖 Copy & open ChatGPT', url: 'https://chatgpt.com' },
    { label: '🧠 Copy & open Claude', url: 'https://claude.ai' },
    { label: '🔍 Copy & open Perplexity', url: 'https://www.perplexity.ai' },
    { label: '⚡ Copy & open Z.ai', url: 'https://chat.z.ai' },
];

function renderSummarizeMenu() {
    summarizeMenu.innerHTML = summarizeOptions.map((opt, i) =>
        `<div class="summarize-option" data-idx="${i}">${escapeHtml(opt.label)}</div>`
    ).join('');

    summarizeMenu.querySelectorAll('.summarize-option').forEach(el => {
        el.addEventListener('click', async (e) => {
            e.stopPropagation();
            summarizeMenu.classList.add('hidden');
            const opt = summarizeOptions[parseInt(el.dataset.idx)];
            summarizeBtn.textContent = 'TL;DW …';
            summarizeBtn.disabled = true;
            const prompt = await getSummarizePrompt();
            summarizeBtn.textContent = 'TL;DW';
            summarizeBtn.disabled = false;
            if (!prompt) {
                nativeAlert('Could not fetch transcript.');
                return;
            }
            navigator.clipboard.writeText(prompt).then(() => {
                if (opt.url) window.open(opt.url, '_blank');
            });
        });
    });
}

summarizeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasOpen = !summarizeMenu.classList.contains('hidden');
    closeAllMenus();
    if (!wasOpen) {
        renderSummarizeMenu();
        summarizeMenu.classList.remove('hidden');
    }
});

summarizeMenu.addEventListener('click', (e) => e.stopPropagation());

// ── WebSocket (Remote Control) ───────────────────────────────────────────────

let _ws = null;
let _wsReconnectTimer = null;
let _wsStateThrottle = null;
let _wsConnected = false;

function connectWebSocket() {
    if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    _ws = new WebSocket(`${proto}//${location.host}/api/ws`);

    _ws.onopen = () => {
        _wsConnected = true;
        if (_wsReconnectTimer) { clearTimeout(_wsReconnectTimer); _wsReconnectTimer = null; }
        console.log('WebSocket connected');
        if (typeof _onWsReconnected === 'function') _onWsReconnected();
    };

    _ws.onclose = () => {
        _wsConnected = false;
        _ws = null;
        if (typeof _onWsDisconnected === 'function') _onWsDisconnected();
        // Auto-reconnect after 5s
        if (!_wsReconnectTimer) {
            _wsReconnectTimer = setTimeout(() => { _wsReconnectTimer = null; connectWebSocket(); }, 5000);
        }
    };

    _ws.onerror = () => { /* onclose will fire */ };

    _ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            _handleWsMessage(msg);
        } catch (e) {
            console.warn('WS message parse error:', e);
        }
    };
}

function wsSend(data) {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify(data));
    }
}

function _handleWsMessage(msg) {
    const type = msg.type;

    if (type === 'command') {
        _handleRemoteCommand(msg);
    } else if (type === 'remote_connected') {
        _showRemoteToast(`Remote connected: ${msg.remote_name}`);
    } else if (type === 'remote_disconnected') {
        _showRemoteToast('Remote disconnected');
    } else if (type === 'state' && typeof _handleRemoteState === 'function') {
        _handleRemoteState(msg);
    } else if (type === 'paired' && typeof _handlePaired === 'function') {
        _handlePaired(msg);
    } else if (type === 'target_disconnected' && typeof _handleTargetDisconnected === 'function') {
        _handleTargetDisconnected();
    } else if (type === 'error') {
        console.warn('WS error:', msg.message);
        if (typeof _handleRemoteError === 'function') _handleRemoteError(msg.message);
    }
}

function _handleRemoteCommand(msg) {
    const action = msg.action;
    if (action === 'play') {
        // Navigate to the video and play it
        const videoId = msg.videoId;
        const title = msg.title || '';
        const channel = msg.channel || '';
        const duration = msg.duration || 0;
        const startTime = msg.startTime || 0;
        if (videoId) {
            if (currentVideoId !== videoId) {
                cacheListView();
                const qs = msg.playlistId ? `v=${videoId}&list=${msg.playlistId}` : `v=${videoId}`;
                history.pushState({ view: 'video', videoId, title, channel, duration }, '', `/watch?${qs}`);
                document.title = title ? `${title} - PYTR` : 'PYTR';
                showVideoView();
            }
            // Always (re)play — handles both new video and reviving a dead stream
            playVideo(videoId, title, channel, duration, startTime);
            if (msg.playlistId) {
                _loadQueue(videoId, msg.playlistId);
            }
        }
    } else if (action === 'pause') {
        videoPlayer.pause();
    } else if (action === 'resume') {
        videoPlayer.play();
    } else if (action === 'seek') {
        if (typeof msg.time === 'number') videoPlayer.currentTime = msg.time;
    } else if (action === 'volume') {
        if (typeof msg.level === 'number') videoPlayer.volume = Math.max(0, Math.min(1, msg.level));
    } else if (action === 'queue_next') {
        if (typeof _queue !== 'undefined' && _queue && typeof _playQueueItem === 'function') {
            _playQueueItem(_queue.currentIndex + 1);
        }
    } else if (action === 'queue_prev') {
        if (typeof _queue !== 'undefined' && _queue && typeof _playQueueItem === 'function') {
            _playQueueItem(_queue.currentIndex - 1);
        }
    }
}

function _broadcastPlayerState() {
    if (!_wsConnected || !currentVideoId) return;
    wsSend({
        type: 'state',
        videoId: currentVideoId,
        title: videoTitle.textContent || '',
        channel: videoChannel.textContent || '',
        thumbnail: `https://img.youtube.com/vi/${currentVideoId}/hqdefault.jpg`,
        currentTime: videoPlayer.currentTime || 0,
        duration: videoPlayer.duration || 0,
        paused: videoPlayer.paused,
        volume: videoPlayer.volume,
        ended: videoPlayer.ended,
    });
}

function _throttledBroadcast() {
    if (_wsStateThrottle) return;
    _broadcastPlayerState();
    _wsStateThrottle = setTimeout(() => { _wsStateThrottle = null; }, 1000);
}

// Hook into player events for state broadcasting
videoPlayer.addEventListener('timeupdate', _throttledBroadcast);
videoPlayer.addEventListener('pause', () => _broadcastPlayerState());
videoPlayer.addEventListener('play', () => _broadcastPlayerState());
videoPlayer.addEventListener('ended', () => _broadcastPlayerState());
videoPlayer.addEventListener('seeked', () => _broadcastPlayerState());

let _remoteToastTimer = null;
function _showRemoteToast(text) {
    let el = document.getElementById('remote-toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'remote-toast';
        el.className = 'remote-toast';
        document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add('visible');
    if (_remoteToastTimer) clearTimeout(_remoteToastTimer);
    _remoteToastTimer = setTimeout(() => el.classList.remove('visible'), 4000);
}

// Boot — called from index.html after all scripts load
