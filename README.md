# YTP - YouTube Player

A simple web interface for searching, streaming, and downloading YouTube videos.

## Quick Start

```bash
docker run -d \
  -p 8000:8000 \
  -e YTP_PASSWORD="your-secret-password" \
  -v ./downloads:/app/downloads \
  --restart unless-stopped \
  teras/ytp:latest
```

Then open http://localhost:8000

## Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  ytp:
    image: teras/ytp:latest
    ports:
      - "8000:8000"
    environment:
      - YTP_PASSWORD=your-secret-password
    volumes:
      - ./downloads:/app/downloads
    restart: unless-stopped
```

Run:
```bash
docker compose up -d
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `YTP_PASSWORD` | Required. Password for web interface login. |

## Features

- Search YouTube videos
- Stream videos directly in browser
- Download videos (multiple quality options)
- Mobile-friendly interface
- Password protection with rate limiting

## Building from Source

```bash
git clone https://github.com/teras/ytp.git
cd ytp
docker compose up --build
```

## License

MIT
