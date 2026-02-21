# YTP - YouTube Player

A simple web interface for searching and streaming YouTube videos.

## Quick Start

```bash
docker run -d -p 8000:8000 -v ./data:/app/data --restart unless-stopped teras/ytp:latest
```

Then open http://localhost:8000

## Building from Source

```bash
git clone https://github.com/teras/ytp.git
cd ytp
docker compose up --build -d
```

On first launch, a setup wizard will guide you through creating an admin profile and setting the app password.

Data is persisted in `./data/` (SQLite database with profiles, history, favorites, and settings).

## Features

- Search YouTube videos with infinite scroll
- Channel browsing and related videos
- Adaptive streaming (DASH up to 4K, HLS fallback for multi-audio)
- Subtitle support (manual and auto-captions)
- Multi-audio language switching
- Netflix-style profiles with watch history, favorites, and per-profile preferences
- Password-protected access with optional per-profile PIN lock
- Browser cookie support for age-restricted content (local mode only, not available in Docker)
- Mobile-friendly responsive interface
