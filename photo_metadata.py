"""Phase 2: Write metadata (dates and GPS) back to photos and videos.

Uses ExifTool to write:
  - Dates (AllDates / QuickTime dates)
  - GPS coordinates (latitude, longitude, altitude)
  - OS-level file modification/access times

Requires ExifTool to be installed:
  brew install exiftool (macOS)
  apt-get install exiftool (Ubuntu/Debian)

Usage:
  python photo_metadata.py
  python photo_metadata.py --db photos.db --dry-run
  python photo_metadata.py --year 2022
"""

import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from photo_db import get_connection, print_stats

load_dotenv()
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "google_photos.db")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Bangkok")


def progress(text: str):
    """Display progress text with overwrite (same line)."""
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    sys.stdout.write(f"\r{text[:terminal_width]:<{terminal_width}}")
    sys.stdout.flush()


def progress_error(text: str):
    """Display error message on a new line (no overwrite)."""
    sys.stdout.write(f"\n{text}\n")
    sys.stdout.flush()


def check_exiftool() -> bool:
    """Check if ExifTool is installed.

    Returns:
        bool: True if exiftool is available, False otherwise.
    """
    if shutil.which("exiftool") is None:
        print("[!] ExifTool not found!")
        print("    Install with: brew install exiftool")
        print("    Or download from: https://exiftool.org/")
        return False
    return True

_READY_PATTERN = re.compile(r"\{ready(\d*)\}")

_FORMAT_TO_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "HEIC": ".heic",
    "TIFF": ".tiff",
    "GIF": ".gif",
    "BMP": ".bmp",
    "WEBP": ".webp",
    "MP4": ".mp4",
    "MOV": ".mov",
}


