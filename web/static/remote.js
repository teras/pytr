// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Remote Control mode: device list, pairing, mini-player

let _remoteMode = false;
let _pairedDeviceName = null;
let _pairedDeviceId = null;   // for re-pairing after reconnect
let _remoteMiniPlayer = null;
let _remoteState = null;  // latest state from target
let _remoteSeeking = false;
let _remoteWsConnected = true; // tracks WS connection status for UI
let _remoteRepairing = false;  // true while auto-retrying pair after reconnect
let _remoteRepairTimer = null; // retry timer for re-pairing
let _remoteHasFreshState = false; // true once we get state from target after (re-)pair

// ── Enter/Exit Remote Mode ──────────────────────────────────────────────────

async function enterRemoteMode() {
    _remoteMode = true;
    document.body.classList.add('remote-mode');

    // Show device list in the main content area
    showListView();
    listHeader.classList.remove('hidden');
    listTitle.textContent = 'Remote Control';
    listTitle.classList.remove('hidden');
    listTabs.classList.add('hidden');
    clearListBtn.classList.add('hidden');

    videoGrid.innerHTML = '<p class="loading-text">Looking for devices...</p>';
    noResults.classList.add('hidden');
    loadMoreContainer.classList.add('hidden');

    try {
        const resp = await fetch('/api/remote/devices');
        if (!resp.ok) throw new Error('Failed to fetch devices');
        const devices = await resp.json();

        if (devices.length === 0) {
            videoGrid.innerHTML = `
                <div class="remote-empty">
                    <div class="remote-empty-icon">📡</div>
                    <p>No other devices online</p>
                    <p class="remote-empty-hint">Open PYTR on another device with the same profile to control it remotely.</p>
                    <button class="remote-refresh-btn" onclick="enterRemoteMode()">Refresh</button>
                    <button class="remote-exit-btn" onclick="exitRemoteMode()">Exit Remote</button>
                </div>`;
            return;
        }

        videoGrid.innerHTML = `
            <div class="remote-device-grid">
                ${devices.map(d => `
                    <div class="remote-device-card" data-device-id="${escapeHtml(d.device_id)}">
                        <div class="remote-device-icon">${_deviceIcon(d.device_name)}</div>
                        <div class="remote-device-name">${escapeHtml(d.device_name || 'Unknown Device')}</div>
                        ${d.has_state ? '<div class="remote-device-status">Now playing</div>' : '<div class="remote-device-status">Idle</div>'}
                    </div>
                `).join('')}
            </div>
            <div class="remote-controls-footer">
                <button class="remote-refresh-btn" onclick="enterRemoteMode()">Refresh</button>
                <button class="remote-exit-btn" onclick="exitRemoteMode()">Exit Remote</button>
            </div>`;

        // Click handlers
        videoGrid.querySelectorAll('.remote-device-card').forEach(card => {
            card.addEventListener('click', () => {
                const deviceId = card.dataset.deviceId;
                _pairWithDevice(deviceId);
            });
        });
    } catch (e) {
        videoGrid.innerHTML = `
            <div class="remote-empty">
                <p>Failed to load devices</p>
                <button class="remote-refresh-btn" onclick="enterRemoteMode()">Retry</button>
                <button class="remote-exit-btn" onclick="exitRemoteMode()">Exit Remote</button>
            </div>`;
    }
}

function exitRemoteMode() {
    if (_pairedDeviceName) {
        remoteDisconnect();
    }
    _remoteMode = false;
    _remoteState = null;
    document.body.classList.remove('remote-mode');
    _removeMiniPlayer();
    _removeHeaderDisconnect();

    // Go home
    history.pushState({ view: 'home' }, '', '/');
    document.title = 'PYTR';
    showListView();
    if (typeof loadHomeTab === 'function') loadHomeTab();
}

function _pairWithDevice(deviceId) {
    _pairedDeviceId = deviceId;
    wsSend({ type: 'pair', device_id: deviceId });
}

function _resetPairing() {
    _pairedDeviceName = null;
    _pairedDeviceId = null;
    _remoteState = null;
    _remoteRepairing = false;
    if (_remoteRepairTimer) { clearTimeout(_remoteRepairTimer); _remoteRepairTimer = null; }
    _removeMiniPlayer();
    _removeHeaderDisconnect();
}

function remoteDisconnect() {
    wsSend({ type: 'unpair' });
    _resetPairing();
}

// ── Handlers (called from app.js WS message handler) ────────────────────────

function _handlePaired(msg) {
    _remoteRepairing = false;
    if (_remoteRepairTimer) { clearTimeout(_remoteRepairTimer); _remoteRepairTimer = null; }
    _remoteHasFreshState = false;
    _pairedDeviceName = msg.device_name || 'Device';
    _showRemoteToast(`Controlling: ${_pairedDeviceName}`);
    _createMiniPlayer();
    _createHeaderDisconnect();
    if (_remoteMiniPlayer) _remoteMiniPlayer.classList.remove('rmp-disconnected');
    if (msg.state) {
        _remoteState = msg.state;
        _remoteHasFreshState = true;
    }
    _updateMiniPlayer();
    // Return to normal browsing (keep remote mode active)
    history.pushState({ view: 'home' }, '', '/');
    document.title = 'PYTR';
    showListView();
    if (typeof loadHomeTab === 'function') loadHomeTab();
}

