// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// PYTR — Shared player engine (used by app.js and embed.js)

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

/** Convert ISO 639 language code to display name */
const _langNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'language' }) : null;
function langName(code) {
    try { return _langNames ? _langNames.of(code) : code.toUpperCase(); }
    catch(e) { return code.toUpperCase(); }
}

/** Pick best quality from available heights given a preferred height */
function getTargetQuality(heights, preferred) {
    if (heights.includes(preferred)) return preferred;
    const below = heights.filter(h => h <= preferred);
    return below.length > 0 ? Math.max(...below) : Math.min(...heights);
}

/** Build quality list from a dash.js player */
function buildQualitiesDash(dp) {
    return (dp.getRepresentationsByType('video') || []).map((rep, idx) => ({
        height: rep.height,
        bandwidth: rep.bandwidth,
        qualityIndex: idx,
    })).sort((a, b) => a.height - b.height);
}

/** Build quality list from an HLS.js player */
function buildQualitiesHls(hp) {
    return (hp.levels || []).map((level, idx) => ({
        height: level.height,
        bandwidth: level.bitrate || level.bandwidth || 0,
        qualityIndex: idx,
    })).sort((a, b) => a.height - b.height);
}

/** Switch to a specific quality entry */
function switchToQuality(type, dp, hp, entry) {
    if (type === 'dash') dp.setRepresentationForTypeByIndex('video', entry.qualityIndex);
    else if (type === 'hls') hp.currentLevel = entry.qualityIndex;
}
