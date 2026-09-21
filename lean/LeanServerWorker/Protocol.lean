import Lean.Data.Json
import Lean.Message

namespace LeanServerWorker

open Lean

def protocolVersion : Nat := 1
def verifyProtocolVersion : Nat := 2

structure CompileRequest where
  requestId : String
  code : String

structure VerifyRequest where
  requestId : String
  formalStatement : String
  content : String
  useDefEq : Bool := true

inductive Request where
  | compile (request : CompileRequest)
  | verify (request : VerifyRequest)

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
  let messageType ← json.getObjVal? "type" >>= Json.getStr?
  let requestId ← json.getObjVal? "request_id" >>= Json.getStr?
  if requestId.isEmpty then
    throw "request_id must be non-empty"
  match version, messageType with
  | 1, "compile" =>
    let code ← json.getObjVal? "code" >>= Json.getStr?
    return .compile { requestId, code }
  | 2, "verify" =>
    let formalStatement ← json.getObjVal? "formal_statement" >>= Json.getStr?
    let content ← json.getObjVal? "content" >>= Json.getStr?
    let useDefEq ← match json.getObjVal? "use_def_eq" with
      | .ok value => value.getBool?
      | .error _ => pure true
    return .verify { requestId, formalStatement, content, useDefEq }
  | 1, _ => throw "protocol v1 type must be compile"
  | 2, _ => throw "protocol v2 type must be verify"
  | _, _ => throw s!"unsupported protocol_version {version}"

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

def verifyResultJson (requestId : String) (status : ResultStatus) (compileMs : Float)
    (formalStatementMs candidateMs declarationsMs : Float)
    (warnings errors : Array Diagnostic) (toolErrors failedDeclarations : Array String) : Json :=
  Json.mkObj [
    ("protocol_version", toJson verifyProtocolVersion),
    ("type", toJson "verify_result"),
    ("request_id", toJson requestId),
    ("status", toJson status.toString),
    ("compile_ms", toJson compileMs),
    ("formal_statement_ms", toJson formalStatementMs),
    ("candidate_ms", toJson candidateMs),
    ("declarations_ms", toJson declarationsMs),
    ("warnings", Json.arr <| warnings.map diagnosticToJson),
    ("errors", Json.arr <| errors.map diagnosticToJson),
    ("tool_errors", toJson toolErrors),
    ("failed_declarations", toJson failedDeclarations)
  ]

def internalErrorDiagnostic (message : String) : Diagnostic where
  severity := "error"
  message := message

end LeanServerWorker
