"""P3-1: shared work-order state machine in medops_common (single source)."""

from __future__ import annotations

from medops_common.constants import WORK_ORDER_TRANSITIONS, WorkOrderStatus


def test_transitions_match_design() -> None:
    """Design §6: pending → in_progress → awaiting_verification → closed."""
    assert WORK_ORDER_TRANSITIONS == {
        WorkOrderStatus.PENDING.value: {WorkOrderStatus.IN_PROGRESS.value},
        WorkOrderStatus.IN_PROGRESS.value: {WorkOrderStatus.AWAITING_VERIFICATION.value},
        WorkOrderStatus.AWAITING_VERIFICATION.value: {WorkOrderStatus.CLOSED.value},
        WorkOrderStatus.CLOSED.value: set(),
    }


def test_can_transition_helper() -> None:
    from medops_common.constants import can_transition_work_order

    assert can_transition_work_order("pending", "in_progress")
    assert can_transition_work_order("in_progress", "awaiting_verification")
    assert can_transition_work_order("awaiting_verification", "closed")
    assert not can_transition_work_order("pending", "closed")
    assert not can_transition_work_order("closed", "in_progress")
    # unknown statuses never transition
    assert not can_transition_work_order("bogus", "pending")
    assert not can_transition_work_order("pending", "bogus")


def test_mcp_server_uses_shared_table() -> None:
    """maintenance-db MCP server imports the shared table, no local copy."""
    import inspect

    from mcp_maintenance_db import server

    src = inspect.getsource(server)
    assert "_WORK_ORDER_TRANSITIONS" not in src, "local copy must be removed"
    assert "WORK_ORDER_TRANSITIONS" in src
