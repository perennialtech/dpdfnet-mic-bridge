from dpdfnet_mic_bridge.realtime_env.windows import \
    build_persistent_command_plan


def test_windows_persistent_command_plan_all():
    plan = build_persistent_command_plan(power=True, services=True)

    assert ("sc", "config", "MMCSS", "start=", "auto") in plan
    assert ("sc", "start", "Audiosrv") in plan
    assert ("powercfg", "/setactive", "SCHEME_MIN") in plan
    assert ("powercfg", "/S", "SCHEME_CURRENT") in plan


def test_windows_persistent_command_plan_can_skip_groups():
    assert build_persistent_command_plan(power=False, services=False) == []
    assert all(
        command[0] == "powercfg"
        for command in build_persistent_command_plan(power=True, services=False)
    )
    assert all(
        command[0] == "sc"
        for command in build_persistent_command_plan(power=False, services=True)
    )
