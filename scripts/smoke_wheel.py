"""Install the built wheel into a clean venv and verify packaged assets away from source."""

import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

wheel = next(Path("dist").glob("*.whl")).resolve()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    venv.create(root / "venv", with_pip=True)
    python = root / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)
    completed = subprocess.run(
        [str(python), "-m", "retrace", "--db", str(root / "smoke.db"), "demo"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["outputs"]["report"]["vectors"] == 512
    subprocess.run(
        [
            str(python),
            "-c",
            "from importlib.resources import files; "
            "assert files('retrace').joinpath('static','index.html').is_file(); "
            "assert files('retrace').joinpath('static','app.js').is_file()",
        ],
        cwd=root,
        check=True,
    )
    print("Clean wheel install, demo, and bundled inspector assets: OK")
