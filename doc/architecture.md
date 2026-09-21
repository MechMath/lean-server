# 本地 Lean 验证服务总体方案

## 1. 系统边界

服务只监听 loopback 或可信内网，调用方主要是本机的 `lean-eval-toolkit`。当前 HTTP 接口为：

- `GET /healthz`
- `GET /readyz`
- `POST /check`
- `POST /verify_proof`

第一阶段只注册 `lean-4.30.0`，其内容是固定版本的 Lean、对应 Mathlib、Lean REPL 和本项目的验证 metaprogram。运行时请求不能改变依赖集合。

## 2. 组件

```text
lean-eval-toolkit
        |
        | POST /verify_proof
        v
+------------------------------+
| Python API / compatibility   |
| - request validation         |
| - bounded queue              |
| - timeout and retry policy   |
| - AXLE response mapping      |
+------------------------------+
        |
        v
+------------------------------+
| Worker supervisor            |
| - one in-flight / worker     |
| - crash detection            |
| - kill and respawn           |
| - max-uses/RSS recycling     |
+------------------------------+
        |
        v
+------------------------------+
| N warm Lean REPL processes   |
| - Mathlib preloaded          |
| - clean base Environment     |
| - custom verifyProof command |
+------------------------------+
```

Python 服务可以借鉴或复用 Kimina Lean Server 的进程池、header cache 和 supervisor 设计。Lean 侧增加自定义 REPL command，避免用 Python 对 Lean 源码做语义判断。

## 3. 一次验证请求

1. API 校验 JSON 大小、environment、timeout 和必要字段。
2. 请求进入有界队列；队列已满时立即返回可重试的过载错误。
3. dispatcher 将请求交给空闲 worker，每个 worker 同时只运行一个请求。
4. worker 从只读的、预加载 Mathlib 的 base environment 分别 elaborate formal statement 和 candidate。
5. Lean metaprogram 找出 formal statement 新增的目标声明。
6. 对每个目标检查 candidate 中同名声明的 kind、类型和必要时的定义值。
7. 检查 candidate 中的 `sorry`、公理依赖、`unsafe` 及禁止命令。
8. 返回结构化 Lean 消息、工具消息、失败声明与 timings。
9. supervisor 根据请求次数、RSS 或异常状态决定复用或重启 worker。

formal 与 candidate 必须从同一个不可变 base environment 分叉，不能让 formal 中的 `sorry` 声明泄漏到 candidate，也不能让前一请求新增的声明进入后一请求。

## 4. 推荐默认配置

```yaml
server:
  host: 127.0.0.1
  port: 8000
  request_body_max_bytes: 2097152

environment:
  id: lean-4.30.0
  lean_version: v4.30.0
  mathlib_revision: v4.30.0
  default_header: |-
    import Mathlib

workers:
  count: auto
  init_count: auto
  max_uses: 500
  max_rss_bytes: 6442450944
  startup_timeout_seconds: 180

requests:
  default_timeout_seconds: 120
  maximum_timeout_seconds: 300
  queue_capacity_factor: 4
  infrastructure_retries: 1

verification:
  use_def_eq: true
  permitted_sorries: []
  allowed_axioms:
    - propext
    - Quot.sound
    - Classical.choice
```

`workers.count` 应取可用物理 CPU 核数和内存预算允许的 worker 数中的较小值，不应机械使用逻辑线程数。AXLE 论文的单机实验显示吞吐在物理核心数处饱和。

## 5. 错误分类

| 类别 | 示例 | 是否内部重试 | 是否重启 worker |
|---|---|---:|---:|
| 验证失败 | 类型不匹配、`sorry`、非法 axiom | 否 | 否 |
| Lean 源码错误 | parser/elaborator error | 否 | 通常否 |
| 请求错误 | 未知 environment、JSON 过大 | 否 | 否 |
| 超时 | tactic 不终止 | 否 | 是 |
| worker 故障 | EOF、crash、协议损坏 | 是，最多一次 | 是 |
| 服务过载 | 队列满、等待超时 | 由客户端退避重试 | 否 |

只有基础设施错误可以自动重试。同一段无效 Lean 代码不能因为重试而占用更多容量。

## 6. 实施阶段

### Phase 0：可复现环境

- 固定 Lean/Mathlib 4.30.0。
- 构建并缓存 Mathlib 与 REPL artifacts。
- 记录源码 revision、toolchain 和镜像 digest。
- 用少量 miniF2F/Putnam 样本确认 `import Mathlib`。

### Phase 1：并发编译服务

- 实现健康检查、环境列表和基础 `/verify_proof` 路由。
- 建立预热 worker 池、有界队列、超时杀进程和自动补 worker。
- 暂时只返回 Lean 编译结果，用于测量启动、延迟、RSS 和吞吐；不得作为最终严格判分上线。

### Phase 2：严格验证

- 实现 Lean 侧 `verifyProof` command。
- 实现声明发现、类型/定义比较、axiom、`sorry` 和 `unsafe` 检查。
- 对齐 AXLE response schema 和错误分类。

### Phase 3：工具链接入

- 在 `lean-eval-toolkit` 中仅修改 `axle.api_url` 完成端到端验证。
- 对 AXLE 已验证的 miniF2F 和 Putnam 样本运行一致性测试。
- 对本地服务错误启用现有 retry 逻辑。

### Phase 4：稳定性与容量

- 执行长时间 soak test、超时风暴、worker crash 和 OOM 测试。
- 根据实测调整 worker 数、RSS 限制、max uses 和队列长度。
- 决定是否需要容器级只读文件系统、断网或更强请求隔离。

## 7. 验收标准

- 对有效证明、无效证明和超时样本均返回确定、结构化结果。
- candidate 修改 theorem statement 时必须拒绝。
- candidate 使用 `sorry`、自定义 axiom 或 `unsafe` 时必须拒绝。
- 一个请求超时或使 Lean crash 后，服务恢复到完整 worker 数，其他 in-flight 请求不受影响。
- 连续请求不能看到前一请求新增的声明、option 或 attribute。
- 对选定的 AXLE golden corpus，判定一致率达到 100%；任何差异必须分类并形成新的 ADR 或已知限制。
- 在目标机器上，并发增加到物理核心数前吞吐应明显扩展；超过饱和点不得作为默认配置。

## 8. 尚待实测的问题

- Lean 4.30.0 下 Mathlib 预加载 worker 的真实常驻内存。
- 标准 Lean REPL 是否足以承载 verifier，还是需要维护小型 fork。
- clean environment 的恢复成本以及是否存在 Lean 进程级全局状态泄漏。
- `use_def_eq=true` 的性能影响和与 AXLE 实际结果的差异。
- worker 最合适的 `max_uses` 与 RSS 回收阈值。
