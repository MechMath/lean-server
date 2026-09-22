# ADR-0005: AXLE-compatible `verify_proof` API

- Status: Proposed
- Date: 2026-09-18

[ADR-0009](0009-semantic-verification-contract.md) defines the implemented input
scope and compatibility decisions.

## Context

`lean-eval-toolkit` uses the official `AxleClient`. Compatible paths and core
response fields let it switch services by changing `api_url`.

## Decision

Implement:

```text
POST /verify_proof
```

Initial request fields:

```json
{
  "formal_statement": "import Mathlib\ntheorem ... := by sorry",
  "content": "import Mathlib\ntheorem ... := by ...",
  "environment": "lean-4.30.0",
  "permitted_sorries": [],
  "mathlib_options": false,
  "use_def_eq": true,
  "ignore_imports": true,
  "timeout_seconds": 120
}
```

Required compatible response fields:

```json
{
  "okay": false,
  "content": "...normalized candidate...",
  "lean_messages": {"errors": [], "warnings": [], "infos": []},
  "tool_messages": {"errors": [], "warnings": [], "infos": []},
  "timings": {
    "total_ms": 0,
    "formal_statement_ms": 0,
    "declarations_ms": 0,
    "candidate_ms": 0
  },
  "failed_declarations": []
}
```

Confirm compatibility through official `axiom-axle` SDK integration tests, beyond
handwritten HTTP requests. Reject unsupported parameter combinations explicitly.
Test status codes and bodies with the SDK so existing retries recognize overload
and infrastructure failures.

## Consequences

- Clients switch between remote AXLE and local service by configuration.
- Pin the tested SDK version and add contract tests as its schema evolves.
- Compatibility covers the required `verify_proof` subset, not all AXLE tools.
