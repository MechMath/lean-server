from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lean_server.preflight import StartupCheckError, run_startup_checks


MATHLIB_REVISION = "a" * 40


def create_project(root: Path, *, toolchain: str = "leanprover/lean4:v4.30.0") -> None:
    (root / "lean-toolchain").write_text(toolchain + "\n")
    (root / "lakefile.toml").write_text(
        'name = "test"\n[[require]]\nname = "mathlib"\nrev = "v4.30.0"\n'
    )
    (root / "lake-manifest.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "mathlib",
                        "inputRev": "v4.30.0",
                        "rev": MATHLIB_REVISION,
                    }
                ]
            }
        )
    )


class StartupCheckTests(unittest.TestCase):
    def test_accepts_matching_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_project(root)
            outputs = [
                "Lean (version 4.30.0, test, Release)\n",
                "Lean (version 4.30.0, test, Release)\n",
                MATHLIB_REVISION + "\n",
                '{"severity":"information","data":"Nat : Type"}\n',
            ]
            with patch("lean_server.preflight._run_command", side_effect=outputs) as run:
                report = run_startup_checks(root)

            self.assertEqual(report.lean_version, "4.30.0")
            self.assertEqual(report.mathlib_revision, MATHLIB_REVISION)
            self.assertEqual(run.call_count, 4)
            self.assertEqual(
                run.call_args_list[-1].kwargs["input_text"],
                "import Mathlib\n#check Nat\n",
            )

    def test_rejects_wrong_toolchain_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_project(root, toolchain="leanprover/lean4:v4.31.0")

            with self.assertRaisesRegex(StartupCheckError, "lean-toolchain"):
                run_startup_checks(root)

    def test_rejects_wrong_runtime_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_project(root)
            with patch(
                "lean_server.preflight._run_command",
                return_value="Lean (version 4.31.0, test, Release)\n",
            ):
                with self.assertRaisesRegex(StartupCheckError, "must use Lean 4.30.0"):
                    run_startup_checks(root)

    def test_rejects_mathlib_checkout_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_project(root)
            outputs = [
                "Lean (version 4.30.0, test, Release)\n",
                "Lean (version 4.30.0, test, Release)\n",
                "b" * 40 + "\n",
            ]
            with patch("lean_server.preflight._run_command", side_effect=outputs):
                with self.assertRaisesRegex(StartupCheckError, "checkout revision mismatch"):
                    run_startup_checks(root)


if __name__ == "__main__":
    unittest.main()
