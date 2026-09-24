# ADR-0006: Strict verification in Lean

- Status: Proposed
- Date: 2026-09-18

[ADR-0009](0009-semantic-verification-contract.md) defines accepted equality,
complete-statement and import behavior.

## Context

Lean compilation accepts `sorry`, new axioms and changed theorem statements.
Python text matching cannot reliably handle namespaces, notation, macros or
definitional equality.

## Decision

Implement verification as a Lean metaprogram. Elaborate formal and candidate
independently from the same fixed base environment:

1. Record base declarations.
2. Elaborate the formal statement and identify new required declarations.
3. Elaborate the candidate independently.
4. Find each formal target by its full name.
5. Compare declaration kinds.
6. Compare elaborated types by definitional equality when `use_def_eq=true`,
   otherwise by unreduced expression structure.
7. Compare types and values of definitions with fixed implementations;
   capture compatibility rules in golden tests.
8. Check candidate declarations and target dependencies for `sorryAx`, axioms and safety.
9. Return failed declarations and structured errors.

Allow only standard axioms by default:

- `propext`
- `Quot.sound`
- `Classical.choice`

With `permitted_sorries=[]`, reject `sorry`, unapproved axioms and unsafe targets
or reachable dependencies. Extra helpers may exist but cannot introduce invalid
dependencies. Check elaborated declarations; source scans are only a preliminary
filter for banned commands.

## Limits

Like AXLE, the first version avoids full environment replay. Crafted metaprograms
may bypass normal kernel-checked declaration paths. The local single-tenant model
accepts this limit; fixed imports, disabled networking, read-only filesystems and
worker recycling reduce exposure.

Hostile-input guarantees would require evaluating SafeVerify, Comparator or kernel
replay. Do not claim safety for arbitrary malicious Lean.

## Required cases

- Accept correct proofs and valid `Classical.choice` use.
- Reject weakened, renamed or reparameterized theorems.
- Reject direct or transitive `sorry`.
- Reject `axiom bad : False` and macros introducing axioms.
- Reject `unsafeCast` and unsafe declarations.
- Reject partial implementations of multiple targets; list missing declarations.
- Reject namespace shadowing using matching short names.
- Return parser/elaborator errors as verification failures, not infrastructure errors.
