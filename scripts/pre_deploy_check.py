"""T-033 unified pre-deploy gate — one command an operator runs before go-live.

Runs, in order:
  1. token freshness check  (jwt_expiry on DHAN_TOKEN_PATH; warn near expiry)
  2. paper gate (optional)
  3. live_read_check.py     (exit 1 on any FAIL, or DEGRADED under --strict)
  4. live_smoke.py          (read-only framework smoke)

Exits 1 with a combined report if any stage fails. Stage paths are injectable
via CLI args so tests can point at fake scripts and never invoke real Dhan.

Usage:
    .venv/bin/python scripts/pre_deploy_check.py [--strict]
    .venv/bin/python scripts/pre_deploy_check.py \
        --live-read scripts/live_read_check.py \
        --live-smoke scripts/live_smoke.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.brokers.dhan_auth import EXPIRY_BUFFER_S, jwt_expiry  # noqa: E402


def token_status(token_path: str) -> tuple[bool, str]:
    """(ok, message) — token freshness check. Warns when unreadable or near expiry."""
    if not token_path or not Path(token_path).exists():
        return True, f"token store {token_path or '(none set)'} — skipped (no token file)"
    try:
        data = Path(token_path).read_text()
        import json
        token = (json.loads(data).get("access_token") or "").strip()
    except Exception as exc:  # noqa: BLE001
        return True, f"token store unreadable ({exc}) — skipped"
    if not token:
        return True, "token store empty — will be minted on connect"
    exp, when = jwt_expiry(token)
    if exp is None:
        return True, "token not JWT-shaped — data plane will decide"
    import time
    remaining = exp - int(time.time())
    if remaining <= EXPIRY_BUFFER_S:
        return False, f"token expires {when} — INSIDE {EXPIRY_BUFFER_S}s buffer, refresh needed"
    return True, f"token valid until {when} ({remaining}s remaining)"


def run_stages(stages: list[tuple[str, list[str]]]) -> int:
    """Run each (name, argv) stage; print a PASS/FAIL banner; aggregate exit code."""
    exit_code = 0
    print("=" * 70)
    for name, argv in stages:
        print(f"\n== {name} ==")
        proc = None
        try:
            proc = subprocess.run(argv, check=False)
            ok = proc.returncode == 0
        except FileNotFoundError as exc:
            print(f"  stage not found: {exc}")
            ok = False
        print(f"== {name}: {'PASS' if ok else 'FAIL'} (exit {proc.returncode if ok else 'n/a'}) ==")
        if not ok:
            exit_code = 1
    print("=" * 70)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Unified pre-deploy gate for ntrade live sessions")
    p.add_argument("--paper-gate", default=None)
    p.add_argument("--live-read", default=str(Path(__file__).resolve().parent / "live_read_check.py"))
    p.add_argument("--live-smoke", default=str(Path(__file__).resolve().parent / "live_smoke.py"))
    p.add_argument("--token-path", default=os.environ.get("DHAN_TOKEN_PATH", ""))
    p.add_argument("--strict", action="store_true",
                   help="fail closed on DEGRADED live-read rows (T-034)")
    args = p.parse_args(argv)

    print("== ntrade pre-deploy check ==")

    # Fail closed: a near-expiry token is a go-live hazard, not a warning.
    exit_code = 0
    ok, msg = token_status(args.token_path)
    print(f"[token] {'OK' if ok else 'FAIL'}: {msg}")
    if not ok:
        exit_code = 1
    stages: list[tuple[str, list[str]]] = []
    if ok and args.paper_gate:
        stages.append(("paper gate", [sys.executable, args.paper_gate]))

    live_read = [sys.executable, args.live_read]
    if args.strict:
        live_read.append("--strict")
    stages.append(("live read", live_read))
    stages.append(("live smoke", [sys.executable, args.live_smoke]))

    # Regression guards for zero-parity fixes (cheap, offline):
    from ntrade.brokers.paper import PaperBroker  # noqa: E402

    offline_checks: list[tuple[str, bool]] = [
        ("paper broker is not a cash authority",
         not getattr(PaperBroker, "reports_cash", True)),
    ]
    parity = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_fill_parity.py",
         "tests/test_paper_single_ledger.py",
         "-q", "--no-header", "-x"],
        capture_output=True, text=True, check=False,
    )
    offline_checks.append(("fill parity + single ledger tests pass",
                           parity.returncode == 0))

    print("\n== offline parity guards ==")
    for label, ok in offline_checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            exit_code = 1
            if parity.returncode != 0 and label.startswith("fill parity"):
                print(parity.stdout[-2000:] if parity.stdout else "")
                print(parity.stderr[-2000:] if parity.stderr else "")

    code = run_stages(stages)
    exit_code = exit_code or code
    print(f"\nPRE-DEPLOY CHECK: {'PASS' if exit_code == 0 else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
