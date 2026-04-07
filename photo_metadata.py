"""
photo_metadata.py - Phase 2: เขียน metadata กลับเข้าไฟล์ภาพ/วิดีโอ

ใช้ ExifTool เขียน:
  - วันที่ (AllDates / QuickTime dates)
  - GPS (latitude, longitude, altitude)
  - อัปเดตวันที่ระดับ OS (file modification/access time)

ข้อกำหนด:
  - ต้องติดตั้ง ExifTool: brew install exiftool (macOS)
  - ต้องรัน Phase 1 (photo_scan.py) ก่อน

การใช้งาน:
  python photo_metadata.py
  python photo_metadata.py --db photos.db --dry-run
  python photo_metadata.py --year 2022
"""

import os
import re
import shutil
import subprocess
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from photo_db import get_connection, print_stats


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def progress(text: str):
    """แสดง progress แบบเขียนทับบรรทัดเดิม"""
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    sys.stdout.write(f"\r{text[:terminal_width]:<{terminal_width}}")
    sys.stdout.flush()


def progress_error(text: str):
    """แสดง error แยกบรรทัดใหม่ (ไม่ถูกเขียนทับ)"""
    sys.stdout.write(f"\n{text}\n")
    sys.stdout.flush()


def check_exiftool() -> bool:
    """ตรวจสอบว่าติดตั้ง ExifTool แล้วหรือยัง"""
    if shutil.which("exiftool") is None:
        print("[!] ไม่พบ ExifTool!")
        print("   ติดตั้งด้วย: brew install exiftool")
        print("   หรือดาวน์โหลดจาก: https://exiftool.org/")
        return False
    return True


# ---------------------------------------------------------------------------
# ExifTool Batch Process  (-stay_open mode)
# เปิด ExifTool process เดียว ส่งคำสั่งผ่าน stdin → เร็วกว่า subprocess ทีละไฟล์หลายเท่า
# ---------------------------------------------------------------------------

