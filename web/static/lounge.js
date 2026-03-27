/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */

// YouTube Lounge — "Link with TV code" UI
(function () {
    let overlay = null;
    let pollTimer = null;
    let loungeActive = false;

    function createOverlay() {
        if (overlay) { overlay.remove(); overlay = null; }
        overlay = document.createElement('div');
        overlay.className = 'lounge-overlay';
        overlay.innerHTML = `
            <div class="lounge-card">
                <div class="lounge-header">
                    <svg width="28" height="20" viewBox="0 0 28 20" fill="none" style="vertical-align:middle;margin-right:8px">
                        <rect width="28" height="20" rx="3" fill="#ff0000"/>
                        <polygon points="11,5 11,15 20,10" fill="#fff"/>
                    </svg>
                    Link with TV code
                </div>
                <p class="lounge-desc">Open the YouTube app on your phone, go to<br><b>Settings → Watch on TV → Enter TV code</b></p>
                <div class="lounge-code" id="lounge-code">
                    <div class="lounge-loading">Connecting...</div>
                </div>
                <div class="lounge-status" id="lounge-status"></div>
                <div class="lounge-actions">
                    <button class="lounge-btn lounge-btn-refresh" id="lounge-refresh">New Code</button>
                    <button class="lounge-btn lounge-btn-close" id="lounge-close">Close</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('#lounge-close').addEventListener('click', hideOverlay);
        overlay.querySelector('#lounge-refresh').addEventListener('click', refreshCode);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) hideOverlay();
        });

        // TV mode focus
        if (window._tv) {
            const closeBtn = overlay.querySelector('#lounge-close');
            if (closeBtn) {
                closeBtn.classList.add('tv-overlay-item');
                window._tv.setFocus(closeBtn);
            }
            overlay.querySelector('#lounge-refresh').classList.add('tv-overlay-item');
        }

        requestAnimationFrame(() => requestAnimationFrame(() => {
            overlay.classList.add('visible');
        }));
    }

    function hideOverlay() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        if (!overlay) return;
        overlay.classList.remove('visible');
        const ref = overlay;
        setTimeout(() => { if (ref.parentNode) ref.remove(); }, 300);
        overlay = null;
    }

    async function fetchStatus() {
        try {
            const resp = await fetch('/api/lounge/status');
            if (!resp.ok) return null;
            return await resp.json();
        } catch { return null; }
    }

    async function startLounge() {
        try {
            const resp = await fetch('/api/lounge/start', { method: 'POST' });
            if (!resp.ok) return null;
            return await resp.json();
        } catch { return null; }
    }

    async function refreshCode() {
        updateUI(null, 'Generating new code...');
        try {
            const resp = await fetch('/api/lounge/reset', { method: 'POST' });
            if (!resp.ok) { updateUI(null, 'Failed to generate code'); return; }
            const data = await resp.json();
            updateUI(data);
        } catch {
            updateUI(null, 'Connection error');
        }
    }

    function updateUI(data, message) {
        const codeEl = document.getElementById('lounge-code');
        const statusEl = document.getElementById('lounge-status');
        if (!codeEl || !statusEl) return;

        if (message) {
            codeEl.innerHTML = `<div class="lounge-loading">${message}</div>`;
            statusEl.textContent = '';
            return;
        }

        if (!data) {
            codeEl.innerHTML = '<div class="lounge-loading">Error</div>';
            return;
        }

        if (data.pairing_code) {
            const digits = data.pairing_code.replace(/-/g, '');
            codeEl.innerHTML = digits.split('').map((d, i) =>
                `<span class="lounge-digit">${d}</span>${i === 2 || i === 5 || i === 8 ? '<span class="lounge-sep">-</span>' : ''}`
            ).join('');
            loungeActive = true;
        } else {
            codeEl.innerHTML = '<div class="lounge-loading">Waiting for code...</div>';
        }

        if (data.connected) {
            statusEl.innerHTML = '<span class="lounge-connected">Connected</span>';
        } else if (data.active) {
            statusEl.textContent = 'Waiting for connection...';
        } else {
            statusEl.textContent = '';
        }
    }

    async function show() {
        createOverlay();
        updateUI(null, 'Starting YouTube Link...');

        // Start lounge if not already active
        const status = await fetchStatus();
        if (status && status.active && status.pairing_code) {
            updateUI(status);
        } else {
            const data = await startLounge();
            updateUI(data || null, data ? null : 'Failed to connect to YouTube');
        }

        // Poll for status updates
        pollTimer = setInterval(async () => {
            const s = await fetchStatus();
            if (s) updateUI(s);
        }, 5000);
    }

    window.showYouTubeLinkOverlay = show;
})();
