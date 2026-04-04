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
import shutil
import subprocess
import sqlite3
import uuid
from typing import Optional

from photo_db import get_connection, print_stats


def check_exiftool() -> bool:
    """ตรวจสอบว่าติดตั้ง ExifTool แล้วหรือยัง"""
    if shutil.which("exiftool") is None:
        print("❌ ไม่พบ ExifTool!")
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
        return True

    # เขียนลงไฟล์ภาพ
    try:
        result = subprocess.run(img_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"    ⚠️  ExifTool error (ภาพ): {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"    ⚠️  Error (ภาพ): {e}")
        return False

    # เขียนลงไฟล์วิดีโอ
    try:
        result = subprocess.run(vid_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"    ⚠️  ExifTool error (วิดีโอ): {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"    ⚠️  Error (วิดีโอ): {e}")
        return False

    return True


def run_phase3(
    db_path: str = "google_photos.db",
    dry_run: bool = False,
    year_filter: Optional[str] = None,
):
    """รัน Phase 3: ประกอบ Live Photo ทั้งหมด"""
    print("=" * 50)
    print("🚀 Phase 3: ประกอบ Live Photo")
    print(f"   Database: {db_path}")
    if dry_run:
        print("   ⚡ DRY RUN MODE - ไม่แก้ไขไฟล์จริง")
    if year_filter:
        print(f"   📅 กรองปี: {year_filter}")
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
        print("\n✅ ไม่มีคู่ Live Photo ที่ต้องประกอบ")
        return

    print(f"\n📸 ต้องประกอบ Live Photo: {total:,} คู่")

    success = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        image_path = row["image_path"]
        video_path = row["video_path"]
        image_name = row["image_name"]
        video_name = row["video_name"]

        # ตรวจสอบว่าทั้งสองไฟล์ยังอยู่
        if not os.path.isfile(image_path):
            print(f"  ⚠️  [{i}/{total}] ไม่พบไฟล์ภาพ: {image_path}")
            failed += 1
            continue
        if not os.path.isfile(video_path):
            print(f"  ⚠️  [{i}/{total}] ไม่พบไฟล์วิดีโอ: {video_path}")
            failed += 1
            continue

        print(f"  [{i}/{total}] {image_name} + {video_name}")

        # ตรวจสอบ ContentIdentifier ที่มีอยู่แล้ว
        existing_img_id = read_existing_content_id(image_path) if not dry_run else None
        existing_vid_id = read_existing_content_id(video_path) if not dry_run else None

        if existing_img_id and existing_vid_id and existing_img_id == existing_vid_id:
            # ทั้งคู่มี ID เหมือนกันแล้ว ไม่ต้องทำอะไร
            content_id = existing_img_id
            print(f"    ✅ มี ContentIdentifier อยู่แล้ว: {content_id}")
        elif existing_img_id:
            # ใช้ ID จากภาพ (เพราะภาพเป็น primary)
            content_id = existing_img_id
            print(f"    🔄 ใช้ ContentIdentifier จากภาพ: {content_id}")
        elif existing_vid_id:
            # ใช้ ID จากวิดีโอ
            content_id = existing_vid_id
            print(f"    🔄 ใช้ ContentIdentifier จากวิดีโอ: {content_id}")
        else:
            # สร้าง UUID ใหม่
            content_id = str(uuid.uuid4()).upper()
            print(f"    🆕 สร้าง ContentIdentifier ใหม่: {content_id}")

        ok = write_content_identifier(
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

                if i % 20 == 0:
                    conn.commit()
        else:
            failed += 1

    conn.commit()

    print(f"\n✅ สำเร็จ: {success:,} คู่")
    if failed > 0:
        print(f"⚠️  ล้มเหลว: {failed:,} คู่")

    print("\n💡 ขั้นตอนต่อไป:")
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
