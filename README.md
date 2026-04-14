# PNG Background Remover v1.0

A lightweight Windows desktop tool for batch-removing backgrounds from PNG and JPEG character images — built for use with [SillyTavern](https://github.com/SillyTavern/SillyTavern) character cards.

## Features

- Removes **black**, **white**, and **checkered** backgrounds automatically
- Handles **non-contiguous background regions** (gaps between arm and body, etc.)
- Converts **JPEG → PNG** with transparency in one pass
- **Live progress** — UI never freezes, images preview as they're processed
- **Full-size viewer** with ◀ ▶ arrow navigation to compare results without leaving the app
- **Advanced controls** — tune tolerances and blur per run without restarting
- Output saved to a `background removal/` subfolder next to your source files

## Usage

### Run from source
```
pip install -r requirements.txt
python main.py
```
Or double-click **`Run.bat`**.

### Build standalone EXE
Double-click **`build.bat`** — requires Python 3.10+ in PATH.
Output: `dist/BGRemover.exe` (~40–50 MB, no install needed).

## Controls

| Control | Description |
|---|---|
| **Folder…** | Select a folder — processes all PNG/JPEG files inside |
| **File(s)…** | Pick one or more individual images |
| **Start Processing** | Begin batch removal with current settings |
| **Open Output Folder** | Opens the `background removal/` subfolder in Explorer |
| **⚙ Advanced ▾** | Expand tolerance and blur sliders |

### Advanced Settings

| Setting | Default | Notes |
|---|---|---|
| Dark BG tolerance | 10 | Lower = safer for dark hair/shadows |
| Light BG tolerance | 20 | Higher catches more white fringe |
| Edge blur radius | 2.0 | Gaussian blur on alpha edges only |

### Viewer
Click any thumbnail to open the full-size viewer. Navigate with **◀ ▶ buttons** or **← → arrow keys**. Press **Escape** or click the background to close.

## How It Works

1. **Classification** — samples ~32 evenly-spaced border pixels and majority-votes for black/white/checkered. Works correctly even when the subject fills the bottom edge (portraits, headshots).
2. **Flood fill** (Pass 1) — seeds from every border pixel on a downscaled copy, removes the connected outer background.
3. **Global color-match** (Pass 2) — removes enclosed background regions (e.g. white gaps inside the silhouette) unreachable by flood fill.
4. **Alpha edge smoothing** — light Gaussian blur on the alpha channel only for clean, anti-aliased edges.

## Requirements

- Python 3.10+
- Pillow
- customtkinter
- darkdetect
- pyinstaller *(build only)*
