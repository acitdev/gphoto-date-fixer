"""
photo_scan.py - Phase 1: สแกน JSON metadata + Media files ลง SQLite

ขั้นตอน:
  1. สแกนทุกโฟลเดอร์ปี (2022/, 2023/, ...) หาไฟล์ .json
  2. อ่าน JSON แต่ละไฟล์ ดึง metadata เก็บลง json_metadata table
  3. สแกนไฟล์ภาพ/วิดีโอ เก็บลง media_files table
  4. จับคู่ media file กับ JSON metadata อัตโนมัติ

การใช้งาน:
  python photo_scan.py /path/to/takeout/root
  python photo_scan.py /path/to/takeout/root --db photos.db
"""

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from photo_db import init_db, print_stats

# นามสกุลไฟล์ที่รองรับ
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif",
    ".webp", ".tiff", ".tif", ".bmp", ".raw", ".cr2",
    ".nef", ".arw", ".dng", ".rw2", ".orf", ".sr2",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".3gp", ".wmv",
    ".m4v", ".mpg", ".mpeg", ".mts",
}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# รูปแบบชื่อไฟล์ JSON ของ Google Takeout
# 1. filename.json                          (มาตรฐาน)
# 2. filename.supplemental-metadata.json    (สำหรับ Live Photo)
# 3. filename.supplemen.json                (ชื่อถูกตัด)
# 4. filename(1).json                       (กรณีชื่อซ้ำ)
JSON_SUFFIX_PATTERNS = [
    ".supplemental-metadata.json",
    ".supplemen.json",
    ".json",
]


def detect_year_from_path(filepath: str) -> Optional[str]:
    """ดึงปีจาก path ของไฟล์ เช่น .../2022/... -> '2022'"""
    parts = Path(filepath).parts
    for part in parts:
        # รองรับทั้ง "2022" และ "Photos from 2022"
        match = re.search(r"(20[0-9]{2})", part)
        if match:
            return match.group(1)
    return None


def parse_json_metadata(json_path: str) -> Optional[dict]:
    """อ่านไฟล์ JSON metadata ของ Google Takeout แล้ว parse ข้อมูลที่ต้องการ"""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError) as e:
        print(f"  ⚠️  อ่าน JSON ไม่ได้: {json_path} ({e})")
        return None

    title = data.get("title", "")
    if not title:
        return None

    # ดึง timestamps
    creation_ts = None
    photo_taken_ts = None
    if "creationTime" in data and "timestamp" in data["creationTime"]:
        creation_ts = int(data["creationTime"]["timestamp"])
    if "photoTakenTime" in data and "timestamp" in data["photoTakenTime"]:
        photo_taken_ts = int(data["photoTakenTime"]["timestamp"])

    # ดึง GPS
    lat, lon, alt = None, None, None
    geo = data.get("geoData") or data.get("geoDataExif")
    if geo:
        lat = geo.get("latitude")
        lon = geo.get("longitude")
        alt = geo.get("altitude")
        # Google Takeout ใส่ 0.0 เมื่อไม่มีข้อมูล GPS
        if lat == 0.0 and lon == 0.0:
            lat, lon, alt = None, None, None

    # ดึงรายชื่อคน
    people = []
    if "people" in data:
        people = [p.get("name", "") for p in data["people"] if p.get("name")]

    # Device type
    device_type = None
    origin = data.get("googlePhotosOrigin", {})
    if "mobileUpload" in origin:
        device_type = origin["mobileUpload"].get("deviceType")

    return {
        "title": title,
        "description": data.get("description", ""),
        "creation_timestamp": creation_ts,
        "photo_taken_timestamp": photo_taken_ts,
        "latitude": lat,
        "longitude": lon,
        "altitude": alt,
        "people": json.dumps(people, ensure_ascii=False) if people else None,
        "url": data.get("url"),
        "device_type": device_type,
        "raw_json": json.dumps(data, ensure_ascii=False),
    }


