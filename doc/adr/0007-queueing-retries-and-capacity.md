# ADR-0007：有界队列、有限重试与按核心配置容量

- 状态：Proposed
- 日期：2026-09-18

## 背景

评测程序可能瞬间提交大量证明。无界任务和协程会增加内存、文件描述符和尾延迟。AXLE 的单机结果也表明并发超过物理核心数后吞吐不再增长。

## 决策

- 每个 worker 最多一个 in-flight 请求。
- API 前设置有界 FIFO 队列，初始容量为 `worker_count * 4`。
- 队列已满时立即返回过载错误，让客户端指数退避；不无限等待。
- request timeout 分为 queue wait 和 execution 两段，并分别记录。
- caller timeout 上限由服务端限制，防止单请求长期占用 worker。
- 仅 worker crash、EOF、协议损坏等基础设施错误在新 worker 上内部重试，默认最多一次。
- Lean error、严格验证失败、请求错误和确定性超时不内部重试。

worker 初始数量：

```text
min(
  可用物理 CPU 核数,
  floor(可分配内存 / 实测单 worker 峰值 RSS)
)
```

保留至少一个 CPU 核和足够内存给 API、模型调用方及操作系统。最终值以目标机器 benchmark 为准。

## 指标

必须采集：

- queue depth、queue wait 和 rejected requests；
- active/ready/restarting worker 数；
- 请求总延迟及 formal/candidate/verification 分段耗时；
- timeout、crash、OOM、internal retry；
- 每个 worker 的 request count 和 RSS；
- 按 verdict 分类的计数。

## 后果

- 高峰时系统提供明确 backpressure，不因无界排队失控。
- 客户端并发应与服务容量协调；把客户端并发无限调大不会增加吞吐。
- 本地单租户暂不需要按 API key 公平队列。
