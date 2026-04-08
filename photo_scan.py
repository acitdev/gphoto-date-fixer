"""Phase 1: Scan Google Takeout JSON metadata and media files into SQLite.

This module performs comprehensive scanning and matching of Google Takeout archives:
  1. Scans all year folders (2022/, 2023/, ...) for JSON metadata files
  2. Parses and stores metadata in json_metadata table
  3. Scans image and video files into media_files table
  4. Matches media files with JSON metadata using multi-pass algorithms
  5. Detects Live Photo pairs (image + video with matching stems)

Usage:
  python photo_scan.py /path/to/takeout/root
  python photo_scan.py /path/to/takeout/root --db photos.db
"""

import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from photo_db import init_db, print_stats

load_dotenv()
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "google_photos.db")

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

JSON_SUFFIX_PATTERNS = [
    ".supplemental-metadata.json",
    ".supplemen.json",
    ".json",
]


def detect_year_from_path(filepath: str) -> Optional[str]:
    """Extract year from file path.

    Args:
        filepath: File path potentially containing a year folder.

    Returns:
        Year as string (e.g., '2022') or None if not found.
    """
    parts = Path(filepath).parts
    for part in parts:
        match = re.search(r"(20[0-9]{2})", part)
        if match:
            return match.group(1)
    return None


