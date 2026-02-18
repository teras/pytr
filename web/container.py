"""Container probing: parse MP4 (ISO BMFF) and WebM (EBML) to find init/index ranges."""
import logging
import struct

import httpx

log = logging.getLogger(__name__)


# ── MP4 ISO BMFF parser ─────────────────────────────────────────────────────

def parse_mp4_ranges(data: bytes) -> dict:
    """Parse MP4 box headers to find initRange (moov) and indexRange (sidx)."""
    offset = 0
    boxes = []
    while offset < len(data) - 8:
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        box_type = data[offset + 4:offset + 8].decode('ascii', errors='replace')
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack('>Q', data[offset + 8:offset + 16])[0]
        elif size == 0:
            size = len(data) - offset
        if size < 8:
            break
        boxes.append({'type': box_type, 'offset': offset, 'size': size})
        offset += size

    result = {}
    for box in boxes:
        if box['type'] == 'moov':
            result['init_end'] = box['offset'] + box['size'] - 1
        elif box['type'] == 'sidx':
            result['index_start'] = box['offset']
            result['index_end'] = box['offset'] + box['size'] - 1
    return result


# ── WebM EBML parser ────────────────────────────────────────────────────────

_EBML_HEADER = 0x1A45DFA3
_SEGMENT     = 0x18538067
_TRACKS      = 0x1654AE6B
_CUES        = 0x1C53BB6B
_CLUSTER     = 0x1F43B675


def _read_vint(data: bytes, pos: int) -> tuple[int | None, int]:
    """Read a variable-length integer (VINT) from EBML data."""
    if pos >= len(data):
        return None, 0
    first = data[pos]
    if first == 0:
        return None, 0
    length = 1
    mask = 0x80
    while (first & mask) == 0 and length < 8:
        length += 1
        mask >>= 1
    value = first & (mask - 1)
    for i in range(1, length):
        if pos + i >= len(data):
            return None, 0
        value = (value << 8) | data[pos + i]
    return value, length


def _read_element_id(data: bytes, pos: int) -> tuple[int | None, int]:
    """Read an EBML element ID."""
    if pos >= len(data):
        return None, 0
    first = data[pos]
    if first == 0:
        return None, 0
    length = 1
    mask = 0x80
    while (first & mask) == 0 and length < 4:
        length += 1
        mask >>= 1
    eid = 0
    for i in range(length):
        if pos + i >= len(data):
            return None, 0
        eid = (eid << 8) | data[pos + i]
    return eid, length


def parse_webm_ranges(data: bytes) -> dict:
    """Parse WebM EBML structure to find init (through Tracks) and index (Cues) ranges.

    Returns dict with init_end, index_start, index_end (same keys as MP4 parser).
    """
    pos = 0

    # EBML header
    eid, eid_len = _read_element_id(data, pos)
    if eid != _EBML_HEADER:
        return {}
    pos += eid_len
    size, size_len = _read_vint(data, pos)
    if size is None:
        return {}
    pos += size_len + size

    # Segment
    eid, eid_len = _read_element_id(data, pos)
    if eid != _SEGMENT:
        return {}
    pos += eid_len
    seg_size, size_len = _read_vint(data, pos)
    if seg_size is None:
        return {}
    pos += size_len

    end = min(pos + seg_size, len(data)) if seg_size and seg_size < len(data) else len(data)

    result = {}
    while pos < end:
        elem_start = pos
        eid, eid_len = _read_element_id(data, pos)
        if eid is None:
            break
        pos += eid_len
        size, size_len = _read_vint(data, pos)
        if size is None:
            break
        pos += size_len

        if eid == _TRACKS:
            # init segment goes from 0 to end of Tracks
            result['init_end'] = pos + size - 1
        elif eid == _CUES:
            result['index_start'] = elem_start
            result['index_end'] = pos + size - 1
            log.debug(f"WebM Cues at bytes {elem_start}-{pos + size - 1} "
                       f"({(pos + size - elem_start) / 1024:.1f} KB)")
            break
        elif eid == _CLUSTER:
            log.debug(f"WebM hit Cluster before Cues at byte {elem_start}")
            break

        pos += size

    return result


# ── Unified async prober ────────────────────────────────────────────────────

async def probe_ranges(url: str) -> dict | None:
    """Probe a URL to find init/index ranges. Auto-detects MP4 vs WebM.

    For MP4: fetches first 4KB (boxes are at the start).
    For WebM: fetches up to 2MB, then 10MB if Cues not found in first pass.

    Returns dict with 'init_end', 'index_start', 'index_end' or None on failure.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            # First fetch: 4KB is enough for MP4, and enough to detect WebM
            resp = await client.get(url, headers={'Range': 'bytes=0-4095'})
            if resp.status_code not in (200, 206):
                return None

            content_type = resp.headers.get('content-type', '')
            data = resp.content

            # Detect container type
            is_webm = ('webm' in content_type
                       or data[:4] == b'\x1a\x45\xdf\xa3')  # EBML magic

            if not is_webm:
                # MP4: 4KB is sufficient
                result = parse_mp4_ranges(data)
                return result if result.get('init_end') else None

            # WebM: need more data for Cues
            for fetch_size in [2 * 1024 * 1024, 10 * 1024 * 1024]:
                resp = await client.get(
                    url, headers={'Range': f'bytes=0-{fetch_size - 1}'}
                )
                if resp.status_code not in (200, 206):
                    return None
                result = parse_webm_ranges(resp.content)
                if result.get('index_start'):
                    log.info(f"WebM probed OK: init=0-{result['init_end']}, "
                             f"cues={result['index_start']}-{result['index_end']}")
                    return result

            # Cues not found even in 10MB — use init only (no seeking)
            if result.get('init_end'):
                log.warning("WebM Cues not found, seeking will not work")
                return result
            return None

    except Exception as e:
        log.warning(f"Probe failed: {e}")
        return None
