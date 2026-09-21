# Lean worker verification protocol v2

协议 v2 在不修改已冻结 v1 compile 消息的前提下增加严格证明验证。worker 启动时仍发送
v1 `ready`；同一进程随后可交替处理 v1 `compile` 和 v2 `verify` 请求。传输仍为 UTF-8
NDJSON，每条消息恰好占一行。

验证请求：

```json
{"protocol_version":2,"type":"verify","request_id":"verify-1","formal_statement":"theorem t : True := by sorry","content":"theorem t : True := True.intro","use_def_eq":true}
```

验证结果：

```json
{"protocol_version":2,"type":"verify_result","request_id":"verify-1","status":"ok","compile_ms":4.0,"formal_statement_ms":1.0,"candidate_ms":2.0,"declarations_ms":1.0,"warnings":[],"errors":[],"tool_errors":[],"failed_declarations":[]}
```

`compile_error` 表示 formal statement 或 candidate 有 Lean parser/elaborator error；严格策略
拒绝（例如 `sorry` 或非标准 axiom）仍使用 `status: "ok"`，并通过 `tool_errors` 和
`failed_declarations` 表达。`internal_error` 仅用于 worker 自身故障。

