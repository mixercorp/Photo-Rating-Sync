"""Spread the ticked rating to every enabled place, then remember the outcome."""
import json
import os
import time
from dataclasses import dataclass, field

from . import export, fsdb
from .fsdb import Write
from .scan import CONFLICT, EMBED_EXTS, OUT_OF_SCOPE

STATE_FILE = "state.json"


@dataclass
class Options:
    raw_sidecar: bool = True         # IMG.xmp, the Adobe convention
    darktable_sidecar: bool = False  # IMG.CR3.xmp
    embed_jpeg: bool = True


@dataclass
class Action:
    photo: object
    value: int
    fs: list = field(default_factory=list)        # real file names to write
    sidecars: list = field(default_factory=list)  # paths to create or update
    jpegs: list = field(default_factory=list)     # (path, XMP value, EXIF stars or None)
    frozen: dict = field(default_factory=dict)    # places holding another value we won't write

    @property
    def writes_anything(self):
        return bool(self.fs or self.sidecars or self.jpegs)


@dataclass
class Result:
    photos: int = 0
    faststone: int = 0
    sidecars: int = 0
    jpegs: int = 0
    errors: list = field(default_factory=list)
    backup: str | None = None
    log: str | None = None


def load_state(data_dir):
    try:
        with open(os.path.join(data_dir, STATE_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(data_dir, state):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
    os.replace(path + ".tmp", path)


def _plan_sidecars(photo, value, options, action):
    """Existing sidecars follow the rating; new ones are only made to hold a rating."""
    handled = set()
    if not (options.raw_sidecar or options.darktable_sidecar):
        return handled
    for name in photo.sidecars:
        source = photo.source("side:" + name[len(photo.stem):].upper())
        if source:
            handled.add(source.key)
            if source.value != value and not (source.value is None and value == 0):
                action.sidecars.append(source.path)
    if value:
        others = [ext for ext in photo.files if ext not in EMBED_EXTS]
        wanted = []
        if others and options.raw_sidecar:
            wanted.append(photo.stem + ".xmp")
        if options.darktable_sidecar:
            wanted += [f"{photo.files[ext]}.xmp" for ext in others]
        have = {name.upper() for name in photo.sidecars}
        action.sidecars += [os.path.join(photo.folder, n) for n in wanted if n.upper() not in have]
    return handled


def _plan_jpegs(photo, value, options, action):
    handled = set()
    if not options.embed_jpeg:
        return handled
    for ext in photo.files:
        in_xmp, in_exif = photo.source("xmp:" + ext), photo.source("exif:" + ext)
        if not in_xmp:  # it doesn't hold its own rating, or it couldn't be read
            continue
        handled.add(in_xmp.key)
        if not in_exif:
            if in_xmp.value != value and not (in_xmp.value is None and value == 0):
                action.jpegs.append((in_xmp.path, value, None))
            continue
        handled.add(in_exif.key)
        # A blank field stays blank unless there's a rating to put in it. EXIF has no Reject.
        want_xmp = None if value == 0 and in_xmp.value is None else value
        want_exif = None if value <= 0 and in_exif.value is None else max(value, 0)
        if want_xmp not in (None, in_xmp.value) or want_exif not in (None, in_exif.value):
            action.jpegs.append((in_xmp.path, want_xmp, want_exif))
    return handled


def plan_action(photo, entries, options):
    value = photo.final
    action = Action(photo, value)
    for name in photo.files.values():
        entry = entries.get(name.upper())
        if (entry.rating != value or (value and fsdb.is_stale(entry, os.path.join(photo.folder, name)))) \
                if entry else value != 0:
            action.fs.append(name)
    handled = _plan_sidecars(photo, value, options, action) | _plan_jpegs(photo, value, options, action)
    for s in photo.sources:
        if not s.is_faststone and s.value is not None and s.value != value and s.key not in handled:
            action.frozen[s.key] = s.value
    return action


def preview(photo, options):
    """Each place's rating before and after OK: source key -> (before, after)."""
    found = {s.key: (s.value, s.value) for s in photo.sources}
    if photo.final is None:
        return found
    action = plan_action(photo, photo.entries, options)
    for ext, name in photo.files.items():
        if name in action.fs:
            found["fs:" + ext] = (found["fs:" + ext][0], action.value)
    for path, value, stars in action.jpegs:
        for s in photo.sources:
            if s.path == path and s.key.startswith("xmp:") and value is not None:
                found[s.key] = (s.value, value)
            elif s.path == path and s.key.startswith("exif:") and stars is not None:
                found[s.key] = (s.value, stars)
    for path in action.sidecars:
        source = next((s for s in photo.sources if s.path == path), None)
        if source:
            found[source.key] = (source.value, action.value)
        else:
            found["side:" + os.path.basename(path)] = (None, action.value)
    return found


def with_work(db, photos, options):
    """The photos a sync would act on: conflicts, and any whose rating isn't everywhere yet."""
    entries = {}
    kept = []
    for photo in photos:
        if photo.folder not in entries:
            entries[photo.folder] = db.entries(photo.folder)
        if photo.status in (CONFLICT, OUT_OF_SCOPE) or (photo.chosen is not None
                                        and plan_action(photo, entries[photo.folder], options).writes_anything):
            kept.append(photo)
    return kept


def plan(db, photos, options):
    entries = {}
    actions = []
    for photo in photos:
        if photo.final is None:
            continue
        if photo.folder not in entries:
            entries[photo.folder] = db.entries(photo.folder)
        actions.append(plan_action(photo, entries[photo.folder], options))
    return actions


def _save_log(data_dir, actions, result):
    folder = os.path.join(data_dir, "logs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S") + "-sync.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"backup": result.backup, "errors": result.errors, "photos": [
            {"folder": a.photo.folder, "photo": a.photo.stem, "final": a.value,
             "before": {s.key: s.value for s in a.photo.sources}, "faststone": a.fs, "sidecars": a.sidecars,
             "jpegs": [j[0] for j in a.jpegs], "not_written": a.frozen} for a in actions if a.writes_anything]},
            f, ensure_ascii=False, indent=1)
    return path


