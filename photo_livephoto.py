"""
photo_livephoto.py - Phase 3: ประกอบ Live Photo

Apple Live Photo ต้องการ:
  1. ไฟล์ภาพ (HEIC/JPG) + ไฟล์วิดีโอ (MP4/MOV) ที่ชื่อ stem เดียวกัน
  2. ทั้งสองไฟล์ต้องมี "Content Identifier" (UUID) เหมือนกัน ฝังใน metadata
     - ภาพ: MakerNotes:ContentIdentifier (Apple MakerNotes)
     - วิดีโอ: QuickTime:ContentIdentifier

วิธีใช้:
  python photo_livephoto.py
  python photo_livephoto.py --db photos.db --dry-run
  python photo_livephoto.py --year 2022

หมายเหตุ:
  - ต้องรัน Phase 1 ก่อน (เพื่อสร้าง live_photos table)
  - ต้องรัน Phase 2 ก่อน (เพื่อเขียน metadata วันที่/GPS)
  - ต้องติดตั้ง ExifTool: brew install exiftool
  - หลังจากรัน Phase 3 แล้ว ต้อง import เข้า Apple Photos app
    โดยเลือกทั้งไฟล์ภาพและวิดีโอพร้อมกัน Photos app จะรวมเป็น Live Photo อัตโนมัติ
"""

import os
import re
import shutil
import subprocess
import sqlite3
import sys
import uuid
from typing import Optional

from photo_db import get_connection, print_stats


# regex จับ error "Not a valid X (looks more like a Y)"
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
    """ถ้า ExifTool บอกว่านามสกุลไม่ตรงกับเนื้อไฟล์จริง ให้เปลี่ยนนามสกุล"""
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
    """แสดง progress แบบเขียนทับบรรทัดเดิม"""
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    sys.stdout.write(f"\r{text[:terminal_width]:<{terminal_width}}")
    sys.stdout.flush()


def progress_error(text: str):
    """แสดง error แยกบรรทัดใหม่"""
    sys.stdout.write(f"\n{text}\n")
    sys.stdout.flush()


def check_exiftool() -> bool:
    """ตรวจสอบว่าติดตั้ง ExifTool แล้วหรือยัง"""
    if shutil.which("exiftool") is None:
        print("[!] ไม่พบ ExifTool!")
        print("   ติดตั้งด้วย: brew install exiftool")
        return False
    return True


