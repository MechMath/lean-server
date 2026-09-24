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
    (profiling : ProfilingConfig) (stdout : IO.FS.Stream) (request : CompileRequest) : IO Unit := do
  let started ← IO.monoNanosNow
  try
    let output ← compileCode baseEnv options request.code
      { profiling with requestId := request.requestId }
    let finished ← IO.monoNanosNow
    let status := if output.errors.isEmpty then ResultStatus.ok else ResultStatus.compileError
    writeMessage stdout <| resultJson request.requestId status
      (elapsedMilliseconds started finished) output.warnings output.errors output.timings
  catch exception =>
    let finished ← IO.monoNanosNow
    let diagnostic := internalErrorDiagnostic exception.toString
    writeMessage stdout <| resultJson request.requestId .internalError
      (elapsedMilliseconds started finished) #[] #[diagnostic]

private def handleVerifyRequest (baseEnv : Environment) (options : Options)
    (profiling : ProfilingConfig) (stdout : IO.FS.Stream) (request : VerifyRequest) : IO Unit := do
  let started ← IO.monoNanosNow
  try
    let output ← verifyProof baseEnv options request.formalStatement request.content
      request.useDefEq { profiling with requestId := request.requestId }
    let finished ← IO.monoNanosNow
    let status := if output.errors.isEmpty then ResultStatus.ok else ResultStatus.compileError
    writeMessage stdout <| verifyResultJson request.requestId status
      (elapsedMilliseconds started finished) output.formalStatementMs output.candidateMs
      output.declarationsMs output.warnings output.errors output.toolErrors
      output.failedDeclarations output.formalTimings output.candidateTimings output.comparisonErrors
  catch exception =>
    let finished ← IO.monoNanosNow
    let diagnostic := internalErrorDiagnostic exception.toString
    writeMessage stdout <| verifyResultJson request.requestId .internalError
      (elapsedMilliseconds started finished) 0 0 0 #[] #[diagnostic] #[] #[]

private def handleRequest (baseEnv : Environment) (options : Options)
    (profiling : ProfilingConfig) (stdout : IO.FS.Stream) :
    Request → IO Unit
  | .compile request => handleCompileRequest baseEnv options profiling stdout request
  | .verify request => handleVerifyRequest baseEnv options profiling stdout request

private partial def requestLoop (baseEnv : Environment) (options : Options)
    (profiling : ProfilingConfig) (stdin stdout : IO.FS.Stream) : IO Unit := do
  let rawLine ← stdin.getLine
  if rawLine.isEmpty then
    return
  let line := trimLineEnding rawLine
  if line.isEmpty then
    IO.eprintln "lean-server-worker: ignoring empty input line"
  else
    match parseRequest line with
    | .ok request => handleRequest baseEnv options profiling stdout request
    | .error message => IO.eprintln s!"lean-server-worker: invalid request: {message}"
  requestLoop baseEnv options profiling stdin stdout

private def envNat (name : String) (fallback : Nat) : IO Nat := do
  let some value ← IO.getEnv name | return fallback
  let some number := value.toNat? | throw <| IO.userError s!"{name} must be a non-negative integer"
  return number

unsafe def main : IO UInt32 := do
  initSearchPath (← findSysroot)
  enableInitializersExecution
  let options : Options := {}
  let profiling : ProfilingConfig := {
    directory := (← IO.getEnv "LEAN_SERVER_PROFILE_DIR").filter (· != "") |>.map System.FilePath.mk
    slowMs := (← envNat "LEAN_SERVER_PROFILE_SLOW_MS" 1000)
    thresholdMs := (← envNat "LEAN_SERVER_PROFILE_THRESHOLD_MS" 10)
  }
  let baseEnv ← importModules (loadExts := true) #[{ module := `Mathlib }] options
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  writeMessage stdout readyJson
  requestLoop baseEnv options profiling stdin stdout
  return 0

end LeanServerWorker

unsafe def main : IO UInt32 :=
  LeanServerWorker.main
