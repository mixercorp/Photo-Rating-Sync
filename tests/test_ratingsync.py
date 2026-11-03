import calendar
import os
import sqlite3
import time
import unittest
from unittest import mock

from helpers import Case, image_part, jpeg_bytes, png_bytes, psd_bytes, xmp_packet
from ratingsync import export, fsdb, sources
from ratingsync.apply import Options, save_state, with_work
from ratingsync.scan import CONFLICT, NO_CONFLICT, REMOVED, apply_bulk, bulk_choice, group_files

HAS_EXIFTOOL = bool(export.find_exiftool())
CAMERA_RAW_SIDECAR = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:Rating="0"
   crs:Version="14.0"
   crs:Exposure2012="+0.35">
   <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


class DosTime(unittest.TestCase):
    def ns(self, *when):
        return calendar.timegm(when) * 10**9

    def test_even_second_stays(self):
        v = fsdb.dos_time(self.ns(2026, 10, 27, 14, 7, 58, 0, 0, 0))
        self.assertEqual(v & 31, 29)

    def test_odd_second_rounds_up_like_faststone(self):
        # 1284.JPG: on disk 14:07:59.70, FastStone stored 14:08:00.
        v = fsdb.dos_time(self.ns(2026, 10, 27, 14, 7, 59, 0, 0, 0) + 699870600)
        self.assertEqual(v, 1566273792)

    def test_fraction_of_even_second_rounds_up(self):
        base = self.ns(2026, 1, 2, 3, 4, 10, 0, 0, 0)
        self.assertEqual(fsdb.dos_time(base + 100) & 31, 6)

    def test_fallback_matches_windows(self):
        ns = self.ns(2026, 10, 27, 14, 7, 59, 0, 0, 0) + 699870600
        with mock.patch.object(fsdb.ctypes, "windll", create=True, new=None):
            self.assertEqual(fsdb.dos_time(ns), 1566273792)


class Database(Case):
    def test_folder_names_like_faststone(self):
        self.assertEqual(fsdb.db_folder_name(r"C:\Photos\Shoot"), "C:\\PHOTOS\\SHOOT\\")

    def test_refuses_other_versions_without_changing_anything(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE DBSettings SET version = 11")
        con.commit()
        con.close()
        with self.assertRaisesRegex(fsdb.FastStoneError, "version 11"):
            fsdb.Database(self.db)

    def test_locked_database(self):
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        with mock.patch.object(sqlite3, "connect", side_effect=lambda p, **k: sqlite3.Connection(p, **{**k, "timeout": 0})):
            with self.assertRaises(fsdb.FastStoneRunning):
                fsdb.Database(self.db)
            with fsdb.Database(self.db, read_only=True) as db:
                self.assertEqual(db.entries(self.root), {})

    def test_missing_database(self):
        with self.assertRaisesRegex(fsdb.FastStoneError, "not found"):
            fsdb.Database(self.db + ".nope")

    def test_new_entry_is_filled_in_so_faststone_keeps_it(self):
        path = self.photo("A.RAF")
        os.utime(path, (1760000001, 1760000001))  # odd second
        with fsdb.Database(self.db) as db:
            db.write_ratings([fsdb.Write(self.root, "A.RAF", 3)])
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT fileName, ratings, fileTime, fileSize, imageType, isFolder, img1 FROM FileList").fetchone()
        con.close()
        self.assertEqual(row, ("A.RAF", 3, fsdb.dos_time(os.stat(path).st_mtime_ns), 7, 24, 0, None))

    def test_updating_a_stale_entry_repairs_its_date_and_size(self):
        path = self.photo("A.JPG")
        self.entry("A.JPG", 1, matching=False)
        with fsdb.Database(self.db) as db:
            db.write_ratings([fsdb.Write(self.root, "A.JPG", 4)])
        row = self.row("A.JPG")
        self.assertEqual((row.rating, row.file_size, row.file_time),
                         (4, os.path.getsize(path), fsdb.dos_time(os.stat(path).st_mtime_ns)))

    def test_zero_adds_no_entry_and_vanished_file_is_skipped(self):
        self.photo("A.RAF")
        with fsdb.Database(self.db) as db:
            db.write_ratings([fsdb.Write(self.root, "A.RAF", 0), fsdb.Write(self.root, "GONE.RAF", 3)])
        self.assertEqual(self.rows(), [])

    def test_failed_write_rolls_back(self):
        self.photo("A.RAF")
        self.photo("B.RAF")
        with fsdb.Database(self.db) as db:
            with mock.patch.object(fsdb.os, "stat", side_effect=[os.stat(os.path.join(self.root, "A.RAF")), PermissionError()]):
                with self.assertRaises(PermissionError):
                    db.write_ratings([fsdb.Write(self.root, "A.RAF", 3), fsdb.Write(self.root, "B.RAF", 3)])
        self.assertEqual(self.rows(), [])

    def test_backups_keep_five(self):
        kept = []
        for second in range(7):  # names sort by time, so the clock has to move
            with mock.patch.object(fsdb.time, "strftime", return_value=f"20261030-00000{second}"):
                kept.append(fsdb.backup(self.db, self.data))
        names = sorted(f for f in os.listdir(os.path.join(self.data, "backups")) if f.endswith(".db"))
        self.assertEqual(len(names), 5)
        self.assertIn(os.path.basename(kept[-1]), names)


class Reading(Case):
    def test_xmp_forms(self):
        self.assertEqual(sources.xmp_rating(b'xmp:Rating="3"'), 3)
        self.assertEqual(sources.xmp_rating(b"xmp:Rating='4'"), 4)
        self.assertEqual(sources.xmp_rating(b"<xmp:Rating>5</xmp:Rating>"), 5)
        self.assertEqual(sources.xmp_rating(b'xap:Rating="2.0"'), 2)
        self.assertEqual(sources.xmp_rating(b'xmp:Rating="-1"'), -1)
        self.assertIsNone(sources.xmp_rating(b'xmp:Rating="9"'))
        self.assertIsNone(sources.xmp_rating(b"no rating"))

    def test_windows_percent_bands(self):
        self.assertEqual([sources.percent_to_stars(p) for p in (0, 1, 25, 50, 75, 99, 100)], [0, 1, 2, 3, 4, 5, 5])

    def test_jpeg_segments(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(2), exif=(4, 75)))
        self.assertEqual(sources.read_jpeg(path), (2, 4))

    def test_jpeg_microsoft_xmp(self):
        path = self.photo("A.JPG", content=jpeg_bytes('<rdf:x MicrosoftPhoto:Rating="50"/>'))
        self.assertEqual(sources.read_jpeg(path), (None, 3))

    def test_not_a_jpeg(self):
        self.assertEqual(sources.read_jpeg(self.photo("A.JPG", content=b"junk")), (None, None))

    def test_raw_xmp_and_exif(self):
        self.assertEqual(sources.read_raw(self.photo("A.CR3", content=b"box " + xmp_packet(2).encode())), 2)
        self.assertEqual(sources.read_raw(self.photo("B.RAF", content=b"FUJI" + jpeg_bytes(exif=(3, 50)))), 3)
        self.assertIsNone(sources.read_raw(self.photo("C.CR3")))


