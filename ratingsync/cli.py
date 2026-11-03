"""Command line: the same scan and apply as the window, for power users and the Claude skill."""
import argparse
import os
import sys
from collections import Counter

from . import fsdb
from .apply import Options, apply, load_state, plan, with_work
from .scan import CONFLICT, OUT_OF_SCOPE, REMOVED, apply_bulk, scan


def stars(value):
    return {None: "-", -1: "Reject", 0: "0"}.get(value, f"{value}*")


def options_from(args):
    return Options(raw_sidecar=not args.no_sidecar, darktable_sidecar=args.darktable_sidecar,
                   embed_jpeg=not args.no_embed)


def report(photos, seen):
    counts = Counter(p.status for p in photos)
    print(f"Total photos: {seen}. To sync: {len(photos) - counts[OUT_OF_SCOPE]} "
          f"({counts[CONFLICT]} conflicts, {counts[REMOVED]} rating removals). "
          f"Out of scope: {counts[OUT_OF_SCOPE]}.")
    for p in photos:
        if p.status == OUT_OF_SCOPE:
            print(f"  OUT OF SCOPE, not touched: {', '.join(sorted(p.files.values()))}")
        elif p.status in (CONFLICT, REMOVED):
            values = ", ".join(f"{s.label} {stars(s.value)}" for s in p.sources if s.value is not None)
            print(f"  {p.status.upper()} {os.path.join(p.folder, p.stem)}: {values}")


def cmd_scan(args):
    with fsdb.Database(args.db, read_only=True) as db:
        photos, seen = scan(db, args.folder, args.subfolders, load_state(args.data_dir))
        report(with_work(db, photos, Options()), seen)


def cmd_sync(args):
    if not args.dry_run and fsdb.faststone_running():
        sys.exit("ERROR: FastStone is open and locks its database. Close it and try again.")
    state = load_state(args.data_dir)
    options = options_from(args)
    with fsdb.Database(args.db, read_only=args.dry_run) as db:
        photos, seen = scan(db, args.folder, args.subfolders, state, inside=options.embed_jpeg)
        photos = with_work(db, photos, options)
        apply_bulk([p for p in photos if p.status == CONFLICT], args.conflicts)
        report(photos, seen)
        actions = plan(db, photos, options)
    skipped = sum(p.chosen is None and p.status != OUT_OF_SCOPE for p in photos)
    if skipped:
        print(f"Skipped, nothing ticked: {skipped}. Use --conflicts to choose a rule.")
    doing = [a for a in actions if a.writes_anything]
    if args.dry_run:
        print(f"Would change {len(doing)} photos: {sum(len(a.fs) for a in doing)} FastStone entries, "
              f"{sum(len(a.sidecars) for a in doing)} sidecars, {sum(len(a.jpegs) for a in doing)} JPEGs.")
        return
    result = apply(args.db, photos, options, args.data_dir, state)
    print(f"Changed {result.faststone} FastStone entries, {result.sidecars} sidecars, {result.jpegs} JPEGs.")
    if result.backup:
        print(f"Backup: {result.backup}")
        print(f"Log: {result.log}")
    for error in result.errors:
        print(f"FAILED {error}")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream:  # the windowed .exe has none
            stream.reconfigure(encoding="utf-8", errors="replace")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("folder", help="photo folder; nothing outside it is read or changed")
    common.add_argument("--subfolders", action="store_true", help="include folders nested under it")
    common.add_argument("--db", default=fsdb.default_db(), help="path to FSIV.db (default: %(default)s)")
    common.add_argument("--data-dir", default=fsdb.default_data_dir(), help="backups, logs and sync history")

    ap = argparse.ArgumentParser(prog="ratingsync", description="Sync star ratings across a photo's files.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", parents=[common], help="report only").set_defaults(func=cmd_scan)
    sync = sub.add_parser("sync", parents=[common], help="apply the default ticks")
    sync.add_argument("--conflicts", choices=["skip", "faststone", "highest", "newest"], default="skip",
                      help="what to do with conflicts (default: skip them)")
    sync.add_argument("--dry-run", action="store_true", help="report what would change; change nothing")
    sync.add_argument("--no-sidecar", action="store_true", help="don't write sidecars")
    sync.add_argument("--darktable-sidecar", action="store_true", help="also write IMG.CR3.xmp")
    sync.add_argument("--no-embed", action="store_true", help="don't write inside JPEGs")
    sync.set_defaults(func=cmd_sync)
    args = ap.parse_args()
    try:
        if not os.path.isdir(args.folder):
            sys.exit(f"ERROR: not a folder: {args.folder}")
        args.func(args)
    except fsdb.FastStoneError as e:
        sys.exit(f"ERROR: {e}")
