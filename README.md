# 🎬 YTP - YouTube Player

[![License](https://img.shields.io/github/license/teras/ytp)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/teras/ytp)](https://hub.docker.com/r/teras/ytp)
[![Docker Image Size](https://img.shields.io/docker/image-size/teras/ytp/latest)](https://hub.docker.com/r/teras/ytp)
[![Last Commit](https://img.shields.io/github/last-commit/teras/ytp)](https://github.com/teras/ytp)

A self-hosted web interface for searching and streaming YouTube videos.

## 🚀 Quick Start

```bash
docker run -d -p 8000:8000 -v ./data:/app/data --restart unless-stopped teras/ytp:latest
```

Then open http://localhost:8000

## 🔧 Building from Source

```bash
git clone https://github.com/teras/ytp.git
cd ytp
docker compose up --build -d
```

On first launch, a setup wizard will guide you through creating an admin profile and setting the app password.

Data is persisted in `./data/` (SQLite database with profiles, history, favorites, and settings).

## ✨ Features

- 🔍 Search YouTube videos with infinite scroll
- 📺 Channel browsing and related videos
- 📡 Adaptive streaming (DASH up to 4K, HLS fallback for multi-audio)
- 💬 Subtitle support (manual and auto-captions)
- 🌐 Multi-audio language switching
- 👥 Netflix-style profiles with watch history, favorites, and per-profile preferences
- 🔒 Password-protected access with optional per-profile PIN lock
- 🖼️ Embeddable player for use with LibRedirect
- 🍪 YouTube cookie support for age-restricted content
- 📱 Mobile-friendly responsive interface

## 🔗 Browser Extension (Redirect YouTube → YTP)

Use [LibRedirect](https://libredirect.github.io) to automatically redirect YouTube URLs and embeds to your YTP instance.

### Firefox

Install from [Firefox Add-ons](https://addons.mozilla.org/firefox/addon/libredirect/).

### Chromium / Brave / Edge (Linux)

1. Download the `.crx` file from [LibRedirect releases](https://github.com/libredirect/browser_extension/releases)
2. Go to `chrome://extensions`, enable **Developer mode**
3. Refresh the page, then drag the `.crx` file into the extensions page

### Setup

1. Open LibRedirect settings → **YouTube**
2. Set frontend to **Invidious**
3. Under **Custom instances**, add your YTP URL (e.g. `http://localhost:8000`)
4. Disable all default public instances
5. Enable **Embeds** to also replace YouTube players on third-party sites

## ⚙️ Configuration

### 🍪 YouTube Cookies

To access age-restricted content, place a `cookies.txt` file in the `./data/` directory. You can export YouTube cookies from your browser using yt-dlp:

**Firefox** (no extra install needed — uses yt-dlp from the Docker image):

```bash
docker run --rm -v ./data:/app/data -v ~/.mozilla/firefox:/tmp/ff:ro \
  -u "$(id -u):$(id -g)" teras/ytp:latest yt-dlp \
  --cookies-from-browser firefox:/tmp/ff \
  --cookies /dev/stdout --skip-download \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null \
  | grep -E '^(#|\.youtube\.com|\.google\.com)' > data/cookies.txt
```

**Any browser** (requires [yt-dlp](https://github.com/yt-dlp/yt-dlp) installed locally):

```bash
yt-dlp --cookies-from-browser BROWSER --cookies /dev/stdout \
  --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null \
  | grep -E '^(#|\.youtube\.com|\.google\.com)' > data/cookies.txt
```

Replace `BROWSER` with `firefox`, `chrome`, `chromium`, `brave`, or `edge`.

After placing the file, restart the container: `docker compose restart`

### 👤 Non-default UID/GID

The container runs as UID/GID `1000:1000` by default, which matches most Linux single-user setups. If your user has a different UID/GID, create a `.env` file:

```bash
UID=1001
GID=1001
```
