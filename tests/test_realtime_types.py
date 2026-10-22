from dpdfnet_mic_bridge.realtime_env.types import (
    AppliedAction,
    CheckResult,
    RuntimeTuningResult,
    ThreadTuningResult,
)


def test_runtime_result_computed_properties():
    warning = CheckResult(
        id="warn",
        title="Warning",
        status="warning",
        severity="warning",
        detail="soft problem",
    )
    critical = CheckResult(
        id="crit",
        title="Critical",
        status="failed",
        severity="critical",
        detail="hard problem",
        fix="fix it",
    )
    failed_action = AppliedAction(
        id="action",
        title="Action",
        status="failed",
        detail="action failed",
    )

    result = RuntimeTuningResult(
        mode="strict",
        checks=(warning, critical),
        actions=(failed_action,),
    )

    assert result.critical_failures == (critical,)
    assert result.failed_actions == (failed_action,)
    assert not result.ok
    assert "warn: soft problem" in result.warnings
    assert "crit: hard problem; fix it" in result.warnings
    assert "action: action failed" in result.warnings


def test_thread_result_ok_for_unrequested_and_failed_for_action():
    assert ThreadTuningResult(requested=False, thread_role="worker").ok

    failed = AppliedAction(
        id="thread",
        title="Thread",
        status="failed",
        detail="no permission",
    )
    result = ThreadTuningResult(
        requested=True,
        thread_role="worker",
        actions=(failed,),
    )

    assert not result.ok
    assert result.warning_lines == ("worker: thread: no permission",)
