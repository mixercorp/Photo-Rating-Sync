"""The Help menu's Manual and About windows."""
import os
import re
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from . import APP_NAME, __version__

# Lines starting "# " are headings, "## " subheadings, "- " bullets; **text** is bold.
MANUAL = """\
# Photo Rating Sync

FastStone Image Viewer is quick for a first pass through a shoot, but its star ratings stay inside FastStone. Rate a JPEG and its RAW stays unrated, so the rating filter only shows half of each pair, and Lightroom, Bridge and other apps never see the ratings at all.

Photo Rating Sync gives every file of a photo the same rating, and writes it where other apps can read it.

## What counts as one photo

Files in the same folder with the same name, apart from the extension, are one photo: for example **IMG_0001.CR3**, **IMG_0001.jpg** and **IMG_0001.psd**. JPEG, PSD, PNG, HEIF and RAW files count.

# Syncing a folder

- **Close FastStone first.** It locks its database while it's open. If it's open, the app tells you and stops.
- **Pick the photo folder.** The app remembers the last one. Folders inside it are included unless you untick **Include subfolders**. Nothing outside the folder is ever read or changed.
- **Choose where else ratings go.** FastStone is always updated. The options are explained below.
- **Click Sync.** The sync window lists the photos that need work. Photos whose ratings already match everywhere aren't listed.
- **Check the list and click OK** to write the ratings, or **Cancel** to change nothing.

## Where ratings can go

- **.xmp beside RAW files**: the small file Lightroom, Bridge, Capture One and DxO read. It's created if missing, and an existing one keeps your edits; only the rating changes.
- **[IMG].[RAW].xmp**: the name darktable and digiKam use, such as IMG.raf.xmp. Off unless you tick it.
- **Inside the file: .jpg, .jpeg, .psd and .png**: these files hold their own rating. Untick this to leave them alone; their FastStone entries still sync.

RAW files themselves are never changed.

# The sync window

The bold line at the top says how many photos were looked at and how many need syncing. Below it are three sections.

- **Out of Scope** lists photos with more than one RAW file, such as IMG.CR3 and IMG.DNG. None of their files are touched.
- **Conflicts** lists photos whose files hold different ratings. They open with **Use highest** switched on.
- **No conflicts** lists photos with one rating that's missing somewhere.

## Reading a row

Each row is one photo. Every column is one place a rating can live, and the columns are always the same.

- **FastStone**: FastStone's own record for each file type.
- **.jpg and .jpeg**: the two ratings inside a JPEG. **XMP** is the one most photo apps read; **EXIF** is the one Windows Explorer shows.
- **.psd, .png**: the rating inside those files.
- **RAW**: a rating the camera wrote inside the RAW. It's shown but never changed.
- **.xmp**: the .xmp file beside the photo.

A cell such as **1→3** means the rating is 1 now and will be 3 after OK. An empty number before the arrow means there's no rating there yet; an empty number after it means the rating will be cleared. A grey cell won't change. An empty cell means the photo has no such file.

## Choosing the final rating

The **Final** column shows the rating every place will get. Click a star to set it, **✖** for unrated or **☠** for rejected. Click the same one again to skip the photo, so nothing about it changes.

The buttons above each list set every photo in it at once, and stay highlighted while their rule applies:

- **Use FastStone**: FastStone's rating (No conflicts only).
- **Use highest**: the highest rating found.
- **Use newest**: the rating that changed most recently. FastStone doesn't date its ratings, so a FastStone rating counts as made when you last opened that folder.
- **Skip all**: leave every photo in the list as it is.

# Good to know

- **Rejected** (☠) is Bridge's Reject mark. FastStone shows a rejected photo as unrated, and Windows can't store a Reject, so JPEGs keep it in their XMP rating only.
- **Moving photos in FastStone** keeps their ratings, but FastStone leaves the .xmp files behind. A later sync in the new folder makes new ones.
- **Undo**: FastStone's database is backed up before every write, and the last 5 copies are kept in %LOCALAPPDATA%\\RatingSync\\backups. To roll back, close FastStone and copy the newest one over %LOCALAPPDATA%\\FastStone\\FSIV\\FSIV.db, naming it FSIV.db. A log of every old value is in the logs folder beside it.
- **FastStone updates**: the tool stops without changing anything if FastStone's database format changes.
"""