def read_existing_content_id(filepath: str) -> Optional[str]:
    """อ่าน ContentIdentifier ที่มีอยู่แล้วในไฟล์ (ถ้ามี)

    บางไฟล์อาจมี ContentIdentifier ฝังอยู่แล้วจากตอนถ่าย
    ถ้ามี ให้ใช้ค่าเดิม ไม่ต้องสร้างใหม่
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
) -> bool:
    """ฝัง ContentIdentifier ลงในทั้งไฟล์ภาพและวิดีโอ

    Args:
        image_path: ที่อยู่ไฟล์ภาพ (HEIC/JPG)
        video_path: ที่อยู่ไฟล์วิดีโอ (MP4/MOV)
        content_id: UUID ที่จะฝัง
        dry_run: แสดงคำสั่งแต่ไม่แก้ไฟล์จริง

    Returns:
        True ถ้าสำเร็จทั้งคู่
    """
    # คำสั่งสำหรับไฟล์ภาพ
    img_ext = os.path.splitext(image_path)[1].lower()

    if img_ext in (".heic", ".heif"):
        # HEIC: ใช้ MakerNotes (Apple specific)
        img_cmd = [
            "exiftool",
            "-overwrite_original",
            f"-MakerNotes:ContentIdentifier={content_id}",
            image_path,
        ]
    else:
        # JPG: ใช้ ImageUniqueID ใน EXIF + MakerNotes
        img_cmd = [
            "exiftool",
            "-overwrite_original",
            f"-MakerNotes:ContentIdentifier={content_id}",
            f"-ImageUniqueID={content_id}",
            image_path,
        ]

    # คำสั่งสำหรับไฟล์วิดีโอ
    vid_cmd = [
        "exiftool",
        "-overwrite_original",
        f"-QuickTime:ContentIdentifier={content_id}",
        video_path,
    ]

    if dry_run:
        print(f"    [DRY RUN] {' '.join(img_cmd)}")
        print(f"    [DRY RUN] {' '.join(vid_cmd)}")
        return True, None, None

    new_image_path = None
    new_video_path = None

    # ลบ temp file ค้างจากรอบก่อน (ถ้ามี)
    for fp in (image_path, video_path):
        tmp = fp + "_exiftool_tmp"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    # เขียนลงไฟล์ภาพ
    try:
        result = subprocess.run(img_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # ลอง rename ถ้านามสกุลไม่ตรง
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
                        progress_error(f"  [!]  ExifTool error (ภาพ retry): {result2.stderr.strip()}")
                        return False, new_image_path, new_video_path
                else:
                    progress_error(f"  [!]  ExifTool error (ภาพ): {stderr}")
                    return False, new_image_path, new_video_path
            else:
                progress_error(f"  [!]  ExifTool error (ภาพ): {stderr}")
                return False, new_image_path, new_video_path
    except Exception as e:
        progress_error(f"  [!]  Error (ภาพ): {e}")
        return False, new_image_path, new_video_path

    # เขียนลงไฟล์วิดีโอ
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
                        progress_error(f"  [!]  ExifTool error (วิดีโอ retry): {result2.stderr.strip()}")
                        return False, new_image_path, new_video_path
                else:
                    progress_error(f"  [!]  ExifTool error (วิดีโอ): {stderr}")
                    return False, new_image_path, new_video_path
            else:
                progress_error(f"  [!]  ExifTool error (วิดีโอ): {stderr}")
                return False, new_image_path, new_video_path
    except Exception as e:
        progress_error(f"  [!]  Error (วิดีโอ): {e}")
        return False, new_image_path, new_video_path

    return True, new_image_path, new_video_path


def run_phase3(
    db_path: str = "google_photos.db",
    dry_run: bool = False,
    year_filter: Optional[str] = None,
):
    """รัน Phase 3: ประกอบ Live Photo ทั้งหมด"""
    print("=" * 50)
    print("[*] Phase 3: ประกอบ Live Photo")
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

    # ดึงคู่ Live Photo ที่ยังไม่ได้ประกอบ
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
        print("\n[X] ไม่มีคู่ Live Photo ที่ต้องประกอบ")
        return

    print(f"\n[/] ต้องประกอบ Live Photo: {total:,} คู่")

    success = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        image_path = row["image_path"]
        video_path = row["video_path"]
        image_name = row["image_name"]
        video_name = row["video_name"]

        # ตรวจสอบว่าทั้งสองไฟล์ยังอยู่
        if not os.path.isfile(image_path):
            progress_error(f"  [!]  [{i}/{total}] ไม่พบไฟล์ภาพ: {image_path}")
            failed += 1
            continue
        if not os.path.isfile(video_path):
            progress_error(f"  [!]  [{i}/{total}] ไม่พบไฟล์วิดีโอ: {video_path}")
            failed += 1
            continue

        # แสดง progress (เขียนทับบรรทัดเดิม)
        pct = i * 100 // total
        progress(f"  [{i:,}/{total:,}] {pct}% {image_name} + {video_name}")

        # ตรวจสอบ ContentIdentifier ที่มีอยู่แล้ว
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
            image_path, video_path, content_id, dry_run
        )

        if ok:
            success += 1
            if not dry_run:
                cursor.execute("""
                    UPDATE live_photos
                    SET content_identifier = ?, assembled = 1
                    WHERE id = ?
                """, (content_id, row["lp_id"]))

                # อัปเดต database ถ้าไฟล์ถูก rename
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

    # จบ progress line
    progress(f"  [{total:,}/{total:,}] 100% เสร็จสิ้น")
    print()

    print(f"\n[X] สำเร็จ: {success:,} คู่")
    if failed > 0:
        print(f"[!]  ล้มเหลว: {failed:,} คู่")

    print("\n[*] ขั้นตอนต่อไป:")
    print("   1. เปิด Apple Photos app")
    print("   2. ลากไฟล์ภาพ + วิดีโอ (ที่เป็นคู่กัน) เข้าไปพร้อมกัน")
    print("   3. Photos app จะรวมเป็น Live Photo อัตโนมัติ")
    print("   (เพราะทั้งคู่มี ContentIdentifier ตรงกัน)")

    print_stats(conn)
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 3: ประกอบ Live Photo (ฝัง ContentIdentifier)"
    )
    parser.add_argument("--db", default="google_photos.db", help="ที่อยู่ไฟล์ SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="แสดงคำสั่งแต่ไม่แก้ไฟล์จริง")
    parser.add_argument("--year", help="กรองเฉพาะปี เช่น 2022")
    args = parser.parse_args()

    run_phase3(args.db, args.dry_run, args.year)
