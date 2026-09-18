# Lean 4.30 HTTP 编译服务

这是一个最小的本地 HTTP 服务：接收 Lean 源码，使用固定的 Lean 4.30.0 与
Mathlib 4.30.0 编译，然后返回是否成功、耗时、warning 和 error。

当前版本有意不实现 AXLE SDK 兼容。服务提供有界 worker pool；默认 backend 为每个请求
启动一个 Lean CLI，也可以使用预加载 Mathlib 的常驻 Lean worker，以获得更低延迟。

## 环境准备

```bash
elan toolchain install leanprover/lean4:v4.30.0
lake update
lake exe cache get
lake build lean-server-worker
```

仓库根目录的 `lean-toolchain` 和 `lake-manifest.json` 分别固定 Lean 与 Mathlib 版本。
服务在绑定 HTTP 端口前会检查 Lean 4.30.0、Mathlib 4.30.0、实际 Mathlib Git revision，
并执行一次 `import Mathlib` smoke compile；任一检查失败时不会启动 HTTP 服务。

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

生产环境推荐使用常驻 worker：

```bash
uv run lean-server \
  --host 127.0.0.1 \
  --port 8000 \
  --workers 4 \
  --queue-capacity 8 \
  --worker-startup-timeout 180 \
  --worker-startup-parallelism 4 \
  --worker-command 'lake env .lake/build/bin/lean-server-worker'
```

常驻 worker 启动时会预加载 Mathlib。服务会等待所有 worker 完成 ready handshake 后再
监听 HTTP 端口。

## Lean 环境故障排查

### Mathlib 无法解析 `HEAD`

如果 `lake update` 报告以下错误，说明 `.lake/packages/mathlib` checkout 不完整或损坏：

```text
could not resolve 'HEAD' to a commit; the repository may be corrupt
```

当前锁定的 Mathlib commit 是
`c5ea00351c28e24afc9f0f84379aa41082b1188f`。先检查 commit 对象是否仍然存在：

```bash
git -C .lake/packages/mathlib rev-parse HEAD
git -C .lake/packages/mathlib cat-file -e \
  c5ea00351c28e24afc9f0f84379aa41082b1188f^{commit}
```

如果第二条命令成功，可以恢复损坏的 `HEAD`：

```bash
git -C .lake/packages/mathlib checkout --detach \
  c5ea00351c28e24afc9f0f84379aa41082b1188f
```

如果 commit 对象也不存在，先保留损坏目录，再重新下载固定版本：

```bash
mv .lake/packages/mathlib \
  .lake/packages/mathlib.corrupt-$(date +%Y%m%d-%H%M%S)
lake update mathlib
git -C .lake/packages/mathlib rev-parse HEAD
git diff -- lake-manifest.json
lake exe cache get
```

`rev-parse HEAD` 应输出上述固定 commit；`lake-manifest.json` 不应出现非预期版本变化。

### Worker 可执行文件缺失或无法执行

如果启动时报错：

```text
could not execute external process '.lake/build/bin/lean-server-worker'
```

重新构建并独立验证 ready handshake：

```bash
lake build lean-server-worker
lake env .lake/build/bin/lean-server-worker </dev/null
```

第二条命令应立即输出包含 `"type":"ready"` 和 `"lean_version":"4.30.0"` 的 JSON。
如果仍无法执行，检查文件权限、CPU 架构和动态链接：

```bash
ls -l .lake/build/bin/lean-server-worker
file .lake/build/bin/lean-server-worker
ldd .lake/build/bin/lean-server-worker
uname -m
```

如果 build 产物来自其他系统或已经损坏，执行 `lake clean` 后重新构建。

## API

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
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
