// Download management: progress tracking, quality selection, cancel

let downloadCancelled = false;
let availableQualities = [];
let selectedDownloadQuality = 0;

const PROGRESS_CIRCUMFERENCE = 62.83;

function setProgress(percent) {
    const offset = PROGRESS_CIRCUMFERENCE - (percent / 100) * PROGRESS_CIRCUMFERENCE;
    progressRingFill.style.strokeDashoffset = offset;
}

function startHdDownload(videoId, quality = 0) {
    cancelDownloadBtn.classList.remove('hidden');
    downloadPill.classList.add('hidden');
    downloadQualityMenu.classList.add('hidden');
    setProgress(0);

    const url = quality ? `/api/play/${videoId}?quality=${quality}` : `/api/play/${videoId}`;
    fetch(url);

    progressInterval = setInterval(async () => {
        try {
            const prog = await fetch(`/api/progress/${videoId}`);
            const progData = await prog.json();

            setProgress(progData.progress);

            if (progData.status === 'finished' || progData.status === 'ready') {
                clearInterval(progressInterval);
                progressInterval = null;
                setProgress(100);

                const currentTime = videoPlayer.currentTime;
                const wasPlaying = !videoPlayer.paused;

                if (dashPlayer) {
                    dashPlayer.destroy();
                    dashPlayer = null;
                }

                videoPlayer.pause();
                videoPlayer.src = `/api/stream/${videoId}`;

                videoPlayer.onloadedmetadata = () => {
                    videoPlayer.currentTime = currentTime;
                    if (wasPlaying) videoPlayer.play();
                    videoPlayer.onloadedmetadata = null;
                    applySubtitlePreference();
                };
                videoPlayer.load();

                setTimeout(() => cancelDownloadBtn.classList.add('hidden'), 800);
            } else if (progData.status === 'error' || progData.status === 'cancelled') {
                clearInterval(progressInterval);
                progressInterval = null;
                setTimeout(() => cancelDownloadBtn.classList.add('hidden'), 500);
            }
        } catch (e) {
            // Ignore
        }
    }, 500);
}

async function checkDownloadOffer(videoId, currentHeight) {
    try {
        const response = await fetch(`/api/formats/${videoId}`);
        const data = await response.json();

        const betterOptions = data.options.filter(opt => opt.height > currentHeight);

        if (betterOptions.length > 0) {
            availableQualities = betterOptions;

            let targetQuality = betterOptions.find(opt => opt.height >= maxDownloadQuality);
            if (!targetQuality) {
                targetQuality = betterOptions[betterOptions.length - 1];
            }
            selectedDownloadQuality = targetQuality.height;

            const sizeText = targetQuality.size_str ? ` (${targetQuality.size_str})` : '';
            downloadAction.textContent = `Download${sizeText}`;

            updateQualityMenu();
            downloadPill.classList.remove('hidden');
        }
    } catch (e) {
        // Ignore
    }
}

function updateQualityMenu() {
    downloadQualityMenu.innerHTML = [...availableQualities].reverse().map(opt => {
        const sizeInfo = opt.size_str ? `<span class="size">${opt.size_str}</span>` : '';
        const selected = opt.height === selectedDownloadQuality ? ' selected' : '';
        return `<div class="quality-option${selected}" data-quality="${opt.height}">
            <span>${opt.label}</span>
            ${sizeInfo}
        </div>`;
    }).join('');

    downloadQualityMenu.querySelectorAll('.quality-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const quality = parseInt(opt.dataset.quality);
            selectedDownloadQuality = quality;
            const selected = availableQualities.find(q => q.height === quality);
            const sizeText = selected?.size_str ? ` (${selected.size_str})` : '';
            downloadAction.textContent = `Download${sizeText}`;
            updateQualityMenu();
            downloadQualityMenu.classList.add('hidden');
        });
    });
}

// Event listeners
downloadAction.addEventListener('click', () => {
    if (currentVideoId && selectedDownloadQuality > 0) {
        startHdDownload(currentVideoId, selectedDownloadQuality);
    }
});

downloadGear.addEventListener('click', (e) => {
    e.stopPropagation();
    downloadQualityMenu.classList.toggle('hidden');
});

downloadQualityMenu.addEventListener('click', (e) => e.stopPropagation());

cancelDownloadBtn.addEventListener('click', async () => {
    if (currentVideoId) {
        downloadCancelled = true;
        await fetch(`/api/cancel/${currentVideoId}`, { method: 'POST' });
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
        cancelDownloadBtn.classList.add('hidden');
        if (availableQualities.length > 0) {
            downloadPill.classList.remove('hidden');
        }
    }
});
