# Photo Rating Sync

Keep star ratings in sync across a photo's RAW, JPEG, PSD and PNG files in FastStone Image Viewer.

FastStone Image Viewer is fast for a first pass through a shoot, but its star ratings stay inside FastStone. Rate a JPEG and its RAW stays unrated, so the rating filter only shows half of each pair. Lightroom, Bridge and other apps never see the ratings at all.

Photo Rating Sync fixes both:

- **Pairs match.** Rate any one file. After a sync, every file with that name in the folder (`IMG_0001.CR3`, `IMG_0001.JPG`, `IMG_0001.PSD`) carries the same rating, even if FastStone is hiding the RAWs.
- **Other apps can see your ratings.** It writes them to `.xmp` sidecars and inside JPEG, PSD and PNG files.
- **You decide when ratings disagree.** If a photo has 5★ in FastStone and 1★ inside the JPEG, it shows both and you tick the one to keep.

For Windows, with FastStone Image Viewer 8.5. Not affiliated with or endorsed by FastStone Soft.

## How to use it

1. Download `PhotoRatingSync.exe` from the [latest release](https://github.com/mixercorp/Photo-Rating-Sync/releases/latest) and double-click it. There's nothing to install. Version 0.1.0 was called `RatingSync.exe`.
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

Python 3.10 or later, standard library only. The app needs ExifTool for JPEGs; `packaging\build.ps1` fetches a pinned copy into `vendor\` and builds `dist\PhotoRatingSync.exe` with PyInstaller. Releases are built from this repository by the GitHub Actions workflow in `.github\workflows\release.yml`, using the same script.

```powershell
python -m ratingsync                                       # the window
python -m ratingsync scan <folder> [--subfolders]          # report only
python -m ratingsync sync <folder> [--conflicts faststone] [--dry-run]
python -m unittest discover -s tests
```

## Privacy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. It reads and writes only the photo folder you pick, FastStone's database, and its own settings, backups and logs in `%LOCALAPPDATA%\RatingSync`.

## Code signing policy

Photo Rating Sync is published by Mixer Inc. Releases are signed as Mixer Inc. with a certificate from [Microsoft Azure Artifact Signing](https://azure.microsoft.com/products/artifact-signing).

- Committers and reviewers: [Tiger](https://github.com/tigershao), for Mixer Inc.
- Release approvers: [Tiger](https://github.com/tigershao), for Mixer Inc.

Signed releases are built from this repository by GitHub Actions and signed in the same run; nothing built elsewhere is signed. Version 0.1.0 and any release made before signing is set up aren't signed.

## Licence

MIT licence. Bundled software is listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
