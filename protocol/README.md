# Lean worker protocol

Lean worker 与 Python 调度层通过 UTF-8 NDJSON 通信：每条消息恰好占一行。
所有消息统一使用 `protocol_version: 2`，通过 `type` 区分用途。
worker 的 stdout 只能包含协议消息，日志写入 stderr。

## 生命周期

1. Python 启动 worker，worker 加载固定的 Lean 4.30.0 与 Mathlib 4.30.0 环境。
2. worker 输出一条 `ready` 消息。
3. Python 发送 `compile` 或 `verify` 请求，等待同一 `request_id` 的对应结果。
4. 每个 worker 同时最多处理一个请求，同一进程可以顺序交替处理两种请求。

Python 负责 wall-clock timeout、杀死进程、重启、排队和过载控制。EOF、非法 JSON、
错误版本或错误 request ID 都视为 worker 故障。Lean worker 负责源码 elaboration、
诊断结构化、严格证明验证和请求间环境隔离。

## 消息

| 用途 | 请求 type | 响应 type | Schema |
| --- | --- | --- | --- |
| 启动完成 | — | `ready` | [ready](ready.schema.json) |
| 编译源码 | `compile` | `result` | [请求](compile-request.schema.json)、[结果](compile-result.schema.json) |
| 验证证明 | `verify` | `verify_result` | [请求](verify-request.schema.json)、[结果](verify-result.schema.json) |

启动成功：

```json
{"protocol_version":2,"type":"ready","lean_version":"4.30.0"}
```

编译请求及结果：

```json
{"protocol_version":2,"type":"compile","request_id":"req-1","code":"def answer : Nat := 42"}
{"protocol_version":2,"type":"result","request_id":"req-1","status":"ok","compile_ms":12.5,"warnings":[],"errors":[]}
```

验证请求及结果：

```json
{"protocol_version":2,"type":"verify","request_id":"verify-1","formal_statement":"theorem t : True := by sorry","content":"theorem t : True := True.intro","use_def_eq":true}
{"protocol_version":2,"type":"verify_result","request_id":"verify-1","status":"ok","compile_ms":4.0,"formal_statement_ms":1.0,"candidate_ms":2.0,"declarations_ms":1.0,"warnings":[],"errors":[],"tool_errors":[],"failed_declarations":[]}
```

Lean parser/elaborator 错误使用 `compile_error`；worker 自身无法完成请求使用
`internal_error`，以便调度层区分源码错误和基础设施故障。严格验证的策略拒绝（例如
`sorry` 或非标准 axiom）仍使用 `status: "ok"`，通过 `tool_errors` 和
`failed_declarations` 表达。

可供测试使用的完整示例位于 [examples/](examples/)。

## 响应大小边界

每条响应上限为 8 MiB（8,388,608 字节），包括 UTF-8 JSON 和末尾换行；恰好达到上限
的消息有效。超限时 Python 不再解析该条消息，返回 HTTP 503：

```json
{"error":"worker message exceeds 8388608 bytes","error_type":"WorkerMessageTooLarge","retryable":false}
```

读取按约 64 KiB 的块进行，最多保留 8 MiB 的未解析消息。发现超限后丢弃已保留内容，
继续排空至换行，然后复用 worker；相同输入不应自动重试。排空不延长请求总 deadline，
另有 2 秒和额外 64 MiB 的上限。若排空达到上限或遇到 EOF，消息边界无法恢复，仍返回
不可重试的大小错误并回收 worker；若请求总 deadline 先到，则返回原有超时结果并回收。
超限不是 Lean 证明拒绝，响应不带 `okay: false`。该上限约束 Python 传输层保留的原始
字节，不是 Lean 生成诊断时的总内存上限。

## 版本与更新

协议版本标识整个 worker 通信协议，不按功能分配。Python 与 Lean worker 必须配套更新；
不支持旧版消息、版本协商或新旧组件混用。部署时重新构建 worker，并重启 Python 服务及
其 worker 进程。版本不匹配会在启动握手时明确报错。

HTTP 编译检查入口为 `/check`，严格证明验证入口为 `/verify_proof`，均不带 `/api/v1` 前缀。
HTTP 路径与内部 worker 协议版本独立；HTTP 调用方无需提供 `protocol_version`。
