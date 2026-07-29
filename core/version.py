"""Best-effort app version label, derived from git -- never hand-maintained,
so it can't quietly go stale. Used by core/backup.py (embedded in backup
filenames) and dashboard_app.py (shown in the sidebar).

Kept free of Streamlit imports, matching every other core/ module, so it
can be unit tested in isolation (see tests/test_version.py, which injects
a fake git module and never shells out to the real git binary).
"""

import os
import re
import subprocess

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VERSION_PREFIX = re.compile(r"^(v\d+(?:\.\d+)?)", re.IGNORECASE)


def current_app_version(*, git_module=None) -> str:
    """This project identifies each version by its branch name
    (v2.3-system-backup, v2.2-monitor-stocks, etc. -- see
    docs/VERSION_CONTROL.md's own Branches table), so the label here is the
    current branch's leading vN.M/vN prefix, e.g. "v2.3-system-backup" ->
    "v2.3". If the current branch doesn't match that pattern (e.g. `main`,
    which has no version of its own), falls back to the nearest tag
    (`git describe --tags --always`) instead. If git itself isn't
    available for any reason, returns "unknown" -- this label is a
    convenience for backup filenames/the sidebar and must never raise or
    block whatever's calling it.

    git_module can be injected for testing (mirrors core/market_data.py's
    yf_module= pattern) -- must expose a `.run(args, **kwargs)` matching
    subprocess.run's interface. Production callers always omit it and get
    the real subprocess module."""
    git_module = git_module or subprocess
    try:
        branch = git_module.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True, cwd=_PROJECT_ROOT,
        ).stdout.strip()
        match = _VERSION_PREFIX.match(branch)
        if match:
            return match.group(1)

        tag = git_module.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, check=True, cwd=_PROJECT_ROOT,
        ).stdout.strip()
        return tag or "unknown"
    except Exception:
        return "unknown"
