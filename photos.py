#!/usr/bin/env python3
"""
gphoto-metadata-toolkit: Google Takeout Photos Metadata Recovery Toolkit.

This module is the main CLI entry point for recovering and restoring metadata
from Google Takeout archives. It provides three main processing phases:

1. **Scan Phase**: Index all JSON metadata and media files into SQLite
2. **Metadata Phase**: Restore date and GPS metadata to image/video files
3. **Live Photo Phase**: Reassemble Live Photo pairs from separated files

Features:
  - Recover lost EXIF/metadata using Google's JSON records
  - Restore original timestamps and geolocation data
  - Reassemble Live Photo pairs (image + video) from Google Takeout
  - Dry-run mode for safe testing before applying changes
  - Per-year filtering and batch processing
  - Comprehensive database queries for data inspection

File Structure:
  photos.py           - This file (main entry point)
  photo_db.py         - SQLite database schema and utilities
  photo_scan.py       - Phase 1: scan JSON + media files to database
  photo_metadata.py   - Phase 2: write metadata back to files
  photo_livephoto.py  - Phase 3: assemble Live Photo pairs

Requirements:
  - Python 3.10+
  - ExifTool: brew install exiftool (macOS) or apt install exiftool (Linux)

Usage Examples:
  # Phase 1: Index all files from Google Takeout
  python photos.py scan /path/to/takeout

  # Phase 2: Restore metadata (test first with --dry-run)
  python photos.py metadata --dry-run
  python photos.py metadata
  python photos.py metadata --year 2022

  # Phase 3: Assemble Live Photos
  python photos.py livephoto --dry-run
  python photos.py livephoto

  # Run all phases in sequence
  python photos.py all /path/to/takeout

  # View statistics
  python photos.py stats

  # Query database
  python photos.py query --unmatched
  python photos.py query --year 2023
  python photos.py query --live-photos

Takeout Directory Structure:
  /path/to/takeout/
  ├── 2022/
  │   ├── IMG_0001.HEIC
  │   ├── IMG_0001.HEIC.supplemental-metadata.json
  │   ├── IMG_0001.mp4
  │   ├── 3aae020f...mov
  │   └── 3aae020f...mov.supplemental-metadata.json
  ├── 2023/
  │   └── ...
  ├── 2024/
  │   └── ...
  └── 2025/
      └── ...

Configuration:
  Database path and default timezone can be configured via environment variables:
  - DATABASE_PATH: Path to SQLite database (default: google_photos.db)
  - DEFAULT_TIMEZONE: Default timezone (default: Asia/Bangkok)

  Create a .env file in the working directory to set these values:
    DATABASE_PATH=~/my_photos/database.db
    DEFAULT_TIMEZONE=US/Eastern
"""

import argparse
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration defaults from environment or hardcoded values
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "google_photos.db")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Bangkok")

from photo_db import init_db, get_connection, print_stats


def cmd_scan(args):
    """Phase 1: Index JSON metadata and media files into SQLite database."""
    from photo_scan import run_phase1
    run_phase1(args.root_dir, args.db)


def cmd_metadata(args):
    """Phase 2: Restore date and GPS metadata to image and video files."""
    from zoneinfo import ZoneInfo
    from photo_metadata import run_phase2
    tz = ZoneInfo(args.timezone)
    run_phase2(
        args.db, args.dry_run, args.year, args.limit, tz,
        verbose=args.verbose, log_file=args.log_file,
    )


def cmd_livephoto(args):
    """Phase 3: Assemble Live Photo pairs from separated image and video files."""
    from photo_livephoto import run_phase3
    run_phase3(
        args.db, args.dry_run, args.year,
        verbose=args.verbose, log_file=args.log_file,
    )


