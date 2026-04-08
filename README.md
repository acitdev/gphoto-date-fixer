# gPhoto Date Fixer

A Python toolkit to restore lost metadata (timestamps, GPS coordinates) to Google Takeout photos and reassemble Apple Live Photos from separated files.

## The Problem

When you export your photos from Google Photos using [Google Takeout](https://takeout.google.com/), the exported files lose their EXIF metadata. Timestamps, GPS coordinates, and other information are stripped from the actual image/video files and stored in separate `.json` sidecar files. Additionally, Apple Live Photos (photo + video pairs) are split into individual files, breaking the Live Photo association.

This toolkit solves both problems:

1. **Metadata Restoration** — Reads the JSON sidecar files, matches them to the correct media files, and writes the metadata (dates, GPS, altitude) back into the actual image/video files using ExifTool.
2. **Live Photo Assembly** — Detects image+video pairs that form Live Photos and embeds matching `ContentIdentifier` UUIDs so Apple Photos can reassemble them.

## Features

- **Multi-pass matching algorithm** — 5-pass strategy to handle exact matches, truncated filenames, renamed extensions, `(N)` duplicates, and edited file variants
- **Live Photo detection** — 3-pass detection using exact stem, prefix matching, and JSON title correlation
- **Batch ExifTool processing** — Uses ExifTool's `-stay_open` mode for 10–50x faster processing compared to individual subprocess calls
- **Auto-repair** — Detects and fixes mislabeled file extensions; repairs corrupted JPEG files via Pillow
- **Unicode normalization** — Handles macOS NFC/NFD filesystem differences
- **Dry-run mode** — Preview all changes before modifying any files
- **SQLite tracking** — Persistent database tracks progress across runs; safe to interrupt and resume
- **Configurable timezone** — Timestamps converted to your local timezone (default: Asia/Bangkok)

## Requirements

- **Python** 3.10+
- **ExifTool** — Install via your package manager:
  ```bash
  # macOS
  brew install exiftool

  # Ubuntu/Debian
  sudo apt install libimage-exiftool-perl

  # Windows (via Chocolatey)
  choco install exiftool
  ```

## Installation

```bash
git clone https://github.com/acitdev/gphoto-date-fixer.git
cd gphoto-date-fixer
pip install -r requirements.txt
```

### Configuration

Copy the example environment file and adjust as needed:

```bash
cp .env.example .env
```

Available settings in `.env`:

| Variable           | Default            | Description                          |
| ------------------ | ------------------ | ------------------------------------ |
| `DATABASE_PATH`    | `google_photos.db` | Path to the SQLite tracking database |
| `DEFAULT_TIMEZONE` | `Asia/Bangkok`     | Timezone for timestamp conversion    |

## Usage

### Quick Start — Run All Phases

```bash
python photos.py all /path/to/takeout --dry-run   # Preview first
python photos.py all /path/to/takeout              # Run for real
```

### Step-by-Step Execution

#### Phase 1: Scan & Match

Scans the Takeout directory, parses JSON metadata files, indexes media files, matches them together, and detects Live Photo pairs.

```bash
python photos.py scan /path/to/takeout
```

#### Phase 2: Write Metadata

Writes timestamps and GPS coordinates back into image/video files using ExifTool. Also updates OS-level file dates.

```bash
python photos.py metadata --dry-run       # Preview changes
python photos.py metadata                 # Write metadata
python photos.py metadata --year 2023     # Process specific year only
python photos.py metadata --limit 100     # Process first 100 files
python photos.py metadata --timezone US/Eastern  # Override timezone
```

#### Phase 3: Assemble Live Photos

Embeds `ContentIdentifier` UUIDs into image+video pairs so Apple Photos recognizes them as Live Photos.

```bash
python photos.py livephoto --dry-run      # Preview changes
python photos.py livephoto                # Write identifiers
python photos.py livephoto --year 2024    # Process specific year
```

### Utilities

```bash
python photos.py stats                    # Show database statistics
python photos.py query --unmatched        # List unmatched media files
python photos.py query --unmatched-json   # List unmatched JSON files
python photos.py query --live-photos      # List detected Live Photo pairs
```

## Expected Takeout Structure

The tool expects your Google Takeout export to be organized by year:

```
/path/to/takeout/
├── Photos from 2022/
│   ├── IMG_0001.HEIC
│   ├── IMG_0001.HEIC.supplemental-metadata.json
│   ├── IMG_0001.mp4
│   └── ...
├── Photos from 2023/
│   └── ...
├── Photos from 2024/
│   └── ...
└── Photos from 2025/
    └── ...
```

## Project Structure

```
gphoto-date-fixer/
├── photos.py             # CLI entry point
├── photo_db.py           # SQLite database schema and utilities
├── photo_scan.py         # Phase 1: Scan, parse, and match files
├── photo_metadata.py     # Phase 2: Write EXIF metadata via ExifTool
├── photo_livephoto.py    # Phase 3: Assemble Live Photos
├── requirements.txt      # Python dependencies
├── .env.example          # Configuration template
└── README.md
```

## How the Matching Works

The toolkit uses a sophisticated multi-pass approach because Google Takeout has several quirks:

**Metadata Matching (5 passes):**

1. **Exact match** — JSON title matches media filename exactly within the same year
2. **Stem match** — Same name but different extension (e.g., `HEIC` → `jpg` conversion)
3. **Live Photo sharing** — Videos inherit metadata from their paired image's JSON
4. **Duplicate & edit handling** — Matches `(N)` numbered duplicates and `-edited` variants
5. **Cleanup pass** — Catches remaining orphan Live Photo videos after pass 4

**Live Photo Detection (3 passes):**

1. **Exact stem** — Image and video share the same filename stem
2. **Prefix match** — One filename is a truncated version of the other
3. **JSON title correlation** — Uses original titles from JSON to link truncated pairs

## Supported Formats

**Images:** JPEG, HEIC/HEIF, PNG, GIF, WebP, TIFF, BMP, RAW, CR2, NEF, ARW, DNG, RW2, ORF, SR2

**Videos:** MP4, MOV, AVI, MKV, 3GP, WMV, M4V, MPG/MPEG, MTS

## Importing Live Photos into Apple Photos

After running Phase 3:

1. Open **Apple Photos**
2. Select both the image and its paired video file
3. Drag them into Photos simultaneously
4. Photos will automatically merge them into a Live Photo (because both files share the same `ContentIdentifier`)

## License

This project is licensed under the [MIT License](LICENSE).
