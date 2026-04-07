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
from datetime import datetime, timezone
from typing import Optional

from photo_db import get_connection, print_stats


def progress(text: str):
    """แสดง progress แบบเขียนทับบรรทัดเดิม"""
    # ล้างบรรทัดเดิมด้วย space แล้วกลับต้นบรรทัด
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


def build_exiftool_command(
    filepath: str,
    timestamp: int,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    is_video: bool = False,
) -> list[str]:
    """สร้างคำสั่ง ExifTool สำหรับเขียน metadata

    Args:
        filepath: ที่อยู่ไฟล์
        timestamp: Unix timestamp ของ photoTakenTime
        latitude: ละติจูด (หรือ None)
        longitude: ลองจิจูด (หรือ None)
        altitude: ความสูง (หรือ None)
        is_video: เป็นไฟล์วิดีโอหรือไม่

    Returns:
        list ของ arguments สำหรับ subprocess
    """
    # แปลง timestamp เป็น format ที่ ExifTool เข้าใจ
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    date_str = dt.strftime("%Y:%m:%d %H:%M:%S")
    date_str_tz = dt.strftime("%Y:%m:%d %H:%M:%S+00:00")

    cmd = [
        "exiftool",
        "-overwrite_original",  # ไม่สร้างไฟล์ _original สำรอง
        "-ignoreMinorErrors",
        "-F",                   # ซ่อมโครงสร้าง metadata ที่เสียหาย
    ]

    if is_video:
        # สำหรับวิดีโอ (MP4/MOV) ใช้ QuickTime tags
        cmd.extend([
            f"-QuickTime:CreateDate={date_str}",
            f"-QuickTime:ModifyDate={date_str}",
            f"-QuickTime:TrackCreateDate={date_str}",
            f"-QuickTime:TrackModifyDate={date_str}",
            f"-QuickTime:MediaCreateDate={date_str}",
            f"-QuickTime:MediaModifyDate={date_str}",
        ])
    else:
        # สำหรับภาพ (JPG/HEIC) ใช้ EXIF tags
        cmd.extend([
            f"-AllDates={date_str}",
            f"-EXIF:OffsetTimeOriginal=+00:00",
        ])

    # GPS coordinates
    if latitude is not None and longitude is not None:
        # กำหนด GPS Reference (N/S, E/W)
        lat_ref = "N" if latitude >= 0 else "S"
        lon_ref = "E" if longitude >= 0 else "W"

        cmd.extend([
            f"-GPSLatitude={abs(latitude)}",
            f"-GPSLatitudeRef={lat_ref}",
            f"-GPSLongitude={abs(longitude)}",
            f"-GPSLongitudeRef={lon_ref}",
        ])

        if altitude is not None:
            alt_ref = 0 if altitude >= 0 else 1  # 0 = above sea level
            cmd.extend([
                f"-GPSAltitude={abs(altitude)}",
                f"-GPSAltitudeRef={alt_ref}",
            ])

    cmd.append(filepath)
    return cmd