class EditedFiles(Case):
    def test_png_and_psd_ratings_are_read(self):
        self.assertEqual(sources.read_inside(self.photo("A.PNG", content=png_bytes(xmp_packet(4)))), (4, None))
        self.assertEqual(sources.read_inside(self.photo("A.PSD", content=psd_bytes(xmp_packet(2)))), (2, None))

    def test_no_rating_and_junk(self):
        self.assertEqual(sources.read_inside(self.photo("B.PNG", content=png_bytes())), (None, None))
        self.assertEqual(sources.read_inside(self.photo("B.PSD", content=psd_bytes())), (None, None))
        self.assertEqual(sources.read_inside(self.photo("C.PNG", content=b"junk")), (None, None))
        self.assertEqual(sources.read_inside(self.photo("C.PSD", content=b"junk")), (None, None))

    def test_damaged_files_read_as_unrated(self):
        self.assertEqual(sources.read_inside(self.photo("D.PSD", content=b"8BPS\x00\x01")), (None, None))
        self.assertEqual(sources.read_inside(self.photo("D.PNG", content=png_bytes()[:12])), (None, None))

    def test_they_pair_with_the_raw(self):
        self.photo("A.RAF"), self.photo("A.PSD", content=psd_bytes()), self.photo("A.PNG", content=png_bytes())
        self.entry("A.RAF", 3)
        self.run_sync(options=Options(embed_jpeg=False, raw_sidecar=False))
        self.assertEqual({r[1]: r[2] for r in self.rows()}, {"A.RAF": 3, "A.PSD": 3, "A.PNG": 3})
        self.assertEqual(self.row("A.PSD").file_size, os.path.getsize(os.path.join(self.root, "A.PSD")))

    def test_column_names(self):
        self.photo("A.JPG", content=jpeg_bytes(xmp_packet(1), exif=(1, 1)))
        self.photo("A.PSD", content=psd_bytes(xmp_packet(1)))
        self.entry("A.JPG", 1)
        labels = {s.key: s.label for s in self.scan()[0].sources}
        self.assertEqual(labels, {"fs:JPG": "FastStone JPG", "fs:PSD": "FastStone PSD", "xmp:JPG": "JPEG XMP",
                                  "exif:JPG": "JPEG EXIF", "xmp:PSD": ".PSD"})

    def test_rating_inside_a_psd_is_a_source(self):
        self.photo("A.RAF", content=b"x"), self.photo("A.PSD", content=psd_bytes(xmp_packet(5)))
        self.entry("A.RAF", 2)
        self.assertEqual(self.scan()[0].status, CONFLICT)

    def test_lone_png_gets_no_sidecar(self):
        self.photo("A.PNG", content=png_bytes())
        self.entry("A.PNG", 3)
        self.run_sync(options=Options(embed_jpeg=False))
        self.assertFalse(os.path.exists(os.path.join(self.root, "A.xmp")))

    @unittest.skipUnless(HAS_EXIFTOOL, "ExifTool isn't in vendor/exiftool")
    def test_rating_written_inside_and_entry_kept_in_step(self):
        path = self.photo("A.PNG", content=png_bytes())
        self.entry("A.PNG", 4)
        self.assertEqual(self.run_sync().errors, [])
        self.assertEqual(sources.read_png(path), 4)
        self.assertEqual(self.row("A.PNG").file_size, os.path.getsize(path))


