import LeanServerWorker.Verifier

open Lean LeanServerWorker

private def expectError (env : Environment) (kind : String) (action : MetaM Bool) : IO Unit := do
  let result ← runComparison env {} `target "type" action
  match result with
  | .ok _ => throw <| IO.userError s!"{kind} was converted to a comparison verdict"
  | .error error =>
    unless error.kind == kind && error.declaration == "target" && error.phase == "type" &&
        !error.message.isEmpty do
      throw <| IO.userError s!"unexpected comparison error: {error.kind}: {error.message}"

def main : IO Unit := do
  let env ← mkEmptyEnvironment
  expectError env "resource_limit" do
    liftM <| Lean.Core.throwMaxHeartbeat `test `maxHeartbeats 1000
    pure true
  expectError env "resource_limit" <| throwMaxRecDepthAt Syntax.missing
  expectError env "interrupted" throwInterruptException
  expectError env "internal_error" <| throwError "synthetic comparison failure"
  for verdict in [true, false] do
    match ← runComparison env {} `target "value" (pure verdict) with
    | .ok actual => unless actual == verdict do throw <| IO.userError "changed verdict"
    | .error _ => throw <| IO.userError "ordinary comparison became an error"
