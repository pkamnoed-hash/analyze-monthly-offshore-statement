import glob
import os
from datetime import datetime

import streamlit as st

from core.backup import (
    DEFAULT_BACKUP_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_STATEMENT_GLOB,
    backup_database,
    backup_statement_file,
    delete_backup,
    list_backups,
)

st.title("System Backup")
st.caption(
    "Manual, on-demand backups of your two most at-risk files -- the live database "
    "and the official Statement workbook. Click a button to take a timestamped, "
    "version-labeled copy into data/backups/ (not tracked in git). Filenames follow "
    "bk-<type>-<version>-[<date range>-]<ddmmyy>-<hhmm>, e.g. "
    "bk-portfolio-v2.3-290726-1430.db -- version is read from your current git branch "
    "automatically, never hand-typed, so it can't go stale. This is backup-only for "
    "now; restoring from a backup isn't built yet."
)

statement_matches = glob.glob(DEFAULT_STATEMENT_GLOB)
statement_path = statement_matches[0] if len(statement_matches) == 1 else None

st.subheader("Current status")
status_col1, status_col2 = st.columns(2)
with status_col1:
    st.markdown("**Database**")
    if os.path.exists(DEFAULT_DB_PATH):
        st.caption(f"Size: {os.path.getsize(DEFAULT_DB_PATH):,} bytes")
        st.caption(f"Last modified: {datetime.fromtimestamp(os.path.getmtime(DEFAULT_DB_PATH)):%d/%m/%Y %H:%M}")
    else:
        st.caption("Not found.")
with status_col2:
    st.markdown("**Official Statement**")
    if statement_path:
        st.caption(f"File: {os.path.basename(statement_path)}")
        st.caption(f"Size: {os.path.getsize(statement_path):,} bytes")
        st.caption(f"Last modified: {datetime.fromtimestamp(os.path.getmtime(statement_path)):%d/%m/%Y %H:%M}")
    elif len(statement_matches) > 1:
        st.caption(f"{len(statement_matches)} matching files found -- ambiguous, can't back up until only one remains.")
    else:
        st.caption("Not found.")

existing_backups = list_backups(DEFAULT_BACKUP_DIR)
if existing_backups.empty:
    st.caption("Last backup: none taken yet.")
else:
    latest = existing_backups.iloc[0]
    st.caption(f"Last backup: {latest['Filename']} ({latest['Created']:%d/%m/%Y %H:%M})")

st.subheader("Take a backup")
action_col1, action_col2 = st.columns(2)
with action_col1:
    db_note = st.text_input("Note (optional)", key="db_backup_note", help="Saved alongside this backup, shown in the history table below.")
    if st.button(
        "Backup Database Now",
        help="Uses SQLite's own backup API, not a plain file copy -- guarantees a "
             "consistent snapshot even while the app has the database open, unlike a "
             "raw copy which can grab a torn, mid-write read.",
    ):
        try:
            filename = backup_database(DEFAULT_DB_PATH, DEFAULT_BACKUP_DIR, note=db_note)
            st.success(f"Saved as {filename}")
        except Exception as e:
            st.error(f"Backup failed: {e}")
with action_col2:
    statement_note = st.text_input("Note (optional)", key="statement_backup_note", help="Saved alongside this backup, shown in the history table below.")
    if st.button(
        "Backup Official Statement Now",
        help="Matched by filename pattern (Offshore_Statements_*.xlsx), not a fixed "
             "name -- keeps working after this file is renamed for a new month's "
             "date range.",
    ):
        try:
            filename = backup_statement_file(DEFAULT_STATEMENT_GLOB, DEFAULT_BACKUP_DIR, note=statement_note)
            st.success(f"Saved as {filename}")
        except Exception as e:
            st.error(f"Backup failed: {e}")

st.subheader("Backup history")
history = list_backups(DEFAULT_BACKUP_DIR)

if history.empty:
    st.caption("No backups taken yet.")
else:
    type_filter = st.radio(
        "Filter by type", ["All", "Database", "Statement"], horizontal=True, label_visibility="collapsed",
    )
    view = history if type_filter == "All" else history[history["Type"] == type_filter]
    st.caption(f"Showing {len(view)} of {len(history)} backups.")

    if view.empty:
        st.caption(f"No {type_filter} backups yet.")
    else:
        # Manual per-row layout (not st.dataframe) so each row can carry its own delete
        # popover -- same pattern as record_trade.py's "Recent Trades" list.
        col_widths = [2.2, 0.9, 0.7, 1.2, 0.9, 1.8, 0.7]
        header_cols = st.columns(col_widths)
        for col, label in zip(header_cols, ["Filename", "Type", "Version", "Created", "Size (bytes)", "Note", ""]):
            col.markdown(f"**{label}**")
        for _, row in view.iterrows():
            c1, c2, c3, c4, c5, c6, c7 = st.columns(col_widths)
            c1.write(row["Filename"])
            c2.write(row["Type"])
            c3.write(row["Version"])
            c4.write(row["Created"].strftime("%d/%m/%Y %H:%M"))
            c5.write(f"{row['Size']:,}")
            c6.write(row["Note"] or "—")
            with c7.popover("Delete"):
                st.write(f"Delete {row['Filename']}? This can't be undone.")
                if st.button("Yes, delete", key=f"confirm_delete_{row['Filename']}"):
                    delete_backup(DEFAULT_BACKUP_DIR, row["Filename"])
                    st.rerun()