def scan_json_files(conn: sqlite3.Connection, root_dir: str):
    """สแกนไฟล์ JSON ทั้งหมดแล้วเก็บลง database"""
    print("\n🔍 Phase 1a: สแกนไฟล์ JSON metadata...")

    root = Path(root_dir)
    count = 0
    skipped = 0

    for json_path in sorted(root.rglob("*.json")):
        json_str = str(json_path)
        year = detect_year_from_path(json_str)

        # ข้ามไฟล์ที่ไม่ใช่ metadata (เช่น metadata.json ของ album)
        meta = parse_json_metadata(json_str)
        if meta is None:
            skipped += 1
            continue

        try:
            conn.execute("""
                INSERT OR REPLACE INTO json_metadata
                (json_filepath, year_folder, title, description,
                 creation_timestamp, photo_taken_timestamp,
                 latitude, longitude, altitude,
                 people, url, device_type, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                json_str, year, meta["title"], meta["description"],
                meta["creation_timestamp"], meta["photo_taken_timestamp"],
                meta["latitude"], meta["longitude"], meta["altitude"],
                meta["people"], meta["url"], meta["device_type"],
                meta["raw_json"],
            ))
            count += 1
        except sqlite3.Error as e:
            print(f"  ⚠️  DB error: {json_path} ({e})")

    conn.commit()
    print(f"  ✅ เก็บ JSON metadata: {count:,} ไฟล์ (ข้าม {skipped:,})")


def scan_media_files(conn: sqlite3.Connection, root_dir: str):
    """สแกนไฟล์ภาพ/วิดีโอทั้งหมดแล้วเก็บลง database"""
    print("\n🔍 Phase 1b: สแกนไฟล์ภาพ/วิดีโอ...")

    root = Path(root_dir)
    count = 0

    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue

        ext = entry.suffix.lower()
        if ext not in MEDIA_EXTENSIONS:
            continue

        filepath = str(entry)
        filename = entry.name
        stem = entry.stem
        year = detect_year_from_path(filepath)
        file_size = entry.stat().st_size
        is_image = 1 if ext in IMAGE_EXTENSIONS else 0
        is_video = 1 if ext in VIDEO_EXTENSIONS else 0

        try:
            conn.execute("""
                INSERT OR REPLACE INTO media_files
                (filepath, filename, stem, extension, year_folder,
                 file_size, is_image, is_video)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                filepath, filename, stem, ext, year,
                file_size, is_image, is_video,
            ))
            count += 1
        except sqlite3.Error as e:
            print(f"  ⚠️  DB error: {entry} ({e})")

    conn.commit()
    print(f"  ✅ เก็บไฟล์ media: {count:,} ไฟล์")


