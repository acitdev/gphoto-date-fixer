#!/usr/bin/env python3
"""
photos.py - เครื่องมือจัดการ Google Takeout Photos

คืน metadata (วันที่, GPS) กลับเข้าไฟล์ภาพ/วิดีโอ
และประกอบ Live Photo จากไฟล์ที่ถูกแยกออกจากกัน

โครงสร้างไฟล์:
  photos.py           - ไฟล์นี้ (main entry point)
  photo_db.py         - โมดูล SQLite database
  photo_scan.py       - Phase 1: สแกน JSON + Media ลง SQLite
  photo_metadata.py   - Phase 2: เขียน metadata กลับเข้าไฟล์
  photo_livephoto.py  - Phase 3: ประกอบ Live Photo

ข้อกำหนด:
  - Python 3.10+
  - ExifTool: brew install exiftool

การใช้งาน:
  # รัน Phase 1: สแกนข้อมูลทั้งหมด
  python photos.py scan /path/to/takeout

  # รัน Phase 2: เขียน metadata กลับ (ลองก่อนด้วย --dry-run)
  python photos.py metadata --dry-run
  python photos.py metadata
  python photos.py metadata --year 2022

  # รัน Phase 3: ประกอบ Live Photo
  python photos.py livephoto --dry-run
  python photos.py livephoto

  # รันทุก Phase ตามลำดับ
  python photos.py all /path/to/takeout

  # ดูสถิติ
  python photos.py stats

  # Query ข้อมูลในฐานข้อมูล
  python photos.py query --unmatched
  python photos.py query --year 2023 --has-gps

ตัวอย่างโครงสร้าง Takeout:
  /path/to/takeout/
  ├── 2022/
  │   ├── IMG_0001.HEIC
  │   ├── IMG_0001.HEIC.supplemental-metadata.json
  │   ├── IMG_0001.mp4
  │   ├── 3aae020f...mov
  │   └── 3aae020f...mov.supplemen.json
  ├── 2023/
  │   └── ...
  ├── 2024/
  │   └── ...
  └── 2025/
      └── ...
"""

import argparse
import sys
import os

from photo_db import init_db, get_connection, print_stats


def cmd_scan(args):
    """Phase 1: สแกน JSON + Media ลง SQLite"""
    from photo_scan import run_phase1
    run_phase1(args.root_dir, args.db)


def cmd_metadata(args):
    """Phase 2: เขียน metadata กลับเข้าไฟล์"""
    from photo_metadata import run_phase2
    run_phase2(args.db, args.dry_run, args.year, args.limit)


def cmd_livephoto(args):
    """Phase 3: ประกอบ Live Photo"""
    from photo_livephoto import run_phase3
    run_phase3(args.db, args.dry_run, args.year)


def cmd_all(args):
    """รันทุก Phase ตามลำดับ"""
    print("🏁 เริ่มทำงานทุก Phase ตามลำดับ\n")

    # Phase 1
    from photo_scan import run_phase1
    run_phase1(args.root_dir, args.db)

    # Phase 2
    from photo_metadata import run_phase2
    run_phase2(args.db, args.dry_run, args.year, args.limit)

    # Phase 3
    from photo_livephoto import run_phase3
    run_phase3(args.db, args.dry_run, args.year)

    print("\n🎉 ทำงานครบทุก Phase แล้ว!")


def cmd_stats(args):
    """แสดงสถิติ"""
    if not os.path.exists(args.db):
        print(f"❌ ไม่พบ database: {args.db}")
        print("   กรุณารัน phase 'scan' ก่อน")
        return
    conn = get_connection(args.db)
    print_stats(conn)
    conn.close()


