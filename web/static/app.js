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

// ── Initialize OSD ──────────────────────────────────────────────────────────
window._osd.init({
    getVideo: () => document.getElementById('video-player'),
    isVideoView: () => { const vv = document.getElementById('video-view'); return vv && !vv.classList.contains('hidden'); },
    containerId: 'player-container',
});

// ── State ───────────────────────────────────────────────────────────────────

let currentVideoId = null;
let currentVideoChannelId = null;
let dashPlayer = null;
let hlsPlayer = null;
let currentPlayerType = null; // 'dash' | 'hls'
let currentAudioLang = null; // current HLS audio language
let hlsAudioTracks = []; // [{lang, default}]
window.currentChapters = []; // [{title, start_time, end_time}]
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
            applyQualitySwitch(entry);
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

function applyQualitySwitch(entry) {
    switchToQuality(currentPlayerType, dashPlayer, hlsPlayer, entry);
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
    if (!wasOpen) { qualityMenu.classList.remove('hidden'); positionMenu(qualityMenu); }
});

qualityMenu.addEventListener('click', (e) => e.stopPropagation());

// ── Audio Selector ──────────────────────────────────────────────────────────

audioBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasOpen = !audioMenu.classList.contains('hidden');
    closeAllMenus();
    if (!wasOpen) { audioMenu.classList.remove('hidden'); positionMenu(audioMenu); }
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
    document.querySelectorAll('.drop-up').forEach(el => el.classList.remove('drop-up'));
}

function positionMenu(menu) {
    menu.classList.remove('drop-up');
    const rect = menu.parentElement.getBoundingClientRect();
    const menuHeight = menu.offsetHeight;
    if (rect.bottom + menuHeight + 8 > window.innerHeight) {
        menu.classList.add('drop-up');
    }
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
    _hideSubTabs();
    _currentMainTab = null;
    if (typeof _removeFollowButton === 'function') _removeFollowButton();
}

