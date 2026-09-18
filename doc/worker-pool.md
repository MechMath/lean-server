# Worker pool 与 HTTP 调度

## 当前实现

HTTP handler 不再直接启动 Lean。请求经过以下路径：

```text
ThreadingHTTPServer
  -> CompilerPoolRuntime（独立 asyncio event loop）
  -> CompilerPool（有界 FIFO queue）
  -> CompilerBackend
      |- LeanCliBackend：默认，每请求一个 Lean CLI
      `- WorkerProcessBackend：常驻 NDJSON worker
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

使用当前可用的 Lean CLI backend：

```bash
uv run lean-server --workers 4 --queue-capacity 16
```

任务 A 的常驻 Lean worker 合并后，通过命令行切换，不需要修改 HTTP 或 pool：

```bash
uv run lean-server \
  --workers 4 \
  --queue-capacity 16 \
  --worker-command '.lake/build/bin/lean-server-worker'
```

`--worker-command` 使用 shell 风格字符串解析，但启动 subprocess 时不经过 shell。

## 生命周期和故障

- 启动时所有 backend 必须 ready，之后才开始监听 HTTP。
- timeout、进程退出、EOF、非法协议、错误 request ID 和 `internal_error` 都会回收当前
  worker slot 并自动创建替代 worker。
- Lean parser、elaborator 或类型错误返回 `compile_error`，不会回收 worker。
- replacement 失败时按 50ms 到 2s 的上限指数退避，避免 respawn storm。
- 服务关闭时取消 active 和 queued 请求，随后关闭所有 backend 和进程组。

pool 状态可以通过 `GET /healthz` 和 `GET /readyz` 查看：

```json
{
  "state": "running",
  "worker_count": 4,
  "ready_workers": 4,
  "active_workers": 1,
  "queue_depth": 2,
  "queue_capacity": 16,
  "replacements": 0
}
```

## HTTP 计时

`POST /api/v1/check` 保留原来的 `time_ms`，并增加：

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
层，不改变冻结的 worker protocol v1。

## 测试边界

- `tests/service/test_pool.py`：并发上限、FIFO、有界队列和关闭状态。
- `tests/service/test_recovery.py`：timeout、crash、协议错误和 capacity recovery。
- `tests/workers/test_process.py`：NDJSON subprocess transport。
- `tests/workers/test_cli.py`：真实 Lean 4.30 CLI backend。
- `tests/test_http.py`：HTTP success、compile error、warning、timeout、overload、crash 和 readiness。

调度测试使用 `tests/fixtures/fake_worker.py`，因此不依赖任务 A 的实现。
