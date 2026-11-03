"""Read the ratings stored inside photos and sidecars, without changing them."""
import os
import re
import struct

RAW_SCAN_BYTES = 4 * 1024 * 1024
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EXIF_HEADER = b"Exif\x00\x00"
TAG_RATING, TAG_RATING_PERCENT = 0x4746, 0x4749

_XMP_RATING = re.compile(rb"""(?:xmp|xap):Rating(?:\s*=\s*["']|>)\s*(-?\d+)(?:\.\d+)?""")
_MS_RATING = re.compile(rb"""MicrosoftPhoto:Rating(?:\s*=\s*["']|>)\s*(\d+)""")


def _valid(value):
    return value if value is not None and -1 <= value <= 5 else None


def xmp_rating(data):
    m = _XMP_RATING.search(data)
    return _valid(int(m.group(1))) if m else None


def percent_to_stars(percent):
    # Windows' own bands: it writes 1, 25, 50, 75 and 99 for 1-5 stars.
    if percent <= 0:
        return 0
    return min(5, 1 + (percent + 12) // 25)


def _exif_tags(tiff):
    """Rating and RatingPercent from a TIFF block's first directory."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None, None
    end = "<" if tiff[:2] == b"II" else ">"
    rating = percent = None
    try:
        start = struct.unpack_from(end + "I", tiff, 4)[0]
        for k in range(struct.unpack_from(end + "H", tiff, start)[0]):
            tag, kind = struct.unpack_from(end + "HH", tiff, start + 2 + 12 * k)
            if tag in (TAG_RATING, TAG_RATING_PERCENT) and kind == 3:
                value = struct.unpack_from(end + "H", tiff, start + 2 + 12 * k + 8)[0]
                if tag == TAG_RATING:
                    rating = value
                else:
                    percent = value
    except struct.error:
        pass
    return rating, percent


def _windows_rating(tiff, xmp):
    rating, percent = _exif_tags(tiff)
    if rating is None and percent is not None:
        rating = percent_to_stars(percent)
    if rating is None:
        m = _MS_RATING.search(xmp)
        if m:
            rating = percent_to_stars(int(m.group(1)))
    return _valid(rating)


def read_jpeg(path):
    """(XMP rating, Windows rating) from a JPEG's header segments; either can be None."""
    tiff = xmp = b""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return None, None
        while True:
            head = f.read(4)
            if len(head) < 4 or head[0] != 0xFF or head[1] in (0xD9, 0xDA):
                break
            size = struct.unpack(">H", head[2:])[0] - 2
            if head[1] != 0xE1:
                f.seek(size, 1)
                continue
            segment = f.read(size)
            if segment.startswith(XMP_HEADER):
                xmp = segment[len(XMP_HEADER):]
            elif segment.startswith(EXIF_HEADER):
                tiff = segment[len(EXIF_HEADER):]
    return xmp_rating(xmp), _windows_rating(tiff, xmp)


def read_png(path):
    """XMP rating from a PNG's iTXt chunk, which can sit anywhere in the file."""
    with open(path, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            return None
        while True:
            head = f.read(8)
            if len(head) < 8:
                return None
            size, kind = struct.unpack(">I4s", head)
            if kind == b"iTXt":
                chunk = f.read(size)
                if chunk.startswith(b"XML:com.adobe.xmp\x00"):
                    return xmp_rating(chunk)
                f.seek(4, 1)
            else:
                f.seek(size + 4, 1)


def read_psd(path):
    """XMP rating from a PSD's image resource 0x0424."""
    with open(path, "rb") as f:
        if f.read(4) != b"8BPS":
            return None
        f.seek(26)
        f.seek(struct.unpack(">I", f.read(4))[0], 1)  # colour mode data
        end = struct.unpack(">I", f.read(4))[0] + f.tell()
        while f.tell() + 12 <= end:
            if f.read(4) != b"8BIM":
                return None
            resource = struct.unpack(">H", f.read(2))[0]
            name = f.read(1)[0]
            f.seek(name + (name + 1) % 2, 1)  # padded so the name and its length are even
            size = struct.unpack(">I", f.read(4))[0]
            if resource == 0x0424:
                return xmp_rating(f.read(size))
            f.seek(size + size % 2, 1)
    return None


def read_inside(path):
    """(XMP rating, Windows rating) from a file that holds its rating inside itself."""
    ext = os.path.splitext(path)[1].upper()
    try:
        if ext == ".PNG":
            return read_png(path), None
        if ext == ".PSD":
            return read_psd(path), None
    except (struct.error, IndexError):  # a damaged file holds no rating we can trust
        return None, None
    return read_jpeg(path)


def read_raw(path):
    """Rating found in the first part of a RAW or HEIF file, where cameras write it."""
    with open(path, "rb") as f:
        data = f.read(RAW_SCAN_BYTES)
    rating = xmp_rating(data)
    if rating is not None:
        return rating
    tiff = data if data[:2] in (b"II", b"MM") else b""
    if not tiff:
        i = data.find(EXIF_HEADER)
        tiff = data[i + len(EXIF_HEADER):] if i >= 0 else b""
    return _windows_rating(tiff, b"")


def read_sidecar(path):
    with open(path, "rb") as f:
        return xmp_rating(f.read())
