from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any

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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.close()

    def verify(
        self,
        formal_statement: str,
        content: str,
        *,
        use_def_eq: bool = True,
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
        process = self.worker.process
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())

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
        return response

    def assert_verified(self, formal_statement: str, content: str) -> dict[str, Any]:
        response = self.verify(formal_statement, content)
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["errors"], [])
        self.assertEqual(response["tool_errors"], [])
        self.assertEqual(response["failed_declarations"], [])
        return response

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
