# Lean worker protocol v1

该协议是 Lean worker 与 Python 调度层之间的唯一接口。传输使用 UTF-8 NDJSON：每条
消息恰好占一行，worker 的 stdout 只能包含协议消息，日志写入 stderr。

## 生命周期

1. Python 启动 worker。
2. worker 加载固定的 Lean 4.30.0 与 Mathlib 4.30.0 环境。
3. worker 向 stdout 写一条 `ready` 消息。
4. Python 每次写一条 `compile` 请求，并等待同一 `request_id` 的 `result`。
5. 每个 worker 同时最多处理一个请求，但同一进程应能顺序处理多个请求。
6. EOF、非法 JSON、错误版本、错误 request ID 都由 Python 视为 worker 故障。

Python 负责 wall-clock timeout、杀死进程、重启、排队和过载控制；Lean worker 不负责
这些调度行为。Lean worker 负责源码 elaboration、诊断结构化以及请求间环境隔离。

## 消息

启动成功：

```json
{"protocol_version":1,"type":"ready","lean_version":"4.30.0"}
```

编译请求：

```json
{"protocol_version":1,"type":"compile","request_id":"req-1","code":"def n : Nat := 1"}
```

编译成功：

```json
{"protocol_version":1,"type":"result","request_id":"req-1","status":"ok","compile_ms":12.5,"warnings":[],"errors":[]}
```

Lean 源码错误使用 `compile_error`；worker 自身无法完成请求使用 `internal_error`。两者
必须分开，以便调度层只对基础设施错误执行重试。

协议 v1 一旦两条开发分支开始工作就冻结。任何字段变更通过新增 v2 完成，避免两个
分支同时修改共享文件。
