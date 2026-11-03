"""Synthetic FastStone databases and photo files for the tests."""
import os
import shutil
import sqlite3
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from ratingsync import apply as applying, fsdb
from ratingsync.apply import load_state
from ratingsync.scan import scan

SCHEMA = """
CREATE TABLE FolderList (folderID INTEGER PRIMARY KEY AUTOINCREMENT, folderName TEXT, lastAccess INTEGER);
CREATE TABLE FileList (ID INTEGER PRIMARY KEY AUTOINCREMENT, folderID INTEGER, itemNO INTEGER, isFolder INTEGER,
  fileName TEXT, isTag INTEGER, ratings INTEGER, XP_rating INTEGER, fileTime INTEGER, exifTime INTEGER,
  fileSize INTEGER, width INTEGER, height INTEGER, pages INTEGER, imageType INTEGER, imgSize1 Integer,
  imgSize2 Integer, img1 BLOB, img2 BLOB);
CREATE TABLE DBSettings (version INTEGER, ThumbnailSizeS INTEGER, ThumbnailSizeB INTEGER);
"""
IMAGE_DATA = bytes(range(1, 255)) * 40  # stands in for compressed image data, after the SOS marker


def jpeg_bytes(xmp=None, exif=None):
    """A small JPEG with optional XMP text and EXIF Rating, parseable by ExifTool."""
    parts = [b"\xff\xd8"]
    if exif is not None:
        rating, percent = exif
        entries = [(0x4746, rating), (0x4749, percent)]
        ifd = struct.pack("<H", len(entries))
        for tag, value in entries:
            ifd += struct.pack("<HHIHH", tag, 3, 1, value, 0)
        tiff = b"II*\x00" + struct.pack("<I", 8) + ifd + struct.pack("<I", 0)
        body = b"Exif\x00\x00" + tiff
        parts.append(b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body)
    if xmp is not None:
        body = b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode()
        parts.append(b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body)
    # Minimal frame so ExifTool accepts the file; the decoder tables don't matter here.
    parts.append(b"\xff\xdb\x00\x43\x00" + bytes([16] * 64))
    parts.append(b"\xff\xc0\x00\x0b\x08\x00\x10\x00\x10\x01\x01\x11\x00")
    parts.append(b"\xff\xc4\x00\x14\x00\x01" + bytes(15) + b"\x00")
    parts.append(b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + IMAGE_DATA + b"\xff\xd9")
    return b"".join(parts)


def png_bytes(xmp=None):
    """A small valid PNG, 1x1 grey, optionally with XMP."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    parts = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))]
    if xmp is not None:
        parts.append(chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp.encode()))
    parts += [chunk(b"IDAT", zlib.compress(b"\x00\x80")), chunk(b"IEND", b"")]
    return b"".join(parts)


def psd_bytes(xmp=None):
    """A PSD's header and image resources, optionally with XMP after another resource."""
    resources = b"8BIM\x03\xed\x00\x00" + struct.pack(">I", 3) + b"abc\x00"
    if xmp is not None:
        data = xmp.encode()
        resources += b"8BIM\x04\x24\x00\x00" + struct.pack(">I", len(data)) + data + b"\x00" * (len(data) % 2)
    header = b"8BPS\x00\x01" + bytes(6) + struct.pack(">HIIHH", 1, 1, 1, 8, 1)
    return header + struct.pack(">I", 0) + struct.pack(">I", len(resources)) + resources + bytes(20)


def xmp_packet(rating):
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="{rating}"/>'
            '</rdf:RDF></x:xmpmeta>')


def image_part(path):
    """Everything from the start-of-scan marker on: the picture itself."""
    with open(path, "rb") as f:
        data = f.read()
    return data[data.rindex(b"\xff\xda"):]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ratingsync-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "Shoot")
        os.makedirs(self.root)
        self.data = os.path.join(self.tmp, "data")
        self.db = os.path.join(self.tmp, "FSIV.db")
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO DBSettings VALUES (?, 4, -1)", (fsdb.SUPPORTED_VERSION,))
        con.commit()
        con.close()
        patcher = mock.patch.object(fsdb, "faststone_running", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def photo(self, name, folder=None, content=None):
        folder = folder or self.root
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(content if content is not None else (jpeg_bytes() if name.upper().endswith("JPG") else b"RAWDATA"))
        return path

    def sidecar(self, name, rating=None, text=None, folder=None):
        return self.photo(name, folder, (text if text is not None else xmp_packet(rating)).encode())

    def entry(self, name, rating, folder=None, matching=True):
        """A FastStone entry; with matching=False, its date and size are wrong for the file."""
        folder = folder or self.root
        with fsdb.Database(self.db) as db:
            fid = db._folder_id_for_write(folder)
            st = os.stat(os.path.join(folder, name))
            when, size = (fsdb.dos_time(st.st_mtime_ns), st.st_size) if matching else (0, 0)
            db.con.execute("INSERT INTO FileList (folderID, itemNO, isFolder, fileName, isTag, ratings, XP_rating, "
                           "fileTime, exifTime, fileSize, width, height, pages, imageType, imgSize1, imgSize2, img1) "
                           "VALUES (?, 1, 0, ?, 0, ?, 0, ?, 0, ?, 0, 0, 0, 0, 0, 0, x'00')",
                           (fid, name.upper(), rating, when, size))

    def row(self, name, folder=None):
        with fsdb.Database(self.db) as db:
            return db.entries(folder or self.root).get(name.upper())

    def rows(self):
        con = sqlite3.connect(self.db)
        found = con.execute("SELECT f.folderName, l.fileName, l.ratings FROM FileList l "
                            "JOIN FolderList f USING(folderID) ORDER BY 1, 2").fetchall()
        con.close()
        return found

    def scan(self, root=None, subfolders=False, inside=True):
        with fsdb.Database(self.db) as db:
            return scan(db, root or self.root, subfolders, load_state(self.data), inside=inside)[0]

    def one(self, stem):
        return next(p for p in self.scan(subfolders=True) if p.stem.upper() == stem.upper())

    def run_sync(self, photos=None, options=None, root=None, subfolders=False):
        options = options or applying.Options()
        photos = self.scan(root, subfolders, options.embed_jpeg) if photos is None else photos
        return applying.apply(self.db, photos, options, self.data)
