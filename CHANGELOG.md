# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-10

### Added

- `--verbose` flag on `metadata`, `livephoto`, and `all` subcommands. When set,
  every ExifTool command is printed to the terminal on its own line. This
  restores the old dry-run behavior for users who want to watch commands stream
  by in real time.
- `--log-file PATH` flag on `metadata`, `livephoto`, and `all` subcommands.
  Writes every ExifTool command to a file (one per line), so dry-runs can be
  reviewed after the fact without flooding the terminal. For the `all` command,
  the path is automatically suffixed with `.metadata` and `.livephoto` so the
  two phases do not overwrite each other.
- Troubleshooting section in `README.md` covering the most common first-run
  errors: `ModuleNotFoundError: No module named 'dotenv'`,
  `externally-managed-environment` on Homebrew Python,
  `exiftool: command not found`, and `python` vs `python3` on macOS.
- Virtual-environment setup instructions in `README.md` (macOS / Linux /
  Windows PowerShell), plus an `exiftool -ver` verification step.

### Changed

- **Dry-run output is now quiet by default.** Previously, `python photos.py
  metadata --dry-run` used `progress_error()` to print every ExifTool command
  on its own line, producing one log line per file (50k+ lines on large
  libraries) and defeating the single-line progress bar. Dry-run now behaves
  like `scan`: it shows only the overwriting progress bar and a final summary.
  Use `--verbose` to opt back into the old behavior, or `--log-file` to capture
  the commands to disk.
- `README.md` installation section rewritten to default to `python3` / `pip3`
  and to recommend a virtual environment, avoiding the
  `externally-managed-environment` error on modern macOS / Debian Python.

### Fixed

- Phase 2 dry-run no longer produces thousands of lines of terminal output for
  large libraries. The previous behavior made the progress bar unreadable and
  was the root cause of the reported "ทำไม dry-run พ่น log เยอะไปหมด" issue.

### Notes

- No database schema changes. Existing `google_photos.db` files are fully
  compatible with this release.
- Default behavior for non-dry-run runs is unchanged. Errors still print on
  their own lines above the progress bar; the progress bar still overwrites
  itself as before.
