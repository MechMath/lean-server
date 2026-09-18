# ADR-0004：轻量隔离与 worker 生命周期

- 状态：Proposed
- 日期：2026-09-18

## 背景

本服务无需承担公网敌对输入，但模型可能生成不终止 tactic、超大 elaboration、进程崩溃或意外副作用。长期 worker 还可能发生内存增长和状态污染。

## 决策

使用进程级 worker 隔离，而不是每请求 sandbox：

- API/controller 永不在自身进程执行 Lean；
- worker 作为独立进程组启动，超时后终止整个进程组；
- worker 设置 RSS/cgroup 内存上限；
- worker crash、EOF、协议错误或超时后立即标记 unhealthy 并异步补充；
- worker 达到 `max_uses` 或 RSS 阈值后，在当前请求结束后优雅回收；
- 一次 infrastructure failure 最多在新 worker 上重试一次；
- Lean 编译或严格验证失败不重试。

部署容器默认断开外网，Mathlib、toolchain 和应用代码只读。可写目录限制为独立 tmpfs；服务不把宿主机 workspace 暴露给 Lean worker。服务只监听 loopback。

## 状态清理测试

至少覆盖：

- 请求 A 定义新声明，请求 B 无法访问；
- 请求 A 修改 option，请求 B 使用默认值；
- 请求 A 注册 attribute/scoped notation，请求 B 不继承；
- 请求 A 超时后，请求 B 在替代 worker 上正常完成；
- worker 重启前后的验证判定一致。

## 后果

- 相比每请求进程，吞吐和延迟更好。
- 单个 worker 的污染最多影响该 worker 的后续请求，因此清理测试和定期回收是上线条件。
- 若发现无法可靠恢复 clean environment，应将 `max_uses` 降为 1，接受冷启动成本，或另立 ADR 引入更强隔离。
