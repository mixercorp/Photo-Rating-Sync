import os
import tkinter as tk
import unittest
from unittest import mock

from helpers import Case, jpeg_bytes, xmp_packet
from ratingsync import export, fsdb, gui, help
from ratingsync.apply import Options

HAS_EXIFTOOL = bool(export.find_exiftool())

try:
    tk.Tk().destroy()
    HAS_DISPLAY = True
except tk.TclError:
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class Windows(Case):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(fsdb, "default_data_dir", return_value=self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = gui.App()
        self.addCleanup(self.app.destroy)
        self.app.folder.set(self.root)
        self.photo("A.JPG", content=jpeg_bytes(xmp_packet(1))), self.photo("A.RAF")
        self.entry("A.RAF", 5)
        self.photo("B.JPG")
        self.entry("B.JPG", 2)

    def window(self, fresh=True):
        """fresh: undo the opening "Use highest", so tests start from nothing chosen."""
        win = gui.SyncWindow(self.app, self.scan(), 7, lambda w: None, Options(raw_sidecar=False))
        if fresh and hasattr(win.conflicts, "table"):
            win.conflicts.bulk("skip")
            win.conflicts.mode.set("")
        win.update()
        return win

    def test_conflicts_open_with_use_highest_on(self):
        win = self.window(fresh=False)
        self.assertEqual(win.conflicts.mode.get(), "highest")
        self.assertEqual(win.conflicts.photos[0].final, 5)
        self.assertEqual(win.others.mode.get(), "")

    def test_conflicts_have_no_use_faststone(self):
        win = self.window()
        buttons = [w.cget("text") for frame in win.conflicts.winfo_children() if isinstance(frame, gui.ttk.Frame)
                   for w in frame.winfo_children() if isinstance(w, gui.ttk.Radiobutton)]
        self.assertEqual(buttons, ["Use highest", "Use newest", "Skip all"])
        others = [w.cget("text") for frame in win.others.winfo_children() if isinstance(frame, gui.ttk.Frame)
                  for w in frame.winfo_children() if isinstance(w, gui.ttk.Radiobutton)]
        self.assertEqual(others[0], "Use FastStone")

    def test_setting_a_photo_by_hand_switches_the_rule_off(self):
        win = self.window(fresh=False)
        self.click(win.conflicts, 0, 2)
        self.assertEqual((win.conflicts.mode.get(), win.conflicts.photos[0].final), ("", 2))

    def click(self, section, row, value):
        table = section.table
        x = table.final_x + table.slot * gui.FINAL_VALUES.index(value) + 2
        table.body.event_generate("<Button-1>", x=x, y=table.row_height * row + 5)
        table.update()

    def test_main_window_shows_the_sync_button_and_defaults(self):
        self.app.update()
        button = self.app.sync_button
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                             self.app.winfo_rooty() + self.app.winfo_height())
        self.assertTrue(self.app.subfolders.get())

    def reopened(self):
        app = gui.App()
        self.addCleanup(app.destroy)
        return app.folder.get()

    def test_first_run_starts_empty(self):
        self.assertEqual(self.reopened(), "")

    def test_sync_remembers_the_folder(self):
        self.app.db_path = self.db + ".missing"
        with mock.patch.object(gui.App, "locate_db", return_value=False):
            self.app.sync()
        self.assertEqual(self.reopened(), "")  # nothing to scan yet, so nothing saved
        with mock.patch.object(gui.App, "locate_db", return_value=True), \
                mock.patch.object(gui.App, "background"):
            self.app.sync()
        self.assertEqual(self.reopened(), self.root)

    def test_browse_remembers_the_folder(self):
        with mock.patch.object(gui.filedialog, "askdirectory", return_value=self.root) as asked:
            self.app.browse()
        self.assertEqual(self.reopened(), self.root)
        self.assertEqual(asked.call_args.kwargs["initialdir"], self.root)

    def test_deleted_folder_is_forgotten(self):
        gui.save_last_folder(self.data, os.path.join(self.tmp, "gone"))
        self.assertEqual(self.reopened(), "")

    def test_damaged_settings_are_ignored(self):
        os.makedirs(self.data)
        with open(os.path.join(self.data, gui.SETTINGS_FILE), "w") as f:
            f.write("not json")
        self.assertEqual(self.reopened(), "")

    def test_file_extensions_in_the_window_are_lower_case(self):
        import re
        texts = [label for _, group, label in gui.COLUMNS] + [group for _, group, _ in gui.COLUMNS]
        texts += [text for _, text in gui.PLACE_OPTIONS]
        for text in texts:
            self.assertEqual(re.findall(r"\.[A-Z]{3,4}\b", text), [], text)

    def test_help_menu_has_manual_and_about(self):
        labels = [self.app.help_menu.entrycget(i, "label") for i in (0, 2)]
        self.assertEqual(labels, ["Manual", "About"])

    def text_of(self, window):
        self.addCleanup(window.destroy)
        return window.doc.get("1.0", "end")

    def test_manual_opens_with_the_guide(self):
        text = self.text_of(help.show_manual(self.app))
        for words in ("Close FastStone first", "Out of Scope", "Use highest", "1→3", "FSIV.db"):
            self.assertIn(words, text)
        self.assertNotIn("**", text)
        self.assertNotIn("# ", text)

    def test_help_windows_show_their_scrollbar_and_close_button(self):
        for show in (help.show_manual, help.show_about):
            window = show(self.app)
            self.addCleanup(window.destroy)
            window.update()
            self.assertGreater(window.doc.scrollbar.winfo_width(), 5)
            close = [w for w in window.winfo_children() if isinstance(w, gui.ttk.Button)][0]
            self.assertLessEqual(close.winfo_rooty() + close.winfo_height(), window.winfo_rooty() + window.winfo_height())

    def test_about_shows_licence_and_credits(self):
        text = self.text_of(help.show_about(self.app))
        for words in (gui.__version__, "Free to use", "MIT License", "Permission is hereby granted", "ExifTool",
                      "Phil Harvey", "not affiliated", "Made by Tiger.", "Copyright (c) 2026 Tiger"):
            self.assertIn(words, text)
        self.assertNotIn("Shao", text)

    def test_no_sidecar_wording_or_preview_in_the_window(self):
        for _name, text in gui.PLACE_OPTIONS:
            self.assertNotIn("sidecar", text.lower())
        self.assertFalse(hasattr(self.app, "preview"))

    def test_sections_split_conflicts_from_the_rest(self):
        win = self.window()
        self.assertEqual([p.stem for p in win.conflicts.photos], ["A"])
        self.assertEqual([p.stem for p in win.others.photos], ["B"])
        self.assertIsNone(win.conflicts.photos[0].final)
        self.assertEqual(win.others.photos[0].final, 2)

    def test_summary_line(self):
        win = self.window()
        labels = [w.cget("text") for w in win.winfo_children() if isinstance(w, gui.ttk.Label)]
        self.assertIn("7 total photos, 2 to be synced.", labels)

    def test_headers_are_fixed_and_grouped(self):
        head = self.window().conflicts.table.head
        shown = [head.itemcget(i, "text") for i in head.find_all() if head.type(i) == "text"]
        self.assertEqual(shown, ["Photo", "Final", ".jpg", ".jpeg", "RAW", ".psd", ".png", "XMP", "EXIF", "XMP", "EXIF",
                                 ".psd", ".png", "RAW", ".xmp", "FastStone", ".jpg", ".jpeg"])
        self.assertEqual(sum(head.type(i) == "line" for i in head.find_all()), 3)

    def colours(self, table, text):
        body = table.body
        return [body.itemcget(i, "fill") for i in body.find_all()
                if body.type(i) == "text" and body.itemcget(i, "text") == text]

    def test_unchanged_cell_is_grey_and_changed_is_coloured(self):
        section = self.window().conflicts
        self.click(section, 0, 5)
        self.assertEqual(section.table.cell(0, "fs:RAW"), ("5", "5"))
        self.assertIn(gui.SAME_COLOUR, self.colours(section.table, "5"))
        self.assertEqual(self.colours(section.table, "1"), [gui.BEFORE_COLOUR])
        self.assertIn(gui.AFTER_COLOUR, self.colours(section.table, "5"))

    @unittest.skipUnless(HAS_EXIFTOOL, "ExifTool isn't in vendor/exiftool")
    def test_jpg_and_jpeg_of_one_photo_have_their_own_columns_and_both_get_written(self):
        self.photo("C.JPG", content=jpeg_bytes(xmp_packet(1)))
        self.photo("C.JPEG", content=jpeg_bytes(xmp_packet(3)))
        self.entry("C.JPG", 1), self.entry("C.JPEG", 3)
        section = self.window().conflicts
        row = [p.stem for p in section.photos].index("C")
        table = section.table
        self.assertEqual((table.cell(row, "xmp:JPG"), table.cell(row, "xmp:JPEG")), (("1", "1"), ("3", "3")))
        self.click(section, row, 5)
        self.assertEqual((table.cell(row, "fs:JPG"), table.cell(row, "fs:JPEG")), (("1", "5"), ("3", "5")))
        self.assertEqual(table.cell(row, "exif:JPEG"), ("", "5"))
        self.run_sync([section.photos[row]], Options(raw_sidecar=False))
        self.assertEqual((self.row("C.JPG").rating, self.row("C.JPEG").rating), (5, 5))
        self.assertEqual(self.one("C").source("xmp:JPEG").value, 5)

    def test_second_file_with_no_entry_shows_as_a_change(self):
        self.photo("C.JPG", content=jpeg_bytes(xmp_packet(1), exif=(1, 1)))
        self.photo("C.JPEG", content=jpeg_bytes(xmp_packet(1), exif=(1, 1)))
        self.entry("C.JPG", 1)
        section = self.window().others
        row = [p.stem for p in section.photos].index("C")
        self.assertEqual(section.table.cell(row, "fs:JPEG"), ("", "1"))
        self.assertTrue(section.table.changes(row, "fs:JPEG"))
        self.assertFalse(section.table.changes(row, "fs:JPG"))

    def test_out_of_scope_section_lists_files_and_stays_out_of_the_count(self):
        self.photo("Z.CR3"), self.photo("Z.DNG")
        win = self.window()
        self.assertEqual(win.excluded.files.get(0, "end"), ("Z.CR3,  Z.DNG",))
        labels = [w.cget("text") for w in win.winfo_children() if isinstance(w, gui.ttk.Label)]
        self.assertIn("7 total photos, 2 to be synced.", labels)
        notes = [w.cget("text") for w in win.excluded.winfo_children() if isinstance(w, gui.ttk.Label)]
        self.assertIn(gui.OUT_OF_SCOPE_NOTE, notes)
        self.assertEqual([p.stem for p in win.conflicts.photos + win.others.photos], ["A", "B"])

    def test_out_of_scope_section_is_always_there(self):
        notes = [w.cget("text") for w in self.window().excluded.winfo_children() if isinstance(w, gui.ttk.Label)]
        self.assertEqual(notes, ["------ Out of Scope ------", "None"])

    def test_every_column_fits_a_normal_window(self):
        table = self.window().conflicts.table
        self.assertLessEqual(table.columns_x + table.column_width * len(gui.COLUMNS), self.app.winfo_screenwidth() - 100)

    def test_ok_stays_in_the_window_with_long_lists(self):
        for i in range(30):
            self.photo(f"C{i:03}.JPG")
            self.entry(f"C{i:03}.JPG", 1)
        win = self.window()
        win.update()
        self.assertLessEqual(win.ok_button.winfo_rooty() + win.ok_button.winfo_height(), win.winfo_rooty() + win.winfo_height())

    def test_cells_show_before_and_after(self):
        win = self.window()
        table = win.conflicts.table
        self.assertEqual(table.cell(0, "fs:RAW"), ("5", "5"))  # nothing chosen yet
        self.click(win.conflicts, 0, 5)
        self.assertEqual(win.conflicts.photos[0].final, 5)
        self.assertEqual(table.cell(0, "fs:RAW"), ("5", "5"))
        self.assertEqual(table.cell(0, "fs:JPG"), ("", "5"))   # no entry yet
        self.assertEqual(table.cell(0, "xmp:JPG"), ("1", "5"))
        self.assertEqual(table.cell(0, "exif:JPG"), ("", "5"))

    def test_irrelevant_format_is_empty(self):
        table = self.window().conflicts.table
        self.assertEqual(table.cell(0, "fs:PSD"), ("", ""))
        self.assertEqual(table.cell(0, "xmp:PNG"), ("", ""))

    def test_clearing_a_rating_leaves_the_after_empty(self):
        section = self.window().conflicts
        self.click(section, 0, 0)
        self.assertEqual(section.photos[0].final, 0)
        self.assertEqual(section.table.cell(0, "fs:RAW"), ("5", ""))
        self.assertEqual(section.table.cell(0, "xmp:JPG"), ("1", ""))

    def test_blank_exif_stays_empty_for_unrated_and_reject(self):
        section = self.window().conflicts
        self.click(section, 0, 0)
        self.assertEqual(section.table.cell(0, "exif:JPG"), ("", ""))
        self.click(section, 0, -1)
        self.assertEqual(section.table.cell(0, "exif:JPG"), ("", ""))

    def test_reject_can_be_set_by_hand_and_is_written(self):
        section = self.window().conflicts
        self.click(section, 0, -1)
        self.assertEqual(section.photos[0].final, -1)
        self.assertEqual(section.table.cell(0, "fs:RAW"), ("5", "-1"))
        self.run_sync(section.photos, Options(raw_sidecar=False))
        self.assertEqual(self.row("A.RAF").rating, -1)

    def test_a_rating_no_source_holds_can_be_set(self):
        section = self.window().conflicts
        self.click(section, 0, 4)
        self.run_sync(section.photos, Options(raw_sidecar=False))
        self.assertEqual({self.row("A.RAF").rating, self.row("A.JPG").rating}, {4})

    def test_second_click_skips(self):
        section = self.window().conflicts
        self.click(section, 0, 3)
        self.click(section, 0, 3)
        self.assertIsNone(section.photos[0].final)

    def test_no_conflict_row_can_be_changed_and_skipped(self):
        section = self.window().others
        self.click(section, 0, 2)
        self.assertIsNone(section.photos[0].final)
        self.click(section, 0, 5)
        self.assertEqual(section.photos[0].final, 5)

    def test_final_symbols(self):
        self.assertEqual(gui.final_symbol(-1, -1), ("☠", gui.REJECT_COLOUR))
        self.assertEqual(gui.final_symbol(0, 0), ("✖", gui.UNRATED_COLOUR))
        self.assertEqual([gui.final_symbol(n, 2)[0] for n in range(1, 6)], list("★★☆☆☆"))
        self.assertEqual([gui.final_symbol(n, 0)[0] for n in range(1, 6)], list("☆☆☆☆☆"))
        self.assertEqual([gui.final_symbol(n, -1)[0] for n in range(1, 6)], list("☆☆☆☆☆"))

    def test_empty_section_has_no_table(self):
        with fsdb.Database(self.db) as db:
            db.con.execute("DELETE FROM FileList WHERE fileName = 'A.RAF'")
        win = self.window()
        self.assertFalse(hasattr(win.conflicts, "table"))
        self.assertEqual(len(win.others.photos), 2)

    def test_each_section_has_its_own_bulk_buttons(self):
        win = self.window()
        win.others.bulk("skip")
        self.assertIsNone(win.others.photos[0].final)
        self.assertIsNone(win.conflicts.photos[0].final)
        win.conflicts.bulk("highest")
        self.assertEqual(win.conflicts.photos[0].final, 5)

    def test_bulk_replaces_a_rating_set_by_hand(self):
        section = self.window().conflicts
        self.click(section, 0, 1)
        section.bulk("highest")
        self.assertEqual(section.photos[0].final, 5)

    def test_long_list_only_draws_what_is_in_view(self):
        for i in range(300):
            self.photo(f"C{i:03}.JPG")
            self.entry(f"C{i:03}.JPG", 1)
        section = self.window().others
        section.table.draw()
        names = [section.table.body.itemcget(i, "text") for i in section.table.body.find_all()]
        self.assertLess(sum(n.startswith("C") for n in names), 40)

    def test_faststone_open_only_warns(self):
        self.assertFalse(hasattr(fsdb, "close_faststone"))
        with mock.patch.object(fsdb, "faststone_running", return_value=True), \
                mock.patch.object(gui.messagebox, "showwarning") as warned:
            self.app.job = {"subfolders": True, "options": None}
            self.app.check_and_scan(self.root)
            event, *args = self.app.events.get_nowait()
            event(*args)
        self.assertIn("Close FastStone", warned.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
