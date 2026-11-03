# Rating Sync for FastStone

FastStone Image Viewer is fast for a first pass through a shoot, but its star ratings stay inside FastStone. Rate a JPEG and its RAW stays unrated, so the rating filter only shows half of each pair. Lightroom, Bridge and other apps never see the ratings at all.

Rating Sync fixes both:

- **Pairs match.** Rate any one file. After a sync, every file with that name in the folder (`IMG_0001.CR3`, `IMG_0001.JPG`, `IMG_0001.PSD`) carries the same rating, even if FastStone is hiding the RAWs.
- **Other apps can see your ratings.** It writes them to `.xmp` sidecars and inside JPEG, PSD and PNG files.
- **You decide when ratings disagree.** If a photo has 5★ in FastStone and 1★ inside the JPEG, it shows both and you tick the one to keep.

For Windows, with FastStone Image Viewer 8.5. Not affiliated with or endorsed by FastStone Soft.

## How to use it

1. Download `RatingSync.exe` and double-click it. There's nothing to install.
2. Pick your photo folder; the app remembers the last one. Folders inside it are included unless you untick **Include subfolders**.
3. Choose where else ratings should go, and click **Sync**. FastStone has to be closed first; the app reminds you if it's open.
4. A window lists the photos that need syncing, one row per photo, with every format in its own column. Each cell shows that place's rating before and after, like `1→3`. An empty before means it had no rating, and an empty after means the rating is being cleared. Conflicts are at the top. Each list has its own buttons for setting everything at once. To set one photo, click in its **Final** column: a star, ✖ for unrated or ☠ for rejected. Click it again to skip the photo. Conflicts you don't set are skipped until next time.
5. Click **OK** to write the ratings, or **Cancel** to change nothing.

Sync only runs when you click it. **Help → Manual** in the app explains every part of the sync window, and **Help → About** shows the licence and credits.

## What it changes, and what it doesn't

- **RAW files are never modified.** Their ratings go in sidecars.
- **Inside JPEG, PSD and PNG files,** only the rating changes. The picture data stays the same, and ExifTool does the writing.
- **Sidecars** keep your Lightroom or Camera Raw edits; only the rating is touched.
- **Only the folder you pick** is read or changed, plus its subfolders if you tick that.
- **FastStone's database is backed up** before every write, and the last 5 copies are kept in `%LOCALAPPDATA%\RatingSync\backups`. Logs of old values are in `logs` beside them.
- **Reject** marks from Bridge (rating −1) appear as a normal choice. FastStone shows those photos as unrated.
- **Tags** in FastStone are left alone.

The tool reads and edits FastStone's database, whose format isn't documented. It stops without changing anything if a FastStone update changes the database version.

## Known limits

- A photo with more than one RAW file, such as `.CR3` and `.DNG` with the same name, isn't synced. It's listed as out of scope, and none of its files are touched.
- Canon, Fujifilm and other RAW files are only tested through their file names and sidecars; only Fujifilm RAF has been run on real photos so far.
- DNG files get a sidecar, but Lightroom reads a DNG's rating from inside the file. Writing inside DNGs is planned.
- Windows may warn about an unknown publisher until the app is signed.

## For developers

Python 3.10 or later, standard library only. The app needs ExifTool for JPEGs; `packaging\build.ps1` fetches a pinned copy into `vendor\` and builds `dist\RatingSync.exe` with PyInstaller.

```powershell
python -m ratingsync                                       # the window
python -m ratingsync scan <folder> [--subfolders]          # report only
python -m ratingsync sync <folder> [--conflicts faststone] [--dry-run]
python -m unittest discover -s tests
```

MIT licence. Bundled software is listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
