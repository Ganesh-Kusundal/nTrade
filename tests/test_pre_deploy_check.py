"""Tests for the pre-deploy gate script (T-033) and live-read exit policy (T-034).

Subprocess-based for T-033 (fake stage scripts, no real Dhan); pure-function
for T-034 (no network / credentials).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pre_deploy = _load_module("pre_deploy_check", "scripts/pre_deploy_check.py")
live_read = _load_module("live_read_check", "scripts/live_read_check.py")


# ------------------------------------------------------------ T-033: gate runner
def _fake_script(tmp_path, code: int, label: str, record_to: Path | None = None) -> Path:
    script = tmp_path / f"fake_{label}.py"
    recorder = ""
    if record_to is not None:
        recorder = (
            f"import json\n"
            f"open({str(record_to)!r}, 'w').write(json.dumps(sys.argv))\n"
        )
    script.write_text(f"import sys\n{recorder}sys.exit({code})\n")
    return script


def test_run_stages_propagates_failure(tmp_path):
    failing = _fake_script(tmp_path, 1, "fail")
    ok = _fake_script(tmp_path, 0, "ok")
    code = pre_deploy.run_stages([
        ("first", [sys.executable, str(ok)]),
        ("second", [sys.executable, str(failing)]),
    ])
    assert code == 1


def test_run_stages_all_green(tmp_path):
    ok = _fake_script(tmp_path, 0, "ok")
    code = pre_deploy.run_stages([
        ("a", [sys.executable, str(ok)]),
        ("b", [sys.executable, str(ok)]),
    ])
    assert code == 0


def test_run_stages_missing_script_fails(tmp_path):
    code = pre_deploy.run_stages([("ghost", [sys.executable, str(tmp_path / "nope.py")])])
    assert code == 1


def test_token_status_nonexistent_is_ok():
    ok, msg = pre_deploy.token_status("")
    assert ok
    assert "skipped" in msg


def test_token_status_near_expiry_warns(tmp_path):
    import base64
    import json
    import time

    def make_token(exp: int) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"{header}.{payload}.sig"

    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(int(time.time()) + 60)}))
    ok, msg = pre_deploy.token_status(str(store))
    assert not ok
    assert "INSIDE" in msg


def test_main_forwards_strict_to_live_read(tmp_path):
    """--strict must appear in the live-read argv (fake scripts, no Dhan)."""
    ok = _fake_script(tmp_path, 0, "ok")
    live_read_argv = tmp_path / "live_read_argv.json"
    live_read = _fake_script(tmp_path, 0, "live_read", record_to=live_read_argv)
    code = pre_deploy.main([
        "--paper-gate", str(ok), "--live-read", str(live_read), "--live-smoke", str(ok),
        "--token-path", "", "--strict",
    ])
    assert code == 0  # all fakes pass
    assert "--strict" in json.loads(live_read_argv.read_text())


def test_main_without_strict_omits_flag(tmp_path):
    """Without --strict, the live-read argv must NOT contain the flag."""
    ok = _fake_script(tmp_path, 0, "ok")
    live_read_argv = tmp_path / "live_read_argv.json"
    live_read = _fake_script(tmp_path, 0, "live_read", record_to=live_read_argv)
    code = pre_deploy.main([
        "--paper-gate", str(ok), "--live-read", str(live_read), "--live-smoke", str(ok),
        "--token-path", "",
    ])
    assert code == 0
    assert "--strict" not in json.loads(live_read_argv.read_text())


def test_main_token_failure_fails_closed(tmp_path):
    """A near-expiry token fails the whole pre-deploy check (exit 1)."""
    import base64
    import time

    def make_token(exp: int) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"{header}.{payload}.sig"

    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(int(time.time()) + 60)}))
    ok_script = _fake_script(tmp_path, 0, "ok")
    code = pre_deploy.main([
        "--paper-gate", str(ok_script), "--live-read", str(ok_script),
        "--live-smoke", str(ok_script), "--token-path", str(store),
    ])
    assert code == 1  # token near expiry -> fail closed even though fakes pass


# ------------------------------------------------- T-036: quota headroom row
def test_quota_status_row_pass_when_clear():
    from ntrade.execution.rate_limit import BrokerRateGate
    gate = BrokerRateGate()
    name, status, summary = live_read.quota_status_row(gate)
    assert name == "rate_gate"
    assert status == "PASS"
    assert "quote=" in summary and "order=" in summary


def test_quota_status_row_pass_when_burst_window_full():
    """A full 1s burst window (quote=1/1) is NORMAL steady state — the login
    probe fills it right before the snapshot, so this must be PASS, not
    DEGRADED (T-036 spurious rate_gate row regression)."""
    from ntrade.execution.rate_limit import BrokerRateGate, Quota
    gate = BrokerRateGate()
    gate.acquire(Quota.QUOTE)  # 1/1 — burst window momentarily full
    name, status, summary = live_read.quota_status_row(gate)
    assert name == "rate_gate"
    assert status == "PASS"
    assert "quote=1/1" in summary


def test_quota_status_row_degraded_when_sustained_window_blocked():
    """A long-horizon window at capacity IS real exhaustion -> DEGRADED."""
    from ntrade.execution.rate_limit import BrokerRateGate, Quota, BLOCKED_MIN_WINDOW_SPAN_S
    gate = BrokerRateGate(windows={Quota.DATA: ((BLOCKED_MIN_WINDOW_SPAN_S, 1),)})
    gate.acquire(Quota.DATA)   # 1/1 on the 60s window
    name, status, summary = live_read.quota_status_row(gate)
    assert status == "DEGRADED"
    assert "data=" in summary


def test_quota_status_row_reflects_cooldown():
    from ntrade.execution.rate_limit import BrokerRateGate, Quota
    gate = BrokerRateGate()
    gate.penalize(Quota.DATA, 3.0)
    name, status, summary = live_read.quota_status_row(gate)
    assert status == "DEGRADED"
    assert "data=" in summary and "cd" in summary


# ------------------------------------------------------------ T-034: exit policy
def test_exit_code_fail_always_fails():
    assert live_read.exit_code([("x", "FAIL", "boom")], strict=False) == 1
    assert live_read.exit_code([("x", "FAIL", "boom")], strict=True) == 1
    assert live_read.exit_code([("x", "PASS", "ok"), ("y", "FAIL", "nope")], strict=False) == 1


def test_exit_code_degraded_strict_only():
    assert live_read.exit_code([("x", "DEGRADED", "lot_size 0")], strict=False) == 0
    assert live_read.exit_code([("x", "DEGRADED", "lot_size 0")], strict=True) == 1


def test_exit_code_clean_passes():
    assert live_read.exit_code([("a", "PASS", "1"), ("b", "PASS", "2")], strict=True) == 0
    assert live_read.exit_code([], strict=True) == 0
