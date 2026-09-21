import LeanServerWorker.Verifier

namespace LeanServerWorker

open Lean

private def elapsedMilliseconds (started finished : Nat) : Float :=
  (finished - started).toFloat / 1000000.0

private def trimLineEnding (line : String) : String :=
  let line := if line.endsWith "\n" then (line.dropEnd 1).toString else line
  if line.endsWith "\r" then (line.dropEnd 1).toString else line

private def writeMessage (stdout : IO.FS.Stream) (json : Json) : IO Unit := do
  stdout.putStrLn json.compress
  stdout.flush

private def handleCompileRequest (baseEnv : Environment) (options : Options)
    (stdout : IO.FS.Stream) (request : CompileRequest) : IO Unit := do
  let started ← IO.monoNanosNow
  try
    let output ← compileCode baseEnv options request.code
    let finished ← IO.monoNanosNow
    let status := if output.errors.isEmpty then ResultStatus.ok else ResultStatus.compileError
    writeMessage stdout <| resultJson request.requestId status
      (elapsedMilliseconds started finished) output.warnings output.errors
  catch exception =>
    let finished ← IO.monoNanosNow
    let diagnostic := internalErrorDiagnostic exception.toString
    writeMessage stdout <| resultJson request.requestId .internalError
      (elapsedMilliseconds started finished) #[] #[diagnostic]

private def handleVerifyRequest (baseEnv : Environment) (options : Options)
    (stdout : IO.FS.Stream) (request : VerifyRequest) : IO Unit := do
  let started ← IO.monoNanosNow
  try
    let output ← verifyProof baseEnv options request.formalStatement request.content
      request.useDefEq
    let finished ← IO.monoNanosNow
    let status := if output.errors.isEmpty then ResultStatus.ok else ResultStatus.compileError
    writeMessage stdout <| verifyResultJson request.requestId status
      (elapsedMilliseconds started finished) output.formalStatementMs output.candidateMs
      output.declarationsMs output.warnings output.errors output.toolErrors
      output.failedDeclarations
  catch exception =>
    let finished ← IO.monoNanosNow
    let diagnostic := internalErrorDiagnostic exception.toString
    writeMessage stdout <| verifyResultJson request.requestId .internalError
      (elapsedMilliseconds started finished) 0 0 0 #[] #[diagnostic] #[] #[]

private def handleRequest (baseEnv : Environment) (options : Options) (stdout : IO.FS.Stream) :
    Request → IO Unit
  | .compile request => handleCompileRequest baseEnv options stdout request
  | .verify request => handleVerifyRequest baseEnv options stdout request

private partial def requestLoop (baseEnv : Environment) (options : Options)
    (stdin stdout : IO.FS.Stream) : IO Unit := do
  let rawLine ← stdin.getLine
  if rawLine.isEmpty then
    return
  let line := trimLineEnding rawLine
  if line.isEmpty then
    IO.eprintln "lean-server-worker: ignoring empty input line"
  else
    match parseRequest line with
    | .ok request => handleRequest baseEnv options stdout request
    | .error message => IO.eprintln s!"lean-server-worker: invalid request: {message}"
  requestLoop baseEnv options stdin stdout

unsafe def main : IO UInt32 := do
  initSearchPath (← findSysroot)
  enableInitializersExecution
  let options : Options := {}
  let baseEnv ← importModules (loadExts := true) #[{ module := `Mathlib }] options
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  writeMessage stdout readyJson
  requestLoop baseEnv options stdin stdout
  return 0

end LeanServerWorker

unsafe def main : IO UInt32 :=
  LeanServerWorker.main
