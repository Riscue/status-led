"""Custom build step: bundle non-Python data (firmware/, integrations/)
into the wheel.

These dirs live at the repo root for developer ergonomics — non-Python
files don't belong under src/. setuptools' package-data mechanism only
sees files inside the package, so we override build_py to mirror them
into build_lib/status_led/ before the wheel stage picks them up.

Editable installs (pipx install -e .) don't run this — they symlink the
source tree, so the dev-path resolution in profiles.py and upload_firmware.py
handles finding root-level dirs directly.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

REPO_ROOT = Path(__file__).parent.resolve()
EXCLUDES = {".pio", "__pycache__", ".git", "*.pyc", "*.pyo"}


class build_py_with_bundled_data(build_py):
    def run(self):
        super().run()
        if not self.dry_run:
            for name in ("firmware", "integrations"):
                src = REPO_ROOT / name
                if src.is_dir():
                    self._copy(src, Path(self.build_lib) / "status_led" / name)

    def _copy(self, src: Path, dst: Path) -> None:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns(*EXCLUDES))


setup(cmdclass={"build_py": build_py_with_bundled_data})
