import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux resource limits are platform-specific", allow_module_level=True)

from dpdfnet_mic_bridge.realtime_env.linux import render_pam_limits_content


def test_render_pam_limits_content_exact():
    assert render_pam_limits_content(
        "alice",
        rtprio=30,
        nice=-10,
        memlock_kb=1048576,
    ) == (
        "# Managed by dpdfnet-mic-bridge.\n"
        "# Grants the current user enough scheduling and memory-lock budget for low-latency audio.\n"
        "alice - rtprio 30\n"
        "alice - nice -10\n"
        "alice - memlock 1048576\n"
    )