function _handleRemoteState(msg) {
    _remoteState = msg;
    _remoteHasFreshState = true;
    _updateMiniPlayer();
}

function _handleTargetDisconnected() {
    _resetPairing();
    _showRemoteToast('Target device disconnected');
    if (_remoteMode) enterRemoteMode();
}

function _handleRemoteError(message) {
    // If we're auto-retrying after reconnect, retry silently
    if (_remoteRepairing) {
        _scheduleRepair();
        return;
    }
    _showRemoteToast(message);
    if (_pairedDeviceName) {
        _resetPairing();
        if (_remoteMode) enterRemoteMode();
    }
}

// ── Video intercept ─────────────────────────────────────────────────────────

// Called from navigateToVideo() in app.js when in remote mode
function _remotePlayVideo(videoId, title, channel, duration) {
    wsSend({
        type: 'command',
        action: 'play',
        videoId: videoId,
        title: title || '',
        channel: channel || '',
        duration: duration || 0,
        thumbnail: `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
    });
    // Update mini-player immediately with what we know
    _remoteState = {
        videoId, title: title || '', channel: channel || '',
        thumbnail: `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
        currentTime: 0, duration: duration || 0, paused: false, volume: 1, ended: false,
    };
    _updateMiniPlayer();
}

// ── Mini-player ─────────────────────────────────────────────────────────────

// Stored references for cleanup
let _rmpDocListeners = [];

function _createMiniPlayer() {
    if (_remoteMiniPlayer) return;
    _remoteMiniPlayer = document.createElement('div');
    _remoteMiniPlayer.id = 'remote-mini-player';
    _remoteMiniPlayer.className = 'remote-mini-player';
    if (!_remoteWsConnected) _remoteMiniPlayer.classList.add('rmp-disconnected');
    _remoteMiniPlayer.innerHTML = `
        <div class="rmp-row rmp-row-info">
            <div class="rmp-thumb-wrap">
                <img class="rmp-thumb" src="" alt="">
            </div>
            <div class="rmp-info">
                <div class="rmp-title">Not playing</div>
                <div class="rmp-channel"></div>
            </div>
        </div>
        <div class="rmp-row rmp-row-seek">
            <button class="rmp-btn rmp-play-btn" title="Play/Pause">${svgIcon(SVG_PLAY, 'rmp-icon')}</button>
            <div class="rmp-time rmp-current">0:00</div>
            <div class="rmp-bar">
                <div class="rmp-bar-track"><div class="rmp-bar-fill"></div></div>
            </div>
            <div class="rmp-time rmp-duration">0:00</div>
        </div>
    `;
    document.body.appendChild(_remoteMiniPlayer);

    // Play/pause
    _remoteMiniPlayer.querySelector('.rmp-play-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        // If no fresh state from target, the player is likely dead (e.g. server restart)
        // Re-send the video to revive it
        if (!_remoteHasFreshState && _remoteState && _remoteState.videoId) {
            wsSend({
                type: 'command', action: 'play',
                videoId: _remoteState.videoId,
                title: _remoteState.title || '',
                channel: _remoteState.channel || '',
                duration: _remoteState.duration || 0,
                thumbnail: _remoteState.thumbnail || '',
                startTime: _remoteState.currentTime || 0,
            });
            return;
        }
        if (_remoteState && _remoteState.paused) {
            wsSend({ type: 'command', action: 'resume' });
        } else {
            wsSend({ type: 'command', action: 'pause' });
        }
    });

    // Draggable seek
    const bar = _remoteMiniPlayer.querySelector('.rmp-bar');
    let dragging = false;

    bar.addEventListener('mousedown', (e) => { dragging = true; _remoteSeeking = true; });
    bar.addEventListener('touchstart', (e) => { dragging = true; _remoteSeeking = true; }, { passive: true });

    const onMove = (clientX) => {
        if (!dragging || !_remoteState || !_remoteState.duration) return;
        const rect = bar.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        const fill = _remoteMiniPlayer.querySelector('.rmp-bar-fill');
        fill.style.width = (pct * 100) + '%';
        _remoteMiniPlayer.querySelector('.rmp-current').innerHTML = formatTime(pct * _remoteState.duration, _remoteState.duration);
    };

    const onMouseMove = (e) => onMove(e.clientX);
    const onTouchMove = (e) => { if (dragging) onMove(e.touches[0].clientX); };

    const onEnd = (clientX) => {
        if (!dragging) return;
        dragging = false;
        _remoteSeeking = false;
        if (_remoteState && _remoteState.duration) {
            const rect = bar.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            wsSend({ type: 'command', action: 'seek', time: pct * _remoteState.duration });
        }
    };

    const onMouseUp = (e) => onEnd(e.clientX);
    const onTouchEnd = (e) => { if (dragging) onEnd(e.changedTouches[0].clientX); };

    // Click on bar (only if not dragging — prevent double seek)
    bar.addEventListener('click', (e) => {
        if (_remoteSeeking) return;
        if (!_remoteState || !_remoteState.duration) return;
        const rect = bar.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        wsSend({ type: 'command', action: 'seek', time: pct * _remoteState.duration });
    });

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('touchmove', onTouchMove, { passive: true });
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchend', onTouchEnd);

    // Store for cleanup
    _rmpDocListeners = [
        ['mousemove', onMouseMove],
        ['touchmove', onTouchMove],
        ['mouseup', onMouseUp],
        ['touchend', onTouchEnd],
    ];
}

