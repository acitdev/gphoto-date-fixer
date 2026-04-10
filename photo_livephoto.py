"""
photo_livephoto.py - Phase 3: Assemble Live Photos

Apple Live Photo assembly requires:
  1. Image file (HEIC/JPG) + video file (MP4/MOV) with the same stem name
  2. Both files must have matching "Content Identifier" (UUID) embedded in metadata
     - Image: MakerNotes:ContentIdentifier (Apple MakerNotes)
     - Video: QuickTime:ContentIdentifier

Usage:
  python photo_livephoto.py
  python photo_livephoto.py --db photos.db --dry-run
  python photo_livephoto.py --year 2022

Requirements:
  - Phase 1 must be run first (to create live_photos table)
  - Phase 2 must be run first (to write date/GPS metadata)
  - ExifTool must be installed: brew install exiftool
  - After running Phase 3, import into Apple Photos app by selecting both
    image and video files together; Photos app will automatically merge as Live Photo
"""

import os
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from dotenv import load_dotenv

from photo_db import get_connection, print_stats

load_dotenv()
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "google_photos.db")


_LOOKS_LIKE_RE = re.compile(r"looks more like a (\w+)")

_FORMAT_TO_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "HEIC": ".heic",
    "TIFF": ".tiff",
    "GIF": ".gif",
    "MOV": ".mov",
    "MP4": ".mp4",
}


def _rename_to_real_format(filepath: str, stderr_output: str) -> Optional[str]:
    """Rename file if ExifTool detects extension mismatch with actual format.

    Args:
        filepath: Path to the file to rename
        stderr_output: stderr output from ExifTool

    Returns:
        New file path if renamed, None otherwise
    """
    match = _LOOKS_LIKE_RE.search(stderr_output)
    if not match:
        return None

    real_format = match.group(1).upper()
    new_ext = _FORMAT_TO_EXT.get(real_format)
    if not new_ext:
        return None

    base, old_ext = os.path.splitext(filepath)
    if old_ext.lower() == new_ext:
        return None

    new_path = base + new_ext
    if os.path.exists(new_path):
        new_path = base + "_renamed" + new_ext

    try:
        os.rename(filepath, new_path)
        return new_path
    except OSError:
        return None


def progress(text: str):
    """Display progress with line overwrite."""
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    sys.stdout.write(f"\r{text[:terminal_width]:<{terminal_width}}")
    sys.stdout.flush()


def progress_error(text: str):
    """Display error on a new line."""
    sys.stdout.write(f"\n{text}\n")
    sys.stdout.flush()


def check_exiftool() -> bool:
    """Check if ExifTool is installed.

    Returns:
        True if ExifTool is available, False otherwise
    """
    if shutil.which("exiftool") is None:
        print("[!] ExifTool not found!")
        print("   Install with: brew install exiftool")
        return False
    return True


def read_existing_content_id(filepath: str) -> Optional[str]:
    """Read existing ContentIdentifier from file if present.

    Some files may already have ContentIdentifier embedded from capture.
    If found, use the existing value instead of creating a new one.

    Args:
        filepath: Path to the file to read

    Returns:
        ContentIdentifier UUID if found, None otherwise
    """
    try:
        result = subprocess.run(
            ["exiftool", "-ContentIdentifier", "-s3", filepath],
            capture_output=True,
            text=True,
            timeout=10,
        )
        content_id = result.stdout.strip()
        if content_id:
            return content_id
    except Exception:
        pass
    return None


