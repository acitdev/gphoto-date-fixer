"""
photo_db.py - โมดูล SQLite Database สำหรับจัดการ metadata ของ Google Takeout Photos

ตาราง:
  - json_metadata: เก็บข้อมูล metadata จากไฟล์ JSON
  - media_files:   เก็บรายชื่อไฟล์ภาพ/วิดีโอที่สแกนได้
  - live_photos:   เก็บคู่ Live Photo (ภาพ + วิดีโอ)
"""

import sqlite3
import os

DEFAULT_DB_PATH = "google_photos.db"


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """สร้างหรือเปิด connection ไปยัง SQLite database"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """สร้างตารางทั้งหมดใน database (ถ้ายังไม่มี)"""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # ตาราง json_metadata - เก็บข้อมูลจากไฟล์ JSON
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS json_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            json_filepath TEXT UNIQUE NOT NULL,
            year_folder TEXT,
            title TEXT,
            description TEXT,
            creation_timestamp INTEGER,
            photo_taken_timestamp INTEGER,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            people TEXT,
            url TEXT,
            device_type TEXT,
            raw_json TEXT
        )
    """)

    # ตาราง media_files - เก็บรายชื่อไฟล์ภาพ/วิดีโอ
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS media_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            stem TEXT NOT NULL,
            extension TEXT NOT NULL,
            year_folder TEXT,
            file_size INTEGER,
            is_image INTEGER DEFAULT 0,
            is_video INTEGER DEFAULT 0,
            json_metadata_id INTEGER,
            metadata_written INTEGER DEFAULT 0,
            FOREIGN KEY (json_metadata_id) REFERENCES json_metadata(id)
        )
    """)

    # ตาราง live_photos - เก็บคู่ Live Photo
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_media_id INTEGER NOT NULL,
            video_media_id INTEGER NOT NULL,
            content_identifier TEXT,
            assembled INTEGER DEFAULT 0,
            FOREIGN KEY (image_media_id) REFERENCES media_files(id),
            FOREIGN KEY (video_media_id) REFERENCES media_files(id)
        )
    """)

    # Indexes สำหรับ query ที่ใช้บ่อย
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_json_title ON json_metadata(title)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_filename ON media_files(filename)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_stem ON media_files(stem)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_year ON media_files(year_folder)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_json_id ON media_files(json_metadata_id)")

    conn.commit()
    return conn


def get_stats(conn: sqlite3.Connection) -> dict:
    """ดึงสถิติรวมของ database"""
    cursor = conn.cursor()
    stats = {}

    cursor.execute("SELECT COUNT(*) FROM json_metadata")
    stats["total_json"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files")
    stats["total_media"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files WHERE is_image = 1")
    stats["total_images"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files WHERE is_video = 1")
    stats["total_videos"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NOT NULL")
    stats["matched"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files WHERE json_metadata_id IS NULL")
    stats["unmatched"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM media_files WHERE metadata_written = 1")
    stats["metadata_written"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM live_photos")
    stats["live_photo_pairs"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM live_photos WHERE assembled = 1")
    stats["live_photos_assembled"] = cursor.fetchone()[0]

    cursor.execute("SELECT DISTINCT year_folder FROM json_metadata WHERE year_folder IS NOT NULL ORDER BY year_folder")
    stats["years"] = [row[0] for row in cursor.fetchall()]

    return stats


def print_stats(conn: sqlite3.Connection):
    """แสดงสถิติรวมของ database"""
    stats = get_stats(conn)
    print("\n" + "=" * 50)
    print("📊 สถิติ Database")
    print("=" * 50)
    print(f"  JSON metadata:      {stats['total_json']:,} ไฟล์")
    print(f"  Media files:        {stats['total_media']:,} ไฟล์")
    print(f"    - ภาพ:            {stats['total_images']:,}")
    print(f"    - วิดีโอ:         {stats['total_videos']:,}")
    print(f"  จับคู่แล้ว:         {stats['matched']:,}")
    print(f"  ยังไม่ได้จับคู่:     {stats['unmatched']:,}")
    print(f"  เขียน metadata แล้ว: {stats['metadata_written']:,}")
    print(f"  Live Photo pairs:   {stats['live_photo_pairs']:,}")
    print(f"  Live Photo assembled: {stats['live_photos_assembled']:,}")
    if stats["years"]:
        print(f"  ปีที่พบ:            {', '.join(stats['years'])}")
    print("=" * 50)
