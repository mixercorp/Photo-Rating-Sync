"""Write ratings into sidecars (our own merge), and JPEG, PSD and PNG files (through ExifTool)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict

from .sources import read_inside, read_sidecar

XMP_NS = "http://ns.adobe.com/xap/1.0/"
_ATTR = re.compile(rb"""\b(xmp|xap):Rating(\s*=\s*)(["'])[^"']*\3""")
_ELEMENT = re.compile(rb"""<(xmp|xap):Rating>[^<]*</\1:Rating>""")
_DESCRIPTION = re.compile(rb"""<rdf:Description(?:\s+[\w:.-]+\s*=\s*(?:"[^"]*"|'[^']*'))*""")
NEW_SIDECAR = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="{XMP_NS}" xmp:Rating="%d"/>
 </rdf:RDF>
</x:xmpmeta>
"""


class ExportError(Exception):
    pass


def merge_rating(data, value):
    """The sidecar's bytes with its rating set, and everything else left byte for byte."""
    text = str(value).encode()
    merged, n = _ATTR.subn(lambda m: b"%s:Rating%s%s%s%s" % (m[1], m[2], m[3], text, m[3]), data, count=1)
    if n:
        return merged
    merged, n = _ELEMENT.subn(lambda m: b"<%s:Rating>%s</%s:Rating>" % (m[1], text, m[1]), data, count=1)
    if n:
        return merged
    m = _DESCRIPTION.search(data)
    if not m:
        raise ExportError("no rdf:Description to add the rating to")
    added = b' xmp:Rating="%s"' % text
    if b"xmlns:xmp=" not in m[0]:
        added = b' xmlns:xmp="%s"' % XMP_NS.encode() + added
    return data[:m.end()] + added + data[m.end():]


def write_sidecar(path, value):
    """Create or update a sidecar through a temporary file, checking the result."""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            old = f.read()
        try:
            ET.fromstring(old)
        except ET.ParseError as e:
            raise ExportError(f"not readable XML, left alone ({e})")
        new = merge_rating(old, value)
    else:
        new = (NEW_SIDECAR % value).encode()
    try:
        ET.fromstring(new)
    except ET.ParseError as e:
        raise ExportError(f"the update would break the file, left alone ({e})")
    temp = path + ".ratingsync-tmp"
    with open(temp, "wb") as f:
        f.write(new)
    os.replace(temp, path)
    if read_sidecar(path) != value:
        raise ExportError("the rating didn't read back after writing")


def find_exiftool():
    bundled = getattr(sys, "_MEIPASS", None)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    places = [os.path.join(bundled, "exiftool", "exiftool.exe")] if bundled else []
    places.append(os.path.join(root, "vendor", "exiftool", "exiftool.exe"))
    return next((p for p in places if os.path.isfile(p)), None) or shutil.which("exiftool")


def write_inside(jobs, exiftool=None):
    """Embed ratings. jobs: (path, XMP value or None, Windows stars or None).

    Returns {path: error}. ExifTool writes to a temporary file and swaps it in, so a failure
    leaves the photo as it was. It keeps the file's modified time, but the size changes.
    """
    exiftool = exiftool or find_exiftool()
    if not jobs:
        return {}
    if not exiftool:
        return {job[0]: "ExifTool was not found" for job in jobs}
    failed = {}
    batches = defaultdict(list)
    for path, in_xmp, windows in jobs:
        batches[in_xmp, windows].append(path)
    for (in_xmp, windows), paths in batches.items():
        args = ["-overwrite_original", "-P", "-charset", "filename=utf8", "-q", "-q"]
        if in_xmp is not None:
            args.append(f"-XMP-xmp:Rating={in_xmp}")
        if windows is not None:
            percent = [0, 1, 25, 50, 75, 99][windows]
            args += [f"-EXIF:Rating={windows}", f"-EXIF:RatingPercent={percent}",
                     f"-XMP-microsoft:RatingPercent={percent}"]
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8", delete=False) as listing:
            listing.write("\n".join(args + paths) + "\n")
        try:
            done = subprocess.run([exiftool, "-@", listing.name], capture_output=True, text=True, errors="replace",
                                  stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            os.remove(listing.name)
        for path in paths:
            try:
                now_xmp, now_windows = read_inside(path)
            except OSError as e:
                failed[path] = str(e)
                continue
            if (in_xmp is not None and now_xmp != in_xmp) or (windows is not None and now_windows != windows):
                failed[path] = (done.stderr.strip().splitlines() or ["the rating didn't read back after writing"])[-1]
    return failed
