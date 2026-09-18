from __future__ import annotations

import json
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .compiler import LEAN_TOOLCHAIN, PROJECT_ROOT


LEAN_VERSION = "4.30.0"
MATHLIB_VERSION = "v4.30.0"
COMMAND_TIMEOUT_SECONDS = 120.0


class StartupCheckError(RuntimeError):
    """The local Lean environment does not match the pinned server environment."""


@dataclass(frozen=True, slots=True)
class StartupReport:
    lean_version: str
    mathlib_revision: str
    elapsed_ms: float


def run_startup_checks(project_root: Path = PROJECT_ROOT) -> StartupReport:
    """Validate the pinned toolchain, Lake environment, Mathlib checkout and imports."""

    started = time.perf_counter()
    root = project_root.resolve()
    expected_mathlib_revision = _check_configuration(root)

    direct_version = _run_command(
        ["elan", "run", LEAN_TOOLCHAIN, "lean", "--version"], cwd=root
    )
    _require_lean_version(direct_version, "pinned Lean toolchain")

    lake_version = _run_command(
        ["elan", "run", LEAN_TOOLCHAIN, "lake", "env", "lean", "--version"], cwd=root
    )
    _require_lean_version(lake_version, "Lake Lean environment")

    mathlib_checkout = root / ".lake" / "packages" / "mathlib"
    actual_mathlib_revision = _run_command(
        ["git", "-C", str(mathlib_checkout), "rev-parse", "HEAD"], cwd=root
    ).strip()
    if actual_mathlib_revision != expected_mathlib_revision:
        raise StartupCheckError(
            "Mathlib checkout revision mismatch: "
            f"expected {expected_mathlib_revision}, got {actual_mathlib_revision}"
        )

    _run_command(
        [
            "elan",
            "run",
            LEAN_TOOLCHAIN,
            "lake",
            "env",
            "lean",
            "--json",
            "--stdin",
        ],
        cwd=root,
        input_text="import Mathlib\n#check Nat\n",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return StartupReport(
        lean_version=LEAN_VERSION,
        mathlib_revision=actual_mathlib_revision,
        elapsed_ms=elapsed_ms,
    )


def _check_configuration(project_root: Path) -> str:
    toolchain_path = project_root / "lean-toolchain"
    try:
        configured_toolchain = toolchain_path.read_text().strip()
    except OSError as exc:
        raise StartupCheckError(f"cannot read {toolchain_path}: {exc}") from exc
    if configured_toolchain != LEAN_TOOLCHAIN:
        raise StartupCheckError(
            f"lean-toolchain must contain {LEAN_TOOLCHAIN!r}, got {configured_toolchain!r}"
        )

    lakefile_path = project_root / "lakefile.toml"
    try:
        with lakefile_path.open("rb") as lakefile:
            lake_config = tomllib.load(lakefile)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StartupCheckError(f"cannot read {lakefile_path}: {exc}") from exc
    requirements = lake_config.get("require", [])
    mathlib = next(
        (item for item in requirements if isinstance(item, dict) and item.get("name") == "mathlib"),
        None,
    )
    if mathlib is None or mathlib.get("rev") != MATHLIB_VERSION:
        configured = None if mathlib is None else mathlib.get("rev")
        raise StartupCheckError(
            f"lakefile.toml must pin Mathlib to {MATHLIB_VERSION!r}, got {configured!r}"
        )

    manifest_path = project_root / "lake-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StartupCheckError(f"cannot read {manifest_path}: {exc}") from exc
    packages = manifest.get("packages", []) if isinstance(manifest, dict) else []
    mathlib_entry = next(
        (item for item in packages if isinstance(item, dict) and item.get("name") == "mathlib"),
        None,
    )
    if mathlib_entry is None or mathlib_entry.get("inputRev") != MATHLIB_VERSION:
        configured = None if mathlib_entry is None else mathlib_entry.get("inputRev")
        raise StartupCheckError(
            f"lake-manifest.json must resolve Mathlib {MATHLIB_VERSION!r}, got {configured!r}"
        )
    revision = mathlib_entry.get("rev")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise StartupCheckError("lake-manifest.json contains an invalid Mathlib revision")
    return revision


def _require_lean_version(output: str, source: str) -> None:
    match = re.search(r"Lean \(version ([^,\s)]+)", output)
    actual = match.group(1) if match else None
    if actual != LEAN_VERSION:
        raise StartupCheckError(
            f"{source} must use Lean {LEAN_VERSION}, got {actual or output.strip()!r}"
        )


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise StartupCheckError(f"required executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise StartupCheckError(
            f"startup command exceeded {COMMAND_TIMEOUT_SECONDS:g} seconds: {' '.join(command)}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise StartupCheckError(
            f"startup command failed ({completed.returncode}): {' '.join(command)}: {detail}"
        )
    return completed.stdout