class Pairing(Case):
    def names(self):
        return {p.stem: (sorted(p.files), p.sidecars) for p in group_files(self.root)}

    def test_same_stem_ignoring_case_groups_three_files(self):
        for n in ("IMG_1.CR3", "img_1.jpg", "IMG_1.DNG", "IMG_2.NEF"):
            self.photo(n)
        groups = group_files(self.root)
        self.assertEqual([sorted(g.files) for g in groups], [["CR3", "DNG", "JPG"], ["NEF"]])

    def test_both_sidecar_styles_and_orphans(self):
        for n in ("A.CR3", "flower1", "notes.txt", "pic.gif"):
            self.photo(n)
        self.sidecar("A.xmp", 1)
        self.sidecar("A.CR3.xmp", 2)
        self.sidecar("ORPHAN.xmp", 3)
        self.assertEqual(self.names(), {"A": (["CR3"], ["A.CR3.xmp", "A.xmp"])})


class Rules(Case):
    def pair(self, jpg=None, raw=None, embedded=None):
        exif = (embedded, 0) if embedded and embedded > 0 else None
        self.photo("A.JPG", content=jpeg_bytes(xmp_packet(embedded), exif=exif) if embedded is not None else None)
        self.photo("A.RAF")
        if jpg is not None:
            self.entry("A.JPG", jpg)
        if raw is not None:
            self.entry("A.RAF", raw)

    def test_unrated_everywhere_has_no_row(self):
        self.pair(0, 0)
        self.assertEqual(self.scan(), [])

    def work(self, options=None):
        with fsdb.Database(self.db) as db:
            return with_work(db, self.scan(), options or Options())

    def test_photo_that_agrees_everywhere_has_nothing_to_sync(self):
        self.pair(3, 3, 3)
        self.sidecar("A.xmp", 3)
        self.assertEqual(self.work(), [])

    def test_missing_place_is_still_work(self):
        self.pair(3, 3, 3)  # no .xmp beside the RAW yet
        self.assertEqual([p.chosen for p in self.work()], ["fs:JPG"])
        self.assertEqual(self.work(Options(raw_sidecar=False)), [])

    def test_partner_at_zero_is_work(self):
        self.pair(3, 0)
        self.assertEqual(len(self.work(Options(raw_sidecar=False, embed_jpeg=False))), 1)

    def test_conflict_is_always_work(self):
        self.pair(5, 3)
        self.assertEqual(len(self.work(Options(raw_sidecar=False, embed_jpeg=False))), 1)

    @unittest.skipUnless(HAS_EXIFTOOL, "ExifTool isn't in vendor/exiftool")
    def test_photo_is_gone_after_its_sync(self):
        self.pair(3, 0)
        self.run_sync()
        self.assertEqual(self.work(), [])

    def test_one_rating_ticks_faststone(self):
        self.pair(0, 5)
        p = self.scan()[0]
        self.assertEqual((p.status, p.chosen, p.final), (NO_CONFLICT, "fs:RAF", 5))

    def test_rating_only_in_file_is_ticked_not_faststones_zero(self):
        self.pair(0, 0, 1)
        p = self.scan()[0]
        self.assertEqual((p.status, p.chosen, p.final), (NO_CONFLICT, "xmp:JPG", 1))

    def test_two_ratings_are_a_conflict_with_nothing_ticked(self):
        self.pair(0, 5, 1)
        p = self.scan()[0]
        self.assertEqual((p.status, p.chosen), (CONFLICT, None))

    def test_reject_alongside_a_rating_is_a_conflict(self):
        self.pair(3, 3, -1)
        self.assertEqual(self.scan()[0].status, CONFLICT)

    def test_reject_alone_is_a_normal_choice(self):
        self.pair(0, 0, -1)
        p = self.scan()[0]
        self.assertEqual((p.status, p.final), (NO_CONFLICT, -1))

    def test_conflicts_sort_first(self):
        self.photo("Z.JPG", content=jpeg_bytes(xmp_packet(1)))
        self.entry("Z.JPG", 2)
        self.photo("B.JPG")
        self.entry("B.JPG", 2)
        self.assertEqual([p.stem for p in self.scan()], ["Z", "B"])

    def test_bulk_buttons(self):
        self.pair(4, 0, 2)
        p = self.scan()[0]
        self.assertEqual(bulk_choice(p, "faststone"), "fs:JPG")
        self.assertEqual(bulk_choice(p, "highest"), "fs:JPG")
        self.assertEqual(bulk_choice(p, "newest"), "xmp:JPG")
        self.assertIsNone(bulk_choice(p, "skip"))

    def opened(self, when):
        t = time.localtime(when)
        v = (((t.tm_year - 1980) << 25) | (t.tm_mon << 21) | (t.tm_mday << 16) | (t.tm_hour << 11)
             | (t.tm_min << 5) | (t.tm_sec // 2))
        with fsdb.Database(self.db) as db:
            db.con.execute("UPDATE FolderList SET lastAccess = ?", (v,))

    def test_folder_opened_reads_faststones_format(self):
        self.pair(1, 1)
        self.opened(1760000000)
        with fsdb.Database(self.db) as db:
            self.assertEqual(db.folder_opened(self.root), 1760000000)
            self.assertIsNone(db.folder_opened(self.tmp))

    def test_newest_faststone_wins_when_its_folder_was_opened_after_the_file_changed(self):
        self.pair(4, 0, 2)
        os.utime(os.path.join(self.root, "A.JPG"), (1760000000, 1760000000))
        self.opened(1760000100)
        self.assertEqual(bulk_choice(self.scan()[0], "newest"), "fs:JPG")

    def test_newest_file_wins_when_it_changed_after_the_folder_was_opened(self):
        self.pair(4, 0, 2)
        os.utime(os.path.join(self.root, "A.JPG"), (1760000200, 1760000200))
        self.opened(1760000100)
        self.assertEqual(bulk_choice(self.scan()[0], "newest"), "xmp:JPG")

    def test_newest_ignores_faststone_rating_unchanged_since_the_last_sync(self):
        self.pair(3, 3, 1)
        os.utime(os.path.join(self.root, "A.JPG"), (1760000000, 1760000000))
        self.opened(1760000100)
        p = self.scan()[0]
        self.assertEqual(bulk_choice(p, "newest"), "fs:JPG")
        save_state(self.data, {p.state_key: {"final": 3, "frozen": {}}})
        self.assertEqual(bulk_choice(self.scan()[0], "newest"), "xmp:JPG")

    def test_newest_leaves_a_tie_between_different_ratings_unticked(self):
        self.pair(5, 3)
        self.opened(1760000100)
        self.assertIsNone(bulk_choice(self.scan()[0], "newest"))

    def test_use_faststone_when_its_files_differ_takes_what_changed(self):
        self.pair(5, 3)
        p = self.scan()[0]
        self.assertIsNone(bulk_choice(p, "faststone"))
        p.last = 3
        self.assertEqual(bulk_choice(p, "faststone"), "fs:JPG")

    def test_use_faststone_never_picks_its_zero(self):
        self.pair(0, 0, 1)
        self.sidecar("A.xmp", 5)
        p = self.scan()[0]
        self.assertEqual(p.status, CONFLICT)
        self.assertIsNone(bulk_choice(p, "faststone"))

    def test_bulk_on_no_conflict_rows_falls_back_to_their_default(self):
        self.pair(0, 0, 1)  # the only rating is inside the JPEG, so "Use FastStone" has no answer
        photos = self.scan()
        photos[0].chosen = None
        apply_bulk(photos, "faststone")
        self.assertEqual(photos[0].chosen, "xmp:JPG")
        apply_bulk(photos, "skip")
        self.assertIsNone(photos[0].chosen)

    def test_bulk_leaves_a_conflict_unticked_when_the_rule_has_no_answer(self):
        self.pair(0, 0, 1)
        self.sidecar("A.xmp", 5)
        photos = self.scan()
        apply_bulk(photos, "faststone")
        self.assertIsNone(photos[0].chosen)


class Sync(Case):
    def test_rating_reaches_partner_both_ways(self):
        self.photo("A.JPG"), self.photo("A.RAF"), self.photo("B.JPG"), self.photo("B.RAF")
        self.entry("A.JPG", 3), self.entry("A.RAF", 0), self.entry("B.JPG", 0), self.entry("B.RAF", 5)
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        self.assertEqual([r[1:] for r in self.rows()],
                         [("A.JPG", 3), ("A.RAF", 3), ("B.JPG", 5), ("B.RAF", 5)])

    def test_hidden_raw_with_no_entry_gets_one(self):
        self.photo("A.JPG"), self.photo("A.CR3")
        self.entry("A.JPG", 2)
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        self.assertEqual(self.row("A.CR3").rating, 2)
        self.assertEqual(self.row("A.CR3").file_size, 7)

    def test_folder_faststone_never_opened(self):
        self.photo("A.JPG", content=jpeg_bytes(xmp_packet(4)))
        self.photo("A.CR3")
        self.run_sync(options=Options(raw_sidecar=False))
        self.assertEqual([r[1:] for r in self.rows()], [("A.CR3", 4), ("A.JPG", 4)])

    def test_three_files_sync_as_one(self):
        for n in ("A.JPG", "A.CR3", "A.PSD"):
            self.photo(n)
        self.entry("A.PSD", 1)
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        self.assertEqual({r[2] for r in self.rows()}, {1})
        self.assertEqual(len(self.rows()), 3)

    def test_photo_with_two_raws_is_listed_but_never_touched(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(5)))
        self.photo("A.CR3"), self.photo("A.DNG")
        self.entry("A.JPG", 3)
        sidecar = self.sidecar("A.xmp", 1)
        with open(path, "rb") as f:
            before = f.read()
        photos = self.scan()
        self.assertEqual((photos[0].status, photos[0].sources), ("out of scope", []))
        with fsdb.Database(self.db) as db:
            self.assertEqual(len(with_work(db, photos, Options())), 1)
        result = self.run_sync(photos)
        self.assertEqual((result.photos, result.backup), (0, None))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(sources.read_sidecar(sidecar), 1)
        self.assertEqual([r[1:] for r in self.rows()], [("A.JPG", 3)])

    def test_one_raw_with_edits_is_in_scope(self):
        self.photo("A.CR3"), self.photo("A.PSD", content=psd_bytes()), self.photo("A.JPG")
        self.entry("A.CR3", 2)
        self.assertEqual(self.scan()[0].status, NO_CONFLICT)

    def test_out_of_scope_comes_first(self):
        self.photo("Z.CR3"), self.photo("Z.NEF"), self.photo("B.JPG")
        self.entry("B.JPG", 2)
        self.assertEqual([p.stem for p in self.scan()], ["Z", "B"])

    def test_unticked_conflict_is_skipped_and_comes_back(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.entry("A.JPG", 1), self.entry("A.RAF", 2)
        result = self.run_sync()
        self.assertEqual((result.photos, result.backup), (0, None))
        self.assertEqual(self.scan()[0].status, CONFLICT)

    def test_rated_entry_that_no_longer_matches_its_file_is_repaired(self):
        path = self.photo("A.JPG")
        self.entry("A.JPG", 3, matching=False)
        self.run_sync(options=Options(embed_jpeg=False))
        self.assertEqual(self.row("A.JPG").file_size, os.path.getsize(path))

    def test_second_sync_changes_nothing(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.entry("A.JPG", 3)
        self.run_sync(options=Options(embed_jpeg=False))
        before = self.rows()
        result = self.run_sync(options=Options(embed_jpeg=False))
        self.assertEqual((self.rows(), result.faststone, result.sidecars, result.backup), (before, 0, 0, None))

    def test_ticked_zero_clears_everywhere(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.entry("A.JPG", 0), self.entry("A.RAF", 4)
        self.sidecar("A.xmp", 4)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos, Options(embed_jpeg=False))
        self.assertEqual({r[2] for r in self.rows()}, {0})
        self.assertEqual(sources.read_sidecar(os.path.join(self.root, "A.xmp")), 0)

    def test_removal_after_a_sync_is_listed_as_removal(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.entry("A.JPG", 3)
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        with fsdb.Database(self.db) as db:
            db.con.execute("UPDATE FileList SET ratings = 0 WHERE fileName = 'A.JPG'")
        p = self.scan()[0]
        self.assertEqual((p.status, p.final), (REMOVED, 0))

    def test_rating_only_partner_is_not_a_removal_before_any_sync(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.entry("A.JPG", 0), self.entry("A.RAF", 3)
        self.assertEqual(self.scan()[0].status, NO_CONFLICT)

    def test_reject_reaches_faststone_as_minus_one(self):
        self.photo("A.JPG"), self.photo("A.RAF")
        self.sidecar("A.xmp", -1)
        self.run_sync(options=Options(embed_jpeg=False))
        self.assertEqual([r[1:] for r in self.rows()], [("A.JPG", -1), ("A.RAF", -1)])

    def test_star_rating_ticked_overwrites_a_reject(self):
        self.photo("A.RAF")
        self.entry("A.RAF", 3)
        path = self.sidecar("A.xmp", -1)
        photos = self.scan()
        photos[0].chosen = "fs:RAF"
        self.run_sync(photos)
        self.assertEqual(sources.read_sidecar(path), 3)

    def test_value_we_never_write_stops_being_a_conflict(self):
        # In-camera rating inside a CR3: never written (D4), so it mustn't conflict forever.
        self.photo("A.CR3", content=b"box " + xmp_packet(2).encode())
        self.entry("A.CR3", 5)
        photos = self.scan()
        self.assertEqual(photos[0].status, CONFLICT)
        photos[0].chosen = "fs:CR3"
        self.run_sync(photos, Options(raw_sidecar=False))
        p = self.scan()[0]
        self.assertEqual((p.status, p.final), (NO_CONFLICT, 5))
        with open(os.path.join(self.root, "A.CR3"), "rb") as f:
            self.assertIn(b'xmp:Rating="2"', f.read())

    def test_raw_file_contents_are_never_touched(self):
        path = self.photo("A.CR3")
        with open(path, "rb") as f:
            before = f.read()
        when = os.path.getmtime(path)
        self.entry("A.CR3", 5)
        self.run_sync()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.path.getmtime(path), when)

    def test_backup_and_log_written(self):
        self.photo("A.RAF")
        self.entry("A.RAF", 5)
        result = self.run_sync()
        self.assertTrue(os.path.isfile(result.backup) and os.path.isfile(result.log))

    def test_running_faststone_blocks_apply(self):
        with mock.patch.object(fsdb, "faststone_running", return_value=True):
            with self.assertRaises(fsdb.FastStoneRunning):
                self.run_sync([])


class Scope(Case):
    def setUp(self):
        super().setUp()
        self.nested = os.path.join(self.root, "Day1", "Deep")
        self.sibling = self.root + "2"  # shares the picked folder's name as a prefix
        for folder in (self.root, self.nested, self.sibling):
            self.photo("A.JPG", folder), self.photo("A.RAF", folder)
            self.entry("A.JPG", 3, folder)

    def rated(self):
        return sorted({r[0] for r in self.rows() if r[1] == "A.RAF"})

    def test_folder_only(self):
        self.assertEqual([p.folder for p in self.scan()], [self.root])
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        self.assertEqual(self.rated(), [fsdb.db_folder_name(self.root)])

    def test_subfolders_are_nested_only(self):
        self.run_sync(options=Options(embed_jpeg=False), subfolders=True)
        self.assertEqual(self.rated(), sorted([fsdb.db_folder_name(self.root), fsdb.db_folder_name(self.nested)]))
        self.assertFalse(os.path.exists(os.path.join(self.sibling, "A.xmp")))


class Sidecars(Case):
    def test_rating_replaced_and_everything_else_kept_byte_for_byte(self):
        path = self.sidecar("A.xmp", text=CAMERA_RAW_SIDECAR)
        export.write_sidecar(path, 4)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), CAMERA_RAW_SIDECAR.replace('xmp:Rating="0"', 'xmp:Rating="4"'))

    def test_element_form(self):
        path = self.sidecar("A.xmp", text=CAMERA_RAW_SIDECAR.replace('   xmp:Rating="0"\n', "").replace(
            "<crs:ToneCurvePV2012>", "<xmp:Rating>1</xmp:Rating><crs:ToneCurvePV2012>"))
        export.write_sidecar(path, -1)
        self.assertEqual(sources.read_sidecar(path), -1)

    def test_rating_added_when_absent(self):
        text = CAMERA_RAW_SIDECAR.replace('   xmp:Rating="0"\n', "")
        path = self.sidecar("A.xmp", text=text)
        export.write_sidecar(path, 2)
        self.assertEqual(sources.read_sidecar(path), 2)
        with open(path, encoding="utf-8") as f:
            self.assertIn('crs:Exposure2012="+0.35"', f.read())

    def test_namespace_added_when_absent(self):
        path = self.sidecar("A.xmp", text='<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/'
                                          '02/22-rdf-syntax-ns#"><rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>')
        export.write_sidecar(path, 5)
        self.assertEqual(sources.read_sidecar(path), 5)

    def test_broken_sidecar_is_left_alone_and_reported(self):
        self.photo("A.RAF")
        self.entry("A.RAF", 3)
        path = self.sidecar("A.xmp", text='xmp:Rating="1" <broken')
        with open(path, "rb") as f:
            before = f.read()
        photos = self.scan()
        photos[0].chosen = "fs:RAF"
        result = self.run_sync(photos)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(len(result.errors), 1)

    def test_styles(self):
        self.photo("A.CR3"), self.photo("A.JPG")
        self.entry("A.CR3", 3)
        self.run_sync(options=Options(darktable_sidecar=True, embed_jpeg=False))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "A.xmp")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "A.CR3.xmp")))

    def test_lone_jpeg_gets_no_sidecar_by_default(self):
        self.photo("A.JPG")
        self.entry("A.JPG", 3)
        self.run_sync(options=Options(embed_jpeg=False))
        self.assertFalse(os.path.exists(os.path.join(self.root, "A.xmp")))

    def test_zero_creates_no_sidecar(self):
        self.photo("A.RAF")
        self.entry("A.RAF", 0)
        self.photo("A.JPG", content=jpeg_bytes(exif=(0, 0)))
        self.assertEqual(self.scan(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "A.xmp")))


