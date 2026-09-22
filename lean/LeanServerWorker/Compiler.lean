import Lean.Elab.Frontend
import LeanServerWorker.Protocol

namespace LeanServerWorker

open Lean

structure CompileOutput where
  warnings : Array Diagnostic := #[]
  errors : Array Diagnostic := #[]
  timings : ElaborationTimings := {}

structure ProfilingConfig where
  directory : Option System.FilePath := none
  slowMs : Nat := 1000
  thresholdMs : Nat := 10
  requestId : String := ""

structure ElaborationOutput extends CompileOutput where
  env : Environment
  commands : Array Syntax := #[]

private def elapsedMilliseconds (started finished : Nat) : Float :=
  (finished - started).toFloat / 1000000.0

private def saveSlowProfile (config : ProfilingConfig) (options : Options)
    (fileName : String) (started : Nat) (elapsedMs : Float)
    (state : Elab.IncrementalState) : IO Unit := do
  let some directory := config.directory | return
  if elapsedMs < config.slowMs.toFloat then return
  try
    let traces := (Language.toSnapshotTree state.initialSnap).getAll.map (·.traces)
    let profile ← Firefox.Profile.export s!"{config.requestId} {fileName}"
      (started.toFloat / 1000000000.0) traces options
    IO.FS.createDirAll directory
    -- Only worker-generated components enter the file name, never request IDs or source paths.
    let path := directory / s!"lean-{← IO.Process.getPID}-{started}.json"
    IO.FS.writeFile path (toJson profile).compress
    IO.eprintln <| (Json.mkObj [
      ("type", toJson "lean_profile"), ("request_id", toJson config.requestId),
      ("phase", toJson fileName), ("elaboration_ms", toJson elapsedMs),
      ("path", toJson path.toString)]).compress
  catch exception =>
    -- Observability failures must not change compilation or verification results.
    IO.eprintln s!"lean-server-worker: profile export failed: {exception}"

private def validPosition (position : Position) : Option Position :=
  if position.line == 0 then none else some position

private def messageToDiagnostic (message : Message) : BaseIO Diagnostic := do
  let serialized ← message.serialize
  return {
    severity := serialized.severity.toString
    message := serialized.data
    fileName := some serialized.fileName
    startPos := validPosition serialized.pos
    endPos := serialized.endPos.bind validPosition
  }

private abbrev DiagnosticKey :=
  String × String × Option String × Option (Nat × Nat) × Option (Nat × Nat)

private def diagnosticKey (diagnostic : Diagnostic) : DiagnosticKey :=
  (diagnostic.severity, diagnostic.message, diagnostic.fileName,
    diagnostic.startPos.map fun pos => (pos.line, pos.column),
    diagnostic.endPos.map fun pos => (pos.line, pos.column))

def diagnosticsFromMessages (messages : MessageLog) : BaseIO CompileOutput := do
  let mut warnings := #[]
  let mut errors := #[]
  -- Tactic expansion can repeat the same diagnostic hundreds of thousands of times.
  -- Keep distinct locations and messages while avoiding transport amplification.
  let mut seen : Std.HashSet DiagnosticKey := {}
  for message in messages.reportedPlusUnreported do
    if message.severity == .information then
      continue
    let diagnostic ← messageToDiagnostic message
    let key := diagnosticKey diagnostic
    if seen.contains key then
      continue
    seen := seen.insert key
    match message.severity with
    | .warning => warnings := warnings.push diagnostic
    | .error => errors := errors.push diagnostic
    | .information => pure ()
  return { warnings, errors }

private def validateImports (baseEnv : Environment) (header : Elab.HeaderSyntax) : Option String :=
  let available := baseEnv.allImportedModuleNames
  let requested := header.imports
  requested.findSome? fun requestedImport =>
    if available.contains requestedImport.module then
      none
    else
      some s!"unknown module '{requestedImport.module}' in fixed Mathlib environment"

def elaborateCode (baseEnv : Environment) (options : Options) (code : String)
    (fileName := "<stdin>") (validateRequestedImports := true)
    (profiling : ProfilingConfig := {}) : IO ElaborationOutput := do
  let started ← IO.monoNanosNow
  let options := if profiling.directory.isSome then
      options.setBool `trace.profiler true
        |>.set `trace.profiler.threshold profiling.thresholdMs
        |>.setBool `trace.profiler.output.pp true
        |>.set `trace.profiler.output "<worker-managed-profile>"
    else options
  let inputContext := Parser.mkInputContext code fileName
  let (header, parserState, headerMessages) ← Parser.parseHeader inputContext
  if let some importError :=
      if validateRequestedImports then validateImports baseEnv header else none then
    let headerFinished ← IO.monoNanosNow
    let headerOutput ← diagnosticsFromMessages headerMessages
    let diagnosticsFinished ← IO.monoNanosNow
    return {
      warnings := headerOutput.warnings
      errors := headerOutput.errors.push {
        severity := "error"
        message := importError
        fileName := some fileName
        startPos := some { line := 1, column := 0 }
      }
      env := baseEnv
      timings := {
        headerMs := elapsedMilliseconds started headerFinished
        diagnosticsMs := elapsedMilliseconds headerFinished diagnosticsFinished
      }
    }
  let headerFinished ← IO.monoNanosNow
  let commandState := Elab.Command.mkState baseEnv headerMessages options
  let (state, profileState?) ← if profiling.directory.isSome then do
      let state ← Elab.IO.processCommandsIncrementally inputContext parserState commandState none
      pure (state.toState, some state)
    else do
      let state ← Elab.IO.processCommands inputContext parserState commandState
      pure (state, none)
  let elaborationFinished ← IO.monoNanosNow
  let output ← diagnosticsFromMessages state.commandState.messages
  let diagnosticsFinished ← IO.monoNanosNow
  let elaborationMs := elapsedMilliseconds headerFinished elaborationFinished
  let mut profilingMs := 0
  if let some profileState := profileState? then
    if elaborationMs >= profiling.slowMs.toFloat then
      saveSlowProfile profiling options fileName headerFinished elaborationMs profileState
      profilingMs := elapsedMilliseconds diagnosticsFinished (← IO.monoNanosNow)
  return { output with
    env := state.commandState.env
    commands := state.commands
    timings := {
      headerMs := elapsedMilliseconds started headerFinished
      elaborationMs
      diagnosticsMs := elapsedMilliseconds elaborationFinished diagnosticsFinished
      profilingMs
    }
  }

def compileCode (baseEnv : Environment) (options : Options) (code : String)
    (profiling : ProfilingConfig := {}) : IO CompileOutput := do
  return (← elaborateCode baseEnv options code (profiling := profiling)).toCompileOutput

end LeanServerWorker
