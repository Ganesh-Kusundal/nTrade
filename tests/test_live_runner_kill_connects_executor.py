"""LiveRunner._on_risk_halted must also trip BrokerExecution.trip_kill_switch."""

from datetime import datetime
from unittest.mock import MagicMock

from ntrade.events.risk import RiskHaltedEvent
from ntrade.runner.live_runner import LiveRunner


def test_on_risk_halted_trips_broker_execution_kill_switch():
    """When risk halts, LiveRunner must call trip_kill_switch on the
    BrokerExecution obtained from the kernel — unifying the two kill mechanisms."""
    runner = object.__new__(LiveRunner)  # skip __init__
    runner.halted = False
    runner.kill_switched = False
    runner.kill_switch_failed = False

    # kernel.ctx.instruments_snapshot() returns empty — no per-instrument kill
    # switch calls. The broker_execution() mock is what we're testing.
    mock_kernel = MagicMock()
    mock_kernel.ctx.instruments_snapshot.return_value = []
    mock_broker_exec = MagicMock()
    mock_kernel.broker_execution.return_value = mock_broker_exec
    runner.kernel = mock_kernel

    event = RiskHaltedEvent(ts=datetime.now(), reason="test risk halt")
    runner._on_risk_halted(event)

    mock_broker_exec.trip_kill_switch.assert_called_once_with(reason="test risk halt")


def test_on_risk_halted_no_broker_execution_is_safe():
    """When the kernel has no BrokerExecution (sim mode), _on_risk_halted
    must not crash."""
    runner = object.__new__(LiveRunner)
    runner.halted = False
    runner.kill_switched = False
    runner.kill_switch_failed = False

    mock_kernel = MagicMock()
    mock_kernel.ctx.instruments_snapshot.return_value = []
    mock_kernel.broker_execution.return_value = None
    runner.kernel = mock_kernel

    event = RiskHaltedEvent(ts=datetime.now(), reason="sim halt")
    runner._on_risk_halted(event)  # must not raise

    assert runner.halted is True
