import importlib

import pytest


def _operation_module():
    try:
        return importlib.import_module("hermes_voice_studio.operation")
    except ModuleNotFoundError as exc:
        pytest.fail(f"operation budget contract is missing: {exc}")


def test_deadline_is_absolute_across_phases(monkeypatch):
    operation = _operation_module()
    now = [100.0]
    monkeypatch.setattr(operation.time, "monotonic", lambda: now[0])

    budget = operation.OperationBudget(5.0)
    now[0] = 104.0
    assert budget.remaining("prepare") == pytest.approx(1.0)
    now[0] = 105.0
    with pytest.raises(TimeoutError, match="inference"):
        budget.checkpoint("inference")


def test_explicit_cancellation_wins_over_an_expired_deadline(monkeypatch):
    operation = _operation_module()
    now = [100.0]
    monkeypatch.setattr(operation.time, "monotonic", lambda: now[0])
    budget = operation.OperationBudget(1.0, cancelled=lambda: True)
    now[0] = 200.0

    from hermes_voice_studio.jobs import JobCancelled

    with pytest.raises(JobCancelled, match="prepare"):
        budget.checkpoint("prepare")


def test_remaining_uses_monotonic_deadline_and_optional_ceiling(monkeypatch):
    operation = _operation_module()
    now = [10.0]
    monkeypatch.setattr(operation.time, "monotonic", lambda: now[0])
    budget = operation.OperationBudget(10.0)

    now[0] = 13.0
    assert budget.remaining("import", ceiling=2.0) == pytest.approx(2.0)
    now[0] = 18.0
    assert budget.remaining("import", ceiling=20.0) == pytest.approx(2.0)