def write_content_identifier(
    image_path: str,
    video_path: str,
    content_id: str,
    dry_run: bool = False,
    verbose: bool = False,
    log_fh=None,
) -> tuple:
    """Embed ContentIdentifier into both image and video files.

    Args:
        image_path: Path to image file (HEIC/JPG)
        video_path: Path to video file (MP4/MOV)
        content_id: UUID to embed
        dry_run: Display commands without modifying files
        verbose: If True, print the full ExifTool commands on their own lines
            during dry-run. Without this flag dry-run stays silent and only
            updates the shared progress bar.
        log_fh: Optional open file handle. If provided, every ExifTool command
            is appended to it (one per line).

    Returns:
        Tuple of (success: bool, new_image_path: Optional[str], new_video_path: Optional[str])
    """
    img_ext = os.path.splitext(image_path)[1].lower()

    if img_ext in (".heic", ".heif"):
        img_cmd = [
            "exiftool",
            "-overwrite_original",
            f"-MakerNotes:ContentIdentifier={content_id}",
            image_path,
        ]
    else:
        img_cmd = [
            "exiftool",
            "-overwrite_original",
            f"-MakerNotes:ContentIdentifier={content_id}",
            f"-ImageUniqueID={content_id}",
            image_path,
        ]

    vid_cmd = [
        "exiftool",
        "-overwrite_original",
        f"-QuickTime:ContentIdentifier={content_id}",
        video_path,
    ]

    if dry_run:
        img_str = " ".join(img_cmd)
        vid_str = " ".join(vid_cmd)
        if verbose:
            progress_error(f"    [DRY RUN] {img_str}")
            progress_error(f"    [DRY RUN] {vid_str}")
        if log_fh:
            log_fh.write(img_str + "\n")
            log_fh.write(vid_str + "\n")
        return True, None, None

    new_image_path = None
    new_video_path = None

    for fp in (image_path, video_path):
        tmp = fp + "_exiftool_tmp"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        result = subprocess.run(img_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "looks more like a" in stderr:
                new_image_path = _rename_to_real_format(image_path, stderr)
                if new_image_path:
                    img_ext2 = os.path.splitext(new_image_path)[1].lower()
                    if img_ext2 in (".heic", ".heif"):
                        img_cmd2 = [
                            "exiftool", "-overwrite_original",
                            f"-MakerNotes:ContentIdentifier={content_id}",
                            new_image_path,
                        ]
                    else:
                        img_cmd2 = [
                            "exiftool", "-overwrite_original",
                            f"-MakerNotes:ContentIdentifier={content_id}",
                            f"-ImageUniqueID={content_id}",
                            new_image_path,
                        ]
                    result2 = subprocess.run(img_cmd2, capture_output=True, text=True, timeout=30)
                    if result2.returncode != 0:
                        progress_error(f"  [!]  ExifTool error (image retry): {result2.stderr.strip()}")
                        return False, new_image_path, new_video_path
                else:
                    progress_error(f"  [!]  ExifTool error (image): {stderr}")
                    return False, new_image_path, new_video_path
            else:
                progress_error(f"  [!]  ExifTool error (image): {stderr}")
                return False, new_image_path, new_video_path
    except Exception as e:
        progress_error(f"  [!]  Error (image): {e}")
        return False, new_image_path, new_video_path

    try:
        result = subprocess.run(vid_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "looks more like a" in stderr:
                new_video_path = _rename_to_real_format(video_path, stderr)
                if new_video_path:
                    vid_cmd2 = [
                        "exiftool", "-overwrite_original",
                        f"-QuickTime:ContentIdentifier={content_id}",
                        new_video_path,
                    ]
                    result2 = subprocess.run(vid_cmd2, capture_output=True, text=True, timeout=30)
                    if result2.returncode != 0:
                        progress_error(f"  [!]  ExifTool error (video retry): {result2.stderr.strip()}")
                        return False, new_image_path, new_video_path
                else:
                    progress_error(f"  [!]  ExifTool error (video): {stderr}")
                    return False, new_image_path, new_video_path
            else:
                progress_error(f"  [!]  ExifTool error (video): {stderr}")
                return False, new_image_path, new_video_path
    except Exception as e:
        progress_error(f"  [!]  Error (video): {e}")
        return False, new_image_path, new_video_path

    return True, new_image_path, new_video_path


def run_phase3(
    db_path: str = None,
    dry_run: bool = False,
    year_filter: Optional[str] = None,
    verbose: bool = False,
    log_file: Optional[str] = None,
):
    """Assemble all Live Photo pairs by embedding matching ContentIdentifiers.

    Args:
        db_path: Path to SQLite database (uses DEFAULT_DB_PATH if not provided)
        dry_run: Display commands without modifying files
        year_filter: Filter to specific year (e.g., '2022')
        verbose: If True, print every ExifTool command on its own line during
            dry-run. Without this flag, dry-run shows only a progress bar.
        log_file: Optional path to a file that will receive every ExifTool
            command (one per line).
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    print("=" * 50)
    print("[*] Phase 3: Assemble Live Photos")
    print(f"   Database: {db_path}")
    if dry_run:
        print("   [*] DRY RUN MODE - Files will not be modified")
    if verbose:
        print("   [*] Verbose: ON")
    if log_file:
        print(f"   [*] Log file: {log_file}")
    if year_filter:
        print(f"   [*] Year filter: {year_filter}")
    print("=" * 50)

    if not dry_run and not check_exiftool():
        return

    conn = get_connection(db_path)
    cursor = conn.cursor()

    query = """
        SELECT lp.id AS lp_id,
               img.filepath AS image_path, img.filename AS image_name,
               vid.filepath AS video_path, vid.filename AS video_name,
               img.year_folder
        FROM live_photos lp
        JOIN media_files img ON lp.image_media_id = img.id
        JOIN media_files vid ON lp.video_media_id = vid.id
        WHERE lp.assembled = 0
    """
    params = []

    if year_filter:
        query += " AND img.year_folder = ?"
        params.append(year_filter)

    query += " ORDER BY img.year_folder, img.filename"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total = len(rows)
    if total == 0:
        print("\n[X] No Live Photo pairs to assemble")
        return

    print(f"\n[/] Assembling Live Photo pairs: {total:,}")

    success = 0
    failed = 0

    log_fh = None
    if log_file:
        try:
            log_fh = open(log_file, "w", encoding="utf-8")
            log_fh.write(
                f"# gphoto-date-fixer livephoto log ({'dry-run' if dry_run else 'live'})\n"
            )
        except OSError as e:
            print(f"[!] Cannot open log file {log_file}: {e}")
            log_fh = None

    for i, row in enumerate(rows, 1):
        image_path = row["image_path"]
        video_path = row["video_path"]
        image_name = row["image_name"]
        video_name = row["video_name"]

        if not os.path.isfile(image_path):
            progress_error(f"  [!]  [{i}/{total}] Image not found: {image_path}")
            failed += 1
            continue
        if not os.path.isfile(video_path):
            progress_error(f"  [!]  [{i}/{total}] Video not found: {video_path}")
            failed += 1
            continue

        pct = i * 100 // total
        progress(f"  [{i:,}/{total:,}] {pct}% {image_name} + {video_name}")

        existing_img_id = read_existing_content_id(image_path) if not dry_run else None
        existing_vid_id = read_existing_content_id(video_path) if not dry_run else None

        if existing_img_id and existing_vid_id and existing_img_id == existing_vid_id:
            content_id = existing_img_id
        elif existing_img_id:
            content_id = existing_img_id
        elif existing_vid_id:
            content_id = existing_vid_id
        else:
            content_id = str(uuid.uuid4()).upper()

        ok, new_img_path, new_vid_path = write_content_identifier(
            image_path, video_path, content_id, dry_run,
            verbose=verbose, log_fh=log_fh,
        )

        if ok:
            success += 1
            if not dry_run:
                cursor.execute("""
                    UPDATE live_photos
                    SET content_identifier = ?, assembled = 1
                    WHERE id = ?
                """, (content_id, row["lp_id"]))

                if new_img_path:
                    new_fn = os.path.basename(new_img_path)
                    new_stem, new_ext = os.path.splitext(new_fn)
                    cursor.execute("""
                        UPDATE media_files
                        SET filepath = ?, filename = ?, stem = ?, extension = ?
                        WHERE filepath = ?
                    """, (new_img_path, new_fn, new_stem,
                          new_ext.lower(), image_path))
                if new_vid_path:
                    new_fn = os.path.basename(new_vid_path)
                    new_stem, new_ext = os.path.splitext(new_fn)
                    cursor.execute("""
                        UPDATE media_files
                        SET filepath = ?, filename = ?, stem = ?, extension = ?
                        WHERE filepath = ?
                    """, (new_vid_path, new_fn, new_stem,
                          new_ext.lower(), video_path))

                if i % 20 == 0:
                    conn.commit()
        else:
            failed += 1

    conn.commit()

    progress(f"  [{total:,}/{total:,}] 100% Complete")
    print()

    if log_fh:
        try:
            log_fh.close()
            print(f"[*] ExifTool commands logged to: {log_file}")
        except OSError:
            pass

    print(f"\n[X] Success: {success:,} pairs")
    if failed > 0:
        print(f"[!]  Failed: {failed:,} pairs")

    print("\n[*] Next steps:")
    print("   1. Open Apple Photos app")
    print("   2. Drag image + video files (matching pairs) into Photos together")
    print("   3. Photos app will automatically merge them as Live Photo")
    print("   (Both files have matching ContentIdentifier)")

    print_stats(conn)
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 3: Assemble Live Photos by embedding ContentIdentifier"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display commands without modifying files"
    )
    parser.add_argument(
        "--year",
        help="Filter to specific year (e.g., 2022)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every ExifTool command to the terminal during dry-run."
    )
    parser.add_argument(
        "--log-file",
        help="Write every ExifTool command to the given file (one per line)."
    )
    args = parser.parse_args()

    run_phase3(
        args.db, args.dry_run, args.year,
        verbose=args.verbose, log_file=args.log_file,
    )
