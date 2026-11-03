"""Writes the Windows version details PyInstaller stamps into the .exe.

SignPath requires the product name and version to be the same on every build, so both
come from here and from ratingsync/__init__.py, never typed by hand.
"""
import os
import re
import sys

PRODUCT = "Photo Rating Sync"
EXE = "PhotoRatingSync.exe"
AUTHOR = "Tiger"


def version():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "ratingsync", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', f.read()).groups()


def render():
    major, minor, patch = version()
    dotted = f"{major}.{minor}.{patch}"
    numbers = f"({major}, {minor}, {patch}, 0)"
    fields = {
        "CompanyName": AUTHOR,
        "FileDescription": f"{PRODUCT} for FastStone Image Viewer",
        "FileVersion": dotted,
        "InternalName": "PhotoRatingSync",
        "LegalCopyright": f"Copyright (c) 2026 {AUTHOR}. MIT License.",
        "OriginalFilename": EXE,
        "ProductName": PRODUCT,
        "ProductVersion": dotted,
    }
    strings = ",\n".join(f"          StringStruct('{k}', '{v}')" for k, v in fields.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
{strings}
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


if __name__ == "__main__":
    os.makedirs(os.path.dirname(os.path.abspath(sys.argv[1])), exist_ok=True)
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write(render())
