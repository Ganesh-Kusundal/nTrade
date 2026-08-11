from ntrade.execution._guard import CircuitBreaker, CircuitState, CircuitBreakerConfig


def test_trip_kill_forces_open():
    cb = CircuitBreaker()
    cb.trip_kill()
    assert cb._state == CircuitState.OPEN


def test_on_success_does_not_untrip_after_kill():
    cb = CircuitBreaker()
    cb.trip_kill()
    cb._on_success()  # should NOT reset to CLOSED
    assert cb._state == CircuitState.OPEN


def test_on_success_works_normally_without_kill():
    cb = CircuitBreaker()
    cb._on_failure()
    cb._on_success()
    assert cb._state == CircuitState.CLOSED


def test_release_kill_restores_normal_behavior():
    cb = CircuitBreaker()
    cb.trip_kill()
    cb.release_kill()
    cb._on_success()
    assert cb._state == CircuitState.CLOSED
