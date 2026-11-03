"""Access to FastStone's rating database (FSIV.db), an undocumented SQLite file.

Each fact it relies on was checked against real databases, and is noted where it's used.
"""
import ctypes
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections import namedtuple

SUPPORTED_VERSION = 10
KEEP_BACKUPS = 5
PROCESS = "FSViewer.exe"
# Image type codes seen in real databases; other types borrow from the database itself.
IMAGE_TYPES = {"JPG": 3, "JPEG": 3, "RAF": 24, "CR2": 22, "CR3": 37, "PSD": 19, "PNG": 8}

Entry = namedtuple("Entry", "id name rating file_time file_size")
Write = namedtuple("Write", "folder name rating")


class FastStoneError(Exception):
    """A problem to show the user as it stands."""


class FastStoneRunning(FastStoneError):
    def __init__(self):
        super().__init__("FastStone is open and locks its database. Close it and try again.")


def default_db():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "FastStone", "FSIV", "FSIV.db")


def default_data_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RatingSync")


def faststone_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {PROCESS}", "/FO", "CSV", "/NH"], capture_output=True,
            text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return PROCESS.lower() in out.lower()


def db_folder_name(path):
    # FastStone stores folder paths upper-cased with a trailing backslash.
    return os.path.abspath(path).rstrip("\\/").upper() + "\\"


