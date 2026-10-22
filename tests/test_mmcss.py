import ctypes
from ctypes import wintypes
from types import SimpleNamespace

from dpdfnet_mic_bridge.realtime_env import threading as rt
from dpdfnet_mic_bridge.realtime_env.types import RuntimeTuningRequest


class Function:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def test_pointer_sized_mmcss_handle_setup_and_cleanup(monkeypatch):
    handle = 0x123456789ABC
    library = SimpleNamespace(
        AvSetMmThreadCharacteristicsW=Function(handle),
        AvSetMmThreadPriority=Function(1),
        AvRevertMmThreadCharacteristics=Function(1),
    )
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *args, **kwargs: library, raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    result = rt._configure_windows_mmcss(RuntimeTuningRequest(mode="auto"), "worker")
    assert result.ok
    assert library.AvSetMmThreadCharacteristicsW.restype == wintypes.HANDLE
    assert library.AvSetMmThreadPriority.argtypes == [wintypes.HANDLE, ctypes.c_int]
    assert library.AvSetMmThreadPriority.calls[0][0] == handle
    rt.close_thread_realtime_context()
    assert library.AvRevertMmThreadCharacteristics.argtypes == [wintypes.HANDLE]
    assert library.AvRevertMmThreadCharacteristics.calls == [(handle,)]
