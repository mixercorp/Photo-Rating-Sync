"""The desktop window: pick a folder, click Sync, settle the choices, OK."""
import ctypes
import json
import os
import queue
import threading
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import APP_NAME, __version__, fsdb, help
from .apply import Options, apply, load_state, preview, with_work
from .scan import CONFLICT, EDIT_EXTS, JPEG_EXTS, OUT_OF_SCOPE, apply_bulk, scan

SETTINGS_FILE = "settings.json"
COLUMNS = [("fs:JPG", "FastStone", ".jpg"), ("fs:JPEG", "FastStone", ".jpeg"), ("fs:RAW", "FastStone", "RAW"),
           ("fs:PSD", "FastStone", ".psd"), ("fs:PNG", "FastStone", ".png"),
           ("xmp:JPG", ".jpg", "XMP"), ("exif:JPG", ".jpg", "EXIF"), ("xmp:JPEG", ".jpeg", "XMP"), ("exif:JPEG", ".jpeg", "EXIF"),
           ("xmp:PSD", "", ".psd"), ("xmp:PNG", "", ".png"), ("raw", "", "RAW"), ("side", "", ".xmp")]
OUT_OF_SCOPE_NOTE = "We don't touch the following files because they are out of the scope of a normal processing flow."
FINAL_VALUES = [-1, 0, 1, 2, 3, 4, 5]  # the spots in the Final column, left to right
HEAD_COLOUR, BEFORE_COLOUR, ARROW_COLOUR, AFTER_COLOUR, SAME_COLOUR = "#f0f0f0", "#b3261e", "#808080", "#0b6e2e", "#bdbdbd"
STAR_COLOUR, OFF_COLOUR, REJECT_COLOUR, UNRATED_COLOUR = "#000000", "#c4c4c4", "#b3261e", "#000000"
PLACE_OPTIONS = [
    ("raw_sidecar", ".xmp beside RAW files, created if missing (Lightroom, Bridge, Capture One, DxO)"),
    ("darktable_sidecar", "[IMG].[RAW].xmp, created if missing, such as IMG.raf.xmp (darktable, digiKam)"),
    ("embed_jpeg", "Inside the file: .jpg, .jpeg, .psd and .png"),
]
BULK_BUTTONS = [("Use FastStone", "faststone"), ("Use highest", "highest"),
                ("Use newest", "newest"), ("Skip all", "skip")]
CONFLICT_BUTTONS = BULK_BUTTONS[1:]  # FastStone's own files often disagree in a conflict


def load_last_folder(data_dir):
    """The folder used last time, if it's still there."""
    try:
        with open(os.path.join(data_dir, SETTINGS_FILE), encoding="utf-8") as f:
            folder = json.load(f)["last_folder"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return folder if isinstance(folder, str) and os.path.isdir(folder) else ""


def save_last_folder(data_dir, folder):
    try:  # a convenience: failing to save mustn't stop a sync
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, SETTINGS_FILE), "w", encoding="utf-8") as f:
            json.dump({"last_folder": folder}, f, ensure_ascii=False)
    except OSError:
        pass


def final_symbol(slot, final):
    """The glyph and colour for one spot in the Final column: ☠, ✖ or a star."""
    if slot == -1:
        return "☠", REJECT_COLOUR if final == -1 else OFF_COLOUR
    if slot == 0:
        return "✖", UNRATED_COLOUR if final == 0 else OFF_COLOUR
    if final is not None and final >= slot:
        return "★", STAR_COLOUR
    return "☆", OFF_COLOUR


def family(key):
    """The fixed column a place belongs to. All RAW types share one, for lack of room."""
    kind, ext = key.split(":", 1)
    if kind == "fs":
        return f"fs:{ext}" if ext in JPEG_EXTS | EDIT_EXTS else "fs:RAW"
    if kind in ("xmp", "exif"):
        return key
    return {"raw": "raw", "side": "side"}[kind]


def _shown(values):
    return "/".join(dict.fromkeys(v for v in values if v))


