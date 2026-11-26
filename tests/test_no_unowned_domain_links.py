"""The project does not own the .io domain of its name, so nothing in the repo
may link to it: anyone could register it. CHANGELOG history is exempt (it
records that the link existed and was removed)."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UNOWNED = "opendqv" + ".io"  # split so this file does not match itself


def test_no_tracked_file_mentions_the_unowned_domain():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "-i", "-I", "-F", UNOWNED, "--", ".", ":!CHANGELOG.md"],
        capture_output=True, text=True,
    )
    assert out.returncode in (0, 1), out.stderr
    assert out.stdout == "", f"links to {UNOWNED}:\n{out.stdout}"
