import json
import os
import sqlite3
from datetime import datetime

import pandas as pd
import pytest

from core.backup import (
    backup_database,
    backup_statement_file,
    backup_turso_database,
    backup_turso_database_bytes,
    delete_backup,
    export_database_to_sqlite,
    list_backups,
)


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


@pytest.fixture
def live_conn():
    """Stands in for the Turso connection: an in-memory SQLite db with an
    AUTOINCREMENT table (creates sqlite_sequence), a second table, an index, a
    view, a trigger, and a table name that needs identifier quoting."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, quantity REAL)")
    conn.execute("CREATE INDEX idx_trades_symbol ON trades (symbol)")
    conn.execute("CREATE TABLE dividends (id INTEGER PRIMARY KEY, symbol TEXT, net REAL)")
    conn.execute('CREATE TABLE "odd ""name"' + " (a TEXT, b INTEGER)")
    conn.execute("CREATE TABLE empty_table (x INTEGER)")
    conn.execute("CREATE VIEW held AS SELECT symbol FROM trades")
    conn.execute("CREATE TABLE audit (n INTEGER)")
    conn.execute("CREATE TRIGGER trades_audit AFTER INSERT ON trades BEGIN INSERT INTO audit VALUES (1); END")
    conn.executemany("INSERT INTO trades (symbol, quantity) VALUES (?, ?)", [("KO", 10.0), ("SHV", 5.0), ("O", 2.5)])
    conn.execute("DELETE FROM audit")
    conn.execute("INSERT INTO dividends (symbol, net) VALUES ('KO', 1.25)")
    conn.execute('INSERT INTO "odd ""name" VALUES (\'x\', 7)')
    conn.commit()
    yield conn
    conn.close()


class TestExportDatabaseToSqlite:
    def test_copies_every_table_row_for_row(self, live_conn, tmp_path):
        dest = str(tmp_path / "copy.db")
        counts = export_database_to_sqlite(live_conn, dest)

        assert counts == {"audit": 0, "dividends": 1, "empty_table": 0, "odd \"name": 1, "trades": 3}
        copy = sqlite3.connect(dest)
        assert copy.execute("SELECT id, symbol, quantity FROM trades ORDER BY id").fetchall() == [
            (1, "KO", 10.0), (2, "SHV", 5.0), (3, "O", 2.5),
        ]
        assert copy.execute("SELECT symbol, net FROM dividends").fetchall() == [("KO", 1.25)]
        assert copy.execute('SELECT a, b FROM "odd ""name"').fetchall() == [("x", 7)]
        copy.close()

    def test_recreates_indexes_and_views(self, live_conn, tmp_path):
        dest = str(tmp_path / "copy.db")
        export_database_to_sqlite(live_conn, dest)

        copy = sqlite3.connect(dest)
        names = {row[0] for row in copy.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'view')")}
        assert {"idx_trades_symbol", "held"} <= names
        assert copy.execute("SELECT COUNT(*) FROM held").fetchone()[0] == 3
        copy.close()

    def test_triggers_are_created_after_the_data_load_so_they_dont_fire_on_copied_rows(self, live_conn, tmp_path):
        dest = str(tmp_path / "copy.db")
        export_database_to_sqlite(live_conn, dest)

        copy = sqlite3.connect(dest)
        assert copy.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0
        copy.execute("INSERT INTO trades (symbol, quantity) VALUES ('NEW', 1.0)")
        assert copy.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1  # the trigger itself does exist
        copy.close()

    def test_autoincrement_continues_after_the_highest_copied_id(self, live_conn, tmp_path):
        dest = str(tmp_path / "copy.db")
        export_database_to_sqlite(live_conn, dest)

        copy = sqlite3.connect(dest)
        copy.execute("INSERT INTO trades (symbol, quantity) VALUES ('NEW', 1.0)")
        assert copy.execute("SELECT MAX(id) FROM trades").fetchone()[0] == 4
        copy.close()

    def test_sqlite_internal_tables_are_not_copied_as_data_tables(self, live_conn, tmp_path):
        counts = export_database_to_sqlite(live_conn, str(tmp_path / "copy.db"))
        assert not any(name.startswith("sqlite_") for name in counts)

    def test_result_is_in_wal_mode_which_tursos_upload_restore_requires(self, live_conn, tmp_path):
        dest = str(tmp_path / "copy.db")
        export_database_to_sqlite(live_conn, dest)

        copy = sqlite3.connect(dest)
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        copy.close()

    def test_refuses_to_overwrite_an_existing_file(self, live_conn, tmp_path):
        dest = tmp_path / "copy.db"
        dest.write_bytes(b"an earlier backup")
        with pytest.raises(FileExistsError):
            export_database_to_sqlite(live_conn, str(dest))
        assert dest.read_bytes() == b"an earlier backup"

    def test_source_is_left_untouched(self, live_conn, tmp_path):
        before = live_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        export_database_to_sqlite(live_conn, str(tmp_path / "copy.db"))
        assert live_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == before

    def test_a_failure_midway_leaves_no_half_written_file(self, tmp_path):
        class ExplodingConn:
            """Serves the schema queries, then fails when the first table's rows are read."""
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if sql.startswith("SELECT * FROM"):
                    raise RuntimeError("connection dropped")
                return self._real.execute(sql, *args)

        real = sqlite3.connect(":memory:")
        real.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
        real.execute("INSERT INTO trades VALUES (1, 'KO')")
        dest = tmp_path / "copy.db"
        with pytest.raises(RuntimeError, match="connection dropped"):
            export_database_to_sqlite(ExplodingConn(real), str(dest))
        assert not dest.exists()
        real.close()


