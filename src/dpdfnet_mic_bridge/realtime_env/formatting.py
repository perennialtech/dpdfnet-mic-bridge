"""Formatting helpers for realtime diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .types import PersistentApplyResult, RuntimeTuningResult


def result_to_jsonable(
    result: RuntimeTuningResult | PersistentApplyResult,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "checks": [asdict(check) for check in result.checks],
        "actions": [asdict(action) for action in result.actions],
    }
    if isinstance(result, RuntimeTuningResult):
        payload["mode"] = result.mode
    return payload


def print_json_result(
    result: RuntimeTuningResult | PersistentApplyResult, *, system: str, mode: str
) -> None:
    payload: dict[str, Any] = {
        "system": system,
        "mode": mode,
        **result_to_jsonable(result),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def print_human_status(
    result: RuntimeTuningResult | PersistentApplyResult,
    *,
    title: str = "Realtime environment",
) -> None:
    print(title)
    print()

    print("Checks")
    if result.checks:
        for check in result.checks:
            print(f"  {check.status:<14} {check.id:<34} {check.detail}")
            if check.fix:
                print(f"  {'':<14} {'':<34} fix: {check.fix}")
    else:
        print("  (none)")

    print()
    print("Actions")
    if result.actions:
        for action in result.actions:
            print(f"  {action.status:<14} {action.id:<34} {action.detail}")
    else:
        print("  (none)")
