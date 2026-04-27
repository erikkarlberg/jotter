"""Rolling database backup manager."""

from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path

from gi.repository import GLib

logger = logging.getLogger(__name__)

_BACKUP_INTERVAL_SECS = 3600  # hourly
_MAX_BACKUPS = 7


def start_auto_backup(db_path: Path) -> None:
    """Take a snapshot now, then schedule hourly snapshots."""
    _run_backup(db_path)

    def _tick() -> bool:
        _run_backup(db_path)
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add_seconds(_BACKUP_INTERVAL_SECS, _tick)


def _run_backup(db_path: Path) -> None:
    try:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        dest = backup_dir / f"jotter_{date.today().isoformat()}.db"
        if not dest.exists():
            shutil.copy2(str(db_path), str(dest))
            logger.info("DB backup written to %s", dest)

        # Keep only the most recent _MAX_BACKUPS files
        all_backups = sorted(backup_dir.glob("jotter_*.db"))
        for old in all_backups[:-_MAX_BACKUPS]:
            old.unlink(missing_ok=True)
            logger.info("Pruned old backup %s", old.name)
    except Exception as exc:
        logger.warning("Backup failed: %s", exc)
