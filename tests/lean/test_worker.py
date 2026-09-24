from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.lean.schema_assertions import assert_matches_schema
from tests.lean.worker_harness import PROJECT_ROOT, Worker


PROTOCOL = PROJECT_ROOT / "protocol"
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

    def test_real_output_matches_schema_and_golden_files(self) -> None:
        ready_schema = read_json(PROTOCOL / "ready.schema.json")
        result_schema = read_json(PROTOCOL / "compile-result.schema.json")
        ready_golden = read_json(EXAMPLES / "ready.json")
        success_golden = read_json(EXAMPLES / "compile-success.json")

        with Worker() as worker:
            self.assertEqual(worker.ready_json, ready_golden)
            assert_matches_schema(worker.ready_json, ready_schema)
            with self.assertRaisesRegex(AssertionError, "unexpected properties"):
                assert_matches_schema({**worker.ready_json, "debug": "leak"}, ready_schema)
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after ready")

            success_json, success = worker.compile_wire("req-1", "def answer : Nat := 42")
            assert_matches_schema(success_json, result_schema)
            self.assertEqual(success.status, "ok")
            expected = {**success_golden, "compile_ms": success_json["compile_ms"],
                        "timings": success_json["timings"]}
            self.assertEqual(success_json, expected)
            self.assertGreater(success.timings.elaboration_ms, 0)
            self.assertEqual(success.timings.profiling_ms, 0)
            self.assertLessEqual(sum(success_json["timings"].values()), success.compile_ms)
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after success result")

            error_json, error = worker.compile_wire("req-error", "def answer : Nat := true")
            assert_matches_schema(error_json, result_schema)
            self.assertEqual(error.status, "compile_error")
            self.assertTrue(worker.stdout_is_quiet(), "unexpected stdout after error result")

    def test_slow_profile_contains_command_and_tactic_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Worker(env={"LEAN_SERVER_PROFILE_DIR": directory,
                             "LEAN_SERVER_PROFILE_SLOW_MS": "100",
                             "LEAN_SERVER_PROFILE_THRESHOLD_MS": "0"}) as worker:
                result = worker.compile("profile/../request", """\
run_cmd Lean.Elab.Command.liftIO (IO.sleep 150)
theorem profiledTarget : True := by trivial
""")
                self.assertEqual(result.status, "ok", result)
                self.assertGreaterEqual(result.timings.elaboration_ms, 100)
                self.assertGreater(result.timings.profiling_ms, 0)
                paths = list(Path(directory).glob("lean-*.json"))
                self.assertEqual(len(paths), 1)
                profile = json.loads(paths[0].read_text())
                self.assertTrue(profile["threads"])
                self.assertTrue(any("profile/../request <stdin>" in thread["name"]
                                    for thread in profile["threads"]))
                labels = [label for thread in profile["threads"] for label in thread["stringArray"]]
                self.assertTrue(any("profiledTarget" in label for label in labels), labels)
                self.assertTrue(any("trivial" in label for label in labels), labels)
                self.assertTrue(worker.stdout_is_quiet())
                fast = worker.compile("fast", "example : True := True.intro")
                self.assertEqual(fast.status, "ok")
                self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_profile_export_failure_does_not_change_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "file"
            blocked.write_text("not a directory")
            with Worker(env={"LEAN_SERVER_PROFILE_DIR": str(blocked),
                             "LEAN_SERVER_PROFILE_SLOW_MS": "0"}) as worker:
                result = worker.compile("export-failure", "example : True := True.intro")
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.errors, ())
                self.assertTrue(worker.stdout_is_quiet())

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

    def test_rejects_non_v2_requests_and_continues(self) -> None:
        with Worker() as worker:
            assert worker.process.stdin is not None
            for version, message_type in ((1, "compile"), (3, "compile"), (1, "verify")):
                request = {
                    "protocol_version": version,
                    "type": message_type,
                    "request_id": "unsupported",
                    "code": "example : True := True.intro",
                    "formal_statement": "theorem t : True := by sorry",
                    "content": "theorem t : True := True.intro",
                }
                worker.process.stdin.write(json.dumps(request) + "\n")
                worker.process.stdin.flush()
                # Unsupported requests must not emit a result or terminate the worker.
                result = worker.compile("following", "example : True := True.intro")
                self.assertEqual(result.status, "ok")

    def test_historical_repeated_diagnostic_does_not_expand_transport(self) -> None:
        # Exact candidate from historical record 0ae3c609-56ae-5904-9cf3-51d4cb9d0221.
        candidate = (PROJECT_ROOT / "tests/fixtures/repeated-diagnostic.lean").read_text()
        with Worker() as worker:
            # Previously: 197,802 identical errors, producing a 27 MB NDJSON line.
            result = worker.compile("historical-repeated-diagnostic", candidate)
            self.assertEqual(result.status, "compile_error")
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(result.errors[0].message, "No goals to be solved")
            self.assertEqual(worker.compile("following", "example : True := True.intro").status, "ok")

    def test_unsupported_worker_fields_are_not_silently_ignored(self) -> None:
        with Worker() as worker:
            assert worker.process.stdin is not None
            base = {
                "protocol_version": 2, "type": "verify", "request_id": "unsupported",
                "formal_statement": "theorem target : True := by sorry",
                "content": "theorem target : True := True.intro", "use_def_eq": True,
            }
            for field, value in (("permitted_sorries", ["target"]), ("mathlib_options", True),
                                 ("global_options", {"maxHeartbeats": 0}), ("verify_negation", True),
                                 ("ignore_imports", False), ("use_def_eqq", False)):
                with self.subTest(field=field):
                    worker.process.stdin.write(json.dumps({**base, field: value}) + "\n")
                    worker.process.stdin.flush()
                    result = worker.compile("following", "example : True := True.intro")
                    self.assertEqual(result.status, "ok")

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
