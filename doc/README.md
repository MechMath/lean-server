# Lean Server 设计文档

本目录描述一个仅供本机或可信内网使用的高频 Lean 4 验证服务。首个目标环境固定为 Lean 4.30.0 与对应 Mathlib，主要服务于 `lean-eval-toolkit` 的批量评测，并提供与 AXLE `verify_proof` 足够兼容的 HTTP 接口。

这不是 AXLE 服务端的复刻。AXLE 没有公开服务端实现；本项目根据其论文披露的行为、公开 API，以及 Kimina Lean Server 的开源 worker-pool 方案实现所需子集。

## 目标

- 避免公共 AXLE 的并发和速率限制。
- 通过长期运行且预加载 Mathlib 的 Lean worker 支持高频验证。
- 严格区分“代码可以编译”和“候选确实证明了给定 formal statement”。
- 拒绝 `sorry`、未许可公理、目标声明篡改和不安全声明。
- worker 卡死、崩溃或超出内存时，只损失当前请求并自动恢复容量。
- 让现有客户端主要通过修改 `axle.api_url` 切换到本地服务。

## 非目标

- 第一阶段不实现 AXLE 的全部 14 个工具。
- 不支持任意用户上传 Lake 项目或运行时安装依赖。
- 不提供公网多租户服务、计费、API-key 公平调度或跨机器自动扩缩容。
- 不在第一阶段实现每请求一个容器或 sandbox 进程。
- 不承诺抵御专门构造的 Lean kernel-bypass metaprogram；当前威胁模型是本地模型输出和可信调用方。

## 文档导航

- [architecture.md](architecture.md)：总体架构、请求流程、阶段计划和验收标准。
- [ADR-0001](adr/0001-service-scope-and-threat-model.md)：服务范围与威胁模型。
- [ADR-0002](adr/0002-pinned-lean-environment.md)：固定 Lean/Mathlib 环境。
- [ADR-0003](adr/0003-warm-repl-worker-pool.md)：预热 REPL worker 池。
- [ADR-0004](adr/0004-worker-isolation-and-lifecycle.md)：轻量隔离与 worker 生命周期。
- [ADR-0005](adr/0005-axle-compatible-api.md)：AXLE-compatible HTTP API。
- [ADR-0006](adr/0006-strict-proof-verification.md)：严格证明验证语义。
- [ADR-0007](adr/0007-queueing-retries-and-capacity.md)：队列、重试与容量规划。
- [ADR-0008](adr/0008-observability-and-validation.md)：可观测性与一致性验证。
- [references.md](references.md)：论文、API 和参考实现。

## ADR 状态约定

- `Proposed`：推荐方案，尚未通过实现和基准测试确认。
- `Accepted`：已经确认并作为实现约束。
- `Superseded`：已被后续 ADR 替代。

当前 ADR 均为 `Proposed`。实现过程中应记录实际测量结果，再决定是否接受或修订。
