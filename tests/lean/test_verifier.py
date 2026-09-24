from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lean_server.protocol import VerifyWorkerResult, decode_worker_message
from tests.lean.schema_assertions import assert_matches_schema
from tests.lean.worker_harness import PROJECT_ROOT, Worker


PROOF_FORMAL = """\
import Mathlib

theorem target (p : Prop) (h : p) : p := by sorry
"""

TRIVIAL_FORMAL = """\
import Mathlib

theorem target : True := by sorry
"""


class LeanVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            ["lake", "build", "lean-server-worker"],
            cwd=PROJECT_ROOT,
            check=True,
        )
        cls.worker = Worker()
        cls.request_index = 0
        cls.result_schema = json.loads(
            (PROJECT_ROOT / "protocol" / "verify-result.schema.json").read_text()
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.close()

    def verify(
        self,
        formal_statement: str,
        content: str,
        *,
        use_def_eq: bool = True,
        omit_use_def_eq: bool = False,
    ) -> dict[str, Any]:
        self.__class__.request_index += 1
        request_id = f"verify-{self.request_index}"
        request = {
            "protocol_version": 2,
            "type": "verify",
            "request_id": request_id,
            "formal_statement": formal_statement,
            "content": content,
            "use_def_eq": use_def_eq,
        }
        if omit_use_def_eq:
            del request["use_def_eq"]
        process = self.worker.process
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        response = json.loads(line)
        assert_matches_schema(response, self.result_schema)
        self.assertIsInstance(decode_worker_message(line), VerifyWorkerResult)

        self.assertEqual(response["protocol_version"], 2)
        self.assertEqual(response["type"], "verify_result")
        self.assertEqual(response["request_id"], request_id)
        self.assertIn(response["status"], ("ok", "compile_error", "internal_error"))
        self.assertIsInstance(response["errors"], list)
        self.assertIsInstance(response["tool_errors"], list)
        self.assertTrue(all(isinstance(item, str) for item in response["tool_errors"]))
        self.assertIsInstance(response["failed_declarations"], list)
        self.assertTrue(
            all(isinstance(item, str) for item in response["failed_declarations"])
        )
        for phase in ("formal_statement", "candidate"):
            self.assertLessEqual(sum(response["timings"][phase].values()), response[f"{phase}_ms"])
        return response

    def assert_verified(self, formal_statement: str, content: str) -> dict[str, Any]:
        response = self.verify(formal_statement, content)
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["errors"], [])
        self.assertEqual(response["tool_errors"], [])
        self.assertEqual(response["failed_declarations"], [])
        self.assertEqual(response["comparison_errors"], [])
        return response

    def test_comparison_resource_limits_are_not_signature_or_value_mismatches(self) -> None:
        cases = (
            ("type", "theorem target : Nat.rec 0 (fun _ n => n + 1) 2000 = 2000 := by sorry",
             "theorem target : 2000 = 2000 := rfl"),
            ("value", "def target : Nat := Nat.rec 0 (fun _ n => n + 1) 2000",
             "def target : Nat := 2000"),
        )
        for phase, formal, candidate in cases:
            with self.subTest(phase=phase):
                response = self.verify(formal, candidate)
                self.assertEqual(response["status"], "ok", response)
                self.assertEqual(response["errors"], [], response)
                self.assertEqual(response["failed_declarations"], [], response)
                self.assertEqual(len(response["comparison_errors"]), 1, response)
                error = response["comparison_errors"][0]
                self.assertEqual(error["declaration"], "target")
                self.assertEqual(error["phase"], phase)
                self.assertEqual(error["kind"], "resource_limit")
                self.assertIn("recursion depth", error["message"])
                self.assertTrue(response["tool_errors"])
                self.assertFalse(any("does not match" in e for e in response["tool_errors"]))
        self.assert_verified(TRIVIAL_FORMAL, "theorem target : True := True.intro")
        mismatch, _ = self.assert_rejected(TRIVIAL_FORMAL, "theorem target : 1 = 1 := rfl")
        self.assertEqual(mismatch["comparison_errors"], [])

    def assert_rejected(
        self,
        formal_statement: str,
        content: str,
        *,
        declaration: str = "target",
    ) -> tuple[dict[str, Any], str]:
        response = self.verify(formal_statement, content)
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["errors"], [])
        self.assertIn(declaration, response["failed_declarations"])
        self.assertTrue(response["tool_errors"], response)
        return response, "\n".join(response["tool_errors"])

    def test_accepts_correct_proof(self) -> None:
        self.assert_verified(
            PROOF_FORMAL,
            """\
import Mathlib

theorem target (p : Prop) (h : p) : p := h
""",
        )

    def test_comparison_exception_tags_survive_meta_io_boundary(self) -> None:
        subprocess.run(
            ["lake", "env", "lean", "--run", "tests/fixtures/comparison-errors.lean"],
            cwd=PROJECT_ROOT, check=True, timeout=60,
        )

    def test_invalid_formal_statement_skips_candidate_and_keeps_worker_usable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "candidate-executed"
            content = (
                f'run_cmd Lean.Elab.Command.liftIO <| '
                f'IO.FS.writeFile {json.dumps(str(marker))} "executed"\n'
                "theorem target : True := True.intro"
            )
            for formal in (
                "theorem target : MissingFormalType := by sorry",
                "theorem target : True :=",
            ):
                with self.subTest(formal=formal):
                    response = self.verify(formal, content)
                    self.assertEqual(response["status"], "compile_error", response)
                    self.assertTrue(response["errors"], response)
                    self.assertTrue(all(
                        error["file_name"] == "<formal_statement>" for error in response["errors"]
                    ), response)
                    self.assertFalse(marker.exists(), "candidate ran despite an invalid statement")
                    self.assertEqual(response["candidate_ms"], 0)
                    self.assertEqual(sum(response["timings"]["candidate"].values()), 0)
                    self.assertEqual(response["declarations_ms"], 0)
                    self.assertGreater(response["formal_statement_ms"], 0)
                    self.assertEqual(response["warnings"], [])
                    self.assertEqual(response["tool_errors"], [])
                    self.assertEqual(response["failed_declarations"], [])

            # Positive control: the same candidate really performs the side effect
            # when the statement is valid, using the same persistent worker.
            self.assert_verified(TRIVIAL_FORMAL, content)
            self.assertEqual(marker.read_text(), "executed")

    def test_compile_and_verify_share_one_protocol_and_process(self) -> None:
        self.assertEqual(self.worker.ready_json["protocol_version"], 2)
        before, _ = self.worker.compile_wire("before-verify", "def hidden : Nat := 42")
        verified = self.assert_verified(TRIVIAL_FORMAL, "theorem target : True := True.intro")
        after, result = self.worker.compile_wire("after-verify", "#check target\n#check hidden")
        self.assertEqual(result.status, "compile_error")
        self.assertEqual(before["protocol_version"], verified["protocol_version"])
        self.assertEqual(after["protocol_version"], verified["protocol_version"])

    def test_default_binders_match_only_in_definitional_equality_mode(self) -> None:
        formal = "theorem target (n : Nat := 60) : n = n := by sorry"
        content = "theorem target (n : Nat) : n = n := rfl"
        self.assert_verified(formal, content)
        structural = self.verify(formal, content, use_def_eq=False)
        self.assertEqual(structural["errors"], [])
        self.assertEqual(structural["failed_declarations"], ["target"])
        self.assertIn("Theorem 'target' does not match expected signature", structural["tool_errors"])
        # Request-local comparison mode must not affect the next verification.
        self.assert_verified(formal, content)
        defaulted = self.verify(formal, content, omit_use_def_eq=True)
        self.assertEqual(defaulted["errors"], [])
        self.assertEqual(defaulted["tool_errors"], [])
        self.assertEqual(defaulted["failed_declarations"], [])

    def test_historical_semantic_compatibility_cases(self) -> None:
        cases = json.loads((PROJECT_ROOT / "tests/fixtures/semantic-compatibility.json").read_text())
        for case in cases:
            with self.subTest(uuid=case["uuid"]):
                self.assert_verified(case["formal_statement"], case["candidate"])
                if case["uuid"] == "Goedel-LM/SFT_dataset_v2=1292094":
                    structural = self.verify(case["formal_statement"], case["candidate"],
                                             use_def_eq=False)
                    self.assertEqual(structural["errors"], [])
                    self.assertTrue(structural["failed_declarations"])
                    self.assertTrue(any("does not match expected signature" in error
                                        for error in structural["tool_errors"]))

    def test_complete_statement_needs_no_sorry_placeholder(self) -> None:
        formal = "theorem target : True := True.intro"
        self.assert_verified(formal, "theorem target : True := by constructor")
        _, errors = self.assert_rejected(formal, "theorem target : 1 = 1 := rfl")
        self.assertIn("does not match expected signature", errors)
        _, errors = self.assert_rejected(formal, "theorem target : True := by sorry")
        self.assertIn("uses 'sorry'", errors)

    def test_statement_without_targets_skips_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "candidate-executed"
            content = (
                f'run_cmd Lean.Elab.Command.liftIO <| '
                f'IO.FS.writeFile {json.dumps(str(marker))} "executed"\n'
                "theorem target : True := True.intro"
            )
            for formal in ("", "import Mathlib", "#check Nat", "-- sorry is just a comment"):
                with self.subTest(formal=formal):
                    response = self.verify(formal, content)
                    self.assertEqual(response["status"], "ok")
                    self.assertEqual(response["errors"], [])
                    self.assertEqual(response["tool_errors"], [
                        "formal_statement contains no verifiable declarations"
                    ])
                    self.assertEqual(response["failed_declarations"], [])
                    self.assertEqual(response["candidate_ms"], 0)
                    self.assertFalse(marker.exists())
            self.assert_verified(TRIVIAL_FORMAL, content)
            self.assertEqual(marker.read_text(), "executed")

    def test_verification_ignores_import_names_but_compilation_validates_them(self) -> None:
        header = "import Legacy.Module.That.Does.Not.Exist\n"
        formal = header + "theorem target : (2 : ℝ) + 2 = 4 := by sorry"
        content = header + "theorem target : (2 : ℝ) + 2 = 4 := by norm_num"
        self.assert_verified(formal, content)
        compiled = self.worker.compile("strict-imports", content)
        self.assertEqual(compiled.status, "compile_error")
        self.assertIn("unknown module", compiled.errors[0].message)
        self.assert_verified(formal, content)

    def test_formal_environment_and_options_do_not_leak_into_candidate(self) -> None:
        response = self.verify(
            "def statementOnly : Nat := 0\ntheorem target : True := by sorry",
            "#check statementOnly\ntheorem target : True := True.intro",
        )
        self.assertEqual(response["status"], "compile_error")
        self.assertTrue(any("statementOnly" in e["message"] for e in response["errors"]))
        self.assert_verified(
            "set_option autoImplicit false\ntheorem target : True := by sorry",
            "def helper (x : α) := x\ntheorem target : True := True.intro",
        )

    def test_accepts_allowed_standard_axioms(self) -> None:
        formal_statement = """\
import Mathlib

theorem uses_propext (h : True ↔ True) : True = True := by sorry

theorem uses_quot_sound {α : Sort u} (r : α → α → Prop) (a b : α) (h : r a b) :
    Quot.mk r a = Quot.mk r b := by sorry

theorem uses_choice {α : Sort u} (h : Nonempty α) : ∃ x : α, x = x := by sorry
"""
        content = """\
import Mathlib

theorem uses_propext (h : True ↔ True) : True = True := propext h

theorem uses_quot_sound {α : Sort u} (r : α → α → Prop) (a b : α) (h : r a b) :
    Quot.mk r a = Quot.mk r b := Quot.sound h

theorem uses_choice {α : Sort u} (h : Nonempty α) : ∃ x : α, x = x :=
  ⟨Classical.choice h, rfl⟩
"""
        self.assert_verified(formal_statement, content)

    def test_rejects_native_decide_axiom(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

theorem target : True := by native_decide
""",
        )
        self.assertIn("Axiom", errors)
        self.assertIn("native_decide.ax_", errors)
        self.assertIn("not in the allowed set of standard axioms", errors)

    def test_rejects_documented_native_decide_disagreement(self) -> None:
        _, errors = self.assert_rejected(
            """\
import Mathlib

theorem number_theory_15221 :
    ((Finset.Ioo 1996 4096).filter fun n => n % 900 = 200 ∨ n % 900 = 600).card = 5 := by
  sorry
""",
            """\
import Mathlib

theorem number_theory_15221 :
    ((Finset.Ioo 1996 4096).filter fun n => n % 900 = 200 ∨ n % 900 = 600).card = 5 := by
  native_decide
""",
            declaration="number_theory_15221",
        )
        self.assertIn("number_theory_15221._native.native_decide.ax_", errors)

    def test_rejects_decide_native_axiom(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

theorem target : True := by decide +native
""",
        )
        self.assertIn("Axiom", errors)
        self.assertIn("_native.decide.ax_", errors)
        self.assertIn("not in the allowed set of standard axioms", errors)

    def test_rejects_explicit_axiom(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

axiom bad : False
theorem target : True := False.elim bad
""",
        )
        self.assertIn("Axiom 'bad' is not in the allowed set of standard axioms", errors)

    def test_rejects_transitive_axiom_dependency(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

axiom bad : False
theorem helper : False := bad
theorem target : True := False.elim helper
""",
        )
        self.assertIn("Axiom 'bad' is not in the allowed set of standard axioms", errors)

    def test_rejects_direct_sorry(self) -> None:
        _, errors = self.assert_rejected(
            PROOF_FORMAL,
            """\
import Mathlib

theorem target (p : Prop) (h : p) : p := by sorry
""",
        )
        self.assertIn("uses 'sorry'", errors)

    def test_rejects_transitive_sorry_dependency(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

theorem helper : False := by sorry
theorem target : True := False.elim helper
""",
        )
        self.assertIn("uses 'sorry'", errors)

    def test_rejects_signature_mismatch(self) -> None:
        _, errors = self.assert_rejected(
            PROOF_FORMAL,
            """\
import Mathlib

theorem target : True := True.intro
""",
        )
        self.assertIn("Theorem 'target' does not match expected signature", errors)

    def test_rejects_signature_changed_by_unfolding_placeholder_definition(self) -> None:
        _, errors = self.assert_rejected(
            """\
import Mathlib

def Family : Nat → Type := by sorry
theorem target : ∀ n : Nat, Nonempty (Family n) := by sorry
""",
            """\
import Mathlib

def Family : Nat → Type := fun _ => Unit
theorem target : ∀ n : Nat, Nonempty Unit := fun _ => ⟨()⟩
""",
        )
        self.assertIn("does not match expected signature", errors)

    def test_accepts_renamed_universe_parameter(self) -> None:
        self.assert_verified(
            """\
import Mathlib

theorem target.{u} {α : Sort u} (value : α) : Nonempty α := by sorry
""",
            """\
import Mathlib

theorem target.{v} {α : Sort v} (value : α) : Nonempty α := ⟨value⟩
""",
        )

    def test_rejects_swapped_universe_parameters(self) -> None:
        _, errors = self.assert_rejected(
            """\
import Mathlib

theorem target.{u, v} (α : Type u) (β : Type v) : True := by sorry
""",
            """\
import Mathlib

theorem target.{u, v} (α : Type v) (β : Type u) : True := True.intro
""",
        )
        self.assertIn("does not match expected signature", errors)

    def test_rejects_missing_declaration(self) -> None:
        _, errors = self.assert_rejected(
            PROOF_FORMAL,
            """\
import Mathlib

theorem other : True := True.intro
""",
        )
        self.assertIn("Missing required declaration 'target'", errors)

    def test_rejects_partial_multi_declaration_candidate(self) -> None:
        response = self.verify(
            """\
import Mathlib

theorem first : True := by sorry
theorem second : True := by sorry
""",
            """\
import Mathlib

theorem first : True := True.intro
""",
        )
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["failed_declarations"], ["second"])
        self.assertIn("Missing required declaration 'second'", response["tool_errors"])

    def test_rejects_namespace_shadowing(self) -> None:
        response = self.verify(
            """\
import Mathlib

namespace Expected
theorem target : True := by sorry
end Expected
""",
            """\
import Mathlib

namespace Other
theorem target : True := True.intro
end Other
""",
        )
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["failed_declarations"], ["Expected.target"])
        self.assertIn(
            "Missing required declaration 'Expected.target'", response["tool_errors"]
        )

    def test_missing_type_dependency_is_a_validation_error(self) -> None:
        response = self.verify(
            """\
import Mathlib

def Expected : Type := Nat
theorem target : Nonempty Expected := by sorry
""",
            """\
import Mathlib

theorem target : Nonempty Nat := ⟨0⟩
""",
        )
        self.assertEqual(response["status"], "ok", response)
        self.assertIn("Expected", response["failed_declarations"])
        self.assertIn(
            "Missing required declaration 'Expected'", response["tool_errors"]
        )

    def test_rejects_changed_inductive_constructors(self) -> None:
        response = self.verify(
            """\
import Mathlib

inductive Color where
  | red
  | blue
""",
            """\
import Mathlib

inductive Color where
  | red
  | blue
  | green
""",
        )
        self.assertEqual(response["status"], "ok", response)
        self.assertIn("Color", response["failed_declarations"])
        self.assertTrue(
            any("metadata" in error for error in response["tool_errors"]), response
        )

    def test_accepts_safe_definition(self) -> None:
        code = """\
import Mathlib

def target : Nat := 0
"""
        self.assert_verified(code, code)

    def test_rejects_changed_recursive_definition(self) -> None:
        formal = """\
def f : Nat → Nat
  | 0 => 0
  | n+1 => f n + 1
theorem target : f 1 = 2 := by sorry
"""
        candidate = formal.replace("f n + 1", "f n + 2").replace("by sorry", "rfl")
        _, errors = self.assert_rejected(formal, candidate, declaration="f")
        self.assertIn("does not match expected value", errors)
        # Structural equality must also check the implementation behind `f._f`.
        structural = self.verify(formal, candidate, use_def_eq=False)
        self.assertIn("f", structural["failed_declarations"], structural)

    def test_accepts_unchanged_recursive_definition_with_new_proof(self) -> None:
        definition = """\
def f : Nat → Nat
  | 0 => 0
  | n+1 => f n + 1
"""
        self.assert_verified(
            definition + "theorem target : f 3 = 3 := by sorry",
            definition + "theorem target : f 3 = 3 := rfl",
        )

    def test_accepts_definitionally_equal_recursive_helper(self) -> None:
        definition = """\
def f : Nat → Nat
  | 0 => 0
  | n+1 => f n + 1
"""
        self.assert_verified(definition, definition.replace("+ 1", "+ (0 + 1)"))

    def test_rejects_changed_mutually_recursive_definition(self) -> None:
        definition = """\
mutual
  def evenStep : Nat → Nat
    | 0 => 0
    | n+1 => oddStep n + 1
  def oddStep : Nat → Nat
    | 0 => 0
    | n+1 => evenStep n + 1
end
"""
        response = self.verify(definition, definition.replace("oddStep n + 1", "oddStep n + 2"))
        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(response["failed_declarations"], response)

    def test_proof_helpers_are_not_part_of_fixed_definition_contract(self) -> None:
        self.assert_verified(
            "theorem target : True := by\n  have unused : True := by sorry\n  sorry",
            "theorem target : True := True.intro",
        )

    def test_rejects_changed_definition_that_depends_on_placeholder(self) -> None:
        _, errors = self.assert_rejected(
            """\
import Mathlib

def seed : Nat := by sorry
def target : Nat := seed
""",
            """\
import Mathlib

def seed : Nat := 0
def target : Nat := 0
""",
        )
        self.assertIn("does not match expected value", errors)

    def test_rejects_unsafe_definition(self) -> None:
        _, errors = self.assert_rejected(
            """\
import Mathlib

def target : Nat := 0
""",
            """\
import Mathlib

unsafe def target : Nat := 0
""",
        )
        self.assertIn("Unsafe declaration 'target' detected", errors)

    def test_rejects_unsafe_cast(self) -> None:
        response = self.verify(
            TRIVIAL_FORMAL,
            """\
import Mathlib

theorem target : True := unsafeCast ()
""",
        )
        self.assertEqual(response["status"], "compile_error", response)
        self.assertTrue(
            any(
                "unsafe declaration 'unsafeCast'" in item["message"]
                for item in response["errors"]
            ),
            response,
        )

    def test_rejects_open_private_command(self) -> None:
        _, errors = self.assert_rejected(
            TRIVIAL_FORMAL,
            """\
import Mathlib

open private mkInfoTree from Lean.Elab.Command
theorem target : True := True.intro
""",
        )
        self.assertIn("Candidate uses banned 'open private' command", errors)


if __name__ == "__main__":
    unittest.main()
