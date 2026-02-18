"""Browse routes: search, channel, related videos."""
import asyncio
import json as json_module
import logging
import re

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException, Query, Depends

from auth import require_auth
from helpers import YDL_OPTS, ydl_search, _yt_url, _format_duration

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/search")
async def search(q: str = Query(..., min_length=1), count: int = Query(default=10, ge=1), auth: bool = Depends(require_auth)):
    """Search YouTube."""
    try:
        count = min(count, 100)
        result = ydl_search.extract_info(f"ytsearch{count}:{q}", download=False)

        videos = []
        for entry in result.get('entries', []):
            if not entry:
                continue
            vid = entry.get('id', '')
            duration = entry.get('duration') or 0
            videos.append({
                'id': vid,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': _format_duration(duration),
                'channel': entry.get('channel') or entry.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

        return {'results': videos}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/related/{video_id}")
async def get_related_videos(video_id: str, auth: bool = Depends(require_auth)):
    """Get related videos for a video."""
    try:
        url = _yt_url(video_id)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
            html = resp.text

        match = re.search(r'var ytInitialData = ({.*?});', html)
        if not match:
            return {'results': []}

        data = json_module.loads(match.group(1))

        contents = data.get('contents', {}).get('twoColumnWatchNextResults', {})
        secondary = contents.get('secondaryResults', {}).get('secondaryResults', {}).get('results', [])

        related = []
        for item in secondary:
            if 'lockupViewModel' in item:
                vm = item['lockupViewModel']
                content_id = vm.get('contentId', '')

                if content_id.startswith('RD'):
                    continue

                metadata = vm.get('metadata', {}).get('lockupMetadataViewModel', {})
                title = metadata.get('title', {}).get('content', '')

                channel = ''
                metadata_rows = metadata.get('metadata', {}).get('contentMetadataViewModel', {}).get('metadataRows', [])
                if metadata_rows:
                    for row in metadata_rows:
                        parts = row.get('metadataParts', [])
                        if parts:
                            channel = parts[0].get('text', {}).get('content', '')
                            break

                duration_str = ''
                content_image = vm.get('contentImage', {}).get('collectionThumbnailViewModel', {})
                primary_thumb = content_image.get('primaryThumbnail', {}).get('thumbnailViewModel', {})
                overlays = primary_thumb.get('overlays', [])
                for overlay in overlays:
                    badge = overlay.get('thumbnailOverlayBadgeViewModel', {})
                    for b in badge.get('thumbnailBadges', []):
                        if 'thumbnailBadgeViewModel' in b:
                            duration_str = b['thumbnailBadgeViewModel'].get('text', '')
                            break

                if content_id and title:
                    related.append({
                        'id': content_id,
                        'title': title,
                        'channel': channel,
                        'duration_str': duration_str,
                        'thumbnail': f"https://i.ytimg.com/vi/{content_id}/mqdefault.jpg",
                    })

        return {'results': related}

    except Exception as e:
        log.error(f"Related videos error: {e}")
        return {'results': []}


@router.get("/channel/{channel_id}")
async def get_channel_videos(
    channel_id: str,
    count: int = Query(default=10, ge=1, le=50),
    auth: bool = Depends(require_auth)
):
    """Get videos from a channel."""
    try:
        url = f"https://www.youtube.com/channel/{channel_id}/videos"

        ydl_opts = {
            **YDL_OPTS,
            'extract_flat': True,
            'playlistend': count,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = await asyncio.to_thread(ydl.extract_info, url, download=False)

        videos = []
        for entry in result.get('entries', []):
            if not entry:
                continue
            vid = entry.get('id', '')
            duration = entry.get('duration') or 0
            videos.append({
                'id': vid,
                'title': entry.get('title', 'Unknown'),
                'duration': duration,
                'duration_str': _format_duration(duration),
                'channel': result.get('channel') or result.get('uploader', 'Unknown'),
                'thumbnail': f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })

        return {
            'channel': result.get('channel') or result.get('uploader', 'Unknown'),
            'channel_id': channel_id,
            'results': videos
        }

    except Exception as e:
        log.error(f"Channel videos error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