function _tvAutoFocus() {
    if (typeof _tvSetFocus === 'function' && window._tv && window._tv.isTvActive()) {
        if (window._tv.isPlayerMode()) window._tv.exitPlayerMode();
        var el = document.getElementById('logo-link');
        if (el) _tvSetFocus(el);
    }
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
    // Stop any in-progress video loading/playback when navigating away from video
    if (!(e.state && e.state.view === 'video') && currentVideoId) stopPlayer();
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
            listViewCache = null;
        } else if (e.state.query) {
            searchVideos(e.state.query, { pushState: false });
        }
    } else {
        document.title = 'PYTR';
        const view = e.state && e.state.view;
        if (listViewCache) {
            const cachedTab = listViewCache.mainTab;
            showListView();
            restoreListCache();
            listViewCache = null;
            if (cachedTab) {
                _currentMainTab = cachedTab;
                _activateMainTab(cachedTab);
            }
        } else if (view === 'history' || view === 'favorites' || view === 'discover') {
            showListView();
            _loadMainTab(view);
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

    if (path === '/watch' && params.get('v') && params.get('v').startsWith('PL')) {
        // Playlist ID passed as video ID — redirect to playlist view
        window.location.replace(`/playlist?list=${encodeURIComponent(params.get('v'))}`);
    } else if (path === '/watch' && params.get('v')) {
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
        const parts = path.slice(2).split('/'); // remove /@
        const handle = parts[0];
        const suffix = parts[1] || '';
        const tab = suffix === 'playlists' ? 'playlists' : 'videos';
        showListView();
        resolveHandleAndLoad(handle, tab);
    } else if (path.startsWith('/channel/')) {
        const parts = path.slice('/channel/'.length).split('/');
        const channelId = parts[0];
        const suffix = parts[1] || '';
        const isPlaylists = suffix === 'playlists';
        const tab = isPlaylists ? 'playlists' : 'videos';
        const cleanPath = isPlaylists ? `/channel/${channelId}/playlists` : `/channel/${channelId}`;
        history.replaceState({ view: 'channel', channelId, channelName: '', tab }, '', cleanPath);
        showListView();
        if (isPlaylists) {
            loadChannelPlaylists(channelId, '');
        } else {
            loadChannelVideos(channelId, '');
        }
    } else if (path === '/playlist' && params.get('list')) {
        // Server resolves first video via yt-dlp and redirects to /watch?v=...&list=...
        window.location.replace(`/playlist?list=${encodeURIComponent(params.get('list'))}`);
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
        _loadMainTab('history');
    } else if (path === '/favorites') {
        history.replaceState({ view: 'favorites' }, '', '/');
        showListView();
        _loadMainTab('favorites');
    } else if (path === '/channels') {
        history.replaceState({ view: 'favorites' }, '', '/');
        showListView();
        localStorage.setItem('lastFavSub', 'fav-channels');
        _loadMainTab('favorites');
    } else if (path === '/remote') {
        history.replaceState({ view: 'home' }, '', '/');
        showListView();
        if (typeof enterRemoteMode === 'function') enterRemoteMode();
    } else {
        // Home page = remembered tab
        showListView();
        loadHomeTab();
        _tvAutoFocus();
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
                thumbnail: item.thumbnail || thumbUrl(item.video_id),
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

const subTabs = document.getElementById('sub-tabs');

const _discoverCategories = [
    { key: 'news', label: 'News' },
    { key: 'sports', label: 'Sports' },
    { key: 'gaming', label: 'Gaming' },
    { key: 'music', label: 'Music' },
    { key: 'live', label: 'Live' },
];

const _favSubTabs = [
    { key: 'fav-videos', label: 'Videos' },
    { key: 'fav-playlists', label: 'Playlists' },
    { key: 'fav-channels', label: 'Channels' },
];

let _currentMainTab = null;

function _buildSubTabs(items, onSelect) {
    subTabs.innerHTML = '';
    subTabs.classList.remove('hidden');
    items.forEach(({ key, label }) => {
        const btn = document.createElement('button');
        btn.className = 'sub-tab';
        btn.dataset.subtab = key;
        btn.textContent = label;
        btn.onclick = () => onSelect(key);
        subTabs.appendChild(btn);
    });
}

function _activateSubTab(key) {
    subTabs.querySelectorAll('.sub-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.subtab === key);
    });
}

function _hideSubTabs() {
    subTabs.innerHTML = '';
    subTabs.classList.add('hidden');
}

function _activateMainTab(tab) {
    listTabs.classList.remove('hidden');
    listTitle.classList.add('hidden');
    listTabs.querySelectorAll('.list-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
        btn.onclick = () => {
            if (btn.dataset.tab === tab) return;
            _switchMainTab(btn.dataset.tab);
        };
    });
}

function _switchMainTab(tab) {
    localStorage.setItem('lastHomeTab', tab);
    history.replaceState({ view: tab }, '', '/');
    _loadMainTab(tab);
}

function _loadMainTab(tab) {
    const tabChanged = _currentMainTab !== tab;
    _currentMainTab = tab;
    _activateMainTab(tab);

    if (tab === 'discover') {
        if (tabChanged) _buildSubTabs(_discoverCategories, _loadDiscoverSub);
        const subKey = localStorage.getItem('lastDiscoverSub') || 'news';
        _loadDiscoverSub(subKey);
    } else if (tab === 'favorites') {
        if (tabChanged) _buildSubTabs(_favSubTabs, _loadFavoritesSub);
        const subKey = localStorage.getItem('lastFavSub') || 'fav-videos';
        _loadFavoritesSub(subKey);
    } else {
        _hideSubTabs();
        _favFilters = { video: true, playlist: true, mix: true };
        loadListPage('/api/profiles/history?limit=50', 'Watch History', {showClear: true, removable: true, clearEndpoint: '/api/profiles/history', clearPrompt: 'Clear all watch history?', keepTabs: true});
    }
}

function _loadDiscoverSub(subKey) {
    localStorage.setItem('lastDiscoverSub', subKey);
    _activateSubTab(subKey);
    _loadTrendingContent(subKey);
}

async function _loadTrendingContent(category) {
    if (typeof _removeChannelTabs === 'function') _removeChannelTabs();
    if (typeof _removeFilterToggles === 'function') _removeFilterToggles();
    _removeFavFilterToggles();
    listHeader.classList.remove('hidden');
    clearListBtn.classList.add('hidden');
    videoGrid.innerHTML = '<div class="loading-more"><div class="loading-spinner"></div></div>';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');
    _listGeneration++;
    loadMoreObserver.disconnect();
    searchInput.value = '';

    try {
        const lang = localStorage.getItem('contentLang') || 'auto';
        const region = localStorage.getItem('contentRegion') || 'auto';
        const params = new URLSearchParams();
        if (lang !== 'auto') params.set('hl', lang);
        if (region !== 'auto') params.set('gl', region);
        const qs = params.toString();
        const resp = await fetch(`/api/trending/${category}${qs ? '?' + qs : ''}`);
        if (!resp.ok) throw new Error('Failed to load trending');
        const data = await resp.json();
        const items = data.results || [];
        if (items.length === 0) {
            videoGrid.innerHTML = '';
            noResults.classList.remove('hidden');
        } else {
            renderVideos(items);
        }
    } catch (err) {
        videoGrid.innerHTML = `<p class="error">Error: ${escapeHtml(err.message)}</p>`;
    }
}

function _loadFavoritesSub(subKey) {
    localStorage.setItem('lastFavSub', subKey);
    _activateSubTab(subKey);

    if (subKey === 'fav-channels') {
        loadChannelsPage();
    } else if (subKey === 'fav-playlists') {
        _favFilters = { video: false, playlist: true, mix: true };
        loadFavoritesPage();
    } else {
        _favFilters = { video: true, playlist: false, mix: false };
        loadFavoritesPage();
    }
}

function _removeFavFilterToggles() {
    const existing = document.getElementById('fav-filter-toggles');
    if (existing) existing.remove();
}

async function loadFavoritesPage() {
    if (typeof _removeChannelTabs === 'function') _removeChannelTabs();
    if (typeof _removeFilterToggles === 'function') _removeFilterToggles();
    listHeader.classList.remove('hidden');
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
        const allResp = await fetch('/api/profiles/favorites?limit=200');
        if (!allResp.ok) throw new Error('Failed to load favorites');
        const allItems = await allResp.json();

        // Apply client-side filter based on active sub-tab
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
                thumbnail: item.thumbnail || thumbUrl(item.first_video_id || item.video_id),
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
            thumbnail: item.thumbnail || thumbUrl(item.video_id),
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
                const initial = escapeHtml(ch.channel_name.charAt(0).toUpperCase());
                const placeholder = `<div class="channel-card-placeholder">${initial}</div>`;
                const avatar = ch.avatar_url
                    ? `<img data-proxy-src="${escapeAttr(proxyImageUrl(ch.avatar_url))}" alt="${escapeHtml(ch.channel_name)}" loading="lazy"><template>${placeholder}</template>`
                    : placeholder;
                return `<a class="video-card channel-card" href="/channel/${escapeAttr(ch.channel_id)}" data-channel-id="${escapeAttr(ch.channel_id)}" data-channel-name="${escapeAttr(ch.channel_name)}">
                    <div class="thumbnail-container channel-avatar-container">
                        ${avatar}
                    </div>
                    <div class="video-info">
                        <h3 class="video-title">${escapeHtml(ch.channel_name)}</h3>
                    </div>
                </a>`;
            }).join('');

            // Load avatars via fetch (needed for iframe/Bearer auth)
            videoGrid.querySelectorAll('img[data-proxy-src]').forEach(loadProxyImage);

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

