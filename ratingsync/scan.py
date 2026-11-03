"""Find the photos in a folder and every rating each one carries (plan, section 3a)."""
import os
from dataclasses import dataclass, field

from .sources import read_inside, read_raw, read_sidecar

JPEG_EXTS = {"JPG", "JPEG"}
HEIF_EXTS = {"HEIC", "HIF"}
EDIT_EXTS = {"PSD", "PNG"}
EMBED_EXTS = JPEG_EXTS | EDIT_EXTS  # hold their own rating, which we write
RAW_EXTS = {"CR3", "CR2", "CRW", "NEF", "NRW", "ARW", "SRF", "SR2", "RAF", "ORF", "RW2", "PEF", "DNG",
            "SRW", "3FR", "IIQ", "X3F", "RWL"}
PHOTO_EXTS = EMBED_EXTS | HEIF_EXTS | RAW_EXTS

CONFLICT, REMOVED, NO_CONFLICT, OUT_OF_SCOPE = "conflict", "removed", "ok", "out of scope"


@dataclass
class Source:
    key: str             # the same for the same kind of place across photos: a table column
    label: str
    value: int | None    # None: this place holds no rating
    path: str | None = None
    mtime: float | None = None
    voting: bool = True  # False: a value we couldn't write last time, already dealt with

    @property
    def is_faststone(self):
        return self.key.startswith("fs:")


@dataclass
class Photo:
    folder: str
    stem: str
    files: dict          # upper-cased extension -> real file name
    sidecars: list       # real file names
    sources: list = field(default_factory=list)
    status: str = NO_CONFLICT
    chosen: str | None = None  # key of the ticked source; None skips the photo
    default: str | None = None  # the tick it started with
    override: int | None = None  # a rating set by hand, which takes the place of the tick
    entries: dict = field(default_factory=dict)  # FastStone's entries for the folder
    last: int | None = None    # final rating at the last sync
    opened: float | None = None  # when FastStone last opened the folder

    @property
    def state_key(self):
        return os.path.abspath(self.folder).upper() + "|" + self.stem.upper()

    @property
    def final(self):
        if self.override is not None:
            return self.override
        return next((s.value for s in self.sources if s.key == self.chosen), None)

    def source(self, key):
        return next((s for s in self.sources if s.key == key), None)

    @property
    def has_several_raws(self):
        return sum(ext not in EMBED_EXTS for ext in self.files) > 1


def walk_folders(root, subfolders):
    """The picked folder and, if asked, the folders nested under it. Links are not followed."""
    if not subfolders:
        return [root]
    return [path for path, _dirs, _files in os.walk(root)]


def group_files(folder):
    """Same-named photos and their sidecars in one folder, as Photo objects."""
    photos, sidecars = {}, {}
    for e in os.scandir(folder):
        if not e.is_file():
            continue
        stem, dot, ext = e.name.rpartition(".")
        if not dot:
            continue
        ext = ext.upper()
        if ext == "XMP":
            inner_stem, dot, inner = stem.rpartition(".")
            sidecars.setdefault((inner_stem if dot and inner.upper() in PHOTO_EXTS else stem).upper(), []).append(e.name)
        elif ext in PHOTO_EXTS:
            photo = photos.setdefault(stem.upper(), Photo(folder, stem, {}, []))
            photo.files[ext] = e.name
    for key, names in sidecars.items():
        if key in photos:  # a sidecar with no photo is left alone
            photos[key].sidecars = sorted(names)
    return [photos[k] for k in sorted(photos)]


def _file_order(ext):
    return (ext not in JPEG_EXTS, ext not in EMBED_EXTS, ext)


def read_sources(photo, entries, inside=True):
    found = []
    exts = sorted(photo.files, key=_file_order)
    for ext in exts:
        entry = entries.get(photo.files[ext].upper())
        found.append(Source("fs:" + ext, f"FastStone {ext}", entry.rating if entry else None))
    for ext in exts:
        path = os.path.join(photo.folder, photo.files[ext])
        try:
            if ext in EMBED_EXTS:
                if not inside:  # the user turned these files off: only FastStone's entry counts
                    continue
                in_xmp, in_exif = read_inside(path)
                if in_xmp == -1 and in_exif == 0:  # EXIF can't hold a Reject, so its 0 says nothing
                    in_exif = None
                label = "JPEG XMP" if ext in JPEG_EXTS else f".{ext}"
                found.append(Source("xmp:" + ext, label, in_xmp, path, os.path.getmtime(path)))
                if ext in JPEG_EXTS:
                    found.append(Source("exif:" + ext, "JPEG EXIF", in_exif, path, os.path.getmtime(path)))
            else:
                found.append(Source("raw:" + ext, f".{ext}", read_raw(path), path, os.path.getmtime(path)))
        except OSError:
            continue
    for name in photo.sidecars:
        path = os.path.join(photo.folder, name)
        suffix = name[len(photo.stem):].upper()
        try:
            found.append(Source("side:" + suffix, suffix, read_sidecar(path), path, os.path.getmtime(path)))
        except OSError:
            continue
    photo.sources = found


