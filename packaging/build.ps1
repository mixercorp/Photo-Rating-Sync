# Builds dist\RatingSync.exe: fetches the pinned ExifTool, then bundles it with PyInstaller.
# Run from the repository root, with PyInstaller installed in the active Python.
$ErrorActionPreference = "Stop"
$version = "13.60"
$sha256 = "548b1f4e0b713cd2a26824b34080c35db4e2d2edd166468eb38b1d55ada90c2e"  # exiftool.org/checksums.txt

if (-not (Test-Path vendor\exiftool\exiftool.exe)) {
    New-Item -ItemType Directory -Force vendor | Out-Null
    curl.exe -sL --fail -A curl -o vendor\exiftool.zip "https://sourceforge.net/projects/exiftool/files/exiftool-${version}_64.zip/download"
    if ((Get-FileHash vendor\exiftool.zip -Algorithm SHA256).Hash -ne $sha256) { throw "ExifTool checksum mismatch" }
    Expand-Archive vendor\exiftool.zip vendor\x -Force
    $folder = (Get-ChildItem vendor\x -Directory)[0].FullName
    New-Item -ItemType Directory -Force vendor\exiftool | Out-Null
    Move-Item "$folder\exiftool(-k).exe" vendor\exiftool\exiftool.exe
    Move-Item "$folder\exiftool_files" vendor\exiftool\exiftool_files
    Remove-Item vendor\x -Recurse
}

python -m PyInstaller --noconfirm --onefile --windowed --name RatingSync `
    --add-data "vendor\exiftool;exiftool" --add-data "THIRD-PARTY-NOTICES.md;." --add-data "LICENSE;." ratingsync\__main__.py
