# 参考资料

## AXLE

- Jimmy Xin 等，[AXLE: A Cloud Infrastructure for Lean 4 Theorem Proving Utilities](https://arxiv.org/abs/2606.26442)，2026。
- [AXLE 发布文章](https://axiommath.ai/territory/releasing-axle)。
- [AXLE Python SDK 与 CLI](https://github.com/AxiomMath/axiom-lean-engine)。公开仓库不包含服务端实现。
- [AXLE `verify_proof` 文档](https://github.com/AxiomMath/axiom-lean-engine/blob/main/docs/tools/verify_proof.md)。

论文中直接影响本设计的事实：

- 每个 AXLE 请求在独立 sandbox process 中执行，禁网并禁止候选写文件系统。
- environment 将 Lean 版本、Mathlib snapshot 和预构建依赖组成固定环境。
- AXLE 和 Kimina 都通过预加载 Mathlib 避免每请求冷启动。
- `verify_proof` 是 Lean metaprogram，检查声明匹配、`sorry`、非许可 axiom 和 `unsafe`。
- AXLE 不 replay 整个 environment；该性能取舍不适合完全敌对输入。
- 单机吞吐在并发接近物理核心数时饱和。

## 可复用实现

- [Kimina Lean Server](https://github.com/project-numina/kimina-lean-server)：MIT 许可的 FastAPI、并行 Lean REPL pool 和 import cache 实现。
- Marco Dos Santos 等，[Kimina Lean Server: Technical Report](https://arxiv.org/abs/2504.21230)，2025。
- [Lean REPL](https://github.com/leanprover-community/repl)：Lean 的机器交互接口。
- [Mathlib](https://github.com/leanprover-community/mathlib4)。

## 更严格的验证工具

- [lean4checker](https://github.com/leanprover/lean4checker)。
- [Comparator](https://github.com/leanprover/comparator)。
- [SafeVerify](https://github.com/GasStationManager/SafeVerify)。

这些工具适合更强的敌对输入模型，但 AXLE 论文中的实验显示 replay/recheck 会显著增加延迟。是否引入它们应由威胁模型决定，而不是默认加入本地高频验证热路径。
