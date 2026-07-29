import json
import os
import sqlite3
from datetime import datetime

import pandas as pd
import pytest

from core.backup import backup_database, backup_statement_file, delete_backup, list_backups


@pytest.fixture
def source_db(tmp_path):
    """A small real SQLite db (not a mock) -- proves backup_database()
    produces a byte-for-byte-equivalent, independently openable database,
    not just a file that happens to exist."""
    path = tmp_path / "portfolio.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, quantity REAL)")
    conn.execute("INSERT INTO trades (symbol, quantity) VALUES ('KO', 10.0), ('SHV', 5.0)")
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def source_statement(tmp_path):
    path = tmp_path / "Offshore_Statements_2023-01_to_2026-06.xlsx"
    path.write_bytes(b"fake xlsx bytes for byte-equality checking")
    return str(path)


class TestBackupDatabase:
    def test_produces_an_openable_db_with_identical_table_contents(self, source_db, tmp_path):
        backup_dir = tmp_path / "backups"
        filename = backup_database(source_db, str(backup_dir), version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))

        conn = sqlite3.connect(backup_dir / filename)
        rows = conn.execute("SELECT symbol, quantity FROM trades ORDER BY symbol").fetchall()
        conn.close()
        assert rows == [("KO", 10.0), ("SHV", 5.0)]

    def test_filename_embeds_version_and_timestamp(self, source_db, tmp_path):
        filename = backup_database(source_db, str(tmp_path / "backups"), version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))
        assert filename == "bk-portfolio-v2.3-290726-1430.db"

    def test_creates_backup_dir_if_missing(self, source_db, tmp_path):
        backup_dir = tmp_path / "does_not_exist_yet"
        backup_database(source_db, str(backup_dir), version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))
        assert backup_dir.is_dir()


class TestBackupStatementFile:
    def test_copies_bytes_exactly(self, source_statement, tmp_path):
        backup_dir = tmp_path / "backups"
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        filename = backup_statement_file(source_glob, str(backup_dir), version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))

        assert (backup_dir / filename).read_bytes() == b"fake xlsx bytes for byte-equality checking"

    def test_filename_embeds_version_date_range_and_timestamp(self, source_statement, tmp_path):
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        filename = backup_statement_file(source_glob, str(tmp_path / "backups"), version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))
        assert filename == "bk-statements-v2.3-2023-01_to_2026-06-290726-1430.xlsx"

    def test_no_match_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            backup_statement_file(str(tmp_path / "Offshore_Statements_*.xlsx"), str(tmp_path / "backups"))

    def test_ambiguous_match_raises_value_error(self, tmp_path):
        (tmp_path / "Offshore_Statements_2023-01_to_2026-05.xlsx").write_bytes(b"old")
        (tmp_path / "Offshore_Statements_2023-01_to_2026-06.xlsx").write_bytes(b"new")
        with pytest.raises(ValueError):
            backup_statement_file(str(tmp_path / "Offshore_Statements_*.xlsx"), str(tmp_path / "backups"))


class TestListBackups:
    def test_missing_dir_returns_empty_frame_not_an_error(self, tmp_path):
        result = list_backups(str(tmp_path / "never_created"))
        assert result.empty
        assert list(result.columns) == ["Filename", "Type", "Version", "Created", "Size", "Note"]

    def test_lists_both_types_sorted_newest_first_with_parsed_fields(self, source_db, source_statement, tmp_path):
        backup_dir = str(tmp_path / "backups")
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0))
        backup_statement_file(source_glob, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30))

        result = list_backups(backup_dir)
        assert len(result) == 2
        # newest (statement, 14:30) first
        assert result.iloc[0]["Type"] == "Statement"
        assert result.iloc[0]["Version"] == "v2.3"
        assert result.iloc[0]["Created"] == datetime(2026, 7, 29, 14, 30)
        assert result.iloc[1]["Type"] == "Database"
        assert result.iloc[1]["Size"] > 0

    def test_stray_unrelated_file_is_skipped_not_erroring(self, source_db, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_database(source_db, str(backup_dir), version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0))
        (backup_dir / "notes.txt").write_text("unrelated file a user might drop in here")

        result = list_backups(str(backup_dir))
        assert len(result) == 1


class TestDeleteBackup:
    def test_deletes_the_file(self, source_db, tmp_path):
        backup_dir = str(tmp_path / "backups")
        filename = backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0))

        delete_backup(backup_dir, filename)
        assert not os.path.exists(os.path.join(backup_dir, filename))

    def test_removes_its_note_from_the_manifest_too(self, source_db, tmp_path):
        backup_dir = str(tmp_path / "backups")
        filename = backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0), note="delete me")

        delete_backup(backup_dir, filename)

        manifest = json.loads((tmp_path / "backups" / "manifest.json").read_text())
        assert filename not in manifest

    def test_does_not_disturb_other_backups(self, source_db, source_statement, tmp_path):
        backup_dir = str(tmp_path / "backups")
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        db_filename = backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0), note="keep")
        stmt_filename = backup_statement_file(source_glob, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30), note="delete me")

        delete_backup(backup_dir, stmt_filename)

        result = list_backups(backup_dir)
        assert len(result) == 1
        assert result.iloc[0]["Filename"] == db_filename
        assert result.iloc[0]["Note"] == "keep"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            delete_backup(str(backup_dir), "bk-portfolio-v2.3-290726-1000.db")


class TestBackupNotes:
    def test_note_on_database_backup_shows_up_in_list_backups(self, source_db, tmp_path):
        backup_dir = str(tmp_path / "backups")
        backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0), note="before testing rebalance")

        result = list_backups(backup_dir)
        assert result.iloc[0]["Note"] == "before testing rebalance"

    def test_note_on_statement_backup_shows_up_in_list_backups(self, source_statement, tmp_path):
        backup_dir = str(tmp_path / "backups")
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        backup_statement_file(source_glob, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30), note="before July import")

        result = list_backups(backup_dir)
        assert result.iloc[0]["Note"] == "before July import"

    def test_no_note_given_shows_empty_string_not_missing(self, source_db, tmp_path):
        backup_dir = str(tmp_path / "backups")
        backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0))

        result = list_backups(backup_dir)
        assert result.iloc[0]["Note"] == ""

    def test_manifest_json_itself_is_not_listed_as_a_backup(self, source_db, tmp_path):
        backup_dir = str(tmp_path / "backups")
        backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0), note="keep this")

        result = list_backups(backup_dir)
        assert len(result) == 1  # manifest.json itself doesn't appear as its own row

    def test_notes_from_multiple_backups_dont_overwrite_each_other(self, source_db, source_statement, tmp_path):
        backup_dir = str(tmp_path / "backups")
        source_glob = str(tmp_path / "Offshore_Statements_*.xlsx")
        backup_database(source_db, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 10, 0), note="db note")
        backup_statement_file(source_glob, backup_dir, version="v2.3", timestamp=datetime(2026, 7, 29, 14, 30), note="statement note")

        result = list_backups(backup_dir)
        notes_by_type = dict(zip(result["Type"], result["Note"]))
        assert notes_by_type == {"Database": "db note", "Statement": "statement note"}
