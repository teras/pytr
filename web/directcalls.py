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
  - POST youtubei/v1/browse   — channel videos/playlists pagination
  - GET  youtube.com/watch     — related videos & playlist contents (HTML scrape)
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


def _extract_lockup_channel(metadata: dict) -> str:
    """Extract channel name from lockupMetadataViewModel's metadata rows."""
    rows = (metadata
            .get("metadata", {})
            .get("contentMetadataViewModel", {})
            .get("metadataRows", []))
    for row in rows:
        parts = row.get("metadataParts", [])
        if parts:
            return parts[0].get("text", {}).get("content", "")
    return ""


def _extract_lockup_duration(vm: dict) -> str:
    """Extract duration string from lockupViewModel overlay badges."""
    content_image = vm.get("contentImage", {})
    thumb_vm = (content_image.get("thumbnailViewModel")
                or content_image.get("collectionThumbnailViewModel", {})
                .get("primaryThumbnail", {}).get("thumbnailViewModel")
                or {})
    for overlay in thumb_vm.get("overlays", []):
        badge = overlay.get("thumbnailOverlayBadgeViewModel", {})
        for b in badge.get("thumbnailBadges", []):
            if "thumbnailBadgeViewModel" in b:
                return b["thumbnailBadgeViewModel"].get("text", "")
    return ""


