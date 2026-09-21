from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tests.lean.schema_assertions import assert_matches_schema
from tests.lean.worker_harness import PROJECT_ROOT, Worker


PROTOCOL = PROJECT_ROOT / "protocol" / "v1"
EXAMPLES = PROTOCOL / "examples"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class LeanWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            ["lake", "build", "lean-server-worker"],
            cwd=PROJECT_ROOT,
            check=True,
        )

    def test_mathlib_code_and_warning(self) -> None:
        with Worker() as worker:
            success = worker.compile(
                "success",
                "import Mathlib\nexample : 1 + 1 = 2 := by norm_num",
            )
            self.assertEqual(success.status, "ok")
            self.assertEqual(success.errors, ())

            warning = worker.compile(
                "warning",
                "def constant (unused : Nat) : Nat := 42",
            )
            self.assertEqual(warning.status, "ok")
            self.assertTrue(any("unused variable" in item.message for item in warning.warnings))

    def test_real_output_matches_frozen_schema_and_golden_files(self) -> None:
        ready_schema = read_json(PROTOCOL / "ready.schema.json")
        result_schema = read_json(PROTOCOL / "result.schema.json")
        ready_golden = read_json(EXAMPLES / "ready.json")
        success_golden = read_json(EXAMPLES / "success.json")

        with Worker() as worker:
            self.assertEqual(worker.ready_json, ready_golden)
            assert_matches_schema(worker.ready_json, ready_schema)
            with self.assertRaisesRegex(AssertionError, "unexpected properties"):
                assert_matches_schema({**worker.ready_json, "debug": "leak"}, ready_schema)
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after ready")

            success_json, success = worker.compile_wire("req-1", "def answer : Nat := 42")
            assert_matches_schema(success_json, result_schema)
            self.assertEqual(success.status, "ok")
            expected = {**success_golden, "compile_ms": success_json["compile_ms"]}
            self.assertEqual(success_json, expected)
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after success result")

            error_json, error = worker.compile_wire("req-error", "def answer : Nat := true")
            assert_matches_schema(error_json, result_schema)
            self.assertEqual(error.status, "compile_error")
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after error result")

    def test_source_errors_do_not_stop_worker(self) -> None:
        with Worker() as worker:
            for request_id, code in (
                ("parser", "def broken :="),
                ("elaborator", "#check nameThatDoesNotExist"),
                ("type", "def answer : Nat := true"),
            ):
                result = worker.compile(request_id, code)
                self.assertEqual(result.status, "compile_error")
                self.assertTrue(result.errors)
                self.assertIsNotNone(result.errors[0].start)

            recovered = worker.compile("recovered", "def answer : Nat := 42")
            self.assertEqual(recovered.status, "ok")

    def test_historical_repeated_diagnostic_does_not_expand_transport(self) -> None:
        source = (
            PROJECT_ROOT / "tests/fixtures/historical_repeated_diagnostic.lean"
        ).read_text()
        with Worker() as worker:
            # Previously: 197,802 identical errors, producing a 27 MB NDJSON line.
            result = worker.compile("historical-repeated-diagnostic", source)
            self.assertEqual(result.status, "compile_error")
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(result.errors[0].message, "No goals to be solved")
            self.assertEqual(worker.compile("following", "example : True := True.intro").status, "ok")

    def test_identical_messages_at_distinct_locations_are_preserved(self) -> None:
        with Worker() as worker:
            result = worker.compile("positions", "#check missingName\n#check missingName")
            self.assertEqual(result.status, "compile_error")
            self.assertEqual(len(result.errors), 2)
            self.assertEqual({item.start.line for item in result.errors}, {1, 2})

    def test_declarations_options_attributes_and_notation_are_isolated(self) -> None:
        with Worker() as worker:
            first = worker.compile(
                "first",
                "def secret : Nat := 42\n"
                "set_option autoImplicit false\n"
                "attribute [-simp] List.reverse_reverse\n"
                'notation "workerOnlyNotation" => (0 : Nat)',
            )
            self.assertEqual(first.status, "ok")

            declaration = worker.compile("declaration", "#check secret")
            self.assertEqual(declaration.status, "compile_error")

            option = worker.compile("option", "def identity (x : alpha) := x")
            self.assertEqual(option.status, "ok")

            attribute = worker.compile(
                "attribute",
                "example (xs : List Nat) : xs.reverse.reverse = xs := by simp",
            )
            self.assertEqual(attribute.status, "ok")

            notation = worker.compile("notation", "#check workerOnlyNotation")
            self.assertEqual(notation.status, "compile_error")

    def test_unknown_import_is_a_compile_error(self) -> None:
        with Worker() as worker:
            result = worker.compile("unknown-import", "import Does.Not.Exist\ndef n := 1")
            self.assertEqual(result.status, "compile_error")
            self.assertIn("unknown module", result.errors[0].message)

    def test_one_hundred_sequential_requests(self) -> None:
        with Worker() as worker:
            for index in range(100):
                result = worker.compile(
                    f"sequential-{index}",
                    f"example : {index} + 1 = {index + 1} := by norm_num",
                )
                self.assertEqual(result.status, "ok", result.errors)


if __name__ == "__main__":
    unittest.main()