CREDITS = [
    ("ExifTool", "by Phil Harvey, exiftool.org. Writes ratings inside JPEG, PSD and PNG files. "
                 "Free software under the same terms as Perl (Artistic License or GPL)."),
    ("Python and Tcl/Tk", "run the app, under the Python Software Foundation License and the Tcl/Tk license."),
    ("FastStone Image Viewer", "by FastStone Soft. This tool works with its database, and is not affiliated "
                               "with or endorsed by FastStone Soft."),
]


def licence_text():
    """The MIT licence shipped beside the code, or bundled into the .exe."""
    roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    for root in roots:
        path = os.path.join(root, "LICENSE")
        if root and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    return "MIT License. The full text is in the LICENSE file at github.com/tigershao/Photo-Rating-Sync."


class Document(tk.Text):
    """Read-only text that understands the small markup used in MANUAL."""

    def __init__(self, parent, **kw):
        super().__init__(parent, wrap="word", relief="flat", padx=16, pady=12, cursor="arrow", **kw)
        base = tkfont.nametofont("TkDefaultFont")
        size = base.actual("size")
        self.fonts = {name: base.copy() for name in ("h1", "h2", "b")}
        self.fonts["h1"].configure(weight="bold", size=size + 5)
        self.fonts["h2"].configure(weight="bold", size=size + 2)
        self.fonts["b"].configure(weight="bold")
        self.configure(font=base, spacing1=2, spacing3=4)
        self.tag_configure("h1", font=self.fonts["h1"], spacing1=14, spacing3=6)
        self.tag_configure("h2", font=self.fonts["h2"], spacing1=10, spacing3=4)
        self.tag_configure("b", font=self.fonts["b"])
        indent = base.measure("    ")
        self.tag_configure("bullet", lmargin1=indent // 2, lmargin2=indent, spacing1=1)

    def show(self, markup):
        self.configure(state="normal")
        self.delete("1.0", "end")
        for line in markup.splitlines():
            if line.startswith("# "):
                self.insert("end", line[2:] + "\n", "h1")
            elif line.startswith("## "):
                self.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("- "):
                self.insert("end", "•  ", "bullet")
                self.inline(line[2:], "bullet")
            elif line:
                self.inline(line, ())
        self.configure(state="disabled")

    def inline(self, text, tags):
        tags = (tags,) if isinstance(tags, str) else tuple(tags)
        for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            self.insert("end", part, tags + (("b",) if i % 2 else ()))
        self.insert("end", "\n", tags)


def _window(master, title, size):
    window = tk.Toplevel(master)
    window.title(title)
    window.geometry(size)
    window.transient(master)
    return window


def _scrolled(window):
    """A document with a scrollbar, above a Close button. Packed edges first, so the
    expanding text can't squeeze them out."""
    ttk.Button(window, text="Close", command=window.destroy).pack(side="bottom", anchor="e", padx=12, pady=10)
    frame = ttk.Frame(window)
    frame.pack(fill="both", expand=True)
    doc = Document(frame, background="#ffffff")
    down = ttk.Scrollbar(frame, orient="vertical", command=doc.yview)
    doc.configure(yscrollcommand=down.set)
    down.pack(side="right", fill="y")
    doc.pack(side="left", fill="both", expand=True)
    doc.scrollbar = down
    return doc


def show_manual(master):
    window = _window(master, f"{APP_NAME}: Manual", "760x700")
    window.doc = _scrolled(window)
    window.doc.show(MANUAL)
    return window


def about_markup():
    lines = [f"# {APP_NAME} {__version__}", "", "For FastStone Image Viewer.", "",
             "**Free to use, share and change**, under the MIT License below.", "",
             "Made by Tiger.", "", "## Credits"]
    lines += [f"- **{name}** {text}" for name, text in CREDITS]
    lines += ["", "## Licence", ""]
    return "\n".join(lines)


def show_about(master):
    window = _window(master, f"About {APP_NAME}", "640x600")
    window.doc = _scrolled(window)
    window.doc.show(about_markup())
    window.doc.configure(state="normal")
    window.doc.insert("end", licence_text())
    window.doc.configure(state="disabled")
    return window
