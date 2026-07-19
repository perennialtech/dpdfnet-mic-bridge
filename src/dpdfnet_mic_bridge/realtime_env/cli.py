"""CLI handlers for realtime diagnostics and persistent setup."""

from __future__ import annotations

import argparse
import platform

from . import linux, windows
from .formatting import print_human_status, print_json_result
from .runtime import RealtimeRuntimeManager
from .types import (AppliedAction, CheckResult, PersistentApplyRequest,
                    PersistentApplyResult, RuntimeTuningRequest,
                    RuntimeTuningResult)


def _status_result(args: argparse.Namespace) -> RuntimeTuningResult:
    system = platform.system()
    request = RuntimeTuningRequest(mode="auto", latency=getattr(args, "latency", None))
    checks = []
    actions = []

    if system == "Linux":
        checks.append(linux.check_nice_limit(request.linux_nice))
        checks.append(linux.check_memlock_limit(request.linux_memlock_kb))
        checks.append(linux.check_cpu_governor())
    elif system == "Windows":
        checks.extend(
            [
                windows.check_mmcss_service(),
                windows.check_audio_service(),
                windows.check_power_plan(),
            ]
        )
    else:
        checks.append(
            CheckResult(
                id="realtime.platform",
                title="Realtime platform support",
                status="not_applicable",
                severity="info",
                detail=f"no realtime status checks implemented for {system}",
                evidence={"system": system},
            )
        )

    if (
        getattr(args, "input", None) is not None
        and getattr(args, "output", None) is not None
    ):
        from ..bridge.config import build_run_config

        with RealtimeRuntimeManager(request) as manager:
            config = build_run_config(
                args,
                realtime_manager=manager,
                app_microphone_name=None,
            )
            route_result = manager.apply_route_tuning(config)
            checks.extend(route_result.checks)
            actions.extend(route_result.actions)

    return RuntimeTuningResult(
        mode="auto", checks=tuple(checks), actions=tuple(actions)
    )


def realtime_status_command(args: argparse.Namespace) -> int:
    result = _status_result(args)
    if args.json:
        print_json_result(result, system=platform.system(), mode="status")
    else:
        print_human_status(result)

    return 0 if result.ok else 2


def realtime_apply_command(args: argparse.Namespace) -> int:
    request = PersistentApplyRequest(
        yes=bool(args.yes),
        linux_rtprio=int(args.rtprio),
        linux_nice=int(args.nice),
        linux_memlock_kb=int(args.memlock_kb),
        windows_power=not bool(args.no_power),
        windows_services=not bool(args.no_services),
    )

    system = platform.system()
    if system == "Linux":
        result = linux.apply_persistent_setup(request)
    elif system == "Windows":
        result = windows.apply_persistent_setup(request)
    else:
        result = PersistentApplyResult(
            actions=(
                AppliedAction(
                    id="realtime.apply",
                    title="Persistent realtime setup",
                    status="not_applicable",
                    detail=f"persistent realtime setup is not implemented for {system}",
                    evidence={"system": system},
                ),
            )
        )

    if args.json:
        print_json_result(result, system=system, mode="apply")
    else:
        print_human_status(result, title="Realtime persistent setup")

    return 0 if result.ok else 2


def realtime_command(args: argparse.Namespace) -> int:
    if args.realtime_command == "status":
        return realtime_status_command(args)
    if args.realtime_command == "apply":
        return realtime_apply_command(args)
    raise RuntimeError("Internal error: unhandled realtime command.")
