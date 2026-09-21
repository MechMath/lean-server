# Worker pool 与 HTTP 调度

## 当前实现

HTTP handler 不再直接启动 Lean。请求经过以下路径：

```text
ThreadingHTTPServer
  -> CompilerPoolRuntime（独立 asyncio event loop）
  -> CompilerPool（有界 FIFO queue）
  -> CompilerBackend
      `- WorkerProcessBackend：默认的常驻 NDJSON worker
```

默认配置为 2 个 worker slot、8 个等待位置。每个 slot 最多一个 in-flight 请求；等待队列
已满时立即返回 HTTP 503：

```json
{"error":"compiler queue is full","retryable":true}
```

## 启动

服务在绑定 HTTP 端口前执行一次启动检查：

- `lean-toolchain` 必须固定为 `leanprover/lean4:v4.30.0`；
- 固定 toolchain 与 `lake env lean` 都必须实际报告 Lean 4.30.0；
- `lakefile.toml` 和 `lake-manifest.json` 必须固定 Mathlib v4.30.0；
- `.lake/packages/mathlib` 的实际 Git revision 必须与 manifest 一致；
- `import Mathlib` smoke compile 必须成功。

任一检查失败时进程以状态码 1 退出，不开放 HTTP 服务。检查成功后会打印实际 Mathlib
revision 与耗时。

默认启动仓库内已构建的常驻 worker：

```bash
uv run lean-server --workers 4 --queue-capacity 16
```

也可以显式覆盖 worker 命令：

```bash
uv run lean-server \
  --workers 4 \
  --queue-capacity 16 \
  --worker-startup-timeout 180 \
  --worker-startup-parallelism 8 \
  --worker-command '.lake/build/bin/lean-server-worker'
