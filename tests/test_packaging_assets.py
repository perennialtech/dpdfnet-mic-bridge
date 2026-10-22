import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("layout", ["stdlib", "prefix", "standalone"])
def test_python_license_layouts(tmp_path, layout):
    script = Path(__file__).resolve().parents[1] / "packaging/stage_notices.py"
    spec = importlib.util.spec_from_file_location("stage_notices_for_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix, stdlib = tmp_path / "python", tmp_path / "python/Lib"
    stdlib.mkdir(parents=True)
    path = (
        stdlib / "LICENSE.txt"
        if layout == "stdlib"
        else prefix
        / ("LICENSE.cpython.txt" if layout == "standalone" else "LICENSE.txt")
    )
    path.write_text("Python license fixture")
    assert module.find_python_license(prefix, stdlib) == path
