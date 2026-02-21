# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct YouTube InnerTube API calls.

Consolidates all direct YouTube API calls in one module. Each function is
stateless: one HTTP call in, structured data out. No sessions, caches, or
global state.

Why bypass yt-dlp for search/channel pagination:
  yt-dlp generators hold ~3.5 MB each (YouTube's full parsed JSON). With many
  concurrent users this is unsustainable. InnerTube continuation tokens are
  ~200 bytes, so we store only those between paginated requests.

Endpoints used:
  - POST youtubei/v1/search   — search pagination
  - POST youtubei/v1/browse   — channel videos pagination
  - GET  youtube.com/watch     — related videos (HTML scrape of ytInitialData)
"""

import json
import logging
import re

from helpers import _format_duration, http_client

log = logging.getLogger(__name__)

# ── InnerTube client context ─────────────────────────────────────────────────

_API_BASE = "https://www.youtube.com/youtubei/v1"

_FALLBACK_CLIENT_VERSION = "2.20250219.01.00"
_cached_client_version: str | None = None


async def _fetch_client_version() -> str:
    """Fetch current WEB client version from YouTube homepage. Cached after first call."""
    global _cached_client_version
    if _cached_client_version:
        return _cached_client_version
    try:
        resp = await http_client.get("https://www.youtube.com/", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        m = re.search(r'"clientVersion":"(\d+\.\d{8}\.\d+\.\d+)"', resp.text)
        if m:
            _cached_client_version = m.group(1)
            log.info(f"InnerTube clientVersion: {_cached_client_version}")
            return _cached_client_version
    except Exception as e:
        log.warning(f"Failed to fetch clientVersion: {e}")
    _cached_client_version = _FALLBACK_CLIENT_VERSION
    log.info(f"Using fallback clientVersion: {_FALLBACK_CLIENT_VERSION}")
    return _cached_client_version


def _build_context(version: str) -> dict:
    return {
        "client": {
            "clientName": "WEB",
            "clientVersion": version,
            "hl": "en",
            "gl": "US",
        }
    }


def _build_headers(version: str) -> dict:
    return {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": version,
    }


# ── Response parsers ─────────────────────────────────────────────────────────

def _parse_video_renderer(renderer: dict) -> dict | None:
    """Extract video info from a videoRenderer object."""
    video_id = renderer.get("videoId")
    if not video_id:
        return None

    title_runs = renderer.get("title", {}).get("runs", [])
    title = title_runs[0].get("text", "") if title_runs else ""

    channel = ""
    channel_runs = renderer.get("ownerText", {}).get("runs", [])
    if channel_runs:
        channel = channel_runs[0].get("text", "")
    if not channel:
        channel_runs = renderer.get("longBylineText", {}).get("runs", [])
        if channel_runs:
            channel = channel_runs[0].get("text", "")

    # Duration: "lengthText" → {"simpleText": "3:45"} or {"runs": [...]}
    duration_text = renderer.get("lengthText", {})
    duration_str = duration_text.get("simpleText", "")
    if not duration_str:
        runs = duration_text.get("runs", [])
        if runs:
            duration_str = runs[0].get("text", "")

    # Parse duration string to seconds for consistency
    duration = 0
    if duration_str:
        parts = duration_str.split(":")
        try:
            if len(parts) == 3:
                duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                duration = int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            pass

    # Published time: relative text like "2 days ago", "3 months ago"
    published = renderer.get("publishedTimeText", {}).get("simpleText", "")

    # Live badge: badges[] → metadataBadgeRenderer.label == "LIVE"
    is_live = any(
        b.get("metadataBadgeRenderer", {}).get("label") == "LIVE"
        for b in renderer.get("badges", [])
    )

    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "duration_str": duration_str or _format_duration(duration),
        "channel": channel or "Unknown",
        "published": published,
        "is_live": is_live,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    }


def _extract_continuation_token(items: list) -> str | None:
    """Find the continuation token in a list of renderer items."""
    for item in items:
        cont_renderer = item.get("continuationItemRenderer", {})
        token = (cont_renderer
                 .get("continuationEndpoint", {})
                 .get("continuationCommand", {})
                 .get("token"))
        if token:
            return token
    return None


# ── InnerTube POST helper ────────────────────────────────────────────────────

async def _innertube_post(endpoint: str, body: dict) -> dict:
    """POST to an InnerTube endpoint and return parsed JSON.

    Automatically injects 'context' with the current client version.
    """
    version = await _fetch_client_version()
    body.setdefault("context", _build_context(version))
    resp = await http_client.post(
        f"{_API_BASE}/{endpoint}",
        params={"prettyPrint": "false"},
        headers=_build_headers(version),
        json=body,
    )
    resp.raise_for_status()
    return resp.json()


# ── Search ───────────────────────────────────────────────────────────────────

async def search_first(query: str) -> tuple[list[dict], str | None]:
    """Initial search request.

    POST youtubei/v1/search with {"query": "...", "context": {...}}
    Returns (results, continuation_token).
    """
    data = await _innertube_post("search", {
        "query": query,
    })

    results = []
    token = None

    # Navigate: contents → twoColumnSearchResultsRenderer → primaryContents
    #         → sectionListRenderer → contents[]
    sections = (data
                .get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", []))

    for section in sections:
        # Video results are inside itemSectionRenderer
        items = section.get("itemSectionRenderer", {}).get("contents", [])
        for item in items:
            renderer = item.get("videoRenderer")
            if renderer:
                video = _parse_video_renderer(renderer)
                if video:
                    results.append(video)

        # Continuation token may be at the section level
        if not token:
            token = _extract_continuation_token([section])

    # Also check for continuation inside the last itemSectionRenderer
    if not token and sections:
        last_items = sections[-1].get("itemSectionRenderer", {}).get("contents", [])
        token = _extract_continuation_token(last_items)

    # Check top-level continuation
    if not token:
        token = _extract_continuation_token(sections)

    return results, token


async def search_next(continuation_token: str) -> tuple[list[dict], str | None]:
    """Paginated search request using a continuation token.

    POST youtubei/v1/search with {"continuation": "...", "context": {...}}
    Returns (results, next_continuation_token | None).
    """
    data = await _innertube_post("search", {
        "continuation": continuation_token,
    })

    results = []
    token = None

    # Continuation responses use onResponseReceivedCommands
    for command in data.get("onResponseReceivedCommands", []):
        items = command.get("appendContinuationItemsAction", {}).get("continuationItems", [])
        for item in items:
            renderer = item.get("videoRenderer")
            if renderer:
                video = _parse_video_renderer(renderer)
                if video:
                    results.append(video)

            # Also check inside itemSectionRenderer (some responses nest further)
            section_items = item.get("itemSectionRenderer", {}).get("contents", [])
            for sub_item in section_items:
                renderer = sub_item.get("videoRenderer")
                if renderer:
                    video = _parse_video_renderer(renderer)
                    if video:
                        results.append(video)

        token = _extract_continuation_token(items)

    return results, token


# ── Channel ──────────────────────────────────────────────────────────────────

# Protobuf-encoded params for the "Videos" tab, sorted by "Recently uploaded"
_CHANNEL_VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


async def channel_first(channel_id: str) -> tuple[str, list[dict], str | None]:
    """Initial channel videos request.

    POST youtubei/v1/browse with browseId + Videos tab params.
    Returns (channel_name, results, continuation_token).
    """
    data = await _innertube_post("browse", {
        "browseId": channel_id,
        "params": _CHANNEL_VIDEOS_PARAMS,
    })

    # Channel name from metadata or header
    channel_name = (data.get("metadata", {})
                    .get("channelMetadataRenderer", {})
                    .get("title", "Unknown"))

    results = []
    token = None

    # Navigate: contents → twoColumnBrowseResultsRenderer → tabs[]
    tabs = (data
            .get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", []))

    for tab in tabs:
        tab_renderer = tab.get("tabRenderer", {})
        # Find the selected/Videos tab
        if not tab_renderer.get("selected", False):
            continue

        # richGridRenderer path (modern layout)
        grid_items = (tab_renderer
                      .get("content", {})
                      .get("richGridRenderer", {})
                      .get("contents", []))

        for item in grid_items:
            rich_item = item.get("richItemRenderer", {})
            renderer = rich_item.get("content", {}).get("videoRenderer")
            if renderer:
                video = _parse_video_renderer(renderer)
                if video:
                    if not video["channel"] or video["channel"] == "Unknown":
                        video["channel"] = channel_name
                    results.append(video)

        token = _extract_continuation_token(grid_items)

        # Also try sectionListRenderer path (older layout)
        if not results:
            section_contents = (tab_renderer
                                .get("content", {})
                                .get("sectionListRenderer", {})
                                .get("contents", []))
            for section in section_contents:
                items = (section
                         .get("itemSectionRenderer", {})
                         .get("contents", [])
                         or [])
                for cont in items:
                    grid = cont.get("gridRenderer", {}).get("items", [])
                    for grid_item in grid:
                        renderer = grid_item.get("gridVideoRenderer")
                        if renderer:
                            video = _parse_video_renderer(renderer)
                            if video:
                                if not video["channel"] or video["channel"] == "Unknown":
                                    video["channel"] = channel_name
                                results.append(video)
                    if not token:
                        token = _extract_continuation_token(grid)

        break  # Only process the selected tab

    return channel_name, results, token


async def channel_next(continuation_token: str) -> tuple[list[dict], str | None]:
    """Paginated channel videos request using a continuation token.

    POST youtubei/v1/browse with {"continuation": "...", "context": {...}}
    Returns (results, next_continuation_token | None).
    """
    data = await _innertube_post("browse", {
        "continuation": continuation_token,
    })

    results = []
    token = None

    # Channel continuation uses onResponseReceivedActions (not Commands)
    actions = (data.get("onResponseReceivedActions", [])
               or data.get("onResponseReceivedCommands", []))

    for action in actions:
        items = (action.get("appendContinuationItemsAction", {})
                 .get("continuationItems", []))
        for item in items:
            # richItemRenderer → content → videoRenderer
            rich_item = item.get("richItemRenderer", {})
            renderer = rich_item.get("content", {}).get("videoRenderer")
            if renderer:
                video = _parse_video_renderer(renderer)
                if video:
                    results.append(video)

            # Also try gridVideoRenderer (older layout)
            renderer = item.get("gridVideoRenderer")
            if renderer:
                video = _parse_video_renderer(renderer)
                if video:
                    results.append(video)

        token = _extract_continuation_token(items)

    return results, token


# ── Related Videos ───────────────────────────────────────────────────────────

async def fetch_related(video_id: str) -> list[dict]:
    """Fetch related videos for a given video ID.

    GET youtube.com/watch?v=ID → parse ytInitialData from HTML.
    Navigates twoColumnWatchNextResults → secondaryResults.
    Filters out RD* (mix/playlist) IDs.

    Returns list of video dicts (may be empty on error).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        resp = await http_client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        html = resp.text

        match = re.search(r"var ytInitialData\s*=\s*\{", html)
        if not match:
            return []

        # Extract JSON by counting braces (regex {.*?} is fragile with nested JSON)
        start = match.end() - 1  # position of opening {
        depth = 0
        in_string = False
        escape = False
        end = start
        for i in range(start, len(html)):
            ch = html[i]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        data = json.loads(html[start:end])

        contents = data.get("contents", {}).get("twoColumnWatchNextResults", {})
        secondary = (contents
                     .get("secondaryResults", {})
                     .get("secondaryResults", {})
                     .get("results", []))

        related = []
        for item in secondary:
            if "lockupViewModel" in item:
                vm = item["lockupViewModel"]
                content_id = vm.get("contentId", "")

                if content_id.startswith(("RD", "PL")):
                    continue

                metadata = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
                title = metadata.get("title", {}).get("content", "")

                channel = ""
                metadata_rows = (metadata
                                 .get("metadata", {})
                                 .get("contentMetadataViewModel", {})
                                 .get("metadataRows", []))
                if metadata_rows:
                    for row in metadata_rows:
                        parts = row.get("metadataParts", [])
                        if parts:
                            channel = parts[0].get("text", {}).get("content", "")
                            break

                duration_str = ""
                content_image = vm.get("contentImage", {})
                thumb_vm = (content_image.get("thumbnailViewModel")
                            or content_image.get("collectionThumbnailViewModel", {})
                            .get("primaryThumbnail", {}).get("thumbnailViewModel")
                            or {})
                overlays = thumb_vm.get("overlays", [])
                for overlay in overlays:
                    badge = overlay.get("thumbnailOverlayBadgeViewModel", {})
                    for b in badge.get("thumbnailBadges", []):
                        if "thumbnailBadgeViewModel" in b:
                            duration_str = b["thumbnailBadgeViewModel"].get("text", "")
                            break

                if content_id and title:
                    related.append({
                        "id": content_id,
                        "title": title,
                        "channel": channel,
                        "duration_str": duration_str,
                        "thumbnail": f"https://i.ytimg.com/vi/{content_id}/mqdefault.jpg",
                    })

        return related

    except Exception as e:
        log.error(f"Related videos error: {e}")
        return []