class ExifToolBatch:
    """Manage ExifTool as a persistent process using -stay_open mode.

    Launches a single ExifTool instance and sends commands via stdin instead
    of spawning a new process for each file. This reduces overhead by 10-50x
    compared to subprocess-per-file.

    Example:
        with ExifToolBatch() as et:
            ok, stderr = et.execute(["-AllDates=2022:01:01 10:00:00", "photo.jpg"])
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._seq = 0

    def start(self):
        """Start the ExifTool process in -stay_open mode."""
        self._process = subprocess.Popen(
            ["exiftool", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def stop(self):
        """Stop the ExifTool process gracefully."""
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write("-stay_open\nFalse\n")
                self._process.stdin.flush()
                self._process.wait(timeout=10)
            except Exception:
                self._process.kill()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def execute(self, args: list[str]) -> tuple[bool, str]:
        """Send command to ExifTool and wait for result.

        Args:
            args: List of command arguments (without "exiftool" prefix).

        Returns:
            Tuple of (success, output) where output combines stdout+stderr.
        """
        if not self._process or self._process.poll() is not None:
            return False, "ExifTool process not running"

        self._seq += 1
        seq = self._seq

        for arg in args:
            self._process.stdin.write(arg + "\n")
        self._process.stdin.write(f"-execute{seq}\n")
        self._process.stdin.flush()

        output_lines = []
        while True:
            line = self._process.stdout.readline()
            if not line:
                break
            m = _READY_PATTERN.match(line.strip())
            if m:
                break
            output_lines.append(line.rstrip("\n"))

        output = "\n".join(output_lines)

        stderr_text = ""
        try:
            import select
            fd = self._process.stderr.fileno()
            while select.select([fd], [], [], 0)[0]:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                stderr_text += chunk.decode("utf-8", errors="replace")
        except Exception:
            pass

        success = "updated" in output or "unchanged" in output
        full_output = stderr_text.strip() if stderr_text.strip() else output
        return success, full_output


def build_exiftool_args(
    filepath: str,
    timestamp: int,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    is_video: bool = False,
    tz: Optional[ZoneInfo] = None,
) -> list[str]:
    """Build ExifTool command arguments.

    Args:
        filepath: Path to the file.
        timestamp: Unix timestamp from photoTakenTime.
        latitude: Latitude coordinate or None.
        longitude: Longitude coordinate or None.
        altitude: Altitude or None.
        is_video: True if file is a video.
        tz: Timezone for time conversion (None = UTC).

    Returns:
        List of ExifTool command arguments.
    """
    target_tz = tz or timezone.utc
    dt = datetime.fromtimestamp(timestamp, tz=target_tz)
    date_str = dt.strftime("%Y:%m:%d %H:%M:%S")
    offset_str = dt.strftime("%z")
    offset_fmt = f"{offset_str[:3]}:{offset_str[3:]}" if offset_str else "+00:00"

    args = [
        "-overwrite_original",
        "-ignoreMinorErrors",
        "-F",
    ]

    if is_video:
        args.extend([
            f"-QuickTime:CreateDate={date_str}",
            f"-QuickTime:ModifyDate={date_str}",
            f"-QuickTime:TrackCreateDate={date_str}",
            f"-QuickTime:TrackModifyDate={date_str}",
            f"-QuickTime:MediaCreateDate={date_str}",
            f"-QuickTime:MediaModifyDate={date_str}",
        ])
    else:
        args.extend([
            f"-AllDates={date_str}",
            f"-EXIF:OffsetTimeOriginal={offset_fmt}",
            f"-EXIF:OffsetTime={offset_fmt}",
            f"-EXIF:OffsetTimeDigitized={offset_fmt}",
        ])

    if latitude is not None and longitude is not None:
        lat_ref = "N" if latitude >= 0 else "S"
        lon_ref = "E" if longitude >= 0 else "W"
        args.extend([
            f"-GPSLatitude={abs(latitude)}",
            f"-GPSLatitudeRef={lat_ref}",
            f"-GPSLongitude={abs(longitude)}",
            f"-GPSLongitudeRef={lon_ref}",
        ])
        if altitude is not None:
            alt_ref = 0 if altitude >= 0 else 1
            args.extend([
                f"-GPSAltitude={abs(altitude)}",
                f"-GPSAltitudeRef={alt_ref}",
            ])

    args.append(filepath)
    return args


def _resolve_unicode_path(filepath: str) -> str:
    """Resolve Unicode normalization issues on macOS.

    macOS filesystems (APFS/HFS+) store names in NFD (decomposed) form,
    while paths from the database may be NFC (composed). This function
    finds the correct filesystem path.
    """
    if os.path.exists(filepath):
        return filepath
    nfd = unicodedata.normalize("NFD", filepath)
    if os.path.exists(nfd):
        return nfd
    nfc = unicodedata.normalize("NFC", filepath)
    if os.path.exists(nfc):
        return nfc
    return filepath


def update_file_dates(filepath: str, timestamp: int, tz: Optional[ZoneInfo] = None):
    """Update OS-level file dates (modification, access, and creation time).

    Args:
        filepath: Path to the file.
        timestamp: Unix timestamp to set.
        tz: Timezone for conversion (None = UTC).
    """
    filepath = _resolve_unicode_path(filepath)
    try:
        os.utime(filepath, (timestamp, timestamp))
    except OSError as e:
        print(f"  [!] Failed to update OS file date: {filepath} ({e})")
        return

    try:
        target_tz = tz or timezone.utc
        dt = datetime.fromtimestamp(timestamp, tz=target_tz)
        setfile_date = dt.strftime("%m/%d/%Y %H:%M:%S")

        if shutil.which("SetFile"):
            subprocess.run(
                ["SetFile", "-d", setfile_date, filepath],
                capture_output=True, timeout=10,
            )
        else:
            touch_date = dt.strftime("%Y%m%d%H%M.%S")
            subprocess.run(
                ["touch", "-t", touch_date, filepath],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


_LOOKS_LIKE_RE = re.compile(r"looks more like a (\w+)")


def find_renamed_file(filepath: str) -> Optional[str]:
    """Find a file that may have been renamed in a previous run.

    Args:
        filepath: Original file path to check.

    Returns:
        Path to the found file, or None if not found.
    """
    base = os.path.splitext(filepath)[0]
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"):
        candidate = base + "_renamed" + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def rename_to_real_format(filepath: str, output: str) -> Optional[str]:
    """Rename file extension to match actual file format.

    ExifTool detects when file extension doesn't match the actual format.
    This function renames the file to the correct extension.

    Args:
        filepath: Path to the file.
        output: ExifTool output containing format detection.

    Returns:
        New path if renamed, or None if rename failed or not needed.
    """
    match = _LOOKS_LIKE_RE.search(output)
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


def repair_image(filepath: str) -> bool:
    """Attempt to repair corrupted image by rewriting with Pillow.

    Args:
        filepath: Path to the image file.

    Returns:
        True if repair succeeded, False otherwise.
    """
    try:
        from PIL import Image
        img = Image.open(filepath)
        img.save(filepath, quality=95, subsampling=0)
        return True
    except Exception:
        return False


def write_metadata_for_file(
    et: ExifToolBatch,
    filepath: str,
    timestamp: int,
    latitude: Optional[float],
    longitude: Optional[float],
    altitude: Optional[float],
    is_video: bool,
    tz: Optional[ZoneInfo] = None,
) -> tuple[bool, Optional[str]]:
    """Write metadata to a single file using ExifToolBatch.

    Args:
        et: ExifToolBatch instance.
        filepath: Path to the file.
        timestamp: Unix timestamp to write.
        latitude: Latitude coordinate or None.
        longitude: Longitude coordinate or None.
        altitude: Altitude or None.
        is_video: True if file is a video.
        tz: Timezone for conversion (None = UTC).

    Returns:
        Tuple of (success, new_filepath) where new_filepath is the path
        if the file was renamed, or None otherwise.
    """
    tmp = filepath + "_exiftool_tmp"
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    args = build_exiftool_args(
        filepath, timestamp, latitude, longitude, altitude, is_video, tz
    )

    ok, output = et.execute(args)

    if not ok:
        if "looks more like a" in output:
            new_path = rename_to_real_format(filepath, output)
            if new_path:
                args2 = build_exiftool_args(
                    new_path, timestamp, latitude, longitude, altitude, is_video, tz
                )
                ok2, output2 = et.execute(args2)
                if ok2:
                    update_file_dates(new_path, timestamp, tz)
                    return True, new_path
                else:
                    progress_error(
                        f"  [!] ExifTool error (retry): {new_path}\n"
                        f"      {output2}"
                    )
                    return False, new_path

        if not is_video and repair_image(filepath):
            ok3, output3 = et.execute(args)
            if ok3:
                update_file_dates(filepath, timestamp, tz)
                return True, None

        progress_error(
            f"  [!] ExifTool error: {filepath}\n"
            f"      {output}"
        )
        return False, None

    update_file_dates(filepath, timestamp, tz)
    return True, None



def run_phase2(
    db_path: str = None,
    dry_run: bool = False,
    year_filter: Optional[str] = None,
    limit: Optional[int] = None,
    tz: Optional[ZoneInfo] = None,
    verbose: bool = False,
    log_file: Optional[str] = None,
):
    """Run Phase 2: Write metadata back to all matched media files.

    Args:
        db_path: Path to SQLite database. Defaults to DATABASE_PATH env var.
        dry_run: If True, show commands without modifying files.
        year_filter: Optional year folder to filter (e.g., "2022").
        limit: Optional limit on number of files to process.
        tz: Timezone for time conversion. Defaults to DEFAULT_TIMEZONE env var.
        verbose: If True, print every ExifTool command on its own line. In
            dry-run mode this restores the noisy full-log behavior; without
            this flag dry-run shows only the single-line progress bar.
        log_file: Optional path to a file that will receive every ExifTool
            command (one per line). Useful for reviewing a dry-run after the
            fact without cluttering the terminal.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    if tz is None:
        tz = ZoneInfo(DEFAULT_TIMEZONE)

    print("=" * 50)
    print("[*] Phase 2: Write metadata back to files")
    print(f"    Database: {db_path}")
    print(f"    Timezone: {tz or 'UTC'}")
    print(f"    Mode: {'batch (-stay_open)' if not dry_run else 'DRY RUN'}")
    if verbose:
        print("    Verbose: ON (per-file ExifTool commands will be printed)")
    if log_file:
        print(f"    Log file: {log_file}")
    if year_filter:
        print(f"    Year filter: {year_filter}")
    print("=" * 50)

    if not dry_run and not check_exiftool():
        return

    conn = get_connection(db_path)
    cursor = conn.cursor()

    query = """
        SELECT m.id, m.filepath, m.filename, m.is_video,
               j.photo_taken_timestamp, j.creation_timestamp,
               j.latitude, j.longitude, j.altitude
        FROM media_files m
        JOIN json_metadata j ON m.json_metadata_id = j.id
        WHERE m.metadata_written = 0
    """
    params = []

    if year_filter:
        query += " AND m.year_folder = ?"
        params.append(year_filter)

    query += " ORDER BY m.year_folder, m.filename"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total = len(rows)
    if total == 0:
        print("\n[X] No files to write metadata (already done or no matches)")
        return

    print(f"\n[/] Files to process: {total:,}\n")

    success = 0
    failed = 0
    renamed = 0

    log_fh = None
    if log_file:
        try:
            log_fh = open(log_file, "w", encoding="utf-8")
            log_fh.write(
                f"# gphoto-date-fixer metadata log ({'dry-run' if dry_run else 'live'})\n"
            )
        except OSError as e:
            print(f"[!] Cannot open log file {log_file}: {e}")
            log_fh = None

    with ExifToolBatch() as et:
        for i, row in enumerate(rows, 1):
            filepath = row["filepath"]
            filename = row["filename"]
            is_video = bool(row["is_video"])

            timestamp = row["photo_taken_timestamp"] or row["creation_timestamp"]
            if timestamp is None:
                progress_error(f"  [!] [{i}/{total}] No timestamp: {filename}")
                failed += 1
                continue

            if not os.path.isfile(filepath):
                found = find_renamed_file(filepath)
                if found:
                    filepath = found
                    filename = os.path.basename(found)
                    new_stem, new_ext = os.path.splitext(filename)
                    cursor.execute("""
                        UPDATE media_files
                        SET filepath = ?, filename = ?, stem = ?, extension = ?
                        WHERE id = ?
                    """, (filepath, filename, new_stem, new_ext.lower(), row["id"]))
                else:
                    progress_error(f"  [!] [{i}/{total}] File not found: {filepath}")
                    failed += 1
                    continue

            target_tz = tz or timezone.utc
            dt = datetime.fromtimestamp(timestamp, tz=target_tz)
            date_display = dt.strftime("%Y-%m-%d %H:%M")
            gps_display = " GPS" if row["latitude"] is not None else ""

            pct = i * 100 // total
            progress(f"  [{i:,}/{total:,}] {pct}% {filename} → {date_display}{gps_display}")

            if dry_run:
                args = build_exiftool_args(
                    filepath, timestamp, row["latitude"], row["longitude"],
                    row["altitude"], is_video, tz,
                )
                cmd_str = "exiftool " + " ".join(args)
                if verbose:
                    progress_error(f"  [DRY RUN] {cmd_str}")
                if log_fh:
                    log_fh.write(cmd_str + "\n")
                success += 1
                continue

            ok, new_path = write_metadata_for_file(
                et=et,
                filepath=filepath,
                timestamp=timestamp,
                latitude=row["latitude"],
                longitude=row["longitude"],
                altitude=row["altitude"],
                is_video=is_video,
                tz=tz,
            )

            if ok:
                success += 1
                if new_path:
                    renamed += 1
                    new_filename = os.path.basename(new_path)
                    new_stem, new_ext = os.path.splitext(new_filename)
                    cursor.execute("""
                        UPDATE media_files
                        SET filepath = ?, filename = ?, stem = ?,
                            extension = ?, metadata_written = 1
                        WHERE id = ?
                    """, (new_path, new_filename, new_stem,
                          new_ext.lower(), row["id"]))
                else:
                    cursor.execute(
                        "UPDATE media_files SET metadata_written = 1 WHERE id = ?",
                        (row["id"],)
                    )
                if i % 100 == 0:
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

    print(f"\n[X] Success: {success:,} files")
    if renamed > 0:
        print(f"[*] Renamed to correct format: {renamed:,} files")
    if failed > 0:
        print(f"[!] Failed: {failed:,} files")

    print_stats(conn)
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 2: Write metadata (dates and GPS) back to photos and videos"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show commands without modifying files"
    )
    parser.add_argument(
        "--year",
        help="Filter to specific year folder (e.g., 2022)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to process"
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone for time conversion (default: {DEFAULT_TIMEZONE})"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every ExifTool command to the terminal (noisy). "
             "Without this flag, dry-run shows only a progress bar."
    )
    parser.add_argument(
        "--log-file",
        help="Write every ExifTool command to the given file (one per line). "
             "Useful for reviewing a --dry-run after the fact."
    )
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)
    run_phase2(
        args.db, args.dry_run, args.year, args.limit, tz,
        verbose=args.verbose, log_file=args.log_file,
    )