def parse_json_metadata(json_path: str) -> Optional[dict]:
    """Parse Google Takeout JSON metadata file.

    Args:
        json_path: Path to JSON metadata file.

    Returns:
        Dictionary with extracted metadata or None if file is invalid/unparseable.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError) as e:
        print(f"  [!] Failed to read JSON: {json_path} ({e})")
        return None

    title = data.get("title", "")
    if not title:
        return None

    creation_ts = None
    photo_taken_ts = None
    if "creationTime" in data and "timestamp" in data["creationTime"]:
        creation_ts = int(data["creationTime"]["timestamp"])
    if "photoTakenTime" in data and "timestamp" in data["photoTakenTime"]:
        photo_taken_ts = int(data["photoTakenTime"]["timestamp"])

    lat, lon, alt = None, None, None
    geo = data.get("geoData") or data.get("geoDataExif")
    if geo:
        lat = geo.get("latitude")
        lon = geo.get("longitude")
        alt = geo.get("altitude")
        # Google Takeout uses 0.0 for missing GPS data
        if lat == 0.0 and lon == 0.0:
            lat, lon, alt = None, None, None

    people = []
    if "people" in data:
        people = [p.get("name", "") for p in data["people"] if p.get("name")]

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
    """Scan and store all JSON metadata files into database.

    Args:
        conn: SQLite database connection.
        root_dir: Root directory of Google Takeout archive.
    """
    print("\n[/] Phase 1a: Scanning JSON metadata files...")

    root = Path(root_dir)
    count = 0
    skipped = 0

    for json_path in sorted(root.rglob("*.json")):
        json_str = str(json_path)
        year = detect_year_from_path(json_str)

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
            print(f"  [!] DB error: {json_path} ({e})")

    conn.commit()
    print(f"  [X] Stored JSON metadata: {count:,} files (skipped {skipped:,})")


def scan_media_files(conn: sqlite3.Connection, root_dir: str):
    """Scan and store all image and video files into database.

    Args:
        conn: SQLite database connection.
        root_dir: Root directory of Google Takeout archive.
    """
    print("\n[/] Phase 1b: Scanning image and video files...")

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
            print(f"  [!] DB error: {entry} ({e})")

    conn.commit()
    print(f"  [X] Stored media files: {count:,} files")


def match_metadata_to_media(conn: sqlite3.Connection):
    """Match JSON metadata to media files using multi-pass algorithm.

    Matching strategy (by precision order):
    1. Exact filename match + same year
    2. Matching stem (filename without extension) + same year
    3. Case-insensitive filename match + same year
    4. Truncated filename matching
    5. Live Photo video companions
    6. Duplicate numbering and edited versions
    """
    print("\n[/] Phase 1c: Matching JSON metadata to media files...")

    cursor = conn.cursor()

    # Pass 1: Exact and case-insensitive filename matching
    cursor.execute("SELECT id, title, year_folder FROM json_metadata")
    json_rows = cursor.fetchall()

    matched = 0
    unmatched_json = []

    for row in json_rows:
        json_id = row["id"]
        title = row["title"]
        year = row["year_folder"]

        # Exact match
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE filename = ? AND year_folder = ? AND json_metadata_id IS NULL
        """, (json_id, title, year))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        # Stem match (handles file extension changes like HEIC→jpg)
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

        # Case-insensitive match
        cursor.execute("""
            UPDATE media_files SET json_metadata_id = ?
            WHERE LOWER(filename) = LOWER(?) AND year_folder = ?
                  AND json_metadata_id IS NULL
        """, (json_id, title, year))

        if cursor.rowcount > 0:
            matched += cursor.rowcount
            continue

        unmatched_json.append(row)

    conn.commit()

    # Pass 2: Truncated filename matching
    # Google Takeout truncates long filenames in both JSON titles and actual files.
    # Strategy: For each unmatched JSON entry, find media files whose stem is a
    # prefix of (or vice versa) the JSON title stem.

    if unmatched_json:
        print(f"  [/] Pass 2: Matching truncated filenames ({len(unmatched_json):,} remaining)...")

        cursor.execute("""
            SELECT id, stem, extension, year_folder, filename
            FROM media_files
            WHERE json_metadata_id IS NULL
        """)
        unmatched_media = cursor.fetchall()

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

            candidates = media_lookup.get((year, title_ext), [])
            best_match = None
            best_len = 0

            for m in candidates:
                media_stem = m["stem"]
                # Match when media stem is prefix of title stem (media filename was truncated)
                if (len(media_stem) >= 10
                        and title_stem.startswith(media_stem)
                        and len(media_stem) > best_len):
                    best_match = m
                    best_len = len(media_stem)

            if best_match is None:
                # Try reverse: title stem is prefix of media stem (title was truncated)
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
                    candidates.remove(best_match)

        conn.commit()
        matched += pass2_matched
        print(f"  [X] Pass 2 matched: {pass2_matched:,} files")

    # Pass 3: Live Photo video companions
    # Google Takeout doesn't create separate JSON for Live Photo videos.
    # Find videos with matching stems to already-matched images.
    print("  [/] Pass 3: Matching Live Photo videos to image JSON...")

    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_video = 1 AND json_metadata_id IS NULL
    """)
    orphan_videos = cursor.fetchall()

    if orphan_videos:
        cursor.execute("""
            SELECT id, stem, year_folder, json_metadata_id FROM media_files
            WHERE is_image = 1 AND json_metadata_id IS NOT NULL
        """)
        matched_images = cursor.fetchall()

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

            json_id = exact_lookup.get((year, vid_stem))

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
        print(f"  [X] Pass 3 Live Photo videos matched: {pass3_matched:,} files")

    # Pass 4: Duplicate numbering (N) and edited versions
    # Google Takeout marks duplicates with (N) suffix and edited versions with -suffix.
    # Pattern 4a: (N) duplicates
    #   JSON: IMG_3557.JPG.supplemental-metadata(1).json → title: IMG_3557.JPG
    #   Media: IMG_3557(1).JPG
    # Pattern 4b: Edited versions (-แก้ไข, -edited, -edit, etc.)
    #   Media: IMG_3557-edited.JPG should match JSON from IMG_3557.JPG

    print("  [/] Pass 4: Matching duplicates (N) and edited versions...")

    dup_pattern = re.compile(r"^(.*)\((\d+)\)$")
    edit_suffixes = ["-แก้ไข", "-edited", "-edit", "-EDIT", "-Edited"]

    def strip_edit_suffix(stem: str) -> Optional[str]:
        """Remove edit suffix from stem, preserving duplicate numbering."""
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
        """Extract (N) number from JSON filepath."""
        m = re.search(r"\((\d+)\)\.json$", path)
        if m:
            return m.group(1)
        return None

    cursor.execute("""
        SELECT id, filename, stem, extension, year_folder
        FROM media_files
        WHERE json_metadata_id IS NULL
    """)
    orphan_media = cursor.fetchall()

    pass4_matched = 0

    cursor.execute("""
        SELECT id, title, year_folder, json_filepath
        FROM json_metadata
    """)
    all_json = cursor.fetchall()

    json_lookup = {}
    for j in all_json:
        dup_num = extract_dup_number(j["json_filepath"]) or ""
        key = (j["year_folder"], j["title"].lower(), dup_num)
        json_lookup[key] = j["id"]
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

        # Try (N) duplicate match: e.g. "IMG_3557(1).JPG" → find JSON with title
        # "IMG_3557.JPG" whose filepath contains "(1).json"
        dup_match = dup_pattern.match(stem)
        if dup_match:
            base_stem = dup_match.group(1)
            dup_num = dup_match.group(2)
            expected_title = (base_stem + ext).lower()

            json_id = json_lookup.get((year, expected_title, dup_num))
            if json_id is None:
                json_id = json_lookup.get((None, expected_title, dup_num))

        # Attempt strip edit suffix
        if json_id is None:
            cleaned_stem = strip_edit_suffix(stem)
            if cleaned_stem:
                dup_match2 = dup_pattern.match(cleaned_stem)
                if dup_match2:
                    base_stem = dup_match2.group(1)
                    dup_num = dup_match2.group(2)
                    expected_title = (base_stem + ext).lower()
                    json_id = (json_lookup.get((year, expected_title, dup_num))
                               or json_lookup.get((None, expected_title, dup_num)))
                else:
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
    print(f"  [X] Pass 4 duplicates/edited matched: {pass4_matched:,} files")

    # Pass 5: Leftover Live Photo videos (after Pass 4)
    # Pass 3 runs before Pass 4, so duplicate-numbered videos that match images
    # found in Pass 4 may be missed. This pass repeats Pass 3 logic.

    cursor.execute("""
        SELECT id, stem, year_folder FROM media_files
        WHERE is_video = 1 AND json_metadata_id IS NULL
    """)
    orphan_videos_pass5 = cursor.fetchall()

    if orphan_videos_pass5:
        print("  [/] Pass 5: Finding leftover Live Photo videos (after Pass 4)...")

        cursor.execute("""
            SELECT id, stem, year_folder, json_metadata_id FROM media_files
            WHERE is_image = 1 AND json_metadata_id IS NOT NULL
        """)
        matched_imgs = cursor.fetchall()

        exact_lk = {}
        prefix_cands = defaultdict(list)
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

            json_id = exact_lk.get((year, vid_stem))

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
        print(f"  [X] Pass 5 leftover videos matched: {pass5_matched:,} files")

    # Summary statistics
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NOT NULL")
    total_matched = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NULL")
    total_unmatched = cursor.fetchone()[0]

    print(f"  [X] Successfully matched: {total_matched:,} files")
    if total_unmatched > 0:
        print(f"  [!] Unmatched: {total_unmatched:,} files")

        cursor.execute("""
            SELECT filename, year_folder FROM media_files
            WHERE json_metadata_id IS NULL LIMIT 10
        """)
        unmatched = cursor.fetchall()
        if unmatched:
            print("  [*] Sample unmatched files:")
            for row in unmatched:
                print(f"     - {row['filename']} (year: {row['year_folder']})")


def detect_live_photos(conn: sqlite3.Connection):
    """Detect Live Photo pairs (image + video with matching stems).

    Uses 3-pass algorithm:
    Pass 1: Exact stem match (e.g., IMG_2465.HEIC + IMG_2465.mp4)
    Pass 2: Truncated stem match (e.g., 474755...63c.jpg + 474755...63.mp4)
    Pass 3: Original JSON title (handles different truncation for image vs video)
    """
    print("\n[/] Phase 1d: Detecting Live Photo pairs...")

    cursor = conn.cursor()

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

    # Pass 1: Exact stem match
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
    print(f"  Pass 1 (exact stem): {pass1_count:,} pairs")

    # Pass 2: Prefix stem match (for truncated filenames)
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

    videos_by_year = defaultdict(list)
    for v in all_videos:
        if v["id"] not in paired_video_ids:
            videos_by_year[v["year_folder"]].append(v)

    for img in all_images:
        if img["id"] in paired_image_ids:
            continue

        img_stem = img["stem"]
        year = img["year_folder"]

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

            if img_stem.startswith(vid_stem) or vid_stem.startswith(img_stem):
                match_len = min(len(img_stem), len(vid_stem))
                if match_len > best_len:
                    best_match = vid
                    best_len = match_len

        if best_match:
            insert_pair(img["id"], best_match["id"])

    pass2_count = count - pass1_count
    if pass2_count > 0:
        print(f"  Pass 2 (prefix stem): {pass2_count:,} pairs")

    # Pass 3: Using JSON title as intermediary
    # Images and videos matched to different JSON entries but with same original stem
    cursor.execute("""
        SELECT m.id AS media_id, m.is_image, m.is_video, m.year_folder,
               j.title AS json_title
        FROM media_files m
        JOIN json_metadata j ON m.json_metadata_id = j.id
        WHERE m.json_metadata_id IS NOT NULL
    """)
    media_with_json = cursor.fetchall()

    title_stem_groups = defaultdict(lambda: {"images": [], "videos": []})
    for row in media_with_json:
        if row["media_id"] in paired_image_ids or row["media_id"] in paired_video_ids:
            continue

        title = row["json_title"]
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
        print(f"  Pass 3 (JSON title): {pass3_count:,} pairs")

    conn.commit()
    print(f"  [X] Total Live Photo pairs found: {count:,} pairs")


def run_phase1(root_dir: str, db_path: str = DEFAULT_DB_PATH):
    """Execute Phase 1: scan JSON metadata and media files into SQLite database.

    Args:
        root_dir: Root directory of Google Takeout archive.
        db_path: Path to SQLite database file.
    """
    print("=" * 50)
    print("[*] Phase 1: Scanning JSON and media into SQLite")
    print(f"   Root directory: {root_dir}")
    print(f"   Database: {db_path}")
    print("=" * 50)

    if not os.path.isdir(root_dir):
        print(f"[!] Directory not found: {root_dir}")
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
        description="Phase 1: Scan Google Takeout JSON metadata and media files into SQLite"
    )
    parser.add_argument("root_dir", help="Root directory containing year folders (2022/, 2023/, ...)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database file")
    args = parser.parse_args()

    run_phase1(args.root_dir, args.db)