class ExifToolBatch:
    """จัดการ ExifTool แบบ persistent process (-stay_open True)

    แทนที่จะ spawn process ใหม่ทุกไฟล์ (47,000 ครั้ง)
    เปิดแค่ครั้งเดียวแล้วส่งคำสั่งผ่าน stdin → ลด overhead 10-50x

    Usage:
        with ExifToolBatch() as et:
            ok, stderr = et.execute(["-AllDates=2022:01:01 10:00:00", "photo.jpg"])
    """

    # sentinel ที่ ExifTool ส่งกลับมาเมื่อจบแต่ละคำสั่ง
    _READY_PATTERN = re.compile(r"\{ready(\d*)\}")

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._seq = 0

    def start(self):
        """เริ่ม ExifTool process"""
        self._process = subprocess.Popen(
            ["exiftool", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def stop(self):
        """ปิด ExifTool process"""
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
        """ส่งคำสั่งเข้า ExifTool แล้วรอผลลัพธ์

        Args:
            args: list ของ arguments (ไม่ต้องมี "exiftool" นำหน้า)

        Returns:
            (success, output) — output รวม stdout+stderr
        """
        if not self._process or self._process.poll() is not None:
            return False, "ExifTool process not running"

        self._seq += 1
        seq = self._seq

        # ส่ง arguments ทีละบรรทัด จบด้วย -execute{seq}
        for arg in args:
            self._process.stdin.write(arg + "\n")
        self._process.stdin.write(f"-execute{seq}\n")
        self._process.stdin.flush()

        # อ่าน stdout จนเจอ {ready{seq}}
        output_lines = []
        while True:
            line = self._process.stdout.readline()
            if not line:
                break
            m = self._READY_PATTERN.match(line.strip())
            if m:
                break
            output_lines.append(line.rstrip("\n"))

        output = "\n".join(output_lines)

        # อ่าน stderr ที่มี (non-blocking ด้วย read1 ผ่าน os)
        stderr_text = ""
        try:
            import select
            while select.select([self._process.stderr], [], [], 0)[0]:
                chunk = self._process.stderr.read(4096)
                if not chunk:
                    break
                stderr_text += chunk
        except Exception:
            pass

        # ตรวจสอบว่าสำเร็จ: ExifTool พิมพ์ "1 image files updated" เมื่อสำเร็จ
        success = "updated" in output or "unchanged" in output
        full_output = stderr_text.strip() if stderr_text.strip() else output
        return success, full_output


# ---------------------------------------------------------------------------
# ExifTool argument builders
# ---------------------------------------------------------------------------

def build_exiftool_args(
    filepath: str,
    timestamp: int,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    is_video: bool = False,
    tz: Optional[ZoneInfo] = None,
) -> list[str]:
    """สร้าง arguments สำหรับ ExifTool (ไม่มี "exiftool" นำหน้า)

    Args:
        filepath: ที่อยู่ไฟล์
        timestamp: Unix timestamp ของ photoTakenTime
        latitude: ละติจูด (หรือ None)
        longitude: ลองจิจูด (หรือ None)
        altitude: ความสูง (หรือ None)
        is_video: เป็นไฟล์วิดีโอหรือไม่
        tz: timezone สำหรับแปลงเวลา (None = UTC)

    Returns:
        list ของ arguments
    """
    # แปลง timestamp เป็นเวลาท้องถิ่น
    target_tz = tz or timezone.utc
    dt = datetime.fromtimestamp(timestamp, tz=target_tz)
    date_str = dt.strftime("%Y:%m:%d %H:%M:%S")
    offset_str = dt.strftime("%z")  # เช่น "+0700"
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

    # GPS coordinates
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


# ---------------------------------------------------------------------------
# File date helpers
# ---------------------------------------------------------------------------

def update_file_dates(filepath: str, timestamp: int, tz: Optional[ZoneInfo] = None):
    """อัปเดตวันที่ระดับ OS (file modification time + access time + creation date)"""
    try:
        os.utime(filepath, (timestamp, timestamp))
    except OSError as e:
        print(f"  [!]  อัปเดตวันที่ OS ไม่ได้: {filepath} ({e})")
        return

    # macOS: ตั้ง creation date ด้วย SetFile
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


# ---------------------------------------------------------------------------
# File rename / repair helpers
# ---------------------------------------------------------------------------

# regex จับ error "Not a valid X (looks more like a Y)"
_LOOKS_LIKE_RE = re.compile(r"looks more like a (\w+)")

# mapping ชื่อ format ที่ ExifTool บอก → นามสกุลไฟล์ที่ถูกต้อง
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


def find_renamed_file(filepath: str) -> Optional[str]:
    """หาไฟล์ที่อาจถูกเปลี่ยนนามสกุลไปแล้วจากรอบก่อน"""
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
    """ถ้า ExifTool บอกว่านามสกุลไม่ตรงกับเนื้อไฟล์จริง ให้เปลี่ยนนามสกุล"""
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
    """ซ่อมไฟล์ภาพที่โครงสร้างภายในเสียหาย โดยเปิดแล้วเซฟใหม่ด้วย Pillow"""
    try:
        from PIL import Image
        img = Image.open(filepath)
        img.save(filepath, quality=95, subsampling=0)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Per-file metadata writer (ใช้ ExifToolBatch)
# ---------------------------------------------------------------------------

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
    """เขียน metadata ลงในไฟล์เดียว ผ่าน ExifToolBatch

    Returns:
        (success, new_filepath)
        - success: True ถ้าสำเร็จ
        - new_filepath: path ใหม่ถ้าไฟล์ถูกเปลี่ยนชื่อ (หรือ None)
    """
    args = build_exiftool_args(
        filepath, timestamp, latitude, longitude, altitude, is_video, tz
    )

    ok, output = et.execute(args)

    if not ok:
        # ลอง retry ถ้า ExifTool บอกว่านามสกุลไม่ตรง
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
                        f"  [!]  ExifTool error (retry): {new_path}\n"
                        f"      {output2}"
                    )
                    return False, new_path

        # ลองซ่อมภาพแล้ว retry (เฉพาะไฟล์ภาพ ไม่ใช่วิดีโอ)
        if not is_video and repair_image(filepath):
            ok3, output3 = et.execute(args)
            if ok3:
                update_file_dates(filepath, timestamp, tz)
                return True, None

        progress_error(
            f"  [!]  ExifTool error: {filepath}\n"
            f"      {output}"
        )
        return False, None

    # อัปเดตวันที่ระดับ OS
    update_file_dates(filepath, timestamp, tz)
    return True, None


# ---------------------------------------------------------------------------
# Main phase runner
# ---------------------------------------------------------------------------

def run_phase2(
    db_path: str = "google_photos.db",
    dry_run: bool = False,
    year_filter: Optional[str] = None,
    limit: Optional[int] = None,
    tz: Optional[ZoneInfo] = None,
):
    """รัน Phase 2: เขียน metadata กลับเข้าไฟล์ทั้งหมดที่จับคู่ได้"""
    print("=" * 50)
    print("[*] Phase 2: เขียน metadata กลับเข้าไฟล์")
    print(f"   Database: {db_path}")
    print(f"   Timezone: {tz or 'UTC'}")
    print(f"   Mode: {'batch (-stay_open)' if not dry_run else 'DRY RUN'}")
    if year_filter:
        print(f"   [*] กรองปี: {year_filter}")
    print("=" * 50)

    if not dry_run and not check_exiftool():
        return

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # ดึงไฟล์ที่จับคู่กับ JSON แล้วแต่ยังไม่ได้เขียน metadata
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
        print("\n[X] ไม่มีไฟล์ที่ต้องเขียน metadata (ทำหมดแล้วหรือยังไม่ได้จับคู่)")
        return

    print(f"\n[/] ต้องเขียน metadata: {total:,} ไฟล์\n")

    success = 0
    failed = 0
    renamed = 0

    # เปิด ExifTool batch process ตัวเดียว ใช้ตลอด
    with ExifToolBatch() as et:
        for i, row in enumerate(rows, 1):
            filepath = row["filepath"]
            filename = row["filename"]
            is_video = bool(row["is_video"])

            # ใช้ photoTakenTime ก่อน ถ้าไม่มีใช้ creationTime
            timestamp = row["photo_taken_timestamp"] or row["creation_timestamp"]
            if timestamp is None:
                progress_error(f"  [!]  [{i}/{total}] ไม่มี timestamp: {filename}")
                failed += 1
                continue

            # ตรวจสอบว่าไฟล์ยังอยู่ (อาจถูกเปลี่ยนนามสกุลจากรอบก่อน)
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
                    progress_error(f"  [!]  [{i}/{total}] ไม่พบไฟล์: {filepath}")
                    failed += 1
                    continue

            # แสดง progress
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
                progress_error(f"  [DRY RUN] exiftool {' '.join(args)}")
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
                # Commit ทุก 100 ไฟล์
                if i % 100 == 0:
                    conn.commit()
            else:
                failed += 1

    conn.commit()

    # จบ progress line
    progress(f"  [{total:,}/{total:,}] 100% เสร็จสิ้น")
    print()

    print(f"\n[X] สำเร็จ: {success:,} ไฟล์")
    if renamed > 0:
        print(f"[*] เปลี่ยนนามสกุลให้ตรงเนื้อไฟล์: {renamed:,} ไฟล์")
    if failed > 0:
        print(f"[!]  ล้มเหลว: {failed:,} ไฟล์")

    print_stats(conn)
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 2: เขียน metadata (วันที่/GPS) กลับเข้าไฟล์ภาพ/วิดีโอ"
    )
    parser.add_argument("--db", default="google_photos.db", help="ที่อยู่ไฟล์ SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="แสดงคำสั่งแต่ไม่แก้ไฟล์จริง")
    parser.add_argument("--year", help="กรองเฉพาะปี เช่น 2022")
    parser.add_argument("--limit", type=int, help="จำกัดจำนวนไฟล์ที่จะประมวลผล")
    parser.add_argument("--timezone", default="Asia/Bangkok",
                        help="Timezone สำหรับแปลงเวลา เช่น Asia/Bangkok (default), US/Eastern")
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)
    run_phase2(args.db, args.dry_run, args.year, args.limit, tz)