```

`--worker-command` 使用 shell 风格字符串解析，但启动 subprocess 时不经过 shell。
常驻 worker 启动时需要预加载 Mathlib，因此每个 ready handshake 默认允许 180 秒；pool
默认最多同时启动 8 个 worker，避免大量进程同时读取 Mathlib，也避免逐个串行启动。两个值可
分别通过 `--worker-startup-timeout` 和 `--worker-startup-parallelism` 调整。

## 生命周期和故障

- 启动时以受限并行方式等待所有 backend ready，之后才开始监听 HTTP。
- timeout、进程退出、EOF、非法协议、错误 request ID 和 `internal_error` 都会回收当前
  worker slot 并自动创建替代 worker。
- Lean parser、elaborator 或类型错误返回 `compile_error`，不会回收 worker。
- replacement 失败时按 50ms 到 2s 的上限指数退避，避免 respawn storm。
- 服务关闭时取消 active 和 queued 请求，随后关闭所有 backend 和进程组。
- 正在启动的 replacement 也由 pool 管理，取消 ready handshake 时会终止并回收进程。
- stderr 按字节块持续读取，最多保留末尾 64 KiB（对外最多 100 行）；无换行的超长日志
  不会中断读取。清理异常会记录日志，不会终止 worker slot 的恢复流程。

`timeout_seconds` 是排队与执行共享的总预算，从请求进入 pool 开始计时。等待 worker
重建也计入预算；排队期间超时或取消会立即释放队列位置，不会执行该任务。执行阶段只使用
剩余预算，超时后回收相应 worker。重建中的 slot 不计入 `active_workers`。

pool 状态可以通过 `GET /healthz` 和 `GET /readyz` 查看：

```json
{
  "state": "running",
  "worker_count": 4,
  "ready_workers": 4,
  "active_workers": 1,
  "queue_depth": 2,
  "queue_capacity": 16,
  "replacements": 0,
  "quarantined_inputs": 0,
  "quarantine_hits": 0,
  "quarantine_evictions": 0
}
```

## 崩溃输入隔离

已识别的 `INTERNAL PANIC: Nat.pow exponent is too big` 返回 HTTP 503、
`error_type: "LeanPanic"`、`retryable: false`。它表示检查未完成，不是 `okay: false`
的证明拒绝。第一次崩溃仍会回收并补充 worker；pool 同时记录输入的 SHA-256 指纹。
之后相同输入在入队前被拒绝，已经排队的重复输入也会立即收到同类错误：

```json
{
  "error": "worker previously panicked for this input; input is quarantined",
  "retryable": false,
  "error_type": "LeanPanic"
}
```

指纹包括协议版本、操作类型和所有 worker 请求字段（编译代码，或验证时的 statement、
content、`use_def_eq`），忽略每次生成的 request ID。HTTP 超时和 `allow_sorry` 不参与
指纹，因为它们不改变 worker 的计算输入。隔离表只存摘要和错误类型，不保留证明正文。
仅明确标记为输入相关 panic 的错误进入隔离；普通证明错误、超时、协议错误和其他进程退出
不会因此被永久判定为坏输入。

相同指纹同一时间最多在一个 worker 执行；重复请求留在有界队列中，仍受自己的排队超时和
取消控制。调度选择最早的可执行请求，跳过等待相同输入的任务，因此其他输入可继续使用
空闲 worker。正常完成的重复请求会分别执行并保留各自的 request ID，不共享检查结果。

隔离范围是单个 pool／服务进程，假定其 Lean、Mathlib 和 worker 环境固定。升级环境应
重启服务。默认容量为 4096 条（嵌入使用时可通过 `CompilerPool(quarantine_capacity=...)`
调整），按最近使用顺序淘汰，不自动过期；服务重启会清空，不跨服务副本共享。因此保护
保证只覆盖仍在隔离表中的精确输入，重启、淘汰、修改输入或换副本后仍可能再次执行。
`quarantine_hits` 统计被隔离拦截的请求，`quarantine_evictions` 统计容量淘汰，均可从
health/readiness 的 pool 状态观察。

故障 worker 重建期间，其他健康 worker 可以继续处理请求；只有一个 worker 时，正常请求
需等待补位，并受总超时预算约束。隔离请求本身不等待补位，也不占用新的 worker。

## HTTP 计时

`POST /check` 保留原来的 `time_ms`，并增加：

```json
{
  "timings": {
    "total_ms": 925.0,
    "queue_ms": 1.0,
    "compile_ms": 924.0
  }
}
```

当前 `queue_ms` 是 `total_ms - compile_ms`，包含很小的 transport 和调度开销。
超时响应则根据实际出队时间分别记录等待与执行耗时；未开始执行的请求 `compile_ms` 为 0。

请求可以通过 `allow_sorry` 控制是否接受包含 `sorry` 的代码：

```json
{
  "code": "theorem unfinished : True := by sorry",
  "allow_sorry": false
}
```

`allow_sorry` 必须是 JSON boolean，默认值为 `false`。为 `false` 时，Lean 原始 sorry
warning 仍保留在 `warnings` 中，同时响应增加一条策略 error 并返回 `okay: false`；为
`true` 时，sorry 保持为 warning，若无其他错误则返回 `okay: true`。该策略位于 HTTP
层，不改变 worker 协议。

## 测试边界

- `tests/service/test_pool.py`：并发上限、FIFO、有界队列和关闭状态。
- `tests/service/test_recovery.py`：timeout、crash、协议错误和 capacity recovery。
- `tests/service/test_quarantine.py`：重复及并发 panic、正常请求隔离、取消与超时、指纹边界、
  容量淘汰和 worker 重建期间的调度。
- `tests/workers/test_process.py`：NDJSON subprocess transport。
- `tests/workers/test_cli.py`：真实 Lean 4.30 CLI backend。
- `tests/test_http.py`：HTTP success、compile error、warning、timeout、overload、crash 和 readiness。

调度测试使用 `tests/fixtures/fake_worker.py`，因此不依赖任务 A 的实现。
