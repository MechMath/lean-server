import Lean.Elab.Frontend
import LeanServerWorker.Protocol

namespace LeanServerWorker

open Lean

structure CompileOutput where
  warnings : Array Diagnostic := #[]
  errors : Array Diagnostic := #[]

structure ElaborationOutput extends CompileOutput where
  env : Environment
  commands : Array Syntax := #[]

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
    (fileName := "<stdin>") (validateRequestedImports := true) : IO ElaborationOutput := do
  let inputContext := Parser.mkInputContext code fileName
  let (header, parserState, headerMessages) ← Parser.parseHeader inputContext
  if let some importError :=
      if validateRequestedImports then validateImports baseEnv header else none then
    let headerOutput ← diagnosticsFromMessages headerMessages
    return {
      warnings := headerOutput.warnings
      errors := headerOutput.errors.push {
        severity := "error"
        message := importError
        fileName := some fileName
        startPos := some { line := 1, column := 0 }
      }
      env := baseEnv
    }
  let commandState := Elab.Command.mkState baseEnv headerMessages options
  let state ← Lean.Elab.IO.processCommands inputContext parserState commandState
  let output ← diagnosticsFromMessages state.commandState.messages
  return { output with env := state.commandState.env, commands := state.commands }

def compileCode (baseEnv : Environment) (options : Options) (code : String) : IO CompileOutput := do
  return (← elaborateCode baseEnv options code).toCompileOutput

end LeanServerWorker
