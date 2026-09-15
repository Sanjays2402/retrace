"""Install the built wheel into a clean venv and verify packaged assets away from source."""

import json
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
wheel = Path("dist", f"retrace_engine-{version}-py3-none-any.whl").resolve()
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
            "import retrace, sys; assert retrace.__version__ == sys.argv[1]; "
            "from importlib.resources import files; "
            "assert files('retrace').joinpath('static','index.html').is_file(); "
            "assert files('retrace').joinpath('static','app.js').is_file()",
            version,
        ],
        cwd=root,
        check=True,
    )
    print("Clean wheel install, demo, and bundled inspector assets: OK")