def _parse_lockup_view_model(vm: dict) -> dict | None:
    """Extract playlist/mix info from a lockupViewModel object.

    Returns a dict with type='playlist' or 'mix', plus first_video_id and
    playlist_id from the watchEndpoint for playback.
    """
    content_id = vm.get("contentId", "")
    if not content_id:
        return None

    # Determine type from contentId prefix
    if content_id.startswith("PL"):
        item_type = "playlist"
    elif content_id.startswith("RD"):
        item_type = "mix"
    else:
        return None

    # Title
    metadata = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = metadata.get("title", {}).get("content", "")
    if not title:
        return None

    channel = _extract_lockup_channel(metadata)

    # Video count from overlay badge (e.g. "22 videos")
    video_count = _extract_lockup_duration(vm)

    # Thumbnail
    content_image = vm.get("contentImage", {})
    thumb_vm = (content_image.get("thumbnailViewModel")
                or content_image.get("collectionThumbnailViewModel", {})
                .get("primaryThumbnail", {}).get("thumbnailViewModel")
                or {})
    thumbnails = thumb_vm.get("image", {}).get("sources", [])
    thumbnail = thumbnails[0].get("url", "") if thumbnails else ""

    # watchEndpoint — first video ID and playlist ID for playback
    first_video_id = ""
    playlist_id = ""
    renderer_ctx = vm.get("rendererContext", {})
    command_ctx = renderer_ctx.get("commandContext", {})
    on_tap = command_ctx.get("onTap", {})
    inner_cmd = on_tap.get("innertubeCommand", {})
    watch_ep = inner_cmd.get("watchEndpoint", {})
    if watch_ep:
        first_video_id = watch_ep.get("videoId", "")
        playlist_id = watch_ep.get("playlistId", "")

    if not first_video_id:
        return None

    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{first_video_id}/mqdefault.jpg"

    return {
        "id": content_id,
        "type": item_type,
        "title": title,
        "channel": channel or "Unknown",
        "video_count": video_count,
        "thumbnail": thumbnail,
        "first_video_id": first_video_id,
        "playlist_id": playlist_id,
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
            else:
                # Playlist/mix items use lockupViewModel
                lvm = item.get("lockupViewModel")
                if lvm:
                    parsed = _parse_lockup_view_model(lvm)
                    if parsed:
                        results.append(parsed)

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
                continue

            lvm = item.get("lockupViewModel")
            if lvm:
                parsed = _parse_lockup_view_model(lvm)
                if parsed:
                    results.append(parsed)
                continue

            # Also check inside itemSectionRenderer (some responses nest further)
            section_items = item.get("itemSectionRenderer", {}).get("contents", [])
            for sub_item in section_items:
                renderer = sub_item.get("videoRenderer")
                if renderer:
                    video = _parse_video_renderer(renderer)
                    if video:
                        results.append(video)
                elif sub_item.get("lockupViewModel"):
                    parsed = _parse_lockup_view_model(sub_item["lockupViewModel"])
                    if parsed:
                        results.append(parsed)

        token = _extract_continuation_token(items)

    return results, token


# ── Handle → Channel ID ──────────────────────────────────────────────────────

_handle_cache: dict[str, str] = {}  # @handle → UCXXXX


async def resolve_handle(handle: str) -> str | None:
    """Resolve a YouTube @handle to a channel ID (UCXXXX).

    Uses InnerTube navigation/resolve_url endpoint.  Results are cached
    in-memory (handles don't change).
    Returns channel ID or None if not found.
    """
    handle_lower = handle.lower()
    if handle_lower in _handle_cache:
        return _handle_cache[handle_lower]

    url = f"https://www.youtube.com/@{handle}"
    try:
        data = await _innertube_post("navigation/resolve_url", {"url": url})
        browse_id = (data.get("endpoint", {})
                     .get("browseEndpoint", {})
                     .get("browseId"))
        if browse_id and browse_id.startswith("UC"):
            _handle_cache[handle_lower] = browse_id
            return browse_id
    except Exception as e:
        log.error(f"Handle resolve error for @{handle}: {e}")
    return None


# ── Channel ──────────────────────────────────────────────────────────────────

# Protobuf-encoded params for the "Videos" tab, sorted by "Recently uploaded"
_CHANNEL_VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


async def channel_first(channel_id: str) -> tuple[str, str, str, list[dict], str | None]:
    """Initial channel videos request.

    POST youtubei/v1/browse with browseId + Videos tab params.
    Returns (channel_name, avatar_url, subscriber_count, results, continuation_token).
    """
    data = await _innertube_post("browse", {
        "browseId": channel_id,
        "params": _CHANNEL_VIDEOS_PARAMS,
    })

    # Channel name from metadata or header
    channel_name = (data.get("metadata", {})
                    .get("channelMetadataRenderer", {})
                    .get("title", "Unknown"))

    # Channel avatar
    avatar_url = ""
    avatar_data = (data.get("metadata", {})
                   .get("channelMetadataRenderer", {})
                   .get("avatar", {})
                   .get("thumbnails", []))
    if avatar_data:
        avatar_url = avatar_data[-1].get("url", "")

    # Subscriber count from header
    subscriber_count = ""
    header = data.get("header", {})
    for renderer_key in ("c4TabbedHeaderRenderer", "pageHeaderRenderer"):
        hdr = header.get(renderer_key, {})
        sub_text = hdr.get("subscriberCountText", {})
        if isinstance(sub_text, dict):
            subscriber_count = sub_text.get("simpleText", "") or "".join(r.get("text", "") for r in sub_text.get("runs", []))
            break
    if not subscriber_count:
        # pageHeaderRenderer stores it in content → metadata
        meta_rows = (header.get("pageHeaderRenderer", {})
                     .get("content", {})
                     .get("pageHeaderViewModel", {})
                     .get("metadata", {})
                     .get("contentMetadataViewModel", {})
                     .get("metadataRows", []))
        for row in meta_rows:
            for part in row.get("metadataParts", []):
                txt = part.get("text", {}).get("content", "")
                if "subscriber" in txt.lower():
                    subscriber_count = txt
                    break
            if subscriber_count:
                break

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

        # Fallback: sectionListRenderer (some channels still use the older layout)
        if not results:
            section_contents = (tab_renderer
                                .get("content", {})
                                .get("sectionListRenderer", {})
                                .get("contents", []))
            for section in section_contents:
                items = (section
                         .get("itemSectionRenderer", {})
                         .get("contents", []))
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

    return channel_name, avatar_url, subscriber_count, results, token


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
            elif item.get("gridVideoRenderer"):
                # Older layout fallback
                renderer = item["gridVideoRenderer"]
                video = _parse_video_renderer(renderer)
                if video:
                    results.append(video)

        token = _extract_continuation_token(items)

    return results, token


# ── Related Videos ───────────────────────────────────────────────────────────

def _parse_related_video(vm: dict, content_id: str) -> dict | None:
    """Extract a regular video from a lockupViewModel in related results."""
    metadata = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = metadata.get("title", {}).get("content", "")
    if not title:
        return None

    channel = _extract_lockup_channel(metadata)
    duration_str = _extract_lockup_duration(vm)

    return {
        "id": content_id,
        "title": title,
        "channel": channel,
        "duration_str": duration_str,
        "thumbnail": f"https://i.ytimg.com/vi/{content_id}/mqdefault.jpg",
    }


def _extract_yt_initial_data(html: str) -> dict | None:
    """Extract ytInitialData JSON from YouTube watch page HTML."""
    match = re.search(r"var ytInitialData\s*=\s*\{", html)
    if not match:
        return None

    start = match.end() - 1
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
        if ch == '"':
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

    try:
        return json.loads(html[start:end])
    except (json.JSONDecodeError, ValueError):
        return None


async def fetch_related(video_id: str) -> list[dict]:
    """Fetch related videos and mixes for a given video ID.

    GET youtube.com/watch?v=ID → parse ytInitialData from HTML.
    Navigates twoColumnWatchNextResults → secondaryResults.
    Includes mixes (RD*) alongside regular videos.
    Dedup: if a mix's first video also exists standalone, remove standalone.

    Returns list of dicts (may be empty on error).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        resp = await http_client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })

        data = _extract_yt_initial_data(resp.text)
        if not data:
            return []

        contents = data.get("contents", {}).get("twoColumnWatchNextResults", {})
        secondary = (contents
                     .get("secondaryResults", {})
                     .get("secondaryResults", {})
                     .get("results", []))

        related = []
        mix_first_video_ids = set()

        for item in secondary:
            if "lockupViewModel" not in item:
                continue
            vm = item["lockupViewModel"]
            content_id = vm.get("contentId", "")

            if content_id.startswith("RD"):
                # Parse as mix
                parsed = _parse_lockup_view_model(vm)
                if parsed:
                    related.append(parsed)
                    mix_first_video_ids.add(parsed["first_video_id"])
                continue

            if content_id.startswith("PL"):
                # Skip playlists in related (per plan: related has videos + mixes only)
                continue

            # Regular video — parse metadata inline (videos don't have
            # watchEndpoint so _parse_lockup_view_model would reject them)
            video = _parse_related_video(vm, content_id)
            if video:
                related.append(video)

        # Dedup: remove standalone videos whose ID matches a mix's first video
        if mix_first_video_ids:
            related = [r for r in related
                       if r.get("type") or r["id"] not in mix_first_video_ids]

        return related

    except Exception as e:
        log.error(f"Related videos error: {e}")
        return []


# ── Playlist/Mix Contents ────────────────────────────────────────────────────

async def fetch_playlist_contents(video_id: str, playlist_id: str) -> dict:
    """Fetch playlist/mix contents from YouTube watch page.

    GET youtube.com/watch?v={video_id}&list={playlist_id}
    Parses ytInitialData → twoColumnWatchNextResults → playlist.playlist.contents[]

    Returns {"title": str, "videos": [...]}.
    """
    url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"

    try:
        resp = await http_client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })

        data = _extract_yt_initial_data(resp.text)
        if not data:
            return {"title": "", "videos": []}

        playlist_data = (data
                         .get("contents", {})
                         .get("twoColumnWatchNextResults", {})
                         .get("playlist", {})
                         .get("playlist", {}))

        title = playlist_data.get("title", "")
        contents = playlist_data.get("contents", [])

        videos = []
        for item in contents:
            renderer = item.get("playlistPanelVideoRenderer", {})
            vid = renderer.get("videoId", "")
            if not vid:
                continue

            title_obj = renderer.get("title", {})
            vtitle = title_obj.get("simpleText", "")
            if not vtitle:
                vtitle_runs = title_obj.get("runs", [])
                vtitle = vtitle_runs[0].get("text", "") if vtitle_runs else ""

            vchannel = ""
            short_byline = renderer.get("shortBylineText", {}).get("runs", [])
            if short_byline:
                vchannel = short_byline[0].get("text", "")

            duration_text = renderer.get("lengthText", {})
            vduration_str = duration_text.get("simpleText", "")
            if not vduration_str:
                runs = duration_text.get("runs", [])
                if runs:
                    vduration_str = runs[0].get("text", "")

            videos.append({
                "id": vid,
                "title": vtitle,
                "channel": vchannel,
                "duration_str": vduration_str,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

        return {"title": title, "videos": videos}

    except Exception as e:
        log.error(f"Playlist contents error: {e}")
        return {"title": "", "videos": []}


# ── Channel Playlists Tab ────────────────────────────────────────────────────

# Protobuf-encoded params for the "Playlists" tab
_CHANNEL_PLAYLISTS_PARAMS = "EglwbGF5bGlzdHPyBgQKAkIA"


async def channel_playlists_first(channel_id: str) -> tuple[str, list[dict], str | None]:
    """Initial channel playlists request.

    POST youtubei/v1/browse with browseId + Playlists tab params.
    Returns (channel_name, results, continuation_token).
    """
    data = await _innertube_post("browse", {
        "browseId": channel_id,
        "params": _CHANNEL_PLAYLISTS_PARAMS,
    })

    channel_name = (data.get("metadata", {})
                    .get("channelMetadataRenderer", {})
                    .get("title", "Unknown"))

    results = []
    token = None

    tabs = (data
            .get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", []))

    for tab in tabs:
        tab_renderer = tab.get("tabRenderer", {})
        if not tab_renderer.get("selected", False):
            continue

        # richGridRenderer path (modern layout)
        grid_items = (tab_renderer
                      .get("content", {})
                      .get("richGridRenderer", {})
                      .get("contents", []))

        for item in grid_items:
            rich_item = item.get("richItemRenderer", {})
            lvm = rich_item.get("content", {}).get("lockupViewModel")
            if lvm:
                parsed = _parse_lockup_view_model(lvm)
                if parsed:
                    results.append(parsed)

        token = _extract_continuation_token(grid_items)

        # Fallback: sectionListRenderer (some channels still use the older layout)
        if not results:
            section_contents = (tab_renderer
                                .get("content", {})
                                .get("sectionListRenderer", {})
                                .get("contents", []))
            for section in section_contents:
                items = (section
                         .get("itemSectionRenderer", {})
                         .get("contents", []))
                for cont in items:
                    grid = cont.get("gridRenderer", {}).get("items", [])
                    for grid_item in grid:
                        lvm = grid_item.get("lockupViewModel")
                        if lvm:
                            parsed = _parse_lockup_view_model(lvm)
                            if parsed:
                                results.append(parsed)
                    if not token:
                        token = _extract_continuation_token(grid)

        break

    return channel_name, results, token


async def channel_playlists_next(continuation_token: str) -> tuple[list[dict], str | None]:
    """Paginated channel playlists request using a continuation token."""
    data = await _innertube_post("browse", {
        "continuation": continuation_token,
    })

    results = []
    token = None

    actions = (data.get("onResponseReceivedActions", [])
               or data.get("onResponseReceivedCommands", []))

    for action in actions:
        items = (action.get("appendContinuationItemsAction", {})
                 .get("continuationItems", []))
        for item in items:
            rich_item = item.get("richItemRenderer", {})
            lvm = rich_item.get("content", {}).get("lockupViewModel")
            if not lvm:
                # Fallback: direct lockupViewModel (older layout)
                lvm = item.get("lockupViewModel")
            if lvm:
                parsed = _parse_lockup_view_model(lvm)
                if parsed:
                    results.append(parsed)

        token = _extract_continuation_token(items)

    return results, token
