import Lean.Elab.Frontend
import LeanServerWorker.Protocol

namespace LeanServerWorker

open Lean

structure CompileOutput where
  warnings : Array Diagnostic := #[]
  errors : Array Diagnostic := #[]

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

private def diagnosticsFromMessages (messages : MessageLog) : BaseIO CompileOutput := do
  let mut warnings := #[]
  let mut errors := #[]
  for message in messages.reportedPlusUnreported do
    match message.severity with
    | .warning => warnings := warnings.push (← messageToDiagnostic message)
    | .error => errors := errors.push (← messageToDiagnostic message)
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

def compileCode (baseEnv : Environment) (options : Options) (code : String) : IO CompileOutput := do
  let inputContext := Parser.mkInputContext code "<stdin>"
  let (header, parserState, headerMessages) ← Parser.parseHeader inputContext
  if let some importError := validateImports baseEnv header then
    let headerOutput ← diagnosticsFromMessages headerMessages
    return {
      warnings := headerOutput.warnings
      errors := headerOutput.errors.push {
        severity := "error"
        message := importError
        fileName := some "<stdin>"
        startPos := some { line := 1, column := 0 }
      }
    }
  let commandState := Elab.Command.mkState baseEnv headerMessages options
  let state ← Lean.Elab.IO.processCommands inputContext parserState commandState
  diagnosticsFromMessages state.commandState.messages

end LeanServerWorker
