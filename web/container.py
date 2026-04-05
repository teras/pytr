# Copyright (c) 2026 Panayotis Katsaloulis
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Container probing: parse MP4 (ISO BMFF) and WebM (EBML) to find init/index ranges."""
import logging
import struct

from helpers import http_client

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

_EBML_HEADER         = 0x1A45DFA3
_SEGMENT             = 0x18538067
_TRACKS              = 0x1654AE6B
_CUES                = 0x1C53BB6B
_CLUSTER             = 0x1F43B675
_CUE_POINT           = 0xBB
_CUE_TIME            = 0xB3
_CUE_TRACK_POSITIONS = 0xB7
_CUE_CLUSTER_POS     = 0xF1


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


def _parse_cue_points(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    """Walk CuePoints inside the Cues element and return [(time_ms, cluster_pos), ...].

    Consumed by dash.py to emit an explicit SegmentList for WebM (works around
    a dash.js bug where the last SegmentBase+Cues segment gets its trailing
    frames truncated — see the long comment in dash.py's MPD builder).

    `cluster_pos` is RELATIVE to the Segment element data offset (per WebM
    spec); parse_webm_ranges converts it to an absolute file offset before
    passing it along.
    """
    cues: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        eid, eid_len = _read_element_id(data, pos)
        if eid is None:
            break
        pos += eid_len
        size, size_len = _read_vint(data, pos)
        if size is None:
            break
        pos += size_len
        if eid == _CUE_POINT:
            cue_time = None
            cluster_pos = None
            p = pos
            cp_end = pos + size
            while p < cp_end:
                cid, cid_len = _read_element_id(data, p)
                if cid is None:
                    break
                p += cid_len
                csize, csize_len = _read_vint(data, p)
                if csize is None:
                    break
                p += csize_len
                if cid == _CUE_TIME:
                    cue_time = int.from_bytes(data[p:p + csize], 'big')
                elif cid == _CUE_TRACK_POSITIONS:
                    # Nested: look for CueClusterPosition
                    pp = p
                    tp_end = p + csize
                    while pp < tp_end:
                        gcid, gcid_len = _read_element_id(data, pp)
                        if gcid is None:
                            break
                        pp += gcid_len
                        gsize, gsize_len = _read_vint(data, pp)
                        if gsize is None:
                            break
                        pp += gsize_len
                        if gcid == _CUE_CLUSTER_POS:
                            cluster_pos = int.from_bytes(data[pp:pp + gsize], 'big')
                        pp += gsize
                p += csize
            if cue_time is not None and cluster_pos is not None:
                cues.append((cue_time, cluster_pos))
        pos += size
    return cues


def parse_webm_ranges(data: bytes) -> dict:
    """Parse WebM EBML structure to find init (through Tracks) and index (Cues) ranges.

    Returns dict with init_end, index_start, index_end (same keys as MP4 parser).
    For WebM with Cues also returns cues=[(time_ms, absolute_byte_pos), ...] —
    consumed by dash.py to emit an explicit SegmentList (see comments there).
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
    segment_data_offset = pos

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
            # Parse all CuePoints (time + cluster position). Used by dash.py
            # to emit an explicit SegmentList — this is the ONLY thing that
            # makes dash.js play the last WebM cluster correctly (see the
            # long comment in dash.py's MPD builder for the full story).
            # CueClusterPosition is relative to the Segment element data
            # offset per WebM spec, so convert each to an absolute file
            # offset here — downstream code can use them as-is in byte-range
            # requests without needing to know about WebM internals.
            #
            # Only emit `cues` if the whole Cues element is within the fetched
            # buffer. Otherwise a partial parse would return a truncated list
            # and downstream code would build a broken SegmentList (massive
            # final segment covering everything unparsed). The `cues_complete`
            # flag lets probe_ranges detect this and re-fetch with more bytes.
            if pos + size <= len(data):
                raw_cues = _parse_cue_points(data, pos, pos + size)
                if raw_cues:
                    result['cues'] = [
                        (t, segment_data_offset + p) for (t, p) in raw_cues
                    ]
                result['cues_complete'] = True
            else:
                result['cues_complete'] = False
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
    For WebM: fetches 4KB, reads the Cues element size header, then targeted-
    fetches exactly `[0..index_end]` so the whole Cues element is covered
    regardless of how large it is.

    Returns dict with 'init_end', 'index_start', 'index_end' or None on failure.
    """
    try:
        # First fetch: 4KB — enough for MP4, enough to detect WebM and usually
        # enough to read the EBML/Segment/Tracks headers + Cues size vint.
        resp = await http_client.get(url, headers={'Range': 'bytes=0-4095'})
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

        # WebM: parse the initial 4KB — best case we have the whole Cues here.
        result = parse_webm_ranges(data)
        if (result.get('index_start')
                and result.get('cues_complete')
                and result.get('init_end')):
            log.info("WebM probed OK from initial 4KB")
            return result

        # We may have learned index_end from the Cues size vint even though
        # the element itself is truncated. If so, do a single targeted fetch
        # for exactly `[0..index_end]` — works for arbitrarily large Cues,
        # no geometric growth needed, no size-based fallback.
        if result.get('index_end'):
            target = result['index_end']
            resp = await http_client.get(
                url, headers={'Range': f'bytes=0-{target}'}
            )
            if resp.status_code in (200, 206):
                result = parse_webm_ranges(resp.content)
                if (result.get('index_start')
                        and result.get('cues_complete')
                        and result.get('init_end')):
                    size_kb = (target + 1) // 1024
                    log.info(f"WebM probed OK: init=0-{result['init_end']}, "
                             f"cues={result['index_start']}-{result['index_end']}, "
                             f"fetch={size_kb}KB (targeted)")
                    return result

        # Cues declaration not found in first 4KB — EBML+Segment+Tracks header
        # must be unusually large. Grow geometrically; once we learn index_end
        # we can take the targeted path on the next iteration.
        for fetch_size in [256 * 1024, 2 * 1024 * 1024, 10 * 1024 * 1024]:
            resp = await http_client.get(
                url, headers={'Range': f'bytes=0-{fetch_size - 1}'}
            )
            if resp.status_code not in (200, 206):
                return None
            result = parse_webm_ranges(resp.content)
            # Full success on this fetch
            if (result.get('index_start')
                    and result.get('cues_complete')
                    and result.get('init_end')):
                log.info(f"WebM probed OK: init=0-{result['init_end']}, "
                         f"cues={result['index_start']}-{result['index_end']}, "
                         f"fetch={fetch_size // 1024}KB")
                return result
            # Learned index_end → switch to targeted fetch
            if result.get('index_end'):
                target = result['index_end']
                resp = await http_client.get(
                    url, headers={'Range': f'bytes=0-{target}'}
                )
                if resp.status_code in (200, 206):
                    result = parse_webm_ranges(resp.content)
                    if (result.get('index_start')
                            and result.get('cues_complete')
                            and result.get('init_end')):
                        size_kb = (target + 1) // 1024
                        log.info(f"WebM probed OK: init=0-{result['init_end']}, "
                                 f"cues={result['index_start']}-{result['index_end']}, "
                                 f"fetch={size_kb}KB (targeted)")
                        return result

        # Cues declaration still not found → file likely uses a SeekHead to
        # locate Cues at the tail, which we don't currently support. Return
        # whatever we have; dash.py will skip SegmentList and use SegmentBase.
        if result and result.get('init_end'):
            log.warning("WebM Cues location unknown, seeking may not work")
            return result
        return None

    except Exception as e:
        log.warning(f"Probe failed: {e}")
        return None
