# AlbumCollage

A local Windows desktop app for collecting high-resolution album art and exporting it as one large grid collage PNG.

## Quick start

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** during setup.
2. Double-click **`run.bat`** — it creates a virtual environment, installs dependencies, and launches the app.

To produce a standalone `.exe`, double-click **`build.bat`**. The result lands at `dist\AlbumCollage.exe` and runs on machines without Python installed.

## Using it

**Adding albums.** Type `Artist - Album` (e.g. `Radiohead - In Rainbows`) and press Enter. By default the app grabs the best match instantly. Tick **Pick cover manually** to see every candidate in a grid and choose the one you want — useful for reissues, live albums, and anything with multiple pressings.

The `Artist - Album` format gives far better matches than a bare album name, but a bare name works too.

**Sources.** Four providers, all toggled in Settings. Every enabled source is searched and results are ranked together by how well artist and title match what you typed. The header shows which ones are currently active.

| Source | Typical resolution | Key | Notes |
| --- | --- | --- | --- |
| iTunes | 1400–3000 px | no | Fast, excellent coverage of mainstream releases. |
| Cover Art Archive | 500–4000 px | no | Community scans via MusicBrainz. Best for obscure, indie and vinyl-only releases. Rate-limited to 1 request/second, so it's slower. |
| Deezer | up to 1000 px | no | Good catalogue coverage, including plenty iTunes misses. |
| Spotify | 640 px max | yes | Excellent matching, but the 640 px ceiling is Spotify's, not the app's — small for a large collage, so it works best as a fallback. |

A note on Deezer: its CDN resizes covers on demand and will happily upscale, so asking for more than 1000 px returns an image that is larger but no more detailed. The app caps it at 1000 px rather than pretend otherwise.

### Spotify setup

Spotify is greyed out in Settings until you give it credentials. They're free:

1. Sign in at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) with any Spotify account.
2. *Create app* — any name and description; the redirect URI can be anything (`http://localhost`) since this app never uses it.
3. Copy the **Client ID** and **Client secret** into the Spotify row in Settings.
4. Press *Test connection* to confirm, then Save.

Credentials are stored in plain text in `settings.json`, the same as any local desktop app config. The app uses the client-credentials flow, which only reads public catalogue data — it never touches your account, playlists, or listening history.

**Importing your own art.** *Import image…* in the toolbar adds local files (PNG, JPG, WEBP, TIFF) for anything the sources don't have.

**Ordering.** Drag covers around the grid to reorder. The order is saved automatically and is the order used in the collage. The *Sort* menu offers artist, album, year, and resolution.

**Exporting.** Press *Create collage* (or Ctrl+E):

- **Columns** — auto makes the grid as square as possible, or set a number.
- **Cover size** — pixels per cover in the output. 1000 px × a 10×10 grid = a 10,000 px PNG.
- **Gap / outer margin / background** — optional spacing and colour between covers.
- **Output limit** — downscale the final PNG to a pixel cap if the full-resolution version is impractically large.

The live preview updates when you press *Refresh preview*. Covers smaller than the chosen cell size are upscaled; non-square scans are centre-cropped.

## Settings

*Settings* on the right of the toolbar (or Ctrl+,) opens the settings pane.

**Appearance** — switch between **Dark**, **Light**, and **Match system**. The theme previews live as you pick it and reverts if you cancel, so you can judge it by eye. Dialogs that are already open keep their old colours until reopened.

**Sources** — tick the providers to search, see what resolution each one gives you, and enter any API keys. A source that needs a key it doesn't have is greyed out with a note on what to enter. This section also holds the *Add the best match automatically* preference, which mirrors the *Pick cover manually* toggle in the header.

**Storage** — choose where albums are saved. Press *Change…* to pick any folder: an external drive, a synced folder, anywhere you have space. The pane shows how many files you currently have and how much room they take.

- **Move existing albums** (ticked by default) relocates your collection to the new folder. Files are copied first and the originals are only deleted once every copy succeeded, so an interrupted move can't lose covers.
- Unticked, the app starts a fresh library in the new folder and leaves your existing one untouched — handy for keeping separate collections.
- *Use default* returns to `%APPDATA%\AlbumCollage`.

A storage change takes effect immediately, whether or not you press Save. If the folder later becomes unreachable — an unplugged drive, an offline network share — the app warns you at startup and falls back to the default location rather than failing.

## Where your data lives

`settings.json` always lives in `%APPDATA%\AlbumCollage`, so the app can always find its own configuration. Your albums live in the storage folder, which defaults to that same directory:

```
library.json     album list and order
covers/          full-resolution downloads
thumbs/          320 px thumbnails for the UI grid
```

*Open data folder* in the toolbar takes you to the current storage folder. Back it up to preserve your collection; deleting it resets the library.

## Project layout

```
main.py                    entry point
albumcollage/
  config.py                paths, settings, storage relocation
  theme.py                 dark / light / system palettes
  sources.py               provider registry: iTunes, Cover Art Archive, Deezer, Spotify
  library.py               album records, cover cache, ordering
  collage.py               Pillow grid renderer
  workers.py               background search/download/export tasks
  ui_main.py               main window
  ui_dialogs.py            cover picker and collage export dialogs
  ui_settings.py           settings pane
requirements.txt
run.bat                    run from source
build.bat                  produce dist/AlbumCollage.exe
```

## Notes and limits

- All network work happens on background threads, so the UI stays responsive while covers download.
- Very large collages (a 20×20 grid at 1000 px is 400 MP) need a lot of RAM — roughly 3 bytes per pixel while rendering. Use the output limit if you hit memory pressure.
- Album art is copyrighted material. This tool is for personal use; check the terms of the source before republishing anything you export.
- The first launch of `AlbumCollage.exe` is slow (a few seconds) because a one-file PyInstaller build unpacks itself to a temp folder. Some antivirus tools also flag unsigned one-file builds — if that's a problem, drop `--onefile` from `build.bat` for a faster-starting folder build.
