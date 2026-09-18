import Lean.Data.Json
import Lean.Message

namespace LeanServerWorker

open Lean

def protocolVersion : Nat := 1

structure Request where
  requestId : String
  code : String

inductive ResultStatus where
  | ok
  | compileError
  | internalError

structure Diagnostic where
  severity : String
  message : String
  fileName : Option String := none
  startPos : Option Position := none
  endPos : Option Position := none

def parseRequest (line : String) : Except String Request := do
  let json ← Json.parse line
  let version ← (json.getObjVal? "protocol_version" >>= Json.getNat?)
  if version != protocolVersion then
    throw s!"protocol_version must be {protocolVersion}"
  let messageType ← json.getObjVal? "type" >>= Json.getStr?
  if messageType != "compile" then
    throw "type must be compile"
  let requestId ← json.getObjVal? "request_id" >>= Json.getStr?
  if requestId.isEmpty then
    throw "request_id must be non-empty"
  let code ← json.getObjVal? "code" >>= Json.getStr?
  return { requestId, code }

private def positionToJson : Option Position → Json
  | some pos => Json.mkObj [("line", toJson pos.line), ("column", toJson pos.column)]
  | none => Json.null

private def diagnosticToJson (diagnostic : Diagnostic) : Json :=
  Json.mkObj [
    ("severity", toJson diagnostic.severity),
    ("message", toJson diagnostic.message),
    ("file_name", toJson diagnostic.fileName),
    ("start", positionToJson diagnostic.startPos),
    ("end", positionToJson diagnostic.endPos)
  ]

private def ResultStatus.toString : ResultStatus → String
  | .ok => "ok"
  | .compileError => "compile_error"
  | .internalError => "internal_error"

def readyJson : Json :=
  Json.mkObj [
    ("protocol_version", toJson protocolVersion),
    ("type", toJson "ready"),
    ("lean_version", toJson "4.30.0")
  ]

def resultJson (requestId : String) (status : ResultStatus) (compileMs : Float)
    (warnings errors : Array Diagnostic) : Json :=
  Json.mkObj [
    ("protocol_version", toJson protocolVersion),
    ("type", toJson "result"),
    ("request_id", toJson requestId),
    ("status", toJson status.toString),
    ("compile_ms", toJson compileMs),
    ("warnings", Json.arr <| warnings.map diagnosticToJson),
    ("errors", Json.arr <| errors.map diagnosticToJson)
  ]

def internalErrorDiagnostic (message : String) : Diagnostic where
  severity := "error"
  message := message

end LeanServerWorker