def apply(db_path, photos, options, data_dir, state=None):
    """Back up, write files, then the database. A photo that fails isn't remembered, so it returns."""
    if fsdb.faststone_running():
        raise fsdb.FastStoneRunning()
    state = load_state(data_dir) if state is None else state
    result = Result()
    with fsdb.Database(db_path) as db:
        actions = plan(db, photos, options)
        doing = [a for a in actions if a.writes_anything]
        if doing:
            result.backup = fsdb.backup(db_path, data_dir)

        failed_jpegs = export.write_inside([j for a in doing for j in a.jpegs])
        problems = {}
        for action in doing:
            for path, _xmp, _stars in action.jpegs:
                if path in failed_jpegs:
                    problems.setdefault(id(action), []).append(f"{os.path.basename(path)}: {failed_jpegs[path]}")
                else:
                    result.jpegs += 1
            for path in action.sidecars:
                try:
                    export.write_sidecar(path, action.value)
                    result.sidecars += 1
                except (export.ExportError, OSError) as e:
                    problems.setdefault(id(action), []).append(f"{os.path.basename(path)}: {e}")

        # Entries of rewritten JPEGs need the new size even if their rating stays the same.
        writes = []
        for action in doing:
            names = set(action.fs) | {os.path.basename(j[0]) for j in action.jpegs if j[0] not in failed_jpegs}
            writes += [Write(action.photo.folder, n, action.value) for n in sorted(names)]
        db.write_ratings(writes)
        result.faststone = sum(len(a.fs) for a in doing)

    for action in actions:
        if id(action) in problems:
            result.errors += problems[id(action)]
        else:
            result.photos += 1
            state[action.photo.state_key] = {"final": action.value, "frozen": action.frozen}
    save_state(data_dir, state)
    if doing:
        result.log = _save_log(data_dir, actions, result)
    return result