def match_metadata_to_media(conn: sqlite3.Connection):
    """จับคู่ JSON metadata กับไฟล์ media

    กลยุทธ์การจับคู่ (เรียงตามลำดับความแม่นยำ):
    1. title ตรงกับ filename ทุกประการ (exact match)
    2. title ตรงกับ filename แบบ case-insensitive
    3. title ที่ถูกตัดชื่อ (Google Takeout บางทีตัดชื่อไฟล์ยาวให้สั้นลง)
    """
    print("\n🔗 Phase 1c: จับคู่ JSON metadata กับไฟล์ media...")

    cursor = conn.cursor()

    # ดึง JSON metadata ทั้งหมดที่ยังไม่ได้จับคู่
    cursor.execute("SELECT id, title, year_folder FROM json_metadata")
    json_rows = cursor.fetchall()

    matched = 0
    for row in json_rows:
        json_id = row["id"]
        title = row["title"]
        year = row["year_folder"]

        # กลยุทธ์ 1: exact match (ชื่อตรง + ปีตรง)
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE filename = ? AND year_folder = ? AND json_metadata_id IS NULL
        """, (json_id, title, year))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        # กลยุทธ์ 2: exact match ไม่สนปี (กรณีไฟล์อยู่คนละโฟลเดอร์)
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE filename = ? AND json_metadata_id IS NULL
        """, (json_id, title))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        # กลยุทธ์ 3: case-insensitive match
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE LOWER(filename) = LOWER(?) AND json_metadata_id IS NULL
        """, (json_id, title))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        # กลยุทธ์ 4: ชื่อไฟล์ถูกตัด - ใช้ LIKE match
        # Google Takeout บางทีตัดชื่อไฟล์ยาว เช่น "very_long_name" -> "very_long_na"
        if len(title) > 10:
            title_prefix = title[:len(title) - 5]  # ตัด 5 ตัวท้าย
            _, ext = os.path.splitext(title)
            cursor.execute("""
                UPDATE media_files SET json_metadata_id = ?
                WHERE filename LIKE ? AND extension = LOWER(?)
                AND json_metadata_id IS NULL
            """, (json_id, f"{title_prefix}%", ext))

            if cursor.rowcount > 0:
                matched += cursor.rowcount

    conn.commit()

    # รายงานสถิติ
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NOT NULL")
    total_matched = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NULL")
    total_unmatched = cursor.fetchone()[0]

    print(f"  ✅ จับคู่สำเร็จ: {total_matched:,} ไฟล์")
    if total_unmatched > 0:
        print(f"  ⚠️  ยังไม่ได้จับคู่: {total_unmatched:,} ไฟล์")

        # แสดงตัวอย่างไฟล์ที่ไม่ได้จับคู่ (สูงสุด 10 ไฟล์)
        cursor.execute("""
            SELECT filename, year_folder FROM media_files
            WHERE json_metadata_id IS NULL LIMIT 10
        """)
        unmatched = cursor.fetchall()
        if unmatched:
            print("  📋 ตัวอย่างไฟล์ที่ยังไม่จับคู่:")
            for row in unmatched:
                print(f"     - {row['filename']} (ปี: {row['year_folder']})")


def detect_live_photos(conn: sqlite3.Connection):
    """ตรวจหาคู่ Live Photo (ภาพ + วิดีโอ ชื่อ stem เดียวกัน)

    เงื่อนไข:
    - ไฟล์ภาพ (HEIC/JPG) + ไฟล์วิดีโอ (MP4/MOV) ที่มี stem เดียวกัน
    - อยู่ในปีเดียวกัน
    - ตรงกับ JSON ที่เป็น supplemental-metadata
    """
    print("\n📸 Phase 1d: ตรวจหาคู่ Live Photo...")

    cursor = conn.cursor()

    # ล้างข้อมูลเก่า
    cursor.execute("DELETE FROM live_photos")

    # หาภาพที่มีวิดีโอคู่กัน (stem เดียวกัน, ปีเดียวกัน)
    cursor.execute("""
        SELECT img.id AS image_id, vid.id AS video_id,
               img.filename AS img_name, vid.filename AS vid_name
        FROM media_files img
        JOIN media_files vid
            ON img.stem = vid.stem
            AND img.is_image = 1
            AND vid.is_video = 1
            AND (img.year_folder = vid.year_folder
                 OR (img.year_folder IS NULL AND vid.year_folder IS NULL))
    """)

    pairs = cursor.fetchall()
    count = 0

    for pair in pairs:
        cursor.execute("""
            INSERT OR IGNORE INTO live_photos (image_media_id, video_media_id)
            VALUES (?, ?)
        """, (pair["image_id"], pair["video_id"]))
        count += cursor.rowcount

    conn.commit()
    print(f"  ✅ พบ Live Photo: {count:,} คู่")


def run_phase1(root_dir: str, db_path: str = "google_photos.db"):
    """รัน Phase 1 ทั้งหมด"""
    print("=" * 50)
    print("🚀 Phase 1: สแกน JSON + Media ลง SQLite")
    print(f"   Root directory: {root_dir}")
    print(f"   Database: {db_path}")
    print("=" * 50)

    if not os.path.isdir(root_dir):
        print(f"❌ ไม่พบโฟลเดอร์: {root_dir}")
        return

    conn = init_db(db_path)

    try:
        scan_json_files(conn, root_dir)
        scan_media_files(conn, root_dir)
        match_metadata_to_media(conn)
        detect_live_photos(conn)
        print_stats(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 1: สแกน Google Takeout JSON + Media ลง SQLite"
    )
    parser.add_argument("root_dir", help="โฟลเดอร์ root ที่มีโฟลเดอร์ปี (2022/, 2023/, ...)")
    parser.add_argument("--db", default="google_photos.db", help="ที่อยู่ไฟล์ SQLite database")
    args = parser.parse_args()

    run_phase1(args.root_dir, args.db)
