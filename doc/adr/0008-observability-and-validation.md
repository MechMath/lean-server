# ADR-0008：通过 AXLE 对照与故障注入验证服务

- 状态：Proposed
- 日期：2026-09-18

## 背景

服务的主要风险不是 HTTP 是否可用，而是本地判定与 AXLE 语义不一致、跨请求状态污染以及异常后容量无法恢复。

## 决策

建立四层测试：

### 1. Lean verifier 单元测试

使用 ADR-0006 中的正反例直接测试 Lean metaprogram，断言 verdict、失败声明和错误类别。

### 2. API contract tests

使用固定版本的官方 `axiom-axle` SDK 调用本地服务，覆盖成功、验证失败、invalid argument、timeout、过载和 internal error。

### 3. Golden parity tests

选取已经由远端 AXLE 验证的 miniF2F 与 Putnam 样本及人工构造反例，保存输入和 AXLE 结果。本地服务必须给出相同 verdict；消息文本可以不同，但错误类别和 `failed_declarations` 应可解释。

不在日常 CI 中持续调用远端 AXLE。远端结果作为带来源、环境和生成日期的版本化 fixture 保存。

### 4. 稳定性与性能测试

- 连续运行至少数千个请求；
- 注入 worker kill、协议 EOF、超时和内存压力；
- 验证 supervisor 自动恢复目标 worker 数；
- 验证请求之间无声明、option 和 attribute 泄漏；
- 在并发 1、2、4、8……下测量吞吐，找到目标机器的饱和点；
- 分别记录冷启动、预热后延迟和 p50/p90/p99。

## 日志约束

每个请求生成 request ID，并记录 environment、timings、worker ID、attempt、verdict 和错误分类。默认不记录完整证明源码，避免日志体积失控；必要时通过显式 debug 配置保存到受控目录。

## 发布门槛

- strict-verification 反例全部通过；
- golden corpus verdict 无未解释差异；
- worker crash/timeout 后容量自动恢复；
- soak test 无持续 RSS 增长或句柄泄漏；
- 性能基准确认预热池明显优于每请求直接启动 Lean；
- 文档中记录当前机器配置、worker 数与可复现 benchmark 命令。
