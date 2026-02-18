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

    for (let i = 0; i < videoPlayer.textTracks.length; i++) {
        const tt = videoPlayer.textTracks[i];
        if (tt.language === saved) {
            tt.mode = 'showing';
            updateSubtitleBtn(saved);
            return;
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
        subtitleBtn.textContent = 'CC';
        subtitleBtn.classList.remove('active', 'loading');
    });

    videoPlayer.appendChild(el);

    subtitleBtn.textContent = `CC: ${trackInfo.lang.toUpperCase()} \u2026`;
    subtitleBtn.classList.add('active');

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
        subtitleBtn.textContent = `CC: ${activeLang.toUpperCase()}`;
        subtitleBtn.classList.add('active');
    } else {
        subtitleBtn.textContent = 'CC';
        subtitleBtn.classList.remove('active');
    }
}

function renderSubtitleMenu() {
    const saved = localStorage.getItem('subtitle_lang');
    const activeLang = (saved && saved !== 'off') ? saved : null;
    const allItems = [{ lang: null, label: 'Off', auto: false }, ...subtitleTracks];

    let items;
    if (activeLang) {
        const activeItem = allItems.find(t => t.lang === activeLang);
        items = activeItem
            ? [activeItem, ...allItems.filter(t => t.lang !== activeLang)]
            : allItems;
    } else {
        items = allItems;
    }

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
