// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Shared pairing flow: request code, render QR, poll for approval.

let _pairPollTimer = null;

function stopPairPolling() {
    if (_pairPollTimer) { clearInterval(_pairPollTimer); _pairPollTimer = null; }
}

/**
 * Start a pairing flow: request a code, display it, poll for approval.
 *
 * @param {object} opts
 * @param {Element} opts.codeEl    - Element to display the pairing code
 * @param {Element} opts.qrEl      - Element to render the QR SVG (optional)
 * @param {Element} opts.statusEl  - Element for status text
 * @param {object}  opts.requestBody - JSON body for POST /api/pair/request (optional)
 * @param {function} opts.onApproved - Called with status response on approval
 * @param {function} opts.onRetry    - Called with reason string on denied/expired.
 *                                     If omitted, auto-retries with the same opts.
 */
async function startPairing(opts) {
    stopPairPolling();
    const { codeEl, qrEl, statusEl, requestBody, onApproved, onRetry } = opts;

    function retry(reason, delay) {
        if (onRetry) {
            onRetry(reason);
        } else {
            statusEl.textContent = reason + ' Retrying...';
            statusEl.style.color = '#ff4444';
            setTimeout(() => startPairing(opts), delay);
        }
    }

    try {
        const fetchOpts = { method: 'POST' };
        if (requestBody) {
            fetchOpts.headers = { 'Content-Type': 'application/json' };
            fetchOpts.body = JSON.stringify(requestBody);
        }
        const res = await fetch('/api/pair/request', fetchOpts);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            statusEl.textContent = err.detail || 'Failed to create pairing code';
            return;
        }
        const data = await res.json();
        codeEl.textContent = data.code;
        if (data.qr_svg && qrEl) {
            const parser = new DOMParser();
            const doc = parser.parseFromString(data.qr_svg, 'image/svg+xml');
            const parsedSvg = doc.documentElement;
            if (parsedSvg.tagName === 'svg') {
                qrEl.innerHTML = '';
                qrEl.appendChild(document.importNode(parsedSvg, true));
            }
            const svg = qrEl.querySelector('svg');
            if (svg) { svg.style.width = '180px'; svg.style.height = '180px'; }
            qrEl.querySelectorAll('path').forEach(p => p.style.fill = '#f1f1f1');
            qrEl.querySelectorAll('rect').forEach(r => r.style.fill = 'transparent');
        }
        statusEl.textContent = 'Waiting for approval...';
        statusEl.style.color = '#aaa';

        _pairPollTimer = setInterval(async () => {
            try {
                const r = await fetch('/api/pair/status/' + data.code);
                const d = await r.json();
                if (d.status === 'approved') {
                    stopPairPolling();
                    statusEl.textContent = 'Approved!';
                    statusEl.style.color = '#4caf50';
                    onApproved(d);
                } else if (d.status === 'denied') {
                    stopPairPolling();
                    retry('Denied.', 2000);
                } else if (d.status === 'expired') {
                    stopPairPolling();
                    retry('Code expired.', 1000);
                }
            } catch (e) {}
        }, 2000);
    } catch (e) {
        statusEl.textContent = 'Network error';
    }
}