function loadHistory() { _switchMainTab('history'); }
function loadFavorites() { _switchMainTab('favorites'); }
function loadChannels() { _switchMainTab('favorites'); localStorage.setItem('lastFavSub', 'fav-channels'); }

function loadHomeTab() {
    const tab = localStorage.getItem('lastHomeTab') || 'discover';
    history.replaceState({ view: tab }, '', '/');
    _loadMainTab(tab);
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
    document.getElementById('private-mode-btn-player').classList.add('hidden');
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
    document.getElementById('private-mode-btn-player').classList.add('hidden');
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
    window.currentChapters = [];
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
    document.getElementById('private-mode-btn-player').classList.remove('hidden');
    qualityBtn.textContent = '\ud83c\udfac \u2014';
    qualityBtn.disabled = true;
    audioBtnContainer.classList.add('hidden');
    relatedVideos.innerHTML = '';

    videoPlayer.dataset.expectedDuration = duration || 0;
    // Restore saved volume
    const savedVol = localStorage.getItem('volume');
    if (savedVol !== null) videoPlayer.volume = parseFloat(savedVol);
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
        // Start SponsorBlock fetch early (in parallel with info)
        if (typeof initSponsorBlock === 'function') initSponsorBlock(videoId);

        const resp = await fetch(appendCookieParam(`/api/info/${videoId}`));
        if (currentVideoId !== videoId) return; // user navigated away
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
        window.currentChapters = info.chapters || [];
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
                    if (currentVideoId !== videoId) return;
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
        videoQualities = buildQualitiesDash(dashPlayer);
        if (videoQualities.length === 0) return;

        const heights = videoQualities.map(q => q.height);
        const targetHeight = getTargetQuality(heights, preferredQuality);
        const targetEntry = videoQualities.find(q => q.height === targetHeight);

        if (targetEntry) {
            applyQualitySwitch(targetEntry);
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
        if (!e.error || currentVideoId !== videoId) return;
        if (!_dashAutoRefreshed) {
            _dashAutoRefreshed = true;
            console.warn('DASH error, auto-refreshing session');
            savePosition();
            playVideo(videoId, videoTitle.textContent, videoChannel.textContent, videoPlayer.dataset.expectedDuration);
        } else if (Hls.isSupported()) {
            console.warn('DASH failed again, falling back to HLS');
            dashPlayer.destroy(); dashPlayer = null;
            startHlsPlayer(videoId, appendCookieParam(`/api/hls/master/${videoId}`));
        }
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
        videoQualities = buildQualitiesHls(hlsPlayer);
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
        if (!data.fatal || currentVideoId !== videoId) return;
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

videoPlayer.addEventListener('volumechange', () => {
    localStorage.setItem('volume', videoPlayer.volume);
});

videoPlayer.addEventListener('timeupdate', () => {
    if (isLiveStream) { updateLiveBadge(); return; }
    if (typeof checkSponsorBlock === 'function') checkSponsorBlock(videoPlayer.currentTime);
    // Position saving is handled by _broadcastPlayerState (throttled 1x/sec via WS)
});

// ── Private Mode ────────────────────────────────────────────────────────────

let _privateMode = false;
const _privateModeBtns = [document.getElementById('private-mode-btn'), document.getElementById('private-mode-btn-player')];

function _togglePrivateMode() {
    _privateMode = !_privateMode;
    for (const btn of _privateModeBtns) {
        if (!btn) continue;
        btn.querySelector('.eye-open').classList.toggle('hidden', _privateMode);
        btn.querySelector('.eye-closed').classList.toggle('hidden', !_privateMode);
    }
    if (currentVideoId) {
        if (_privateMode) {
            // Went private: delete current video from history
            fetch(`/api/profiles/history/${currentVideoId}`, { method: 'DELETE' });
        } else if (!videoPlayer.paused) {
            // Went public while playing: save position immediately
            _broadcastPlayerState();
        }
    }
}
for (const btn of _privateModeBtns) {
    if (btn) btn.addEventListener('click', _togglePrivateMode);
}

// ── Event Listeners ─────────────────────────────────────────────────────────

searchBtn.addEventListener('click', () => searchVideos(searchInput.value));
searchInput.addEventListener('keypress', e => e.key === 'Enter' && searchVideos(searchInput.value));

document.getElementById('logo-link').addEventListener('click', (e) => {
    e.preventDefault();
    history.pushState({ view: 'home' }, '', '/');
    document.title = 'PYTR';
    showListView();
    loadHomeTab();
    _tvAutoFocus();
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
    const tsRe = /(\d{2}):(\d{2}):(\d{2})[.\d]* --> /;
    const cues = [];
    let curTime = null;
    for (const raw of vttText.split('\n')) {
        const line = raw.trim();
        if (!line || /^WEBVTT/.test(line) || /^Kind:/.test(line) || /^Language:/.test(line) || /^\d+$/.test(line)) continue;
        const m = line.match(tsRe);
        if (m) {
            const h = parseInt(m[1], 10);
            const mm = parseInt(m[2], 10);
            const ss = parseInt(m[3], 10);
            curTime = h > 0 ? `${h}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${mm}:${String(ss).padStart(2, '0')}`;
            continue;
        }
        const text = line.replace(/<[^>]+>/g, '').trim();
        if (!text) continue;
        cues.push({ time: curTime, text });
    }
    // Deduplicate consecutive identical lines (YouTube karaoke overlap), keep first timestamp
    const deduped = [];
    for (const cue of cues) {
        if (deduped.length === 0 || cue.text !== deduped[deduped.length - 1].text) {
            deduped.push(cue);
        }
    }
    return deduped.map(c => c.time != null ? `[${c.time}] ${c.text}` : c.text).join('\n');
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
        const url = `${location.origin}/watch?v=${currentVideoId}`;
        return `Summarize this video transcript in 1-2 paragraphs (more if needed for longer videos). At the end, list the key topics/sections in this exact format:\n- [MM:SS](${url}&t=SECONDS) Topic title\n\nExample:\n- [0:00](${url}&t=0) Introduction\n- [3:45](${url}&t=225) Main topic\n\nTranscript:\n` + text;
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
        positionMenu(summarizeMenu);
    }
});

summarizeMenu.addEventListener('click', (e) => e.stopPropagation());

// ── WebSocket (Remote Control) ───────────────────────────────────────────────

let _ws = null;
let _wsReconnectTimer = null;
let _wsStateThrottle = null;
let _wsConnected = false;
let _wsReconnectDelay = 500;
let _wsConsecutiveFailures = 0;
let _serverDownNotified = false;
const _tabId = Math.random().toString(36).slice(2, 10);
function connectWebSocket() {
    if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    _ws = new WebSocket(`${proto}//${location.host}/api/ws?tab=${_tabId}`);

    _ws.onopen = () => {
        _wsConnected = true;
        _wsConsecutiveFailures = 0;
        _serverDownNotified = false;
        if (_wsReconnectTimer) { clearTimeout(_wsReconnectTimer); _wsReconnectTimer = null; }
        // _onWsReconnected returns true if auto-repair started (backoff resets on pair success instead)
        const autoRepairing = typeof _onWsReconnected === 'function' && _onWsReconnected();
        if (!autoRepairing) _wsReconnectDelay = 500;
    };

    _ws.onclose = () => {
        _wsConnected = false;
        _ws = null;
        _wsConsecutiveFailures++;
        console.warn('[PYTR] WS onclose, consecutive failures:', _wsConsecutiveFailures);
        if (_wsConsecutiveFailures >= 3 && !_serverDownNotified) {
            console.warn('[PYTR] Server down detected, notifying...');
            _notifyServerDown();
        }
        if (typeof _onWsDisconnected === 'function') _onWsDisconnected();
        // Auto-reconnect with exponential backoff (500ms → 1s → 2s → 4s → 5s max)
        if (!_wsReconnectTimer) {
            _wsReconnectTimer = setTimeout(() => {
                _wsReconnectTimer = null;
                connectWebSocket();
            }, _wsReconnectDelay);
            _wsReconnectDelay = Math.min(_wsReconnectDelay * 2, 5000);
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

// Immediately reconnect when page becomes visible again
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        if (_wsReconnectTimer) { clearTimeout(_wsReconnectTimer); _wsReconnectTimer = null; }
        _wsReconnectDelay = 500;
        connectWebSocket();
    }
});

function _notifyServerDown() {
    _serverDownNotified = true;
    const tvMode = localStorage.getItem('tv-mode');
    console.warn('[PYTR] _notifyServerDown, tv-mode:', tvMode);
    if (tvMode === 'webos') {
        window.parent.postMessage({ type: 'pytr-server-down' }, window._pytrParentOrigin());
    } else if (tvMode === 'android') {
        console.warn('[PYTR] Navigating to pytr://server-down');
        window.location.href = 'pytr://server-down';
    }
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
    const msg = {
        type: 'state',
        videoId: currentVideoId,
        title: videoTitle.textContent || '',
        channel: videoChannel.textContent || '',
        thumbnail: thumbUrl(currentVideoId),
        currentTime: videoPlayer.currentTime || 0,
        duration: videoPlayer.duration || 0,
        paused: videoPlayer.paused,
        volume: videoPlayer.volume,
        ended: videoPlayer.ended,
    };
    if (_privateMode) msg.private = true;
    wsSend(msg);
}

function _throttledBroadcast() {
    if (_wsStateThrottle) return;
    _broadcastPlayerState();
    _wsStateThrottle = setTimeout(() => { _wsStateThrottle = null; }, 1000);
}

// Exclusive playback: pause other tabs/embeds when playing here
exclusivePlayback(videoPlayer, () => currentProfile && currentProfile.exclusive_playback);

// Hook into player events for state broadcasting
videoPlayer.addEventListener('timeupdate', _throttledBroadcast);
videoPlayer.addEventListener('pause', () => _broadcastPlayerState());
videoPlayer.addEventListener('play', () => _broadcastPlayerState());
videoPlayer.addEventListener('ended', () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    _broadcastPlayerState();
});
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
