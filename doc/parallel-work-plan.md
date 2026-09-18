# Lean Server 并行开发拆分

## 固定边界

两条任务通过 `protocol/v1` 中的 NDJSON 协议通信。开始并行开发后，以下文件视为冻结：

- `protocol/v1/**`
- `src/lean_server/protocol.py`
- `src/lean_server/backend.py`
- `tests/contract/**`

如发现协议不足，先在主分支协调修改；不要在两个任务分支分别修改协议。需要不兼容修改
时新增 v2，不原地改变 v1。

## 任务 A：优化 Lean 部分

### 独占修改范围

- `lean/**`
- `tests/lean/**`
- 必要的 Lake target 配置

不修改 Python HTTP、队列、pool 或 supervisor。除添加 Lean executable target 外，不修改
根目录 Python 配置。

### 交付内容

1. 实现 `lean-server-worker` 常驻 executable。
2. 启动时加载 Lean 4.30.0 和 Mathlib 4.30.0，完成后输出 v1 `ready`。
3. 逐行读取 v1 `compile`，逐行输出对应 `result`。
4. 从同一个干净 base environment 编译每次请求，不能让声明、option、attribute 或 notation
   泄漏到后续请求。
5. 使用 Lean 自己的结构化 message 数据生成 warning/error 和源码位置。
6. stdout 不输出调试信息；日志只能写 stderr。
7. 增加协议 golden tests、连续请求隔离测试，以及至少 100 次顺序请求测试。
8. 记录冷启动、首请求和预热后请求耗时。

### 验收条件

- `import Mathlib` 的正确代码返回 `ok`。
- parser、elaborator 和类型错误返回 `compile_error`，worker 不退出。
- warning 不导致失败。
- 连续请求无法访问前一请求声明。
- worker 输出通过 `tests/contract` 中的 v1 decoder。

## 任务 B：优化并行与接口调度

### 独占修改范围

- `src/lean_server/service/**`
- `src/lean_server/workers/**`
- `src/lean_server/http.py`、`src/lean_server/__main__.py`
- `tests/service/**`

不修改 `lean/**`。开发和 CI 使用一个实现 v1 的 fake worker，因此不依赖任务 A 是否已经
完成。

### 交付内容

1. 实现 `CompilerBackend` 的 NDJSON subprocess transport。
2. 建立固定大小 worker pool，每 worker 一个 in-flight 请求。
3. 建立有界 FIFO 队列；队列满立即返回过载响应。
4. 分离并记录 queue wait、compile 和 total 时间。
5. wall-clock timeout 后杀死整个 worker 进程组并补充容量。
6. EOF、非法 JSON、错误 request ID 触发 worker replacement；Lean 编译错误不重试。
7. 实现 graceful shutdown，关闭监听后排空或取消任务并回收进程。
8. 保持当前 `/api/v1/check` 请求和基础响应字段兼容。

### 验收条件

- fake worker 下验证并发上限、FIFO、过载、timeout、crash 和 replacement。
- 一个 worker crash 不影响其他 worker 的 in-flight 请求。
- 连续压力测试后 worker 数恢复到配置值。
- HTTP handler 不直接创建或操作 Lean subprocess。

## 合并顺序

1. 先合并本协议与任务拆分提交。
2. A、B 都从该提交创建分支。
3. A 先或 B 先合并均可；B 的默认 backend 在 A 合并后只需要配置 worker executable 路径。
4. 两边合并后增加一组端到端测试：真实 worker × pool × HTTP；这组测试单独提交，不归
   属于任一并行任务。

## 明确不并行修改的文件

根目录 `README.md`、`pyproject.toml`、`lakefile.toml` 容易冲突。任务期间：

- A 只在 `lakefile.toml` 添加 executable target，不做格式重排。
- B 不修改 `lakefile.toml`。
- 两边均不更新根 `README.md`；最终集成提交统一更新。
- 新增 Python 依赖由 B 单独修改 `pyproject.toml` 和 `uv.lock`；A 不修改这两个文件。
