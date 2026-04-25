"""Unit tests for jotter.models (Database, Note, Folder)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jotter.models import Database, Note


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture()
def folder(db: Database):
    return db.ensure_folder("Notes", "Notes")


class TestFolders:
    def test_ensure_folder_creates(self, db):
        f = db.ensure_folder("Work", "Work")
        assert f.id > 0
        assert f.name == "Work"

    def test_ensure_folder_idempotent(self, db):
        f1 = db.ensure_folder("Notes", "Notes")
        f2 = db.ensure_folder("Notes", "Notes")
        assert f1.id == f2.id

    def test_get_folders_sorted(self, db):
        db.ensure_folder("Zephyr", "Zephyr")
        db.ensure_folder("Alpha", "Alpha")
        names = [f.name for f in db.get_folders()]
        assert names == sorted(names)

    def test_delete_folder(self, db):
        f = db.ensure_folder("Temp", "Temp")
        db.delete_folder(f.id)
        assert all(folder.id != f.id for folder in db.get_folders())


class TestNotes:
    def test_save_and_retrieve(self, db, folder):
        note = Note(folder_id=folder.id, subject="Hello", body_text="World")
        db.save_note(note)
        assert note.id > 0
        fetched = db.get_note(note.id)
        assert fetched is not None
        assert fetched.subject == "Hello"
        assert fetched.body_text == "World"

    def test_update_existing_note(self, db, folder):
        note = Note(folder_id=folder.id, subject="Original")
        db.save_note(note)
        note.subject = "Updated"
        db.save_note(note)
        fetched = db.get_note(note.id)
        assert fetched.subject == "Updated"

    def test_get_notes_excludes_deleted(self, db, folder):
        note = Note(folder_id=folder.id, subject="Live")
        db.save_note(note)
        deleted = Note(folder_id=folder.id, subject="Gone")
        db.save_note(deleted)
        db.delete_note(deleted.id)

        notes = db.get_notes(folder.id)
        ids = [n.id for n in notes]
        assert note.id in ids
        assert deleted.id not in ids

    def test_soft_delete_goes_to_trash(self, db, folder):
        note = Note(folder_id=folder.id, subject="Trash me")
        db.save_note(note)
        db.delete_note(note.id)

        deleted = db.get_deleted_notes()
        assert any(n.id == note.id for n in deleted)

    def test_restore_note(self, db, folder):
        note = Note(folder_id=folder.id, subject="Restore me")
        db.save_note(note)
        db.delete_note(note.id)
        db.restore_note(note.id)

        assert db.get_note(note.id) is not None
        live = db.get_notes(folder.id)
        assert any(n.id == note.id for n in live)
        assert not db.get_deleted_notes()

    def test_purge_note_removes_permanently(self, db, folder):
        note = Note(folder_id=folder.id, subject="Purge me")
        db.save_note(note)
        db.delete_note(note.id)
        db.purge_note(note.id)

        assert db.get_note(note.id) is None
        assert not db.get_deleted_notes()

    def test_purge_old_deleted_notes(self, db, folder):
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        old = Note(folder_id=folder.id, subject="Old trash", modified_at=old_ts)
        db.save_note(old)
        db.delete_note(old.id)
        # Manually set modified_at to old timestamp so it qualifies for purge
        db._conn().execute(
            "UPDATE notes SET modified_at=? WHERE id=?", (old_ts, old.id)
        )
        db._conn().commit()

        recent = Note(folder_id=folder.id, subject="Recent trash")
        db.save_note(recent)
        db.delete_note(recent.id)

        purged = db.purge_old_deleted_notes(30)
        assert purged == 1
        assert db.get_note(old.id) is None
        assert any(n.id == recent.id for n in db.get_deleted_notes())


class TestSearch:
    def test_fts_search_finds_note(self, db, folder):
        note = Note(folder_id=folder.id, subject="Jotter rocks",
                    body_text="Unit testing is great")
        db.save_note(note)

        results = db.get_notes(folder.id, search="rocks")
        assert any(n.id == note.id for n in results)

    def test_fts_search_no_match(self, db, folder):
        note = Note(folder_id=folder.id, subject="Hello", body_text="World")
        db.save_note(note)

        results = db.get_notes(folder.id, search="zyxwvut")
        assert not results

    def test_get_all_notes_search(self, db, folder):
        note = Note(folder_id=folder.id, subject="Searchable", body_text="content here")
        db.save_note(note)

        results = db.get_all_notes(search="Searchable")
        assert any(n.id == note.id for n in results)


class TestSort:
    def test_sort_by_title_asc(self, db, folder):
        for title in ("Zebra", "Apple", "Mango"):
            n = Note(folder_id=folder.id, subject=title)
            db.save_note(n)

        notes = db.get_notes(folder.id, sort_by="title_asc")
        subjects = [n.subject for n in notes]
        assert subjects == sorted(subjects, key=str.lower)

    def test_sort_by_title_desc(self, db, folder):
        for title in ("Aardvark", "Zebra", "Mango"):
            n = Note(folder_id=folder.id, subject=title)
            db.save_note(n)

        notes = db.get_notes(folder.id, sort_by="title_desc")
        subjects = [n.subject for n in notes]
        assert subjects == sorted(subjects, key=str.lower, reverse=True)


class TestMeta:
    def test_set_and_get_meta(self, db):
        db.set_meta("test_key", "hello")
        assert db.get_meta("test_key") == "hello"

    def test_get_missing_meta_returns_none(self, db):
        assert db.get_meta("nonexistent") is None

    def test_update_meta(self, db):
        db.set_meta("key", "first")
        db.set_meta("key", "second")
        assert db.get_meta("key") == "second"


class TestNotePreview:
    def test_preview_shows_second_line(self):
        note = Note(body_text="Title line\nPreview text here")
        assert note.preview == "Preview text here"

    def test_preview_empty_when_single_line(self):
        note = Note(body_text="Only one line")
        assert note.preview == ""

    def test_preview_truncated_at_80_chars(self):
        long_body = "Title\n" + "word " * 30
        note = Note(body_text=long_body)
        assert len(note.preview) <= 81  # allow for ellipsis

    def test_is_dirty_when_not_synced(self):
        note = Note(synced_at=None)
        assert note.is_dirty is True

    def test_is_dirty_when_modified_after_sync(self):
        note = Note(modified_at="2026-01-02T00:00:00", synced_at="2026-01-01T00:00:00")
        assert note.is_dirty is True

    def test_not_dirty_when_synced(self):
        note = Note(modified_at="2026-01-01T00:00:00", synced_at="2026-01-01T00:00:00")
        assert note.is_dirty is False
