// Subtitle track management

let subtitleTracks = [];
let failedSubtitles = new Set();

function loadSubtitleTracks(videoId, tracks) {
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
    subtitleTracks = tracks || [];

    if (subtitleTracks.length === 0) {
        subtitleBtnContainer.classList.add('hidden');
        return;
    }

    subtitleBtnContainer.classList.remove('hidden');
    applySubtitlePreference();
}

function applySubtitlePreference() {
    const saved = localStorage.getItem('subtitle_lang');
    if (!saved || saved === 'off') {
        updateSubtitleBtn(null);
        return;
    }

    if (failedSubtitles.has(`${currentVideoId}:${saved}`)) {
        updateSubtitleBtn(null);
        return;
    }

    // Only reuse a track if it has a corresponding <track> element we created
    // (dash.js creates phantom TextTracks from manifests that have no cues)
    const trackEls = videoPlayer.querySelectorAll('track');
    for (const el of trackEls) {
        if (el.srclang === saved) {
            for (let i = 0; i < videoPlayer.textTracks.length; i++) {
                if (videoPlayer.textTracks[i].language === saved) {
                    videoPlayer.textTracks[i].mode = 'showing';
                    updateSubtitleBtn(saved);
                    return;
                }
            }
        }
    }

    const track = subtitleTracks.find(t => t.lang === saved)
               || subtitleTracks.find(t => t.lang.startsWith(saved + '-'));

    if (track) {
        activateTrack(track);
    } else {
        updateSubtitleBtn(null);
    }
}

function activateTrack(trackInfo) {
    [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());

    const el = document.createElement('track');
    el.kind = 'subtitles';
    el.srclang = trackInfo.lang;
    el.label = trackInfo.label;
    el.src = `/api/subtitle/${currentVideoId}?lang=${encodeURIComponent(trackInfo.lang)}`;

    el.addEventListener('load', () => {
        updateSubtitleBtn(trackInfo.lang);
    });
    el.addEventListener('error', () => {
        failedSubtitles.add(`${currentVideoId}:${trackInfo.lang}`);
        if (localStorage.getItem('subtitle_lang') === trackInfo.lang) {
            localStorage.setItem('subtitle_lang', 'off');
        }
        subtitleBtn.textContent = '\ud83d\udcac Off';
    });

    videoPlayer.appendChild(el);

    subtitleBtn.textContent = `\ud83d\udcac ${langName(trackInfo.lang)} \u2026`;

    const activate = (e) => {
        if (e.track.language === trackInfo.lang) {
            e.track.mode = 'showing';
            videoPlayer.textTracks.removeEventListener('addtrack', activate);
        }
    };
    videoPlayer.textTracks.addEventListener('addtrack', activate);
    for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        if (videoPlayer.textTracks[i].language === trackInfo.lang) {
            videoPlayer.textTracks[i].mode = 'showing';
            videoPlayer.textTracks.removeEventListener('addtrack', activate);
            break;
        }
    }
}

function updateSubtitleBtn(activeLang) {
    if (activeLang) {
        subtitleBtn.textContent = `\ud83d\udcac ${langName(activeLang)}`;
    } else {
        subtitleBtn.textContent = '\ud83d\udcac Off';
    }
}

function renderSubtitleMenu() {
    const saved = localStorage.getItem('subtitle_lang');
    const activeLang = (saved && saved !== 'off') ? saved : null;

    // Order: Off, last selected (if any), English, then rest alphabetically
    const offItem = { lang: null, label: 'Off' };
    const promoted = new Set([activeLang]);
    const items = [offItem];

    // Last selected language (if available for this video)
    if (activeLang) {
        const activeTrack = subtitleTracks.find(t => t.lang === activeLang);
        if (activeTrack) items.push(activeTrack);
    }

    // English (if not already the active language)
    const enTrack = subtitleTracks.find(t => t.lang === 'en' || t.lang.startsWith('en-'));
    if (enTrack && enTrack.lang !== activeLang) {
        items.push(enTrack);
        promoted.add(enTrack.lang);
    }

    // Rest, sorted by label
    const rest = subtitleTracks
        .filter(t => !promoted.has(t.lang))
        .sort((a, b) => (a.label || '').localeCompare(b.label || ''));
    items.push(...rest);

    subtitleMenu.innerHTML = items.map(t => {
        const isActive = t.lang === activeLang;
        return `<div class="subtitle-option${isActive ? ' selected' : ''}" data-lang="${t.lang || ''}">
            ${escapeHtml(t.label || 'Off')}
        </div>`;
    }).join('');

    subtitleMenu.querySelectorAll('.subtitle-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const lang = opt.dataset.lang || null;
            selectSubtitle(lang);
            subtitleMenu.classList.add('hidden');
        });
    });
}

function selectSubtitle(lang) {
    localStorage.setItem('subtitle_lang', lang || 'off');
    if (!lang) {
        [...videoPlayer.querySelectorAll('track')].forEach(t => t.remove());
        updateSubtitleBtn(null);
        return;
    }
    const track = subtitleTracks.find(t => t.lang === lang);
    if (track) activateTrack(track);
}

// Event listeners
subtitleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    renderSubtitleMenu();
    subtitleMenu.classList.toggle('hidden');
});

subtitleMenu.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => subtitleMenu.classList.add('hidden'));