def cmd_query(args):
    """Query ข้อมูลในฐานข้อมูล"""
    if not os.path.exists(args.db):
        print(f"❌ ไม่พบ database: {args.db}")
        return

    conn = get_connection(args.db)
    cursor = conn.cursor()

    if args.unmatched:
        print("\n📋 ไฟล์ media ที่ยังไม่ได้จับคู่กับ JSON:")
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
        print(f"\n  รวม: {len(rows):,} ไฟล์")

    elif args.unmatched_json:
        print("\n📋 JSON metadata ที่ไม่มีไฟล์ media จับคู่:")
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
        print(f"\n  รวม: {len(rows):,} ไฟล์")

    elif args.live_photos:
        print("\n📸 คู่ Live Photo ที่พบ:")
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
            status = "✅" if row["assembled"] else "⏳"
            cid = row["cid"][:8] + "..." if row["cid"] else "—"
            print(f"  {status} {row['year_folder'] or '??'} | {row['img']} + {row['vid']} [{cid}]")
        print(f"\n  รวม: {len(rows):,} คู่")

    else:
        # แสดงภาพรวมตามปี
        print("\n📊 สรุปตามปี:")
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
        print(f"  {'ปี':<8} {'รวม':>8} {'ภาพ':>8} {'วิดีโอ':>8} {'จับคู่':>8} {'เขียนแล้ว':>10}")
        print(f"  {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 10}")
        for row in rows:
            yr = row["year_folder"] or "ไม่ทราบ"
            print(f"  {yr:<8} {row['total']:>8,} {row['images']:>8,} {row['videos']:>8,} {row['matched']:>8,} {row['written']:>10,}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Google Takeout Photos - คืน metadata และประกอบ Live Photo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ตัวอย่างการใช้งาน:
  python photos.py scan ~/Takeout           # สแกนข้อมูล
  python photos.py metadata --dry-run       # ทดลองเขียน metadata
  python photos.py metadata                 # เขียน metadata จริง
  python photos.py livephoto                # ประกอบ Live Photo
  python photos.py all ~/Takeout --dry-run  # รันทุก phase (ทดลอง)
  python photos.py stats                    # ดูสถิติ
  python photos.py query --unmatched        # ดูไฟล์ที่ไม่ได้จับคู่
        """
    )
    parser.add_argument("--db", default="google_photos.db", help="ที่อยู่ไฟล์ SQLite database")

    subparsers = parser.add_subparsers(dest="command", help="คำสั่งที่ต้องการ")

    # scan
    p_scan = subparsers.add_parser("scan", help="Phase 1: สแกน JSON + Media ลง SQLite")
    p_scan.add_argument("root_dir", help="โฟลเดอร์ root ที่มีโฟลเดอร์ปี")
    p_scan.set_defaults(func=cmd_scan)

    # metadata
    p_meta = subparsers.add_parser("metadata", help="Phase 2: เขียน metadata กลับเข้าไฟล์")
    p_meta.add_argument("--dry-run", action="store_true", help="แสดงคำสั่งแต่ไม่แก้ไฟล์จริง")
    p_meta.add_argument("--year", help="กรองเฉพาะปี เช่น 2022")
    p_meta.add_argument("--limit", type=int, help="จำกัดจำนวนไฟล์")
    p_meta.set_defaults(func=cmd_metadata)

    # livephoto
    p_live = subparsers.add_parser("livephoto", help="Phase 3: ประกอบ Live Photo")
    p_live.add_argument("--dry-run", action="store_true", help="แสดงคำสั่งแต่ไม่แก้ไฟล์จริง")
    p_live.add_argument("--year", help="กรองเฉพาะปี เช่น 2022")
    p_live.set_defaults(func=cmd_livephoto)

    # all
    p_all = subparsers.add_parser("all", help="รันทุก Phase ตามลำดับ")
    p_all.add_argument("root_dir", help="โฟลเดอร์ root ที่มีโฟลเดอร์ปี")
    p_all.add_argument("--dry-run", action="store_true", help="แสดงคำสั่งแต่ไม่แก้ไฟล์จริง")
    p_all.add_argument("--year", help="กรองเฉพาะปี เช่น 2022")
    p_all.add_argument("--limit", type=int, help="จำกัดจำนวนไฟล์ (Phase 2)")
    p_all.set_defaults(func=cmd_all)

    # stats
    p_stats = subparsers.add_parser("stats", help="แสดงสถิติ")
    p_stats.set_defaults(func=cmd_stats)

    # query
    p_query = subparsers.add_parser("query", help="Query ข้อมูลในฐานข้อมูล")
    p_query.add_argument("--unmatched", action="store_true", help="แสดงไฟล์ media ที่ไม่ได้จับคู่")
    p_query.add_argument("--unmatched-json", action="store_true", help="แสดง JSON ที่ไม่มีไฟล์ media")
    p_query.add_argument("--live-photos", action="store_true", help="แสดงคู่ Live Photo")
    p_query.set_defaults(func=cmd_query)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