function _removeMiniPlayer() {
    if (_remoteMiniPlayer) {
        _remoteMiniPlayer.remove();
        _remoteMiniPlayer = null;
    }
    for (const [evt, fn] of _rmpDocListeners) {
        document.removeEventListener(evt, fn);
    }
    _rmpDocListeners = [];
}

function _updateMiniPlayer() {
    if (!_remoteMiniPlayer || !_remoteState) return;
    const s = _remoteState;

    const thumb = _remoteMiniPlayer.querySelector('.rmp-thumb');
    if (s.thumbnail) {
        thumb.src = s.thumbnail;
        thumb.style.display = '';
    } else {
        thumb.style.display = 'none';
    }

    _remoteMiniPlayer.querySelector('.rmp-title').textContent = s.title || 'Not playing';
    _remoteMiniPlayer.querySelector('.rmp-channel').textContent = s.channel || '';

    if (!_remoteSeeking) {
        _remoteMiniPlayer.querySelector('.rmp-current').innerHTML = formatTime(s.currentTime || 0, s.duration);
        const pct = s.duration > 0 ? ((s.currentTime || 0) / s.duration) * 100 : 0;
        _remoteMiniPlayer.querySelector('.rmp-bar-fill').style.width = pct + '%';
    }
    _remoteMiniPlayer.querySelector('.rmp-duration').textContent = formatTime(s.duration || 0);

    const playBtn = _remoteMiniPlayer.querySelector('.rmp-play-btn');
    // If no fresh state from target, assume player is dead → show play icon
    const isPaused = !_remoteHasFreshState || s.paused;
    playBtn.innerHTML = isPaused
        ? svgIcon(SVG_PLAY, 'rmp-icon')
        : svgIcon(SVG_PAUSE, 'rmp-icon');
}

// ── Header disconnect button ────────────────────────────────────────────────

function _createHeaderDisconnect() {
    _removeHeaderDisconnect();
    const profileBtn = document.getElementById('profile-switcher-btn');
    if (!profileBtn) return;

    // Wrap X + profile in a group so they stay together
    const wrap = document.createElement('div');
    wrap.id = 'remote-disconnect-wrap';
    wrap.className = 'remote-disconnect-wrap';

    const btn = document.createElement('button');
    btn.className = 'remote-disconnect-header-btn';
    btn.title = 'Disconnect remote';
    btn.textContent = '✕';
    btn.addEventListener('click', () => {
        remoteDisconnect();
        if (_remoteMode) enterRemoteMode();
    });

    profileBtn.parentNode.insertBefore(wrap, profileBtn);
    wrap.appendChild(btn);
    wrap.appendChild(profileBtn);
}

function _removeHeaderDisconnect() {
    const wrap = document.getElementById('remote-disconnect-wrap');
    if (!wrap) return;
    // Move profile button back to header
    const profileBtn = document.getElementById('profile-switcher-btn');
    if (profileBtn) {
        wrap.parentNode.insertBefore(profileBtn, wrap);
    }
    wrap.remove();
}

// ── WS connection status ────────────────────────────────────────────────────

function _onWsDisconnected() {
    _remoteWsConnected = false;
    if (_remoteMiniPlayer) {
        _remoteMiniPlayer.classList.add('rmp-disconnected');
    }
}

function _onWsReconnected() {
    _remoteWsConnected = true;
    // Don't remove rmp-disconnected yet — wait for successful re-pair
    // Re-pair with the same device if we were paired
    if (_remoteMode && _pairedDeviceId) {
        _remoteRepairing = true;
        wsSend({ type: 'pair', device_id: _pairedDeviceId });
    } else if (_remoteMiniPlayer) {
        _remoteMiniPlayer.classList.remove('rmp-disconnected');
    }
}

function _scheduleRepair() {
    if (_remoteRepairTimer) return;
    _remoteRepairTimer = setTimeout(() => {
        _remoteRepairTimer = null;
        if (_remoteMode && _pairedDeviceId && _remoteWsConnected) {
            wsSend({ type: 'pair', device_id: _pairedDeviceId });
        }
    }, 5000);
}

function _deviceIcon(name) {
    if (!name) return '🖥️';
    const n = name.toLowerCase();
    if (n.includes('tv') || n.includes('webos') || n.includes('tizen')) return '📺';
    if (n.includes('iphone') || n.includes('android phone')) return '📱';
    if (n.includes('ipad') || n.includes('tablet')) return '📱';
    if (n.includes('mac')) return '💻';
    if (n.includes('linux') || n.includes('windows')) return '🖥️';
    return '🖥️';
}
