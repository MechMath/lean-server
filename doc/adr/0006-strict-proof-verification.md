# ADR-0006：在 Lean 内部实现严格证明验证

- 状态：Proposed
- 日期：2026-09-18

## 背景

普通 Lean 编译会接受 `sorry`、新公理或被改写的 theorem statement，因此不能直接作为模型评测结果。Python 字符串或正则匹配无法可靠处理 namespace、notation、宏展开和 definitional equality。

## 决策

严格检查实现为 Lean metaprogram，并在同一固定 base environment 中分别 elaborate formal statement 和 candidate。

验证步骤：

1. 记录 base environment 的 declarations。
2. elaborate formal statement，找出相对 base 新增且需要 candidate 实现的声明。
3. 从同一 base 独立 elaborate candidate。
4. 对每个 formal target 查找 candidate 同名声明。
5. 比较 declaration kind。
6. 比较 elaborated type；`use_def_eq=true` 时使用 Lean definitional equality，否则比较未约简表达式。
7. 对 formal 中具有非占位实现的 definition，比较其类型和定义值；具体兼容规则用 golden tests 固化。
8. 对 candidate 声明及目标的传递依赖检查 `sorryAx`、公理和 safety。
9. 汇总每个失败声明及结构化错误消息。

默认仅允许 Lean 标准公理：

- `propext`
- `Quot.sound`
- `Classical.choice`

默认 `permitted_sorries=[]`。任何 `sorry`、未许可 axiom 或目标/可达依赖中的 `unsafe` 都导致失败。额外 helper declaration 可以存在，但不能借此向目标引入非法依赖。

检查必须基于 elaborated declaration，而不是只扫描源码文本；源码扫描只能作为禁止明显危险 command 的前置防线。

## 已知边界

与 AXLE 相同的性能取舍是：首版不从零 replay 整个 Lean environment。专门构造的 metaprogram 可能绕过正常 kernel-checked declaration 路径。当前本地、单租户威胁模型接受该限制，并通过固定 imports、禁网、只读文件系统和 worker 回收降低风险。

如未来需要敌对输入保证，应评估 SafeVerify、Comparator 或 kernel replay；不得仅把当前实现描述为对任意恶意 Lean 都严格安全。

## 必须覆盖的反例

- 正确证明：接受。
- theorem statement 被弱化、改名或改变参数：拒绝。
- 直接或经 helper 使用 `sorry`：拒绝。
- `axiom bad : False` 后证明任意目标：拒绝。
- 宏展开为 axiom：拒绝。
- 使用 `unsafeCast` 或不安全声明：拒绝。
- formal 中多个目标只证明一部分：拒绝并列出缺失声明。
- namespace shadowing 伪造同名短名称：拒绝。
- 合法使用 `Classical.choice`：接受。
- parser/elaborator error：作为验证失败返回，而不是 infrastructure error。
