"""Task 6: Coverage per-module + git dirt cleanup gate."""

import pathlib
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python <3.11 fallback (project requires >=3.10, but use tomli if needed)
    import tomli as tomllib  # type: ignore


def test_gitignore_contains_required_entries():
    text = pathlib.Path(".gitignore").read_text()
    required = [
        "graphify-out/",
        ".qoder/repowiki/",
        "data/ohlcv/",
        "Dependencies/",
        ".ntrade_cache/",
    ]
    for entry in required:
        assert entry in text, f".gitignore missing {entry!r}\n{text}"
    # Must not ignore src (the source tree) — generic check
    assert "src" not in text.splitlines() or all("src" not in line for line in text.splitlines() if line.strip() == "src" or line.strip() == "src/")
    # More precise: ensure ntrade/ is not ignored via check-ignore
    result = subprocess.run(
        ["git", "check-ignore", "-v", "ntrade/__init__.py"],
        capture_output=True,
        text=True,
    )
    # check-ignore returns 0 if ignored, 1 if not ignored
    assert result.returncode != 0, "ntrade source should not be ignored by .gitignore"


def test_gitignore_does_not_ignore_ntrade():
    out = subprocess.run(
        ["git", "check-ignore", "ntrade/domain/instruments/base.py"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 1, "ntrade files must not be gitignored"


def test_coverage_fail_under_90():
    data = pathlib.Path("pyproject.toml").read_bytes()
    # tomllib requires binary
    cfg = tomllib.loads(data.decode())
    fail_under = cfg["tool"]["coverage"]["report"]["fail_under"]
    assert fail_under == 90, f"fail_under should be 90, got {fail_under}"


def test_coverage_exclude_lines():
    data = pathlib.Path("pyproject.toml").read_bytes()
    cfg = tomllib.loads(data.decode())
    report = cfg["tool"]["coverage"]["report"]
    assert "exclude_lines" in report, "exclude_lines missing from coverage report config"
    exclude = report["exclude_lines"]
    assert "pragma: no cover" in exclude
    assert "if TYPE_CHECKING:" in exclude


def test_gitignore_hides_graphify_out():
    # graphify-out should be ignored
    result = subprocess.run(
        ["git", "check-ignore", "-v", "graphify-out/foo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "graphify-out/ should be ignored"


def test_gitignore_hides_data_ohlcv():
    result = subprocess.run(
        ["git", "check-ignore", "-v", "data/ohlcv/foo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "data/ohlcv/ should be ignored"


def test_gitignore_hides_qoder_repowiki():
    result = subprocess.run(
        ["git", "check-ignore", "-v", ".qoder/repowiki/foo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, ".qoder/repowiki/ should be ignored"


def test_git_status_clean_no_unstaged_qoder_repowiki():
    out = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    # Unstaged deletion is " D .qoder/repowiki"
    assert " D .qoder/repowiki" not in out
    assert " D \".qoder/repowiki" not in out
    # Untracked graphify-out should not appear (it is ignored)
    assert "?? graphify-out" not in out