def dos_time(mtime_ns):
    """A file's modified time as FastStone stores it: UTC, DOS format, rounded up to 2 s.

    FastStone discards an entry, and its rating, if this differs by even one unit. Windows'
    own conversion matched all 72 real entries checked, including odd-second files.
    """
    ticks = mtime_ns // 100 + 116444736000000000
    try:
        filetime = (ctypes.c_uint32 * 2)(ticks & 0xFFFFFFFF, ticks >> 32)
        date, clock = ctypes.c_uint16(), ctypes.c_uint16()
        if ctypes.windll.kernel32.FileTimeToDosDateTime(filetime, ctypes.byref(date), ctypes.byref(clock)):
            return (date.value << 16) | clock.value
    except AttributeError:  # not Windows
        pass
    t = time.gmtime(-(-(mtime_ns // 100) // 20000000) * 2)
    return (((t.tm_year - 1980) << 25) | (t.tm_mon << 21) | (t.tm_mday << 16)
            | (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2))


def is_stale(entry, path):
    """True if FastStone may drop this entry, and its rating, for not matching the file."""
    st = os.stat(path)
    return (entry.file_time, entry.file_size) != (dos_time(st.st_mtime_ns), st.st_size)


class Database:
    def __init__(self, path, read_only=False):
        if not os.path.isfile(path):
            raise FastStoneError(f"FastStone's database was not found at {path}.")
        self.path = path
        self._snapshot = None
        self.con = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self._check_version()
        except sqlite3.OperationalError as e:
            self.con.close()
            if "locked" not in str(e):
                raise FastStoneError(f"Could not read FastStone's database: {e}")
            if not read_only:
                raise FastStoneRunning()
            # FastStone holds an exclusive lock while open, even against reads.
            self._snapshot = tempfile.mkdtemp(prefix="ratingsync-")
            for suffix in ("", "-wal"):
                if os.path.isfile(path + suffix):
                    shutil.copy2(path + suffix, os.path.join(self._snapshot, "FSIV.db" + suffix))
            self.con = sqlite3.connect(os.path.join(self._snapshot, "FSIV.db"), isolation_level=None)
            self._check_version()
        except FastStoneError:
            self.close()
            raise

    def _check_version(self):
        version = self.con.execute("SELECT version FROM DBSettings").fetchone()
        if not version or version[0] != SUPPORTED_VERSION:
            raise FastStoneError(
                f"FastStone's database is version {version and version[0]}; this tool was only tested "
                f"on version {SUPPORTED_VERSION}. Nothing was changed. Look for an updated tool.")

    def close(self):
        self.con.close()
        if self._snapshot:
            shutil.rmtree(self._snapshot, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def folder_ids(self, folder):
        rows = self.con.execute("SELECT folderID FROM FolderList WHERE folderName = ? ORDER BY folderID",
                                (db_folder_name(folder),)).fetchall()
        return [r[0] for r in rows]

    def entries(self, folder):
        """Upper-cased file name -> Entry, for one folder."""
        found = {}
        for fid in self.folder_ids(folder):
            for row in self.con.execute("SELECT ID, fileName, ratings, fileTime, fileSize FROM FileList "
                                        "WHERE folderID = ? AND isFolder = 0", (fid,)):
                found[row[1].upper()] = Entry(*row)
        return found

    def folder_opened(self, folder):
        """When FastStone last opened a folder, as a Unix time, or None.

        It's the only clock FastStone keeps near a rating: ratings themselves have no time.
        Packed like fileTime, but in local time, not UTC.
        """
        v = max((self.con.execute("SELECT lastAccess FROM FolderList WHERE folderID = ?", (fid,)).fetchone()[0] or 0
                 for fid in self.folder_ids(folder)), default=0)
        if not v:
            return None
        return time.mktime(((v >> 25) + 1980, (v >> 21) & 15, (v >> 16) & 31, (v >> 11) & 31,
                            (v >> 5) & 63, (v & 31) * 2, 0, 0, -1))

    def image_type(self, ext):
        ext = ext.upper()
        if ext in IMAGE_TYPES:
            return IMAGE_TYPES[ext]
        row = self.con.execute("SELECT imageType FROM FileList WHERE fileName LIKE ? AND imageType > 0 "
                               "GROUP BY imageType ORDER BY COUNT(*) DESC LIMIT 1", ("%." + ext,)).fetchone()
        return row[0] if row else 0

    def _folder_id_for_write(self, folder):
        ids = self.folder_ids(folder)
        if ids:
            return ids[-1]
        access = self.con.execute("SELECT MAX(lastAccess) FROM FolderList").fetchone()[0] or 0
        return self.con.execute("INSERT INTO FolderList (folderName, lastAccess) VALUES (?, ?)",
                                (db_folder_name(folder), access)).lastrowid

    def write_ratings(self, writes):
        """Set ratings in one transaction, creating missing entries.

        Every entry written also gets the file's current date and size: FastStone drops an
        entry that doesn't match the file, and a rating would be lost with it.
        """
        known = {}
        self.con.execute("BEGIN IMMEDIATE")
        try:
            for w in writes:
                try:
                    st = os.stat(os.path.join(w.folder, w.name))
                except FileNotFoundError:  # removed since the scan
                    continue
                when, size = dos_time(st.st_mtime_ns), st.st_size
                image_type = self.image_type(w.name.rpartition(".")[2])
                if w.folder not in known:
                    known[w.folder] = self.entries(w.folder)
                entry = known[w.folder].get(w.name.upper())
                if entry:
                    self.con.execute("UPDATE FileList SET ratings = ?, fileTime = ?, fileSize = ?, "
                                     "imageType = CASE imageType WHEN 0 THEN ? ELSE imageType END WHERE ID = ?",
                                     (w.rating, when, size, image_type, entry.id))
                elif w.rating:
                    self.con.execute(
                        "INSERT INTO FileList (folderID, itemNO, isFolder, fileName, isTag, ratings, XP_rating, "
                        "fileTime, exifTime, fileSize, width, height, pages, imageType, imgSize1, imgSize2) "
                        "VALUES (?, 0, 0, ?, 0, ?, 0, ?, 0, ?, 0, 0, 0, ?, 0, 0)",
                        (self._folder_id_for_write(w.folder), w.name.upper(), w.rating, when, size, image_type))
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise


def backup(db_path, data_dir):
    """Copy the database aside, keeping the newest KEEP_BACKUPS. Returns the copy's path."""
    folder = os.path.join(data_dir, "backups")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"FSIV-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}.db")
    for suffix in ("", "-wal"):
        if os.path.isfile(db_path + suffix):
            shutil.copy2(db_path + suffix, target + suffix)
    copies = sorted(f for f in os.listdir(folder) if f.endswith(".db"))
    for old in copies[:-KEEP_BACKUPS]:
        for suffix in ("", "-wal"):
            try:
                os.remove(os.path.join(folder, old + suffix))
            except FileNotFoundError:
                pass
    return target