def cmd_all(args):
    """Execute all three processing phases in sequence."""
    from zoneinfo import ZoneInfo
    print("[*] Running all phases in sequence...\n")

    # Phase 1: Scan
    from photo_scan import run_phase1
    run_phase1(args.root_dir, args.db)

    # Phase 2: Metadata
    from photo_metadata import run_phase2
    tz = ZoneInfo(args.timezone)
    run_phase2(
        args.db, args.dry_run, args.year, args.limit, tz,
        verbose=args.verbose,
        log_file=(args.log_file + ".metadata") if args.log_file else None,
    )

    # Phase 3: Live Photo
    from photo_livephoto import run_phase3
    run_phase3(
        args.db, args.dry_run, args.year,
        verbose=args.verbose,
        log_file=(args.log_file + ".livephoto") if args.log_file else None,
    )

    print("\n[X] All phases completed!")


def cmd_stats(args):
    """Display statistics and summary from the database."""
    if not os.path.exists(args.db):
        print(f"[!] Database not found: {args.db}")
        print("    Please run the 'scan' phase first")
        return
    conn = get_connection(args.db)
    print_stats(conn)
    conn.close()


def cmd_query(args):
    """Query and inspect data in the database with various filters."""
    if not os.path.exists(args.db):
        print(f"[!] Database not found: {args.db}")
        return

    conn = get_connection(args.db)
    cursor = conn.cursor()

    if args.unmatched:
        print("\n[*] Media files not matched with JSON metadata:")
        cursor.execute("""
            SELECT filename, year_folder, extension, file_size
            FROM media_files
            WHERE json_metadata_id IS NULL
            ORDER BY year_folder, filename
        """)
        rows = cursor.fetchall()
        for row in rows:
            size_mb = (row["file_size"] or 0) / 1024 / 1024
            print(f"  {row['year_folder'] or '??'} | {row['filename']} ({size_mb:.1f} MB)")
        print(f"\n  Total: {len(rows):,} files")

    elif args.unmatched_json:
        print("\n[*] JSON metadata with no matching media file:")
        cursor.execute("""
            SELECT j.title, j.year_folder
            FROM json_metadata j
            LEFT JOIN media_files m ON m.json_metadata_id = j.id
            WHERE m.id IS NULL
            ORDER BY j.year_folder, j.title
        """)
        rows = cursor.fetchall()
        for row in rows:
            print(f"  {row['year_folder'] or '??'} | {row['title']}")
        print(f"\n  Total: {len(rows):,} files")

    elif args.live_photos:
        print("\n[/] Live Photo pairs found:")
        cursor.execute("""
            SELECT img.filename AS img, vid.filename AS vid,
                   lp.content_identifier AS cid, lp.assembled,
                   img.year_folder
            FROM live_photos lp
            JOIN media_files img ON lp.image_media_id = img.id
            JOIN media_files vid ON lp.video_media_id = vid.id
            ORDER BY img.year_folder, img.filename
        """)
        rows = cursor.fetchall()
        for row in rows:
            status = "[X]" if row["assembled"] else "[ ]"
            cid = row["cid"][:8] + "..." if row["cid"] else "—"
            print(f"  {status} {row['year_folder'] or '??'} | {row['img']} + {row['vid']} [{cid}]")
        print(f"\n  Total: {len(rows):,} pairs")

    else:
        # Summary by year
        print("\n[*] Summary by year:")
        cursor.execute("""
            SELECT year_folder,
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_image = 1 THEN 1 ELSE 0 END) AS images,
                   SUM(CASE WHEN is_video = 1 THEN 1 ELSE 0 END) AS videos,
                   SUM(CASE WHEN json_metadata_id IS NOT NULL THEN 1 ELSE 0 END) AS matched,
                   SUM(CASE WHEN metadata_written = 1 THEN 1 ELSE 0 END) AS written
            FROM media_files
            GROUP BY year_folder
            ORDER BY year_folder
        """)
        rows = cursor.fetchall()
        print(f"  {'Year':<8} {'Total':>8} {'Images':>8} {'Videos':>8} {'Matched':>8} {'Written':>10}")
        print(f"  {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 10}")
        for row in rows:
            yr = row["year_folder"] or "Unknown"
            print(f"  {yr:<8} {row['total']:>8,} {row['images']:>8,} {row['videos']:>8,} {row['matched']:>8,} {row['written']:>10,}")

    conn.close()


