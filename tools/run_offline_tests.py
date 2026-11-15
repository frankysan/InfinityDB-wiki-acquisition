"""Run the producer's offline pytest suite via the managed script-job lane.

Only tracked test modules are selectable; no shell, network crawler or
arbitrary Python expression is exposed by this helper.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(args: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    for target in args:
        path = root / target
        if (
            not target.startswith("tests/test_")
            or not target.endswith(".py")
            or not path.is_file()
            or path.resolve().parent != root / "tests"
        ):
            raise ValueError(f"Not a permitted offline test module: {target}")
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
