# Lean 4.30 HTTP 编译服务

这是一个最小的本地 HTTP 服务：接收 Lean 源码，使用固定的 Lean 4.30.0 与
Mathlib 4.30.0 编译，然后返回是否成功、耗时、warning 和 error。

当前版本有意不实现 AXLE SDK 兼容、严格证明验证、常驻 REPL 或 worker 池。每个请求
都会启动一个新的 Lean 进程，因此实现简单且请求之间没有状态泄漏，但吞吐量有限。

## 环境准备

```bash
elan toolchain install leanprover/lean4:v4.30.0
lake update
lake exe cache get
```

仓库根目录的 `lean-toolchain` 和 `lake-manifest.json` 分别固定 Lean 与 Mathlib 版本。

## 启动

无需安装 Python 运行时依赖：

```bash
PYTHONPATH=src python3 -m lean_server --host 127.0.0.1 --port 8000
```

也可以安装项目后运行：

```bash
uv sync
uv run lean-server
```

## API

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

编译代码：

```bash
curl -s http://127.0.0.1:8000/api/v1/check \
  -H 'Content-Type: application/json' \
  -d '{"code":"import Mathlib\nexample : 1 + 1 = 2 := by norm_num"}'
```

请求格式：

```json
{
  "code": "def answer : Nat := 42",
  "timeout_seconds": 30
}
```

`timeout_seconds` 可省略，默认 30 秒，最大 120 秒。请求体最大 2 MiB。

成功响应示例：

```json
{
  "okay": true,
  "timed_out": false,
  "time_ms": 181.2,
  "lean_version": "4.30.0",
  "warnings": [],
  "errors": []
}
```

Lean 编译错误仍返回 HTTP 200，并使用 `okay: false` 表示。JSON、字段或请求大小错误
使用对应的 HTTP 4xx 状态码。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

更完整的架构与后续阶段仍保存在 `doc/`，不属于当前 MVP。
