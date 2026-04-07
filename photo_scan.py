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
        print(f"  [!]  อ่าน JSON ไม่ได้: {json_path} ({e})")
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
    print("\n[/] Phase 1a: สแกนไฟล์ JSON metadata...")

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
            print(f"  [!]  DB error: {json_path} ({e})")

    conn.commit()
    print(f"  [X] เก็บ JSON metadata: {count:,} ไฟล์ (ข้าม {skipped:,})")


def scan_media_files(conn: sqlite3.Connection, root_dir: str):
    """สแกนไฟล์ภาพ/วิดีโอทั้งหมดแล้วเก็บลง database"""
    print("\n[/] Phase 1b: สแกนไฟล์ภาพ/วิดีโอ...")

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
            print(f"  [!]  DB error: {entry} ({e})")

    conn.commit()
    print(f"  [X] เก็บไฟล์ media: {count:,} ไฟล์")


def match_metadata_to_media(conn: sqlite3.Connection):
    """จับคู่ JSON metadata กับไฟล์ media (บังคับปีเดียวกันเสมอ)

    กลยุทธ์การจับคู่ (เรียงตามลำดับความแม่นยำ):
    1. title ตรงกับ filename ทุกประการ + ปีเดียวกัน (exact match)
    2. stem match + ปีเดียวกัน (ชื่อเดียวกันแต่ ext ต่างกัน เช่น HEIC→jpg)
    3. case-insensitive match + ปีเดียวกัน
    """
    print("\n[/] Phase 1c: จับคู่ JSON metadata กับไฟล์ media...")

    cursor = conn.cursor()

    # === Pass 1: จับคู่จากฝั่ง JSON → Media (exact + case-insensitive) ===
    cursor.execute("SELECT id, title, year_folder FROM json_metadata")
    json_rows = cursor.fetchall()

    matched = 0
    unmatched_json = []

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

        # กลยุทธ์ 2: stem match + ปีเดียวกัน (ชื่อเดียวกันแต่นามสกุลต่างกัน)
        # สำหรับไฟล์ที่ถูกเปลี่ยนนามสกุลไปแล้ว เช่น IMG_0954.HEIC → IMG_0954.jpg
        # ต้องจับคู่ในปีเดียวกันก่อน เพื่อไม่ให้ไปแย่งไฟล์จากปีอื่น
        # เรียง is_video ASC เพื่อให้ภาพได้ก่อนวิดีโอ (วิดีโอจะจับคู่ใน Pass 3)
        title_stem = os.path.splitext(title)[0]
        cursor.execute("""
            SELECT id FROM media_files
            WHERE stem = ? AND year_folder = ? AND json_metadata_id IS NULL
            ORDER BY is_video ASC
        """, (title_stem, year))
        stem_match = cursor.fetchone()
        if stem_match:
            cursor.execute(
                "UPDATE media_files SET json_metadata_id = ? WHERE id = ?",
                (json_id, stem_match["id"])
            )
            matched += 1
            continue

        # กลยุทธ์ 3: case-insensitive match + ปีเดียวกัน
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE LOWER(filename) = LOWER(?) AND year_folder = ?
                  AND json_metadata_id IS NULL
        """, (json_id, title, year))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        # เก็บไว้ทำ Pass 2
        unmatched_json.append(row)

    conn.commit()

    # === Pass 2: จับคู่ไฟล์ที่ชื่อถูกตัด (truncated filename matching) ===
    # Google Takeout ตัดชื่อไฟล์ยาวให้สั้นลงทั้งฝั่ง media และ JSON
    # เช่น title = "474755...63c7334d.22050706.jpg"
    #      ไฟล์จริง = "474755...63c.jpg"
    # วิธี: เอา stem ของ media file ไปเช็คว่า title (ไม่รวม ext) ขึ้นต้นด้วย stem นั้น

    if unmatched_json:
        print(f"  [/] Pass 2: จับคู่ไฟล์ชื่อถูกตัด ({len(unmatched_json):,} JSON ที่เหลือ)...")

        # ดึงไฟล์ media ที่ยังไม่ได้จับคู่
        cursor.execute("""
            SELECT id, stem, extension, year_folder, filename
            FROM media_files
            WHERE json_metadata_id IS NULL
        """)
        unmatched_media = cursor.fetchall()

        # สร้าง lookup: (year, extension) -> [(media_id, stem, filename)]
        from collections import defaultdict
        media_lookup = defaultdict(list)
        for m in unmatched_media:
            key = (m["year_folder"], m["extension"])
            media_lookup[key].append({
                "id": m["id"],
                "stem": m["stem"],
                "filename": m["filename"],
            })

        pass2_matched = 0
        for row in unmatched_json:
            json_id = row["id"]
            title = row["title"]
            year = row["year_folder"]

            title_stem, title_ext_raw = os.path.splitext(title)
            title_ext = title_ext_raw.lower()

            # หาไฟล์ media ที่ stem เป็น prefix ของ title_stem
            # (ไฟล์จริงถูกตัดให้สั้นกว่า title)
            candidates = media_lookup.get((year, title_ext), [])
            best_match = None
            best_len = 0

            for m in candidates:
                media_stem = m["stem"]
                # เช็คว่า title_stem ขึ้นต้นด้วย media_stem
                # และ media_stem ต้องยาวพอสมควร (ป้องกัน false positive)
                if (len(media_stem) >= 10
                        and title_stem.startswith(media_stem)
                        and len(media_stem) > best_len):
                    best_match = m
                    best_len = len(media_stem)

            if best_match is None:
                # ลองกลับด้าน: เอา title_stem เป็น prefix ของ media_stem
                # (กรณี title ถูกตัดสั้นกว่าชื่อไฟล์จริง)
                for m in candidates:
                    media_stem = m["stem"]
                    if (len(title_stem) >= 10
                            and media_stem.startswith(title_stem)
                            and len(title_stem) > best_len):
                        best_match = m
                        best_len = len(title_stem)

            if best_match:
                cursor.execute("""
                    UPDATE media_files SET json_metadata_id = ?
                    WHERE id = ? AND json_metadata_id IS NULL
                """, (json_id, best_match["id"]))
                if cursor.rowcount > 0:
                    pass2_matched += 1
                    # ลบออกจาก lookup เพื่อไม่ให้จับคู่ซ้ำ
                    candidates.remove(best_match)

        conn.commit()
        matched += pass2_matched
        print(f"  [X] Pass 2 จับคู่เพิ่ม: {pass2_matched:,} ไฟล์")

    # === Pass 3: วิดีโอ Live Photo ใช้ JSON ร่วมกับภาพ ===
    # Google Takeout ไม่สร้าง JSON แยกสำหรับวิดีโอ Live Photo
    # เช่น IMG_0017.JPG.supplemental-metadata.json → title: IMG_0017.JPG
    #      IMG_0017.JPG ได้จับคู่กับ JSON แล้ว
    #      IMG_0017.MP4 ไม่มี JSON → ต้องเอา JSON ของ IMG_0017.JPG มาใช้ร่วม
    #
    # กลยุทธ์: หาวิดีโอที่ยังไม่มี JSON แล้ว stem ตรงกับภาพที่มี JSON แล้ว
    # (รองรับทั้ง exact stem และ prefix match)
    print("  [/] Pass 3: จับคู่วิดีโอ Live Photo กับ JSON ของภาพ...")

    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_video = 1 AND json_metadata_id IS NULL
    """)
    orphan_videos = cursor.fetchall()

    if orphan_videos:
        # ดึงภาพที่มี JSON แล้ว เพื่อใช้เป็นตัวกลาง
        cursor.execute("""
            SELECT id, stem, year_folder, json_metadata_id FROM media_files
            WHERE is_image = 1 AND json_metadata_id IS NOT NULL
        """)
        matched_images = cursor.fetchall()

        # สร้าง lookup: (year, stem) → json_metadata_id (exact)
        # และ list สำหรับ prefix match
        from collections import defaultdict
        exact_lookup = {}
        prefix_candidates = defaultdict(list)
        for img in matched_images:
            key = (img["year_folder"], img["stem"])
            exact_lookup[key] = img["json_metadata_id"]
            prefix_candidates[img["year_folder"]].append({
                "stem": img["stem"],
                "json_id": img["json_metadata_id"],
            })

        pass3_matched = 0
        for vid in orphan_videos:
            vid_stem = vid["stem"]
            year = vid["year_folder"]
            json_id = None

            # 3a: exact stem match
            json_id = exact_lookup.get((year, vid_stem))

            # 3b: prefix match (ถ้า exact ไม่เจอ)
            if json_id is None and len(vid_stem) >= 10:
                best_len = 0
                for img in prefix_candidates.get(year, []):
                    img_stem = img["stem"]
                    if len(img_stem) < 10:
                        continue
                    if (vid_stem.startswith(img_stem) or img_stem.startswith(vid_stem)):
                        match_len = min(len(vid_stem), len(img_stem))
                        if match_len > best_len:
                            json_id = img["json_id"]
                            best_len = match_len

            if json_id:
                cursor.execute("""
                    UPDATE media_files SET json_metadata_id = ?
                    WHERE id = ? AND json_metadata_id IS NULL
                """, (json_id, vid["id"]))
                if cursor.rowcount > 0:
                    pass3_matched += 1

        conn.commit()
        matched += pass3_matched
        print(f"  [X] Pass 3 จับคู่วิดีโอ Live Photo: {pass3_matched:,} ไฟล์")

    # === Pass 4: (N) duplicates และ -แก้ไข (edited files) ===
    # Pattern 4a: (N) duplicates
    #   JSON: IMG_3557.JPG.supplemental-metadata(1).json → title: IMG_3557.JPG
    #   Media: IMG_3557(1).JPG  (วงเล็บอยู่ในชื่อไฟล์ ไม่ใช่ก่อน ext)
    #   กติกา: ถ้า JSON มี (N) ไฟล์ media ที่จับคู่ก็ต้องมี (N) ตัวเดียวกัน
    #
    # Pattern 4b: -แก้ไข (edited version - Google Photos เพิ่มคำนี้เมื่อแก้ไขภาพ)
    #   Media: IMG_3557-แก้ไข.JPG → ใช้ JSON เดียวกับ IMG_3557.JPG
    #   Media: IMG_3557-แก้ไข(1).JPG → ใช้ JSON เดียวกับ IMG_3557(1).JPG
    #   รองรับ: -แก้ไข, -edited, -EDIT (case-insensitive)

    print("  [/] Pass 4: จับคู่ไฟล์ (N) duplicates และ -แก้ไข (edited)...")

    import re as _re

    # regex จับ (N) ที่ต่อท้าย stem เช่น "IMG_3557(1)" → base="IMG_3557", num="1"
    dup_pattern = _re.compile(r"^(.*)\((\d+)\)$")

    # regex จับ edit suffix เช่น "-แก้ไข", "-edited", "-EDIT"
    # รองรับทั้งก่อนและหลัง (N)
    edit_suffixes = ["-แก้ไข", "-edited", "-edit", "-EDIT", "-Edited"]

    def strip_edit_suffix(stem: str) -> Optional[str]:
        """ตัด edit suffix ออก คืน stem ใหม่ หรือ None ถ้าไม่มี suffix"""
        # รองรับทั้ง "IMG-แก้ไข" และ "IMG-แก้ไข(1)"
        base = stem
        dup_suffix = ""
        m = dup_pattern.match(stem)
        if m:
            base = m.group(1)
            dup_suffix = f"({m.group(2)})"

        for suf in edit_suffixes:
            if base.endswith(suf):
                cleaned_base = base[: -len(suf)]
                return cleaned_base + dup_suffix
        return None

    def extract_dup_number(path: str) -> Optional[str]:
        """ดึงเลข (N) จาก JSON filepath เช่น '....supplemental-metadata(1).json' → '1'"""
        m = _re.search(r"\((\d+)\)\.json$", path)
        if m:
            return m.group(1)
        return None

    # ดึงไฟล์ media ที่ยังไม่ได้จับคู่
    cursor.execute("""
        SELECT id, filename, stem, extension, year_folder
        FROM media_files
        WHERE json_metadata_id IS NULL
    """)
    orphan_media = cursor.fetchall()

    pass4_matched = 0

    # สร้าง lookup ของ JSON ทั้งหมด พร้อมเลข (N) ที่ดึงจาก filepath
    cursor.execute("""
        SELECT id, title, year_folder, json_filepath
        FROM json_metadata
    """)
    all_json = cursor.fetchall()

    # lookup: (year, title_lower, dup_num) → json_id
    json_lookup = {}
    for j in all_json:
        dup_num = extract_dup_number(j["json_filepath"]) or ""
        key = (j["year_folder"], j["title"].lower(), dup_num)
        json_lookup[key] = j["id"]
        # เผื่อกรณีไม่สนปี
        key_noyear = (None, j["title"].lower(), dup_num)
        if key_noyear not in json_lookup:
            json_lookup[key_noyear] = j["id"]

    for m in orphan_media:
        media_id = m["id"]
        filename = m["filename"]
        stem = m["stem"]
        ext = m["extension"]
        year = m["year_folder"]

        json_id = None

        # === 4a: ลองจับคู่ (N) duplicate ===
        # เช่น "IMG_3557(1).JPG" → base_stem="IMG_3557", dup_num="1"
        # ต้องหา JSON ที่ title="IMG_3557.JPG" + filepath มี "(1).json"
        dup_match = dup_pattern.match(stem)
        if dup_match:
            base_stem = dup_match.group(1)
            dup_num = dup_match.group(2)
            expected_title = (base_stem + ext).lower()

            json_id = json_lookup.get((year, expected_title, dup_num))
            if json_id is None:
                json_id = json_lookup.get((None, expected_title, dup_num))

        # === 4b: ลอง strip edit suffix ===
        # เช่น "IMG_3557-แก้ไข" → "IMG_3557" → หา JSON "IMG_3557.JPG"
        # หรือ "IMG_3557-แก้ไข(1)" → "IMG_3557(1)" → หา JSON "IMG_3557.JPG" + dup="1"
        if json_id is None:
            cleaned_stem = strip_edit_suffix(stem)
            if cleaned_stem:
                # ลองเป็น (N) ก่อน
                dup_match2 = dup_pattern.match(cleaned_stem)
                if dup_match2:
                    base_stem = dup_match2.group(1)
                    dup_num = dup_match2.group(2)
                    expected_title = (base_stem + ext).lower()
                    json_id = (json_lookup.get((year, expected_title, dup_num))
                               or json_lookup.get((None, expected_title, dup_num)))
                else:
                    # ไม่มี (N) - หา JSON ปกติ
                    expected_title = (cleaned_stem + ext).lower()
                    json_id = (json_lookup.get((year, expected_title, ""))
                               or json_lookup.get((None, expected_title, "")))

        if json_id:
            cursor.execute("""
                UPDATE media_files SET json_metadata_id = ?
                WHERE id = ? AND json_metadata_id IS NULL
            """, (json_id, media_id))
            if cursor.rowcount > 0:
                pass4_matched += 1

    conn.commit()
    matched += pass4_matched
    print(f"  [X] Pass 4 จับคู่ (N)/แก้ไข: {pass4_matched:,} ไฟล์")

    # === Pass 5: เก็บตกวิดีโอ Live Photo หลัง Pass 4 ===
    # Pass 3 รันก่อน Pass 4 ทำให้วิดีโอ (N) ที่ภาพเพิ่ง match ใน Pass 4 ตกหล่น
    # เช่น IMG_0216(1).JPG ถูก match ใน Pass 4 แต่ IMG_0216(1).MP4 ข้าม Pass 3 ไปแล้ว
    # Pass 5 ทำ logic เดียวกับ Pass 3 อีกครั้งเพื่อเก็บตก

    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_video = 1 AND json_metadata_id IS NULL
    """)
    orphan_videos_pass5 = cursor.fetchall()

    if orphan_videos_pass5:
        print("  [/] Pass 5: เก็บตกวิดีโอ Live Photo (หลัง Pass 4)...")

        from collections import defaultdict as _defaultdict

        # ดึงภาพที่มี JSON แล้ว (อัปเดตหลัง Pass 4)
        cursor.execute("""
            SELECT id, stem, year_folder, json_metadata_id FROM media_files
            WHERE is_image = 1 AND json_metadata_id IS NOT NULL
        """)
        matched_imgs = cursor.fetchall()

        exact_lk = {}
        prefix_cands = _defaultdict(list)
        for img in matched_imgs:
            exact_lk[(img["year_folder"], img["stem"])] = img["json_metadata_id"]
            prefix_cands[img["year_folder"]].append({
                "stem": img["stem"],
                "json_id": img["json_metadata_id"],
            })

        pass5_matched = 0
        for vid in orphan_videos_pass5:
            vid_stem = vid["stem"]
            year = vid["year_folder"]
            json_id = None

            # exact stem
            json_id = exact_lk.get((year, vid_stem))

            # prefix match
            if json_id is None and len(vid_stem) >= 10:
                best_len = 0
                for img in prefix_cands.get(year, []):
                    img_stem = img["stem"]
                    if len(img_stem) < 10:
                        continue
                    if (vid_stem.startswith(img_stem) or img_stem.startswith(vid_stem)):
                        match_len = min(len(vid_stem), len(img_stem))
                        if match_len > best_len:
                            json_id = img["json_id"]
                            best_len = match_len

            if json_id:
                cursor.execute("""
                    UPDATE media_files SET json_metadata_id = ?
                    WHERE id = ? AND json_metadata_id IS NULL
                """, (json_id, vid["id"]))
                if cursor.rowcount > 0:
                    pass5_matched += 1

        conn.commit()
        matched += pass5_matched
        print(f"  [X] Pass 5 เก็บตกวิดีโอ: {pass5_matched:,} ไฟล์")

    # รายงานสถิติ
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NOT NULL")
    total_matched = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NULL")
    total_unmatched = cursor.fetchone()[0]

    print(f"  [X] จับคู่สำเร็จ: {total_matched:,} ไฟล์")
    if total_unmatched > 0:
        print(f"  [!]  ยังไม่ได้จับคู่: {total_unmatched:,} ไฟล์")

        # แสดงตัวอย่างไฟล์ที่ไม่ได้จับคู่ (สูงสุด 10 ไฟล์)
        cursor.execute("""
            SELECT filename, year_folder FROM media_files
            WHERE json_metadata_id IS NULL LIMIT 10
        """)
        unmatched = cursor.fetchall()
        if unmatched:
            print("  [*] ตัวอย่างไฟล์ที่ยังไม่จับคู่:")
            for row in unmatched:
                print(f"     - {row['filename']} (ปี: {row['year_folder']})")


def detect_live_photos(conn: sqlite3.Connection):
    """ตรวจหาคู่ Live Photo (ภาพ + วิดีโอ ชื่อ stem เดียวกัน)

    กลยุทธ์ 3 ระดับ:
    Pass 1: stem ตรงกันทุกประการ (เช่น IMG_2465.HEIC + IMG_2465.mp4)
    Pass 2: stem ถูกตัด - ตัวหนึ่งเป็น prefix ของอีกตัว
            (เช่น 474755...63c.jpg + 474755...63.mp4)
    Pass 3: ใช้ JSON title เป็นตัวกลาง - ภาพกับวิดีโอที่มี original stem เดียวกัน
            แต่ถูก Takeout ตัดให้สั้นลงคนละแบบ
    """
    print("\n[/] Phase 1d: ตรวจหาคู่ Live Photo...")

    cursor = conn.cursor()

    # ล้างข้อมูลเก่า
    cursor.execute("DELETE FROM live_photos")

    paired_image_ids = set()
    paired_video_ids = set()
    count = 0

    def insert_pair(image_id, video_id):
        nonlocal count
        if image_id in paired_image_ids or video_id in paired_video_ids:
            return
        cursor.execute("""
            INSERT OR IGNORE INTO live_photos (image_media_id, video_media_id)
            VALUES (?, ?)
        """, (image_id, video_id))
        if cursor.rowcount > 0:
            paired_image_ids.add(image_id)
            paired_video_ids.add(video_id)
            count += 1

    # === Pass 1: exact stem match ===
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
    pass1_pairs = cursor.fetchall()
    for pair in pass1_pairs:
        insert_pair(pair["image_id"], pair["video_id"])
    pass1_count = count
    print(f"  Pass 1 (exact stem): {pass1_count:,} คู่")

    # === Pass 2: prefix stem match (ชื่อถูกตัด) ===
    # ดึงภาพและวิดีโอที่ยังไม่ได้จับคู่
    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_image = 1
    """)
    all_images = cursor.fetchall()

    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_video = 1
    """)
    all_videos = cursor.fetchall()

    # จัดกลุ่มวิดีโอตามปี
    from collections import defaultdict
    videos_by_year = defaultdict(list)
    for v in all_videos:
        if v["id"] not in paired_video_ids:
            videos_by_year[v["year_folder"]].append(v)

    for img in all_images:
        if img["id"] in paired_image_ids:
            continue

        img_stem = img["stem"]
        year = img["year_folder"]

        # ต้องยาวพอ ป้องกัน false positive
        if len(img_stem) < 10:
            continue

        best_match = None
        best_len = 0

        for vid in videos_by_year.get(year, []):
            if vid["id"] in paired_video_ids:
                continue
            vid_stem = vid["stem"]
            if len(vid_stem) < 10:
                continue

            # เช็คว่าตัวหนึ่งเป็น prefix ของอีกตัว
            if img_stem.startswith(vid_stem) or vid_stem.startswith(img_stem):
                match_len = min(len(img_stem), len(vid_stem))
                if match_len > best_len:
                    best_match = vid
                    best_len = match_len

        if best_match:
            insert_pair(img["id"], best_match["id"])

    pass2_count = count - pass1_count
    if pass2_count > 0:
        print(f"  Pass 2 (prefix stem): {pass2_count:,} คู่")

    # === Pass 3: ใช้ JSON title เป็นตัวกลาง ===
    # ภาพกับวิดีโอที่จับคู่กับ JSON คนละตัว แต่ original title มี stem เดียวกัน
    # เช่น JSON title "IMG_2465.HEIC" → ภาพ IMG_2465.HEIC
    #      JSON title "IMG_2465.HEIC" (supplemental) → วิดีโอ IMG_2465.mp4
    # ใช้ original stem จาก title แทน stem ของไฟล์จริง
    cursor.execute("""
        SELECT m.id AS media_id, m.is_image, m.is_video, m.year_folder,
               j.title AS json_title
        FROM media_files m
        JOIN json_metadata j ON m.json_metadata_id = j.id
        WHERE m.json_metadata_id IS NOT NULL
    """)
    media_with_json = cursor.fetchall()

    # สร้าง lookup: original_stem (จาก title) → {images: [], videos: []}
    title_stem_groups = defaultdict(lambda: {"images": [], "videos": []})
    for row in media_with_json:
        if row["media_id"] in paired_image_ids or row["media_id"] in paired_video_ids:
            continue

        title = row["json_title"]
        # ดึง stem จาก title (ชื่อเต็มก่อนถูกตัด)
        original_stem = os.path.splitext(title)[0]

        key = (original_stem, row["year_folder"])
        if row["is_image"]:
            title_stem_groups[key]["images"].append(row["media_id"])
        elif row["is_video"]:
            title_stem_groups[key]["videos"].append(row["media_id"])

    for key, group in title_stem_groups.items():
        for img_id in group["images"]:
            for vid_id in group["videos"]:
                insert_pair(img_id, vid_id)

    pass3_count = count - pass1_count - pass2_count
    if pass3_count > 0:
        print(f"  Pass 3 (JSON title): {pass3_count:,} คู่")

    conn.commit()
    print(f"  [X] พบ Live Photo รวม: {count:,} คู่")


def run_phase1(root_dir: str, db_path: str = "google_photos.db"):
    """รัน Phase 1 ทั้งหมด"""
    print("=" * 50)
    print("[*] Phase 1: สแกน JSON + Media ลง SQLite")
    print(f"   Root directory: {root_dir}")
    print(f"   Database: {db_path}")
    print("=" * 50)

    if not os.path.isdir(root_dir):
        print(f"[!] ไม่พบโฟลเดอร์: {root_dir}")
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
