import Lean.Meta
import Lean.DeclarationRange
import Lean.Util.CollectAxioms
import Lean.Util.FoldConsts
import Lean.Util.Sorry
import LeanServerWorker.Compiler

namespace LeanServerWorker

open Lean

structure VerifyOutput where
  warnings : Array Diagnostic := #[]
  errors : Array Diagnostic := #[]
  toolErrors : Array String := #[]
  failedDeclarations : Array String := #[]
  formalStatementMs : Float := 0
  candidateMs : Float := 0
  declarationsMs : Float := 0
  formalTimings : ElaborationTimings := {}
  candidateTimings : ElaborationTimings := {}
  comparisonErrors : Array ComparisonError := #[]

private def elapsedMilliseconds (started finished : Nat) : Float :=
  (finished - started).toFloat / 1000000.0

private def runCoreIn (env : Environment) (options : Options) (x : CoreM α) : IO α :=
  x.toIO' {
    fileName := "<verify>"
    fileMap := FileMap.ofString ""
    options
  } { env }

-- Keep Lean exceptions typed until after classification; CoreM.toIO' erases their tags.
def runComparison (env : Environment) (options : Options) (name : Name) (phase : String)
    (action : MetaM Bool) : IO (Except ComparisonError Bool) := do
  let result ← ((Lean.Meta.MetaM.run' action).run' {
    fileName := "<verify>"
    fileMap := FileMap.ofString ""
    options
    initHeartbeats := (← IO.getNumHeartbeats)
  } { env }).toBaseIO
  match result with
  | .ok matched => return .ok matched
  | .error exception =>
    let kind := if exception.isRuntime then "resource_limit"
      else if exception.isInterrupt then "interrupted" else "internal_error"
    return .error {
      declaration := name.toString
      phase
      kind
      message := (← exception.toMessageData.toString)
    }

private def newDeclarationNames (baseEnv env : Environment) (options : Options) :
    IO (Array Name) := do
  let names := env.constants.foldStage2
    (fun names name _ => if baseEnv.contains name then names else names.push name)
    (#[] : Array Name)
  runCoreIn env options do
    let mut sourceNames := #[]
    for name in names do
      if (← findDeclarationRangesCore? name).isSome then
        sourceNames := sourceNames.push name
    return sourceNames.qsort Name.quickLt

private def declarationKind : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quotient"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

private def isTargetDeclaration : ConstantInfo → Bool
  | .axiomInfo _ | .defnInfo _ | .thmInfo _ | .opaqueInfo _ | .inductInfo _
  | .ctorInfo _ => true
  | _ => false

private def declarationMetadataMatches : ConstantInfo → ConstantInfo → Bool
  | .inductInfo formal, .inductInfo candidate =>
    formal.numParams == candidate.numParams &&
      formal.numIndices == candidate.numIndices &&
      formal.all == candidate.all &&
      formal.ctors == candidate.ctors &&
      formal.numNested == candidate.numNested &&
      formal.isRec == candidate.isRec &&
      formal.isReflexive == candidate.isReflexive
  | .ctorInfo formal, .ctorInfo candidate =>
    formal.induct == candidate.induct &&
      formal.cidx == candidate.cidx &&
      formal.numParams == candidate.numParams &&
      formal.numFields == candidate.numFields
  | _, _ => true

private def expressionsMatch (env : Environment) (options : Options) (useDefEq : Bool)
    (irreducibleNames : Array Name) (formalInfo candidateInfo : ConstantInfo)
    (phase : String) (getExpr : ConstantInfo → Option Expr) :
    IO (Except ComparisonError Bool) := do
  if formalInfo.levelParams.length != candidateInfo.levelParams.length then
    return .ok false
  let some formalExpr := getExpr formalInfo | return .ok false
  let some candidateExpr := getExpr candidateInfo | return .ok false
  if formalExpr.getUsedConstants.any fun name => !env.contains name then
    return .ok false
  runComparison env options formalInfo.name phase do
    let levels := (List.range formalInfo.levelParams.length).map fun index =>
      Level.param (.num `_verify index)
    let formalExpr := formalExpr.instantiateLevelParams formalInfo.levelParams levels
    let candidateExpr := candidateExpr.instantiateLevelParams candidateInfo.levelParams levels
    if useDefEq then
      Lean.Meta.withTransparency .all <| Lean.Meta.withCanUnfoldPred
        (fun _ info => pure (!irreducibleNames.contains info.name)) <|
        Lean.Meta.isDefEq formalExpr candidateExpr
    else
      return formalExpr == candidateExpr

private def typesMatch (env : Environment) (options : Options) (useDefEq : Bool)
    (irreducibleNames : Array Name) (formalInfo candidateInfo : ConstantInfo) :
    IO (Except ComparisonError Bool) :=
  expressionsMatch env options useDefEq irreducibleNames formalInfo candidateInfo "type" fun info =>
    some info.type

private def valuesMatch (env : Environment) (options : Options) (useDefEq : Bool)
    (irreducibleNames : Array Name) (formalInfo candidateInfo : ConstantInfo) :
    IO (Except ComparisonError Bool) :=
  expressionsMatch env options useDefEq irreducibleNames formalInfo candidateInfo "value"
    fun info => info.value? (allowOpaque := true)

private def isDirectPlaceholderDefinition (info : ConstantInfo) : Bool :=
  match info with
  | .defnInfo _ | .opaqueInfo _ =>
    (info.value? (allowOpaque := true)).any Expr.hasSorry
  | _ => false

private def shouldCompareValue (info : ConstantInfo) : Bool :=
  match info with
  | .defnInfo _ | .opaqueInfo _ => !isDirectPlaceholderDefinition info
  | _ => false

-- A source declaration can delegate its entire implementation to generated constants
-- (for example, `f := fun n => Nat.brecOn n f._f`). Those constants are part of the
-- fixed contract even when they have no declaration range. Do not follow theorem
-- proof bodies: candidates are allowed to supply different proofs and proof helpers.
private def requiredDeclarations (baseEnv env : Environment) (roots : Array Name) :
    Array (Name × Name) := Id.run do
  let mut pending := roots.map fun name => (name, name)
  let mut seen : NameSet := {}
  let mut required := #[]
  while !pending.isEmpty do
    let (name, owner) := pending.back!
    pending := pending.pop
    if baseEnv.contains name || seen.contains name then
      continue
    seen := seen.insert name
    let some info := env.find? name | continue
    let owner := if roots.contains name then name else owner
    required := required.push (name, owner)
    let mut dependencies := info.type.getUsedConstants
    if shouldCompareValue info then
      if let some value := info.value? (allowOpaque := true) then
        dependencies := dependencies ++ value.getUsedConstants
    if let .inductInfo value := info then
      dependencies := dependencies ++ value.ctors.toArray
    pending := pending ++ dependencies.map fun dependency => (dependency, owner)
  return required

private def collectAxiomsIn (env : Environment) (options : Options)
    (name : Name) : IO (Array Name) :=
  runCoreIn env options <| collectAxioms name

private def allowedAxiom (name : Name) : Bool :=
  name == ``propext || name == ``Quot.sound || name == ``Classical.choice

private def declarationLabel (info : ConstantInfo) : String :=
  if declarationKind info == "theorem" then "Theorem" else "Declaration"

private def appendFailure (failed : Array String) (name : Name) : Array String :=
  if failed.contains name.toString then failed else failed.push name.toString

private partial def syntaxContainsKind (stx : Syntax) (kind : Name) : Bool :=
  stx.getKind == kind || stx.getArgs.any (syntaxContainsKind · kind)

private def usesPrivateAccessCommand (commands : Array Syntax) : Bool :=
  commands.any fun command =>
    syntaxContainsKind command `Lean.Elab.Command.openPrivate ||
      syntaxContainsKind command `Lean.Elab.Command.exportPrivate

def verifyProof (baseEnv : Environment) (options : Options) (formalStatement content : String)
    (useDefEq := true) (profiling : ProfilingConfig := {}) : IO VerifyOutput := do
  let formalStarted ← IO.monoNanosNow
  let formal ← elaborateCode baseEnv options formalStatement
    (fileName := "<formal_statement>") (validateRequestedImports := false) (profiling := profiling)
  let formalFinished ← IO.monoNanosNow

  -- An invalid statement cannot be verified; do not execute candidate commands or tactics.
  if !formal.errors.isEmpty then
    return {
      errors := formal.errors
      formalStatementMs := elapsedMilliseconds formalStarted formalFinished
      formalTimings := formal.timings
    }

  let targetsStarted ← IO.monoNanosNow
  let formalNames ← newDeclarationNames baseEnv formal.env options
  let targets := formalNames.filter fun name =>
    (formal.env.find? name).any isTargetDeclaration
  let targetsFinished ← IO.monoNanosNow
  let targetsMs := elapsedMilliseconds targetsStarted targetsFinished
  if targets.isEmpty then
    return {
      toolErrors := #["formal_statement contains no verifiable declarations"]
      formalStatementMs := elapsedMilliseconds formalStarted formalFinished
      declarationsMs := targetsMs
      formalTimings := formal.timings
    }

  let candidateStarted ← IO.monoNanosNow
  let candidate ← elaborateCode baseEnv options content
    (fileName := "<content>") (validateRequestedImports := false) (profiling := profiling)
  let candidateFinished ← IO.monoNanosNow

  let errors := formal.errors ++ candidate.errors
  let warnings := candidate.warnings
  let mut toolErrors := #[]
  let mut failedDeclarations := #[]
  let mut comparisonErrors := #[]
  let declarationsStarted ← IO.monoNanosNow

  if errors.isEmpty then
    let required := requiredDeclarations baseEnv formal.env targets
    let irreducibleNames := required.filterMap fun (name, _) =>
      if (formal.env.find? name).any isDirectPlaceholderDefinition then some name else none
    if usesPrivateAccessCommand candidate.commands then
      toolErrors := toolErrors.push "Candidate uses banned 'open private' command"
      for name in targets do
        failedDeclarations := appendFailure failedDeclarations name
    else
      let mut missingRequired := false
      for (name, owner) in required do
        if !(candidate.env.contains name) then
          toolErrors := toolErrors.push s!"Missing required declaration '{name}'"
          failedDeclarations := appendFailure failedDeclarations owner
          missingRequired := true

      if !missingRequired then
        for (name, owner) in required do
          let some formalInfo := formal.env.find? name | continue
          let some candidateInfo := candidate.env.find? name
            | toolErrors := toolErrors.push s!"Missing required declaration '{name}'"
              failedDeclarations := appendFailure failedDeclarations owner
              continue

          let mut declarationFailed := false
          if declarationKind formalInfo != declarationKind candidateInfo then
            toolErrors := toolErrors.push s!"Kind mismatch for '{name}': candidate has \
              {declarationKind candidateInfo} but expected {declarationKind formalInfo}"
            declarationFailed := true
          else if !declarationMetadataMatches formalInfo candidateInfo then
            toolErrors := toolErrors.push s!"Declaration '{name}' does not match expected metadata"
            declarationFailed := true
          else
            match ← typesMatch candidate.env options useDefEq irreducibleNames
                formalInfo candidateInfo with
            | .error error => comparisonErrors := comparisonErrors.push error
            | .ok false =>
              toolErrors := toolErrors.push s!"{declarationLabel formalInfo} '{name}' \
                does not match expected signature"
              declarationFailed := true
            | .ok true =>
              if shouldCompareValue formalInfo then
                match ← valuesMatch candidate.env options useDefEq irreducibleNames
                    formalInfo candidateInfo with
                | .error error => comparisonErrors := comparisonErrors.push error
                | .ok true => pure ()
                | .ok false =>
                  toolErrors := toolErrors.push
                    s!"Declaration '{name}' does not match expected value"
                  declarationFailed := true

          if candidateInfo.isUnsafe then
            toolErrors := toolErrors.push s!"Unsafe declaration '{name}' detected"
            declarationFailed := true

          let axioms ← collectAxiomsIn candidate.env options name
          for axiomName in axioms do
            if axiomName == ``sorryAx then
              toolErrors := toolErrors.push s!"Declaration '{name}' is incomplete (uses 'sorry')"
              declarationFailed := true
            else if !allowedAxiom axiomName then
              toolErrors := toolErrors.push s!"In '{name}': Axiom '{axiomName}' is not in the \
                allowed set of standard axioms"
              declarationFailed := true

          if declarationFailed then
            failedDeclarations := appendFailure failedDeclarations owner

  let declarationsFinished ← IO.monoNanosNow
  for error in comparisonErrors do
    toolErrors := toolErrors.push s!"Comparison failed for '{error.declaration}' \
      ({error.phase}, {error.kind}): {error.message}"
  return {
    warnings
    errors
    toolErrors
    failedDeclarations
    comparisonErrors
    formalTimings := formal.timings
    candidateTimings := candidate.timings
    formalStatementMs := elapsedMilliseconds formalStarted formalFinished
    candidateMs := elapsedMilliseconds candidateStarted candidateFinished
    declarationsMs := targetsMs + elapsedMilliseconds declarationsStarted declarationsFinished
  }

end LeanServerWorker
