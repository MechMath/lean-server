# ADR-0002：固定 Lean/Mathlib 环境

- 状态：Proposed
- 日期：2026-09-18

## 背景

Lean 证明依赖精确的 Lean、Mathlib 和项目依赖版本。允许请求动态改变依赖会破坏可复现性，也会迫使 worker 重新构建或加载环境。

## 决策

初始环境 ID 为 `lean-4.30.0`，固定：

- Lean toolchain `v4.30.0`；
- Mathlib tag/revision `v4.30.0`；
- 与该版本兼容的 Lean REPL revision；
- 本项目严格验证 metaprogram 的精确 commit；
- 默认用户可见 header 为 `import Mathlib`。

环境在镜像构建阶段完成依赖下载、`lake update`、Mathlib cache 获取和 verifier build。运行中的服务不访问网络，也不修改依赖。

每个环境通过 manifest 记录版本、Git revision、构建时间和产物摘要。API 接受显式的 `environment`，未知值立即失败，不静默回退。

candidate 中的 import 默认按 AXLE `ignore_imports=true` 的思路归一为注册环境的固定 header。是否支持 `ignore_imports=false` 延后决定。

## 后果

- 所有 benchmark 和回归结果可复现。
- `import MiniF2F.ProblemImports` 等环境外依赖不会被运行时解析；数据必须使用 `import Mathlib` 或注册新的环境。
- 增加 Lean 版本意味着新增一个完整的预构建环境，而不是修改现有环境。
- 镜像和缓存占用增加，但请求热路径更简单、稳定。