def row_cells(photo, options):
    """Column -> (before, after, changed) for one photo. A rating cleared by the sync shows no after."""
    parts = defaultdict(list)
    for key, (before, after) in preview(photo, options).items():
        cleared = after == 0 and before not in (None, 0)
        parts[family(key)].append(("" if before is None else str(before),
                                   "" if after is None or cleared else str(after)))
    # A file's own before and after decide "changed": two files can share a column and hide it.
    return {column: (_shown(b for b, _ in pairs), _shown(a for _, a in pairs), any(b != a for b, a in pairs))
            for column, pairs in parts.items()}


class RatingTable(ttk.Frame):
    """One row per photo. Drawn on a canvas, because a Treeview can't colour part of a cell or
    take clicks on single stars. Only the rows in view are drawn, so long lists stay quick."""

    def __init__(self, parent, photos, name_of, options, on_click=None):
        super().__init__(parent)
        self.photos, self.name_of, self.options, self.on_click = photos, name_of, options, on_click
        self.font = tkfont.nametofont("TkDefaultFont")
        self.bold = self.font.copy()
        self.bold.configure(weight="bold")
        self.row_height = int(self.font.metrics("linespace") * 1.7)
        self.pad = self.font.measure("00")
        self.slot = self.font.measure("★") + self.font.measure("0")
        self.name_width = self.bold.measure("0000000000000000")
        self.final_x = self.name_width + self.pad
        self.columns_x = self.final_x + self.slot * len(FINAL_VALUES) + self.pad
        self.column_width = self.font.measure("-1→-1") + self.pad
        width = self.columns_x + self.column_width * len(COLUMNS)
        height = self.row_height * len(photos)
        self.cells = {}

        self.head_height = self.font.metrics("linespace") * 2 + self.pad // 2
        self.head = tk.Canvas(self, height=self.head_height, background=HEAD_COLOUR, highlightthickness=0)
        self.body = tk.Canvas(self, height=self.row_height * min(len(photos), 10), background="#ffffff",
                              highlightthickness=0, scrollregion=(0, 0, width, height))
        self.head.configure(scrollregion=(0, 0, width, self.head_height))
        self.down = ttk.Scrollbar(self, orient="vertical", command=self.body.yview)
        across = ttk.Scrollbar(self, orient="horizontal", command=self.scroll_across)
        self.body.configure(yscrollcommand=self.scrolled, xscrollcommand=across.set)
        self.head.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.down.grid(row=1, column=1, sticky="ns")
        across.grid(row=2, column=0, sticky="ew")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.body.bind("<Configure>", lambda event: self.draw())
        self.body.bind("<Button-1>", self.clicked)
        self.body.bind("<MouseWheel>", lambda e: self.body.yview_scroll(-e.delta // 120, "units"))
        self.draw_head()

    def scroll_across(self, *args):
        self.body.xview(*args)
        self.head.xview(*args)

    def scrolled(self, first, last):
        self.down.set(first, last)
        self.draw()

    def draw_head(self):
        line = self.font.metrics("linespace")
        top, bottom = line // 2 + 2, line + line // 2 + 6
        middle = self.head_height // 2
        self.head.create_text(self.pad // 2, middle, text="Photo", anchor="w", font=self.bold)
        self.head.create_text(self.final_x, middle, text="Final", anchor="w", font=self.bold)
        for i, (_, group, label) in enumerate(COLUMNS):
            x = self.columns_x + self.column_width * i
            self.head.create_text(x, bottom if group else middle, text=label, anchor="w", font=self.font)
        for group in dict.fromkeys(g for _, g, _ in COLUMNS if g):
            spots = [i for i, (_, g, _) in enumerate(COLUMNS) if g == group]
            left = self.columns_x + self.column_width * spots[0]
            right = self.columns_x + self.column_width * (spots[-1] + 1) - self.pad
            self.head.create_text((left + right) // 2, top, text=group, font=self.font)
            self.head.create_line(left, line + 3, right, line + 3, fill=ARROW_COLOUR)

    def cell(self, row, column):
        if row not in self.cells:
            self.cells[row] = row_cells(self.photos[row], self.options)
        return self.cells[row].get(column, ("", "", False))[:2]

    def changes(self, row, column):
        self.cell(row, column)
        return self.cells[row].get(column, ("", "", False))[2]

    def draw(self):
        self.body.delete("all")
        first = max(int(self.body.canvasy(0)) // self.row_height, 0)
        last = min(first + self.body.winfo_height() // self.row_height + 2, len(self.photos))
        for row in range(first, last):
            self.draw_row(row)

    def draw_row(self, row):
        photo = self.photos[row]
        y = self.row_height * row + self.row_height // 2
        self.body.create_text(self.pad // 2, y, text=self.name_of(photo), anchor="w", font=self.bold)
        final = photo.final
        for i, value in enumerate(FINAL_VALUES):
            glyph, colour = final_symbol(value, final)
            self.body.create_text(self.final_x + self.slot * i, y, text=glyph, anchor="w", font=self.bold, fill=colour)
        for i, (column, _, _) in enumerate(COLUMNS):
            before, after = self.cell(row, column)
            if not before and not after:
                continue
            same = not self.changes(row, column)  # nothing is written here: the values are only for reading
            x = self.columns_x + self.column_width * i
            text = self.body.create_text(x, y, text=before, anchor="w", font=self.font,
                                         fill=SAME_COLOUR if same else BEFORE_COLOUR)
            x = self.body.bbox(text)[2] + 2
            arrow = self.body.create_text(x, y, text="→", anchor="w", font=self.font,
                                          fill=SAME_COLOUR if same else ARROW_COLOUR)
            x = self.body.bbox(arrow)[2] + 2
            self.body.create_text(x, y, text=after, anchor="w", font=self.font if same else self.bold,
                                  fill=SAME_COLOUR if same else AFTER_COLOUR)

    def value_at(self, x):
        slot = (x - self.final_x) // self.slot
        return FINAL_VALUES[slot] if 0 <= slot < len(FINAL_VALUES) else None

    def clicked(self, event):
        row = int(self.body.canvasy(event.y)) // self.row_height
        value = self.value_at(int(self.body.canvasx(event.x)))
        if value is None or not 0 <= row < len(self.photos):
            return
        photo = self.photos[row]
        if photo.final == value:
            photo.chosen, photo.override = None, None
        else:
            photo.override = value
        self.refresh()
        if self.on_click:
            self.on_click()

    def refresh(self):
        self.cells.clear()
        self.draw()


class Section(ttk.Frame):
    """A titled list of photos with its own bulk buttons, one row per photo. The buttons show
    which rule is in force; setting a photo by hand switches that off."""

    def __init__(self, window, title, photos, options, buttons=BULK_BUTTONS, start=None):
        super().__init__(window)
        self.window, self.photos = window, photos
        self.mode = tk.StringVar(value="")
        ttk.Label(self, text=f"------ {title} ------").pack(anchor="w")
        if not photos:
            ttk.Label(self, text="None").pack(anchor="w", pady=(2, 0))
            return

        bulk = ttk.Frame(self)
        bulk.pack(fill="x", pady=4)
        for i, (text, mode) in enumerate(buttons):
            ttk.Radiobutton(bulk, text=text, value=mode, variable=self.mode, style="Toolbutton",
                            command=lambda m=mode: self.bulk(m)).pack(side="left", padx=(6 if i else 0, 0), ipadx=8, ipady=2)
        self.table = RatingTable(self, photos, window.row_name, options, on_click=lambda: self.mode.set(""))
        self.table.pack(fill="both", expand=True)
        if start:
            self.bulk(start)

    def bulk(self, mode):
        self.mode.set(mode)
        apply_bulk(self.photos, mode)
        self.table.refresh()


class OutOfScope(ttk.Frame):
    """Photos the sync leaves alone, listed so nobody wonders why they weren't synced."""

    def __init__(self, window, photos):
        super().__init__(window)
        ttk.Label(self, text="------ Out of Scope ------").pack(anchor="w")
        if not photos:
            ttk.Label(self, text="None").pack(anchor="w", pady=(2, 0))
            return
        ttk.Label(self, text=OUT_OF_SCOPE_NOTE).pack(anchor="w", pady=4)
        body = ttk.Frame(self)
        body.pack(fill="x")
        self.files = tk.Listbox(body, height=min(len(photos), 4), activestyle="none", selectmode="none",
                                highlightthickness=0, borderwidth=0)
        down = ttk.Scrollbar(body, orient="vertical", command=self.files.yview)
        self.files.configure(yscrollcommand=down.set)
        self.files.pack(side="left", fill="x", expand=True)
        down.pack(side="left", fill="y")
        for photo in photos:
            self.files.insert("end", ",  ".join(sorted(photo.files.values(), key=str.upper)))


class SyncWindow(tk.Toplevel):
    """Conflicts above, the rest below. Each row shows every place's rating before and after."""

    def __init__(self, master, photos, seen, on_apply, options=None):
        super().__init__(master)
        options = options or Options()
        self.title("Sync: choose the final ratings")
        self.geometry(f"{min(self.winfo_screenwidth() - 80, 1360)}x{min(self.winfo_screenheight() - 120, 820)}")
        self.photos, self.on_apply = photos, on_apply
        conflicts = [p for p in photos if p.status == CONFLICT]
        others = [p for p in photos if p.status not in (CONFLICT, OUT_OF_SCOPE)]
        excluded = [p for p in photos if p.status == OUT_OF_SCOPE]

        self.bold = tkfont.nametofont("TkDefaultFont").copy()  # Tk drops a font Python doesn't hold
        self.bold.configure(weight="bold")
        ttk.Label(self, text=f"{seen} total photos, {len(conflicts) + len(others)} to be synced.", font=self.bold).pack(
            anchor="w", padx=12, pady=(10, 0))
        ttk.Label(self, text="").pack()

        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", padx=12, pady=12)  # first, so the lists can't push it off
        ttk.Label(bottom, text="Click in Final to set the rating, and click it again to skip the photo. "
                               "☠ is rejected, ✖ is unrated.").pack(side="left")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        self.ok_button = ttk.Button(bottom, text="OK", command=self.ok)
        self.ok_button.pack(side="right", padx=(0, 6))

        self.excluded = OutOfScope(self, excluded)
        self.excluded.pack(fill="x", padx=12)
        ttk.Label(self, text="").pack()
        self.conflicts = Section(self, "Conflicts", conflicts, options, CONFLICT_BUTTONS, start="highest")
        self.conflicts.pack(fill="both", expand=bool(conflicts), padx=12)
        ttk.Label(self, text="").pack()
        self.others = Section(self, "No conflicts", others, options)
        self.others.pack(fill="both", expand=bool(others), padx=12)


    def row_name(self, photo):
        root = self.master.folder.get()
        inside = os.path.relpath(photo.folder, root)
        return photo.stem if inside == "." else os.path.join(inside, photo.stem)

    def ok(self):
        self.ok_button.state(["disabled"])
        self.on_apply(self)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.db_path = fsdb.default_db()
        self.data_dir = fsdb.default_data_dir()
        self.events = queue.Queue()
        self.folder = tk.StringVar(value=load_last_folder(self.data_dir))
        self.subfolders = tk.BooleanVar(value=True)
        defaults = Options()
        self.places = {name: tk.BooleanVar(value=getattr(defaults, name)) for name, _ in PLACE_OPTIONS}

        menu = tk.Menu(self)
        guide = tk.Menu(menu, tearoff=False)
        guide.add_command(label="Manual", command=lambda: help.show_manual(self))
        guide.add_separator()
        guide.add_command(label="About", command=lambda: help.show_about(self))
        menu.add_cascade(label="Help", menu=guide)
        self.configure(menu=menu)
        self.help_menu = guide

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Photo folder").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(2, 4))
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(frame, text="Include subfolders", variable=self.subfolders).pack(anchor="w")

        places = ttk.LabelFrame(frame, text="Also write ratings to (FastStone is always updated)", padding=8)
        places.pack(fill="x", pady=10)
        for name, text in PLACE_OPTIONS:
            ttk.Checkbutton(places, text=text, variable=self.places[name]).pack(anchor="w")

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(4, 0))
        self.status = ttk.Label(bottom, text="FastStone needs to be closed while syncing.")
        self.status.pack(side="left")
        self.sync_button = ttk.Button(bottom, text="Sync", command=self.sync)
        self.sync_button.pack(side="right")
        self.update_idletasks()  # fit the contents, whatever the display scaling
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.pumping = self.after(100, self.pump)

    def options(self):
        return Options(**{name: var.get() for name, var in self.places.items()})

    def browse(self):
        chosen = filedialog.askdirectory(parent=self, title="Choose a photo folder",
                                         initialdir=self.folder.get() or None)
        if chosen:
            self.folder.set(os.path.normpath(chosen))
            save_last_folder(self.data_dir, self.folder.get())

    def pump(self):
        while True:
            try:
                event, *args = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                event(*args)
            except Exception as e:
                self.failed(f"Something went wrong: {e!r}")
        self.pumping = self.after(100, self.pump)

    def destroy(self):
        self.after_cancel(self.pumping)
        super().destroy()

    def background(self, work, *args):
        """Run work off the window's thread; a FastStoneError ends up in a message box."""
        def run():
            try:
                work(*args)
            except fsdb.FastStoneError as e:
                self.events.put((self.failed, str(e)))
            except Exception as e:
                self.events.put((self.failed, f"Something went wrong: {e!r}"))
        threading.Thread(target=run, daemon=True).start()

    def failed(self, message):
        self.busy(False)
        messagebox.showerror(APP_NAME, message, parent=self)

    def busy(self, on, text="FastStone needs to be closed while syncing."):
        self.sync_button.state(["disabled"] if on else ["!disabled"])
        self.status.configure(text=text)

    def locate_db(self):
        if os.path.isfile(self.db_path):
            return True
        messagebox.showinfo(APP_NAME, "FastStone's database wasn't found in the usual place. A portable "
                                           "FastStone keeps FSIV.db beside FSViewer.exe. Please point to it.",
                            parent=self)
        chosen = filedialog.askopenfilename(parent=self, filetypes=[("FastStone database", "FSIV.db")])
        if chosen:
            self.db_path = os.path.normpath(chosen)
        return bool(chosen)

    def sync(self):
        folder = self.folder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror(APP_NAME, "Choose a photo folder first.", parent=self)
            return
        if not self.locate_db():
            return
        save_last_folder(self.data_dir, os.path.abspath(folder))
        self.busy(True, "Starting…")
        # Tk variables belong to the window's thread, so the workers get plain copies.
        self.job = {"subfolders": self.subfolders.get(), "options": self.options()}
        self.background(self.check_and_scan, os.path.abspath(folder))

    def check_and_scan(self, folder):
        if fsdb.faststone_running():
            self.events.put((self.warn_running,))
        else:
            self.run_scan(folder)

    def warn_running(self):
        self.busy(False)
        messagebox.showwarning(APP_NAME, "FastStone is open, and it locks its database. "
                                              "Close FastStone, then click Sync again.", parent=self)

    def run_scan(self, folder):
        state = load_state(self.data_dir)
        with fsdb.Database(self.db_path) as db:
            photos, seen = scan(db, folder, self.job["subfolders"], state,
                                lambda n: self.events.put((self.busy, True, f"Reading ratings: {n} photos…")),
                                self.job["options"].embed_jpeg)
            photos = with_work(db, photos, self.job["options"])
        self.events.put((self.scanned, photos, seen, state))

    def scanned(self, photos, seen, state):
        self.busy(False)
        if not photos:
            messagebox.showinfo(APP_NAME, f"{seen} total photos. All in sync already.", parent=self)
            return
        SyncWindow(self, photos, seen, lambda w: self.start_apply(w, state), self.job["options"])

    def start_apply(self, window, state):
        self.busy(True, "Writing ratings…")
        self.background(self.run_apply, window, state)

    def run_apply(self, window, state):
        result = apply(self.db_path, window.photos, self.job["options"], self.data_dir, state)
        self.events.put((self.applied, window, result))

    def applied(self, window, result):
        self.busy(False)
        window.destroy()
        lines = [f"{result.photos} {'photo' if result.photos == 1 else 'photos'} synced in total."]
        if result.errors:
            lines += ["", f"{len(result.errors)} could not be written and will come back next time:"]
            lines += result.errors[:10]
        (messagebox.showwarning if result.errors else messagebox.showinfo)(APP_NAME, "\n".join(lines), parent=self)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    App().mainloop()
