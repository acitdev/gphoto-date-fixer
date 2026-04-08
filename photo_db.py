"""SQLite database module for Google Takeout Photos metadata recovery.

This module provides database initialization and utilities for the
gphoto-metadata-toolkit. It manages three main tables:
  - json_metadata: Metadata from JSON files
  - media_files: Discovered image and video files
  - live_photos: Live Photo pairs (image + video)
"""

import os
import sqlite3
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "google_photos.db")
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "UTC")


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create or open a connection to the SQLite database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection object with Row factory enabled.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Initialize the database with required tables and indexes.

    Creates three tables if they don't exist:
    - json_metadata: Metadata extracted from JSON files
    - media_files: Discovered image and video files
    - live_photos: Live Photo pairs linking image and video files

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection object.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_json_title ON json_metadata(title)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_filename ON media_files(filename)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_stem ON media_files(stem)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_year ON media_files(year_folder)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_json_id ON media_files(json_metadata_id)")

    conn.commit()
    return conn


def get_stats(conn: sqlite3.Connection) -> Dict[str, any]:
    """Retrieve comprehensive statistics from the database.

    Args:
        conn: A sqlite3 database connection.

    Returns:
        A dictionary containing counts and summaries of database contents.
    """
    cursor = conn.cursor()
    stats: Dict[str, any] = {}

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


def print_stats(conn: sqlite3.Connection) -> None:
    """Print formatted database statistics to console.

    Args:
        conn: A sqlite3 database connection.
    """
    stats = get_stats(conn)
    print("\n" + "=" * 50)
    print("[*] Database Statistics")
    print("=" * 50)
    print(f"  JSON metadata:        {stats['total_json']:,} files")
    print(f"  Media files:          {stats['total_media']:,} files")
    print(f"    - Images:           {stats['total_images']:,}")
    print(f"    - Videos:           {stats['total_videos']:,}")
    print(f"  Matched:              {stats['matched']:,}")
    print(f"  Unmatched:            {stats['unmatched']:,}")
    print(f"  Metadata written:     {stats['metadata_written']:,}")
    print(f"  Live Photo pairs:     {stats['live_photo_pairs']:,}")
    print(f"  Live Photos assembled: {stats['live_photos_assembled']:,}")
    if stats["years"]:
        print(f"  Years found:          {', '.join(stats['years'])}")
    print("=" * 50)