class TestBackupTursoDatabase:
    def test_filename_carries_env_version_and_timestamp(self, live_conn, tmp_path):
        filename, counts = backup_turso_database(
            live_conn, str(tmp_path / "backups"), env_label="prod", version="v4.14",
            timestamp=datetime(2026, 9, 19, 15, 30),
        )
        assert filename == "bk-turso-prod-v4.14-190926-1530.db"
        assert counts["trades"] == 3
        assert (tmp_path / "backups" / filename).is_file()

    def test_dev_and_prod_backups_get_different_names(self, live_conn, tmp_path):
        stamp = datetime(2026, 9, 19, 15, 30)
        prod, _ = backup_turso_database(live_conn, str(tmp_path), env_label="prod", version="v4.14", timestamp=stamp)
        dev, _ = backup_turso_database(live_conn, str(tmp_path), env_label="dev", version="v4.14", timestamp=stamp)
        assert prod != dev

    def test_not_picked_up_by_the_old_backup_page_history_list(self, live_conn, tmp_path):
        backup_turso_database(live_conn, str(tmp_path), env_label="prod", version="v4.14", timestamp=datetime(2026, 9, 19, 15, 30))
        assert list_backups(str(tmp_path)).empty


class TestBackupTursoDatabaseBytes:
    def test_bytes_open_as_a_database_with_the_same_rows(self, live_conn, tmp_path):
        filename, data, counts = backup_turso_database_bytes(
            live_conn, env_label="prod", version="v4.14", timestamp=datetime(2026, 9, 19, 15, 30),
        )
        assert filename == "bk-turso-prod-v4.14-190926-1530.db"
        assert counts["trades"] == 3

        restored = tmp_path / filename
        restored.write_bytes(data)
        copy = sqlite3.connect(restored)
        assert copy.execute("SELECT symbol FROM trades ORDER BY id").fetchall() == [("KO",), ("SHV",), ("O",)]
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        copy.close()

    def test_leaves_nothing_behind_in_the_working_directory(self, live_conn, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        backup_turso_database_bytes(live_conn, env_label="dev", version="v4.14", timestamp=datetime(2026, 9, 19, 15, 30))
        assert list(tmp_path.iterdir()) == []

    def test_a_caller_supplied_connection_is_not_closed(self, live_conn):
        backup_turso_database_bytes(live_conn, env_label="dev", version="v4.14")
        assert live_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 3  # still usable


class TestEnvLabelIsFilenameSafe:
    def test_punctuation_and_path_characters_are_stripped(self, live_conn, tmp_path):
        filename, _ = backup_turso_database(
            live_conn, str(tmp_path), env_label="../pr od!", version="v4.14", timestamp=datetime(2026, 9, 19, 15, 30),
        )
        assert filename == "bk-turso-prod-v4.14-190926-1530.db"

    def test_an_all_punctuation_label_falls_back_to_unknown(self, live_conn, tmp_path):
        filename, _ = backup_turso_database(
            live_conn, str(tmp_path), env_label="!!!", version="v4.14", timestamp=datetime(2026, 9, 19, 15, 30),
        )
        assert filename.startswith("bk-turso-unknown-")
