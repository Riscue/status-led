"""Built-in subcommand registry.

Each handler is `run(argv: list[str]) -> int` — takes the args after the
subcommand name, returns the process exit code.

Why an explicit registry instead of pkgutil.iter_modules auto-discovery:
- Hyphenated names work cleanly (module is `upload_firmware.py`, command is
  `upload-firmware`).
- Test injection is trivial: REGISTRY["mock"] = fake_handler.
- Adding a subcommand is one file + one line here, no magic.
- Avoids accidentally picking up helper modules with non-standard names.
"""
from __future__ import annotations

from typing import Callable

from . import (
    daemon,
    raw,
    service,
    smoke,
    status,
    upload_firmware,
    validate_integrations,
)

REGISTRY: dict[str, Callable[[list[str]], int]] = {
    "daemon": daemon.run,
    "raw": raw.run,
    "service": service.run,
    "smoke-test": smoke.run,
    "status": status.run,
    "upload-firmware": upload_firmware.run,
    "validate-integrations": validate_integrations.run,
}

__all__ = ["REGISTRY"]