def _default_tick(photo, value):
    matching = [s for s in photo.sources if s.value == value]
    return next((s.key for s in matching if s.is_faststone), matching[0].key)


def classify(photo, remembered):
    """Set status and the default tick. Returns False if the photo is unrated everywhere."""
    remembered = remembered or {}
    frozen = remembered.get("frozen", {})
    photo.last = remembered.get("final")
    present = [s for s in photo.sources if s.value is not None]
    if not any(s.value for s in present):
        return False
    for s in present:
        s.voting = frozen.get(s.key) != s.value
        # FastStone keeps no time per rating. The folder's last opening is the best evidence,
        # and only for a rating that changed since the last sync: opening a folder isn't rating.
        if s.is_faststone and s.value and s.value != photo.last:
            s.mtime = photo.opened
    values = {s.value for s in present if s.voting}
    rated = values - {0}
    photo.status = NO_CONFLICT
    if len(rated) > 1:
        photo.status, photo.chosen = CONFLICT, None
    elif rated:
        value = rated.pop()
        if 0 in values and photo.last == value:
            photo.status, photo.chosen = REMOVED, _default_tick(photo, 0)
        else:
            photo.chosen = _default_tick(photo, value)
    else:
        photo.chosen = _default_tick(photo, 0) if values else None
    photo.default = photo.chosen
    return True


def scan(db, root, subfolders, state, progress=None, inside=True):
    """Photos with a rating anywhere under the picked folder, conflicts first."""
    root = os.path.abspath(root)
    rows, seen = [], 0
    for folder in walk_folders(root, subfolders):
        entries = db.entries(folder)
        opened = db.folder_opened(folder)
        for photo in group_files(folder):
            seen += 1
            photo.opened = opened
            photo.entries = entries
            if photo.has_several_raws:  # never read or written, only listed
                photo.status = OUT_OF_SCOPE
                rows.append(photo)
                continue
            read_sources(photo, entries, inside)
            if classify(photo, state.get(photo.state_key)):
                rows.append(photo)
            if progress and seen % 25 == 0:
                progress(seen)
    order = {OUT_OF_SCOPE: 0, CONFLICT: 1, REMOVED: 2, NO_CONFLICT: 3}
    rows.sort(key=lambda p: (order[p.status], p.folder.upper(), p.stem.upper()))
    return rows, seen


def bulk_choice(photo, mode):
    """Which source a bulk button ticks for a conflict; None when it can't tell."""
    voting = [s for s in photo.sources if s.value is not None and s.voting]
    rated = [s for s in voting if s.value]
    if mode == "faststone":
        rated = [s for s in rated if s.is_faststone]
        if len({s.value for s in rated}) > 1:  # FastStone's own files differ: take what changed
            rated = [s for s in rated if s.value != photo.last]
        return rated[0].key if len({s.value for s in rated}) == 1 else None
    if mode == "highest":
        return max(rated, key=lambda s: s.value).key if rated else None
    if mode == "newest":
        dated = [s for s in rated if s.mtime is not None]
        if not dated:
            return None
        newest = max(s.mtime for s in dated)
        tied = [s for s in dated if s.mtime == newest]
        return tied[0].key if len({s.value for s in tied}) == 1 else None
    return None


def apply_bulk(photos, mode):
    """Tick a choice on each photo. Where a rule has no answer, a conflict is left unticked and
    a photo with no conflict keeps its default. Returns how many have a tick."""
    for photo in photos:
        choice = bulk_choice(photo, mode)
        if choice is None and mode != "skip":
            choice = photo.default
        photo.chosen, photo.override = choice, None
    return sum(p.chosen is not None for p in photos)
