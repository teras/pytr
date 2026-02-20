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

- Search YouTube videos
- Stream videos directly in browser (DASH, up to 4K)
- Netflix-style profiles with preferences, watch history, and favorites
- Password-protected access
- Optional per-profile PIN lock
- Mobile-friendly interface

## License

MIT
