# ADR-0005：提供 AXLE-compatible `verify_proof` API

- 状态：Proposed
- 日期：2026-09-18

## 背景

现有 `lean-eval-toolkit` 使用官方 `AxleClient`。如果本地服务兼容其请求路径和核心响应字段，客户端只需修改 `api_url`，无需维护第二套评测协议。

## 决策

实现：

```text
POST /api/v1/verify_proof
```

首版接受以下字段：

```json
{
  "formal_statement": "import Mathlib\ntheorem ... := by sorry",
  "content": "import Mathlib\ntheorem ... := by ...",
  "environment": "lean-4.30.0",
  "permitted_sorries": [],
  "mathlib_options": false,
  "use_def_eq": true,
  "ignore_imports": true,
  "timeout_seconds": 120
}
```

至少返回以下兼容字段：

```json
{
  "okay": false,
  "content": "...normalized candidate...",
  "lean_messages": {"errors": [], "warnings": [], "infos": []},
  "tool_messages": {"errors": [], "warnings": [], "infos": []},
  "timings": {
    "total_ms": 0,
    "formal_statement_ms": 0,
    "declarations_ms": 0,
    "candidate_ms": 0
  },
  "failed_declarations": []
}
```

兼容性通过官方 `axiom-axle` Python SDK 的集成测试确认，而不是只用手写 HTTP 请求确认。

对于首版不支持的参数组合，返回明确的 invalid-argument 响应，不得静默忽略。HTTP status 与响应 body 需要经过 SDK 实测，确保服务过载和基础设施错误能被现有 retry 策略识别。

## 后果

- `lean-eval-toolkit` 可以通过配置切换远端 AXLE 和本地服务。
- API schema 会受到官方 SDK 演进影响；应固定测试过的 SDK 版本并加入 contract tests。
- “兼容”仅覆盖 `verify_proof` 所需子集，不代表实现其他 AXLE 工具。
