// ── Shared utilities (used by app.js and standalone pages) ──────────────────

// ── SVG Icons ───────────────────────────────────────────────────────────────

const SVG_PLAY  = '<path d="M2.5 0 21.5 12 2.5 24z" fill-rule="evenodd"/>';
const SVG_PAUSE = '<path d="M3.158 0h6.316v24H3.158Zm11.368 0h6.316v24h-6.316z" fill-rule="evenodd"/>';

function svgIcon(inner, cls, size) {
    const w = size ? ` width="${size}" height="${size}"` : '';
    const c = cls ? ` class="${cls}"` : '';
    return `<svg${c} viewBox="0 0 24 24"${w} fill="currentColor">${inner}</svg>`;
}

// ── HTML escaping ───────────────────────────────────────────────────────────

const _escDiv = document.createElement('div');
function escapeHtml(text) {
    _escDiv.textContent = text;
    return _escDiv.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Thumbnail fallback ──────────────────────────────────────────────────────

function thumbUrl(videoId) {
    return `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg`;
}

// YouTube returns a 120x90 placeholder when a thumbnail variant doesn't exist.
const _YT_PLACEHOLDER_MAX_WIDTH = 120;
const _YT_VI_RE = /^https?:\/\/(?:i\.ytimg\.com|img\.youtube\.com)\/vi(?:_webp)?\/([A-Za-z0-9_-]{11})\//;

function probeBestYtThumb(videoId, onBest) {
    const base = `https://i.ytimg.com/vi/${videoId}`;
    const tryLoad = (url, next) => {
        const probe = new Image();
        probe.onload = () => {
            if (probe.naturalWidth > _YT_PLACEHOLDER_MAX_WIDTH) onBest(url);
            else if (next) next();
        };
        probe.onerror = () => { if (next) next(); };
        probe.src = url;
    };
    tryLoad(`${base}/maxresdefault.jpg`, () => tryLoad(`${base}/sddefault.jpg`, null));
}

function _upgradeYtThumb(img) {
    if (img.dataset.thumbUpgraded) return;
    const m = img.src.match(_YT_VI_RE);
    if (!m) return;
    img.dataset.thumbUpgraded = '1';
    probeBestYtThumb(m[1], url => { if (img.isConnected) img.src = url; });
}

function upgradeYtThumbs(root) {
    (root || document).querySelectorAll('img').forEach(_upgradeYtThumb);
}

if (typeof MutationObserver !== 'undefined') {
    const _thumbObserver = new MutationObserver(muts => {
        for (const m of muts) {
            for (const node of m.addedNodes) {
                if (node.nodeType !== 1) continue;
                if (node.tagName === 'IMG') _upgradeYtThumb(node);
                else if (node.querySelectorAll) node.querySelectorAll('img').forEach(_upgradeYtThumb);
            }
        }
    });
    const _startThumbObserver = () => {
        upgradeYtThumbs(document);
        _thumbObserver.observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _startThumbObserver);
    else _startThumbObserver();
}

// ── Image proxy ─────────────────────────────────────────────────────────────

function proxyImageUrl(url) {
    if (!url) return '';
    return '/api/img-proxy?url=' + encodeURIComponent(url);
}

function loadProxyImage(img) {
    const url = img.dataset.proxySrc;
    if (!url) return;
    fetch(url).then(r => {
        if (!r.ok) throw new Error(r.status);
        return r.blob();
    }).then(blob => {
        img.src = URL.createObjectURL(blob);
    }).catch(() => {
        const tpl = img.nextElementSibling;
        if (tpl) img.outerHTML = tpl.innerHTML;
    });
}

// ── YouTube URL → internal PYTR link conversion ────────────────────────────

function youtubeToInternalLink(url) {
    try {
        const u = new URL(url);
        const hn = u.hostname.replace(/^www\./, '').replace(/^m\./, '');
        const p = u.pathname;
        // youtu.be/ID, /shorts/ID, /live/ID → all point to a video, normalize to /watch?v=ID
        if (hn === 'youtu.be') {
            const videoId = p.slice(1);
            if (!videoId) return null;
            u.searchParams.set('v', videoId);
            return `/watch?${u.searchParams.toString()}`;
        }
        if (hn !== 'youtube.com' && hn !== 'music.youtube.com') return null;
        if (p.startsWith('/shorts/') || p.startsWith('/live/')) {
            const videoId = p.split('/')[2];
            if (!videoId) return null;
            u.searchParams.set('v', videoId);
            return `/watch?${u.searchParams.toString()}`;
        }
        // Paths we pass through as-is (handleInitialRoute knows how to route them)
        if (p === '/watch' || p === '/playlist' || p === '/results'
            || p.startsWith('/channel/') || p.startsWith('/@')
            || p.startsWith('/c/') || p.startsWith('/user/')) {
            return `${p}${u.search || ''}`;
        }
        return null;
    } catch { return null; }
}

// ── Text linkification ──────────────────────────────────────────────────────

function linkifyText(text) {
    const escaped = escapeHtml(text);
    // First linkify URLs, converting YouTube links to internal PYTR links
    let result = escaped.replace(/(https?:\/\/[^\s<]+)/g, (match) => {
        const href = match.replace(/&quot;/g, '%22').replace(/&#39;/g, '%27').replace(/&amp;/g, '&');
        const safeHref = escapeAttr(href);
        const pytrLink = youtubeToInternalLink(href);
        if (pytrLink) {
            return `<a href="${escapeAttr(pytrLink)}" data-internal="1">${match}</a>`;
        }
        return `<a href="${safeHref}" target="_blank" rel="noopener">${match}</a>`;
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

// ── Exclusive playback (pause other tabs/embeds via BroadcastChannel) ───────

const _exclusiveChannel = new BroadcastChannel('pytr-playback');

/**
 * Attach exclusive playback to a <video> element.
 * @param {HTMLVideoElement} video
 * @param {() => boolean} isEnabled — returns true when exclusive playback is active
 */
function exclusivePlayback(video, isEnabled) {
    _exclusiveChannel.addEventListener('message', () => {
        if (!video.paused && isEnabled()) video.pause();
    });
    video.addEventListener('play', () => {
        if (isEnabled()) _exclusiveChannel.postMessage('pause');
    });
}
