// ── Shared utilities (used by app.js and standalone pages) ──────────────────

// ── HTML escaping ───────────────────────────────────────────────────────────

const _escDiv = document.createElement('div');
function escapeHtml(text) {
    _escDiv.textContent = text;
    return _escDiv.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── YouTube URL → internal PYTR link conversion ────────────────────────────

function youtubeToInternalLink(url) {
    try {
        const u = new URL(url);
        let videoId = null;
        if (u.hostname === 'youtu.be') {
            videoId = u.pathname.slice(1);
        } else if ((u.hostname === 'www.youtube.com' || u.hostname === 'youtube.com' || u.hostname === 'm.youtube.com') && u.pathname === '/watch') {
            videoId = u.searchParams.get('v');
        }
        if (!videoId) return null;
        // For youtu.be, video ID is in the path — move it to ?v= and keep all other params
        if (u.hostname === 'youtu.be') {
            u.searchParams.set('v', videoId);
        }
        return `/watch?${u.searchParams.toString()}`;
    } catch { return null; }
}

// ── Text linkification ──────────────────────────────────────────────────────

function linkifyText(text) {
    const escaped = escapeHtml(text);
    // First linkify URLs, converting YouTube links to internal PYTR links
    let result = escaped.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
        const href = match.replace(/&quot;/g, '%22').replace(/&#39;/g, '%27').replace(/&amp;/g, '&');
        const pytrLink = youtubeToInternalLink(href);
        if (pytrLink) {
            return `<a href="${pytrLink}" data-internal="1">${match}</a>`;
        }
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
