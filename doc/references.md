# References

## AXLE

- Jimmy Xin et al., [AXLE: A Cloud Infrastructure for Lean 4 Theorem Proving Utilities](https://arxiv.org/abs/2606.26442), 2026.
- [AXLE announcement](https://axiommath.ai/territory/releasing-axle).
- [AXLE Python SDK and CLI](https://github.com/AxiomMath/axiom-lean-engine). The server is not public.
- [`verify_proof` docs](https://github.com/AxiomMath/axiom-lean-engine/blob/main/docs/tools/verify_proof.md).

Design inputs from the paper:

- Each request runs in a separate sandbox process, without network or candidate filesystem writes.
- Environments pin Lean, Mathlib and prebuilt dependencies.
- AXLE and Kimina preload Mathlib to avoid cold starts.
- `verify_proof` checks declarations, `sorry`, unapproved axioms and `unsafe` in Lean.
- AXLE skips full environment replay, a tradeoff unsuitable for fully hostile input.
- Single-machine throughput saturates near the physical core count.

## Reusable implementations

- [Kimina Lean Server](https://github.com/project-numina/kimina-lean-server): MIT-licensed FastAPI server, parallel REPL pool and import cache.
- Marco Dos Santos et al., [Kimina Lean Server: Technical Report](https://arxiv.org/abs/2504.21230), 2025.
- [Lean REPL](https://github.com/leanprover-community/repl): machine interface for Lean.
- [Mathlib](https://github.com/leanprover-community/mathlib4).

## Stronger verification

- [lean4checker](https://github.com/leanprover/lean4checker).
- [Comparator](https://github.com/leanprover/comparator).
- [SafeVerify](https://github.com/GasStationManager/SafeVerify).

These suit stronger threat models. AXLE's experiments show substantial replay and
recheck latency; adopt them based on the threat model, not by default in the hot path.
