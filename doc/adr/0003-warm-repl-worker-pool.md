# ADR-0003：使用预热 Lean REPL worker 池

- 状态：Proposed
- 日期：2026-09-18

## 背景

每个请求直接启动 Lean 并加载 Mathlib 会让冷启动成为主要开销。AXLE 的论文实验中，直接调用 Lean 的吞吐约为预加载方案的一半。Kimina Lean Server 已验证了长期 REPL、并行 worker 和 import cache 的可行性。

## 决策

服务维护固定数量的长期 Lean REPL 子进程：

- 每个 worker 启动时加载固定环境和 verifier command；
- 每个 worker 同时只处理一个请求；
- dispatcher 只向 ready worker 分配任务；
- worker 数由 CPU 与内存预算共同决定，默认不超过可用物理核心数；
- 启动时预热 worker，`readyz` 仅在最低 worker 容量可用后成功；
- Python 层借鉴或复用 Kimina 的 pool/supervisor 思路，Lean 侧维护本项目自己的严格验证 command。

禁止在请求之间复用 candidate 修改后的 environment。worker 应持有只读 base environment，每次请求从 base 分别创建 formal 和 candidate environment，并在响应后丢弃派生状态。

## 备选方案

### 每请求执行 `lake env lean`

实现最简单、隔离最好，但 Mathlib 冷启动降低吞吐，不作为默认路径。

### 每请求独立预热 sandbox

接近 AXLE，但如何低成本复制预加载 environment 没有公开实现细节，第一阶段成本过高。

### 单个共享 REPL

无法利用多核，任一超时或 crash 会阻塞整个服务，因此拒绝。

## 后果

- 正常请求获得低延迟和接近按核心扩展的吞吐。
- 必须正确实现 environment 清理和 worker 回收。
- worker 常驻内存较高，需要用基准测试确定机器可承载的并发数。
- 与 AXLE 相比，请求级状态隔离较弱，但符合单租户本地服务的威胁模型。
