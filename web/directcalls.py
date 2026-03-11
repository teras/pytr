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

import asyncio
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


_detected_region: str | None = None
_detected_lang: str | None = None


async def _detect_region() -> tuple[str | None, str]:
    """Detect server region + language via free geo IP service. Cached after first call.

    Returns (country_code, language_code). Primary language extracted from
    ipapi.co's 'languages' field (e.g. "el-GR,en,fr" → "el").
    """
    global _detected_region, _detected_lang
    if _detected_region is not None:
        return (_detected_region or None, _detected_lang or "en")

    # ipapi.co returns both country_code and languages in one call
    try:
        resp = await http_client.get("https://ipapi.co/json/", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        code = data.get("country_code", "")
        # languages is like "el-GR,en,fr" — take the primary language code
        langs = data.get("languages", "")
        lang = langs.split(",")[0].split("-")[0].strip() if langs else ""
        if re.fullmatch(r'[A-Z]{2}', code):
            _detected_region = code
            _detected_lang = lang if re.fullmatch(r'[a-z]{2}', lang) else "en"
            log.info(f"Detected server region: {code}, language: {_detected_lang}")
            return (code, _detected_lang)
    except Exception as e:
        log.warning(f"Region detection failed (ipapi.co): {e}")

    # Fallback: ip-api.com (country only, no language)
    try:
        resp = await http_client.get(
            "http://ip-api.com/json/?fields=countryCode", timeout=5.0)
        resp.raise_for_status()
        code = resp.json().get("countryCode", "")
        if re.fullmatch(r'[A-Z]{2}', code):
            _detected_region = code
            _detected_lang = "en"
            log.info(f"Detected server region: {code}, language: en (fallback)")
            return (code, "en")
    except Exception as e:
        log.warning(f"Region detection failed (ip-api.com): {e}")

    _detected_region = ""
    _detected_lang = "en"
    log.warning("Could not detect region, omitting gl parameter")
    return (None, "en")


def _build_context(version: str, gl: str | None = None, hl: str = "en") -> dict:
    ctx = {
        "client": {
            "clientName": "WEB",
            "clientVersion": version,
            "hl": hl,
        }
    }
    if gl:
        ctx["client"]["gl"] = gl
    return ctx


def _build_headers(version: str) -> dict:
    return {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": version,
    }


# ── Response parsers ─────────────────────────────────────────────────────────

def _is_live_renderer(renderer: dict) -> bool:
    """Detect live status from a videoRenderer/gridVideoRenderer.

    Checks two YouTube signals (both use internal enum values, not localized):
    1. badges[] → metadataBadgeRenderer.style == "BADGE_STYLE_TYPE_LIVE_NOW"
    2. thumbnailOverlays[] → thumbnailOverlayTimeStatusRenderer.style == "LIVE"
    """
    if any(
        b.get("metadataBadgeRenderer", {}).get("style") == "BADGE_STYLE_TYPE_LIVE_NOW"
        for b in renderer.get("badges", [])
    ):
        return True
    for overlay in renderer.get("thumbnailOverlays", []):
        if overlay.get("thumbnailOverlayTimeStatusRenderer", {}).get("style") == "LIVE":
            return True
    return False


def _extract_duration_str(renderer: dict) -> str:
    """Extract duration string from lengthText or thumbnailOverlay fallback."""
    duration_text = renderer.get("lengthText", {})
    duration_str = duration_text.get("simpleText", "")
    if not duration_str:
        runs = duration_text.get("runs", [])
        if runs:
            duration_str = runs[0].get("text", "")
    # Fallback: thumbnailOverlayTimeStatusRenderer (some pages omit lengthText)
    if not duration_str:
        for overlay in renderer.get("thumbnailOverlays", []):
            tsr = overlay.get("thumbnailOverlayTimeStatusRenderer", {})
            if tsr.get("style") != "LIVE":
                text = tsr.get("text", {}).get("simpleText", "")
                if text:
                    duration_str = text
                    break
    return duration_str


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

    duration_str = _extract_duration_str(renderer)

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

    # View count: short form like "1.4M views"
    views_obj = renderer.get("shortViewCountText", {})
    views = views_obj.get("simpleText", "")
    if not views:
        runs = views_obj.get("runs", [])
        if runs:
            views = "".join(r.get("text", "") for r in runs)

    is_live = _is_live_renderer(renderer)

    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "duration_str": duration_str or ("" if is_live else _format_duration(duration)),
        "channel": channel or "Unknown",
        "published": published,
        "views": views,
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


def _extract_lockup_metadata(metadata: dict) -> dict:
    """Extract all metadata parts from lockupMetadataViewModel's metadata rows.

    Returns dict with channel, views, date extracted from metadataRows.
    Layout is positional: row 0 = [channel], row 1 = [views, date].
    """
    rows = (metadata
            .get("metadata", {})
            .get("contentMetadataViewModel", {})
            .get("metadataRows", []))

    result = {"channel": "", "views": "", "date": ""}

    # Row 0, part 0: channel name
    if rows:
        parts0 = rows[0].get("metadataParts", [])
        if parts0:
            result["channel"] = parts0[0].get("text", {}).get("content", "")

    # Row 1: views (part 0) and date (part 1)
    if len(rows) > 1:
        parts1 = rows[1].get("metadataParts", [])
        if parts1:
            result["views"] = parts1[0].get("text", {}).get("content", "")
        if len(parts1) > 1:
            result["date"] = parts1[1].get("text", {}).get("content", "")

    return result


def _extract_lockup_overlay(vm: dict) -> tuple[str, bool]:
    """Extract duration/count string and live status from lockupViewModel overlay badges.

    Returns (text, is_live). Uses badgeStyle to detect live streams
    (language-independent) instead of matching badge text.
    """
    content_image = vm.get("contentImage", {})
    thumb_vm = (content_image.get("thumbnailViewModel")
                or content_image.get("collectionThumbnailViewModel", {})
                .get("primaryThumbnail", {}).get("thumbnailViewModel")
                or {})
    for overlay in thumb_vm.get("overlays", []):
        # New format: thumbnailBottomOverlayViewModel.badges[]
        bottom = overlay.get("thumbnailBottomOverlayViewModel", {})
        for b in bottom.get("badges", []):
            bvm = b.get("thumbnailBadgeViewModel", {})
            is_live = bvm.get("badgeStyle") == "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE"
            text = bvm.get("text", "")
            if text or is_live:
                return text, is_live
        # Legacy format: thumbnailOverlayBadgeViewModel.thumbnailBadges[]
        badge = overlay.get("thumbnailOverlayBadgeViewModel", {})
        for b in badge.get("thumbnailBadges", []):
            if "thumbnailBadgeViewModel" in b:
                bvm = b["thumbnailBadgeViewModel"]
                is_live = bvm.get("badgeStyle") == "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE"
                text = bvm.get("text", "")
                if text or is_live:
                    return text, is_live
    return "", False


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
    video_count, _ = _extract_lockup_overlay(vm)

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
    # Music uses watchPlaylistEndpoint (no first videoId, just playlistId)
    if not first_video_id:
        wpl_ep = inner_cmd.get("watchPlaylistEndpoint", {})
        if wpl_ep:
            playlist_id = wpl_ep.get("playlistId", "")

    if not first_video_id and not playlist_id:
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

    Automatically injects 'context' with the current client version and
    detected region (gl). Callers can override by providing their own 'context'.
    """
    version = await _fetch_client_version()
    gl, hl = await _detect_region()
    body.setdefault("context", _build_context(version, gl=gl, hl=hl))
    body.setdefault("racyCheckOk", True)
    body.setdefault("contentCheckOk", True)
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
        # Layout is positional: row 0 = [@handle], row 1 = [subscribers, videos]
        meta_rows = (header.get("pageHeaderRenderer", {})
                     .get("content", {})
                     .get("pageHeaderViewModel", {})
                     .get("metadata", {})
                     .get("contentMetadataViewModel", {})
                     .get("metadataRows", []))
        if len(meta_rows) > 1:
            parts = meta_rows[1].get("metadataParts", [])
            if parts:
                subscriber_count = parts[0].get("text", {}).get("content", "")

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


# ── Trending / Discover ──────────────────────────────────────────────────────

# YouTube topic channels (work without authentication)
_TRENDING_CHANNELS = {
    "gaming":  "UCOpNcN46UbXVtpKMrmU4Abg",
    "news":    "UCYfdidRxbB8Qhf0Nx7ioOYw",
    "sports":  "UCEgdi0XIXXZ-qJOFPf4JSKw",
    "live":    "UC4R8DWoMoI7CAwX8_LjQHig",
    "music":   "UC-9-kyTW8ZkZNDHQJ6FgpwQ",
}

# Protobuf: field 2 = "trending"
_TRENDING_PARAMS = "Egh0cmVuZGluZw=="


def _parse_grid_video_renderer(renderer: dict) -> dict | None:
    """Extract video info from a gridVideoRenderer object."""
    video_id = renderer.get("videoId")
    if not video_id:
        return None

    title_runs = renderer.get("title", {}).get("runs", [])
    title = title_runs[0].get("text", "") if title_runs else ""

    channel = ""
    for key in ("shortBylineText", "longBylineText", "ownerText"):
        ch_runs = renderer.get(key, {}).get("runs", [])
        if ch_runs:
            channel = ch_runs[0].get("text", "")
            break

    views = renderer.get("viewCountText", {}).get("simpleText", "")
    published = renderer.get("publishedTimeText", {}).get("simpleText", "")

    duration_str = _extract_duration_str(renderer)

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

    is_live = _is_live_renderer(renderer)

    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "duration_str": duration_str or ("" if is_live else _format_duration(duration)),
        "channel": channel or "Unknown",
        "published": published,
        "is_live": is_live,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    }


async def fetch_trending(category: str, hl: str | None = None, gl: str | None = None) -> list[dict]:
    """Fetch trending videos for a category via YouTube topic channels.

    Uses InnerTube browse API with special YouTube topic channel IDs.
    No authentication required. Region auto-detected from server IP.
    Optional hl/gl overrides for content language/region (user preferences).

    Returns list of video/playlist dicts (may be empty on error).
    """
    browse_id = _TRENDING_CHANNELS.get(category)
    if not browse_id:
        return []

    try:
        body = {"browseId": browse_id, "params": _TRENDING_PARAMS}
        if hl or gl:
            # Override auto-detected values with user preferences
            version = await _fetch_client_version()
            auto_gl, auto_hl = await _detect_region()
            body["context"] = _build_context(version, gl=gl or auto_gl, hl=hl or auto_hl)
        data = await _innertube_post("browse", body)
    except Exception as e:
        log.error(f"Trending fetch error ({category}): {e}")
        return []

    results = []
    tabs = (data
            .get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", []))

    for tab in tabs:
        content = tab.get("tabRenderer", {}).get("content", {})

        # Path 1: sectionListRenderer → shelfRenderer → gridRenderer/expandedShelf
        for section in content.get("sectionListRenderer", {}).get("contents", []):
            for item in section.get("itemSectionRenderer", {}).get("contents", []):
                shelf = item.get("shelfRenderer", {})
                if not shelf:
                    continue
                shelf_content = shelf.get("content", {})

                for g in shelf_content.get("gridRenderer", {}).get("items", []):
                    gvr = g.get("gridVideoRenderer")
                    if gvr:
                        video = _parse_grid_video_renderer(gvr)
                        if video:
                            results.append(video)

                for g in shelf_content.get("expandedShelfContentsRenderer", {}).get("items", []):
                    vr = g.get("videoRenderer")
                    if vr:
                        video = _parse_video_renderer(vr)
                        if video:
                            results.append(video)

        # Path 2: richGridRenderer → richItemRenderer/richSectionRenderer
        for item in content.get("richGridRenderer", {}).get("contents", []):
            ri = item.get("richItemRenderer", {})
            if ri:
                vr = ri.get("content", {}).get("videoRenderer")
                if vr:
                    video = _parse_video_renderer(vr)
                    if video:
                        results.append(video)

            rshelf = (item.get("richSectionRenderer", {})
                      .get("content", {})
                      .get("richShelfRenderer", {}))
            if rshelf:
                for si in rshelf.get("contents", []):
                    ri2 = si.get("richItemRenderer", {})
                    vr2 = ri2.get("content", {}).get("videoRenderer")
                    if vr2:
                        video = _parse_video_renderer(vr2)
                        if video:
                            results.append(video)
                    # Music uses lockupViewModel (playlists)
                    lvm = ri2.get("content", {}).get("lockupViewModel")
                    if lvm:
                        parsed = _parse_lockup_view_model(lvm)
                        if parsed:
                            results.append(parsed)

    return results


# ── Related Videos ───────────────────────────────────────────────────────────

def _parse_related_video(vm: dict, content_id: str) -> dict | None:
    """Extract a regular video from a lockupViewModel in related results."""
    metadata = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = metadata.get("title", {}).get("content", "")
    if not title:
        return None

    meta = _extract_lockup_metadata(metadata)
    duration_str, is_live = _extract_lockup_overlay(vm)

    result = {
        "id": content_id,
        "title": title,
        "channel": meta["channel"],
        "views": meta["views"],
        "date": meta["date"],
        "duration_str": "" if is_live else duration_str,
        "thumbnail": f"https://i.ytimg.com/vi/{content_id}/mqdefault.jpg",
    }
    if is_live:
        result["is_live"] = True
    return result


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

            vduration_str = _extract_duration_str(renderer)

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