def main():
    """Parse command-line arguments and execute the appropriate command."""
    parser = argparse.ArgumentParser(
        description="gphoto-metadata-toolkit: Recover and restore metadata from Google Takeout archives",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python photos.py scan ~/Takeout              # Index files from Takeout
  python photos.py metadata --dry-run          # Test metadata restoration
  python photos.py metadata                    # Restore metadata
  python photos.py livephoto                   # Assemble Live Photos
  python photos.py all ~/Takeout --dry-run     # Run all phases (test mode)
  python photos.py stats                       # View statistics
  python photos.py query --unmatched           # Find unmatched files
  python photos.py query --live-photos         # List Live Photo pairs
        """
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Scan subcommand
    p_scan = subparsers.add_parser(
        "scan",
        help="Phase 1: Index JSON metadata and media files into database"
    )
    p_scan.add_argument(
        "root_dir",
        help="Root directory containing year folders from Google Takeout"
    )
    p_scan.set_defaults(func=cmd_scan)

    # Metadata subcommand
    p_meta = subparsers.add_parser(
        "metadata",
        help="Phase 2: Restore date and GPS metadata to media files"
    )
    p_meta.add_argument(
        "--dry-run",
        action="store_true",
        help="Show commands without modifying files"
    )
    p_meta.add_argument(
        "--year",
        help="Filter by year (e.g., 2022)"
    )
    p_meta.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to process"
    )
    p_meta.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone for date conversion (default: {DEFAULT_TIMEZONE})"
    )
    p_meta.add_argument(
        "--verbose",
        action="store_true",
        help="Print every ExifTool command in dry-run (noisy). "
             "Without this flag dry-run shows only a progress bar."
    )
    p_meta.add_argument(
        "--log-file",
        help="Write every ExifTool command to the given file (one per line). "
             "Recommended for reviewing a --dry-run after the fact."
    )
    p_meta.set_defaults(func=cmd_metadata)

    # Live Photo subcommand
    p_live = subparsers.add_parser(
        "livephoto",
        help="Phase 3: Assemble Live Photo pairs"
    )
    p_live.add_argument(
        "--dry-run",
        action="store_true",
        help="Show commands without modifying files"
    )
    p_live.add_argument(
        "--year",
        help="Filter by year (e.g., 2022)"
    )
    p_live.add_argument(
        "--verbose",
        action="store_true",
        help="Print every ExifTool command in dry-run (noisy)."
    )
    p_live.add_argument(
        "--log-file",
        help="Write every ExifTool command to the given file (one per line)."
    )
    p_live.set_defaults(func=cmd_livephoto)

    # All subcommand
    p_all = subparsers.add_parser(
        "all",
        help="Execute all phases (scan, metadata, livephoto) in sequence"
    )
    p_all.add_argument(
        "root_dir",
        help="Root directory containing year folders from Google Takeout"
    )
    p_all.add_argument(
        "--dry-run",
        action="store_true",
        help="Show commands without modifying files"
    )
    p_all.add_argument(
        "--year",
        help="Filter by year (e.g., 2022)"
    )
    p_all.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to process (Phase 2)"
    )
    p_all.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone for date conversion (default: {DEFAULT_TIMEZONE})"
    )
    p_all.add_argument(
        "--verbose",
        action="store_true",
        help="Print every ExifTool command in dry-run (noisy)."
    )
    p_all.add_argument(
        "--log-file",
        help="Write every ExifTool command to a file. Phases append .metadata / "
             ".livephoto suffixes to the given path."
    )
    p_all.set_defaults(func=cmd_all)

    # Stats subcommand
    p_stats = subparsers.add_parser(
        "stats",
        help="Display database statistics and summary"
    )
    p_stats.set_defaults(func=cmd_stats)

    # Query subcommand
    p_query = subparsers.add_parser(
        "query",
        help="Query and inspect data in the database"
    )
    p_query.add_argument(
        "--unmatched",
        action="store_true",
        help="Show media files not matched with JSON metadata"
    )
    p_query.add_argument(
        "--unmatched-json",
        action="store_true",
        help="Show JSON metadata with no matching media file"
    )
    p_query.add_argument(
        "--live-photos",
        action="store_true",
        help="Show detected Live Photo pairs"
    )
    p_query.set_defaults(func=cmd_query)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