def update_file_dates(filepath: str, timestamp: int):
    """อัปเดตวันที่ระดับ OS (file modification time + access time)

    ExifTool แก้ metadata ภายในไฟล์ แต่ไม่แก้วันที่ของไฟล์บน file system
    ต้องใช้ os.utime() ด้วย เพื่อให้ Finder/file manager แสดงวันที่ถูกต้อง

    สำหรับ macOS: ใช้ SetFile หรือ touch เพื่อตั้ง creation date ด้วย
    """
    try:
        os.utime(filepath, (timestamp, timestamp))
    except OSError as e:
        print(f"  [!]  อัปเดตวันที่ OS ไม่ได้: {filepath} ({e})")
        return

    # macOS: ตั้ง creation date ด้วย (modification date อย่างเดียวไม่พอ)
    # ใช้ SetFile (ถ้ามี) หรือ xattr
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        # SetFile format: "MM/DD/YYYY HH:MM:SS"
        setfile_date = dt.strftime("%m/%d/%Y %H:%M:%S")

        if shutil.which("SetFile"):
            subprocess.run(
                ["SetFile", "-d", setfile_date, filepath],
                capture_output=True, timeout=10,
            )
        else:
            # ใช้ touch -t เป็น fallback (แก้ได้แค่ mtime)
            touch_date = dt.strftime("%Y%m%d%H%M.%S")
            subprocess.run(
                ["touch", "-t", touch_date, filepath],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass  # ไม่ใช่ปัญหาร้ายแรงถ้า creation date ตั้งไม่ได้


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
    """หาไฟล์ที่อาจถูกเปลี่ยนนามสกุลไปแล้วจากรอบก่อน

    เช่น DB บอกว่า IMG_3133.DNG แต่รอบก่อนเปลี่ยนเป็น IMG_3133.jpg ไปแล้ว
    ให้ลองหาไฟล์ชื่อเดียวกันแต่นามสกุลอื่น

    Returns:
        path ที่พบ หรือ None
    """
    base = os.path.splitext(filepath)[0]
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    # ลอง _renamed ด้วย
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"):
        candidate = base + "_renamed" + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def rename_to_real_format(filepath: str, stderr: str) -> Optional[str]:
    """ถ้า ExifTool บอกว่านามสกุลไม่ตรงกับเนื้อไฟล์จริง ให้เปลี่ยนนามสกุล

    เช่น IMG_0954.PNG ที่จริงเป็น JPEG → เปลี่ยนเป็น IMG_0954.jpg

    Returns:
        path ใหม่หลังเปลี่ยนชื่อ หรือ None ถ้าไม่ต้องเปลี่ยน
    """
    match = _LOOKS_LIKE_RE.search(stderr)
    if not match:
        return None

    real_format = match.group(1).upper()
    new_ext = _FORMAT_TO_EXT.get(real_format)
    if not new_ext:
        return None

    base, old_ext = os.path.splitext(filepath)
    if old_ext.lower() == new_ext:
        return None  # นามสกุลตรงอยู่แล้ว

    new_path = base + new_ext
    # ถ้าไฟล์ปลายทางมีอยู่แล้ว ให้เพิ่ม _renamed
    if os.path.exists(new_path):
        new_path = base + "_renamed" + new_ext

    try:
        os.rename(filepath, new_path)
        return new_path
    except OSError:
        return None


def write_metadata_for_file(
    filepath: str,
    timestamp: int,
    latitude: Optional[float],
    longitude: Optional[float],
    altitude: Optional[float],
    is_video: bool,
    dry_run: bool = False,
) -> tuple[bool, Optional[str]]:
    """เขียน metadata ลงในไฟล์เดียว

    Returns:
        (success, new_filepath)
        - success: True ถ้าสำเร็จ
        - new_filepath: path ใหม่ถ้าไฟล์ถูกเปลี่ยนชื่อ (หรือ None)
    """
    cmd = build_exiftool_command(
        filepath, timestamp, latitude, longitude, altitude, is_video
    )

    if dry_run:
        progress_error(f"  [DRY RUN] {' '.join(cmd)}")
        return True, None

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            # ลอง retry ถ้า ExifTool บอกว่านามสกุลไม่ตรง
            if "looks more like a" in result.stderr:
                new_path = rename_to_real_format(filepath, result.stderr)
                if new_path:
                    # เปลี่ยนนามสกุลเงียบ ๆ ไม่ต้องพ่น error
                    # สร้างคำสั่งใหม่ด้วย path ใหม่
                    cmd2 = build_exiftool_command(
                        new_path, timestamp, latitude, longitude, altitude, is_video
                    )
                    result2 = subprocess.run(
                        cmd2, capture_output=True, text=True, timeout=30
                    )
                    if result2.returncode == 0:
                        update_file_dates(new_path, timestamp)
                        return True, new_path
                    else:
                        progress_error(
                            f"  [!]  ExifTool error (retry): {new_path}\n"
                            f"      stderr: {result2.stderr.strip()}"
                        )
                        return False, new_path

            progress_error(
                f"  [!]  ExifTool error: {filepath}\n"
                f"      stderr: {result.stderr.strip()}"
            )
            return False, None

        # อัปเดตวันที่ระดับ OS
        update_file_dates(filepath, timestamp)
        return True, None

    except subprocess.TimeoutExpired:
        progress_error(f"  [!]  ExifTool timeout: {filepath}")
        return False, None
    except Exception as e:
        progress_error(f"  [!]  Error: {filepath} ({e})")
        return False, None


def run_phase2(
    db_path: str = "google_photos.db",
    dry_run: bool = False,
    year_filter: Optional[str] = None,
    limit: Optional[int] = None,
):
    """รัน Phase 2: เขียน metadata กลับเข้าไฟล์ทั้งหมดที่จับคู่ได้"""
    print("=" * 50)
    print("[*] Phase 2: เขียน metadata กลับเข้าไฟล์")
    print(f"   Database: {db_path}")
    if dry_run:
        print("   [*] DRY RUN MODE - ไม่แก้ไขไฟล์จริง")
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
                # อัปเดต DB ให้ตรงกับชื่อไฟล์ปัจจุบัน
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

        # แสดง progress (เขียนทับบรรทัดเดิม)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        date_display = dt.strftime("%Y-%m-%d %H:%M")
        gps_display = ""
        if row["latitude"] is not None:
            gps_display = f" GPS"

        pct = i * 100 // total
        progress(f"  [{i:,}/{total:,}] {pct}% {filename} → {date_display}{gps_display}")

        ok, new_path = write_metadata_for_file(
            filepath=filepath,
            timestamp=timestamp,
            latitude=row["latitude"],
            longitude=row["longitude"],
            altitude=row["altitude"],
            is_video=is_video,
            dry_run=dry_run,
        )

        if ok:
            success += 1
            if not dry_run:
                # ถ้าไฟล์ถูกเปลี่ยนชื่อ อัปเดต path ใน DB ด้วย
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
                # Commit ทุก 50 ไฟล์
                if i % 50 == 0:
                    conn.commit()
        else:
            failed += 1

    conn.commit()

    # จบ progress line แล้วขึ้นบรรทัดใหม่
    progress(f"  [{total:,}/{total:,}] 100% เสร็จสิ้น")
    print()  # newline

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
    args = parser.parse_args()

    run_phase2(args.db, args.dry_run, args.year, args.limit)
