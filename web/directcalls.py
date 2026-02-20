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

import httpx

from helpers import _format_duration

log = logging.getLogger(__name__)

# ── InnerTube client context ─────────────────────────────────────────────────

_API_BASE = "https://www.youtube.com/youtubei/v1"

_CLIENT_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20250219.01.00",
        "hl": "en",
        "gl": "US",
    }
}

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-YouTube-Client-Name": "1",
    "X-YouTube-Client-Version": "2.20250219.01.00",
}

# Reuse a single httpx client for all InnerTube API calls.
# Protected by a lock since httpx.Client is not thread-safe and we call
# from asyncio.to_thread() which may run concurrent requests.
import threading
_innertube_client = httpx.Client(timeout=30.0, headers=_HEADERS)
_innertube_lock = threading.Lock()


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

    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "duration_str": duration_str or _format_duration(duration),
        "channel": channel or "Unknown",
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


# ── Search ───────────────────────────────────────────────────────────────────

def search_first(query: str) -> tuple[list[dict], str | None]:
    """Initial search request.

    POST youtubei/v1/search with {"query": "...", "context": {...}}
    Returns (results, continuation_token).
    """
    body = {
        "query": query,
        "context": _CLIENT_CONTEXT,
    }

    with _innertube_lock:
        resp = _innertube_client.post(
            f"{_API_BASE}/search",
            params={"prettyPrint": "false"},
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()

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


def search_next(continuation_token: str) -> tuple[list[dict], str | None]:
    """Paginated search request using a continuation token.

    POST youtubei/v1/search with {"continuation": "...", "context": {...}}
    Returns (results, next_continuation_token | None).
    """
    body = {
        "continuation": continuation_token,
        "context": _CLIENT_CONTEXT,
    }

    with _innertube_lock:
        resp = _innertube_client.post(
            f"{_API_BASE}/search",
            params={"prettyPrint": "false"},
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()

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


def channel_first(channel_id: str) -> tuple[str, list[dict], str | None]:
    """Initial channel videos request.

    POST youtubei/v1/browse with browseId + Videos tab params.
    Returns (channel_name, results, continuation_token).
    """
    body = {
        "browseId": channel_id,
        "params": _CHANNEL_VIDEOS_PARAMS,
        "context": _CLIENT_CONTEXT,
    }

    with _innertube_lock:
        resp = _innertube_client.post(
            f"{_API_BASE}/browse",
            params={"prettyPrint": "false"},
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()

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


def channel_next(continuation_token: str) -> tuple[list[dict], str | None]:
    """Paginated channel videos request using a continuation token.

    POST youtubei/v1/browse with {"continuation": "...", "context": {...}}
    Returns (results, next_continuation_token | None).
    """
    body = {
        "continuation": continuation_token,
        "context": _CLIENT_CONTEXT,
    }

    with _innertube_lock:
        resp = _innertube_client.post(
            f"{_API_BASE}/browse",
            params={"prettyPrint": "false"},
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()

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

_async_client = httpx.AsyncClient(
    timeout=30.0,
    follow_redirects=True,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    },
)


async def fetch_related(video_id: str) -> list[dict]:
    """Fetch related videos for a given video ID.

    GET youtube.com/watch?v=ID → parse ytInitialData from HTML.
    Navigates twoColumnWatchNextResults → secondaryResults.
    Filters out RD* (mix/playlist) IDs.

    Returns list of video dicts (may be empty on error).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        resp = await _async_client.get(url)
        html = resp.text

        match = re.search(r"var ytInitialData = ({.*?});", html)
        if not match:
            return []

        data = json.loads(match.group(1))

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

                if content_id.startswith("RD"):
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