@unittest.skipUnless(HAS_EXIFTOOL, "ExifTool isn't in vendor/exiftool")
class JpegEmbedding(Case):
    def test_rating_written_picture_untouched_entry_kept_in_step(self):
        path = self.photo("A.JPG")
        os.utime(path, (1760000001, 1760000001))
        picture = image_part(path)
        self.entry("A.JPG", 3)
        result = self.run_sync()
        self.assertEqual(result.errors, [])
        self.assertEqual(sources.read_jpeg(path), (3, 3))
        self.assertEqual(image_part(path), picture)
        self.assertEqual(os.path.getmtime(path), 1760000001)
        row = self.row("A.JPG")
        self.assertEqual((row.file_size, row.file_time, row.rating),
                         (os.path.getsize(path), fsdb.dos_time(os.stat(path).st_mtime_ns), 3))

    def test_entry_of_rewritten_jpeg_is_refreshed_even_if_its_rating_was_right(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(1)))
        self.entry("A.JPG", 3)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos)
        self.assertEqual(self.row("A.JPG").file_size, os.path.getsize(path))

    def test_blank_field_counts_as_a_difference_and_gets_filled(self):
        for name, content, fields, final in (("A.JPG", jpeg_bytes(xmp_packet(4)), (4, None), 4),
                                             ("B.JPG", jpeg_bytes(exif=(2, 25)), (None, 2), 2)):
            path = self.photo(name, content=content)
            self.entry(name, final)
            self.assertEqual(sources.read_jpeg(path), fields)
        with fsdb.Database(self.db) as db:
            self.assertEqual(len(with_work(db, self.scan(), Options(raw_sidecar=False))), 2)
        self.run_sync()
        self.assertEqual(sources.read_jpeg(os.path.join(self.root, "A.JPG")), (4, 4))
        self.assertEqual(sources.read_jpeg(os.path.join(self.root, "B.JPG")), (2, 2))
        with fsdb.Database(self.db) as db:
            self.assertEqual(with_work(db, self.scan(), Options()), [])

    def test_jpeg_box_off_ignores_what_is_inside_the_file(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(5), exif=(1, 1)))
        self.photo("A.RAF")
        self.entry("A.JPG", 3), self.entry("A.RAF", 3)
        with open(path, "rb") as f:
            before = f.read()
        off = Options(raw_sidecar=False, embed_jpeg=False)
        photos = self.scan(inside=False)
        self.assertEqual({s.key for s in photos[0].sources}, {"fs:JPG", "fs:RAF", "raw:RAF"})
        with fsdb.Database(self.db) as db:
            self.assertEqual(with_work(db, photos, off), [])
        self.assertEqual(self.run_sync(options=off).jpegs, 0)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_jpeg_box_off_still_syncs_the_faststone_entries(self):
        self.photo("A.JPG", content=jpeg_bytes(xmp_packet(5)))
        self.photo("A.RAF")
        self.entry("A.RAF", 3)
        self.run_sync(options=Options(raw_sidecar=False, embed_jpeg=False))
        self.assertEqual(self.row("A.JPG").rating, 3)
        self.assertEqual(sources.read_jpeg(os.path.join(self.root, "A.JPG")), (5, None))

    def test_box_off_also_covers_psd_and_png(self):
        path = self.photo("A.PSD", content=psd_bytes(xmp_packet(5)))
        self.entry("A.PSD", 3)
        self.assertEqual({s.key for s in self.scan(inside=False)[0].sources}, {"fs:PSD"})
        self.assertEqual(sources.read_psd(path), 5)

    def test_real_rating_change_fills_both_fields(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(4)))
        self.entry("A.JPG", 2)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (2, 2))

    def test_disagreeing_fields_are_a_conflict_even_if_nothing_else_differs(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(5), exif=(1, 1)))
        self.entry("A.JPG", 1)
        photos = self.scan()
        self.assertEqual((photos[0].status, photos[0].chosen), (CONFLICT, None))
        with fsdb.Database(self.db) as db:
            self.assertEqual(len(with_work(db, photos, Options())), 1)
        photos[0].chosen = "exif:JPG"
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (1, 1))
        self.assertEqual(self.scan()[0].status, NO_CONFLICT)

    def test_exif_zero_beside_a_rating_is_work_but_not_a_conflict(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(3), exif=(0, 0)))
        self.entry("A.JPG", 3)
        photos = self.scan()
        self.assertEqual((photos[0].status, photos[0].final), (NO_CONFLICT, 3))
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (3, 3))

    def test_clearing_leaves_a_blank_field_blank(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(3)))
        self.entry("A.JPG", 0)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (0, None))

    def test_clearing_leaves_blank_xmp_blank(self):
        path = self.photo("A.JPG", content=jpeg_bytes(exif=(3, 50)))
        self.entry("A.JPG", 0)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (None, 0))

    def test_reject_leaves_blank_exif_blank(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(2)))
        self.entry("A.JPG", 2)
        photos = self.scan()
        photos[0].override = -1
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (-1, None))

    def test_reject_over_a_filled_exif_clears_it(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(2), exif=(2, 25)))
        self.entry("A.JPG", 2)
        photos = self.scan()
        photos[0].override = -1
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (-1, 0))

    def test_reject_stays_a_reject_after_a_sync(self):
        self.photo("A.JPG")
        self.sidecar("A.xmp", -1)
        self.run_sync()
        p = self.one("A")
        self.assertEqual((p.source("xmp:JPG").value, p.source("exif:JPG").value, p.status), (-1, None, NO_CONFLICT))
        with fsdb.Database(self.db) as db:
            self.assertEqual(with_work(db, self.scan(), Options()), [])

    def test_existing_windows_rating_is_kept_in_step(self):
        path = self.photo("A.JPG", content=jpeg_bytes(exif=(1, 1)))
        self.entry("A.JPG", 5)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        self.run_sync(photos)
        self.assertEqual(sources.read_jpeg(path), (5, 5))

    def test_reject_in_jpeg_and_windows_falls_to_zero(self):
        path = self.photo("A.JPG", content=jpeg_bytes(exif=(0, 0)))
        self.sidecar("A.xmp", -1)
        self.run_sync()
        self.assertEqual(sources.read_jpeg(path), (-1, 0))

    def test_unicode_paths(self):
        folder = os.path.join(self.root, "圣诞 été")
        path = self.photo("A.JPG", folder)
        self.entry("A.JPG", 2, folder)
        self.assertEqual(self.run_sync(root=folder).errors, [])
        self.assertEqual(sources.read_jpeg(path)[0], 2)

    def test_second_sync_leaves_the_file_alone(self):
        path = self.photo("A.JPG")
        self.entry("A.JPG", 2)
        self.run_sync()
        with open(path, "rb") as f:
            before = f.read()
        self.assertIsNone(self.run_sync().backup)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)


class JpegFailures(Case):
    def test_without_exiftool_the_photo_is_reported_and_returns(self):
        path = self.photo("A.JPG", content=jpeg_bytes(xmp_packet(1)))
        self.photo("A.RAF")
        self.entry("A.JPG", 3)
        photos = self.scan()
        photos[0].chosen = "fs:JPG"
        with mock.patch.object(export, "find_exiftool", return_value=None):
            result = self.run_sync(photos, Options(raw_sidecar=False))
        self.assertEqual((len(result.errors), result.photos), (1, 0))
        self.assertIn("ExifTool", result.errors[0])
        self.assertEqual(sources.read_jpeg(path)[0], 1)
        self.assertEqual(self.row("A.RAF").rating, 3)  # FastStone's part still went through
        self.assertEqual(self.scan()[0].status, CONFLICT)


if __name__ == "__main__":
    unittest.main()
