# 仓库任务性能测试

## 运行

```powershell
.venv/Scripts/python.exe -m repopilot.benchmark --output-root D:/学习/RepoPilot-agent-tests --timeout 360 --max-turns 20
```

测试使用当前 `.env` 中的真实模型。每次运行创建新的时间戳目录，不覆盖已有仓库；每项为独立 Git 仓库，最多 20 轮、360 秒。一项仅一次尝试，客户端有界网络重试包含在该尝试中，不由评测器事后挑选最好结果。`--case` 可以单独复测，但应保留原始失败记录。

监督进程中断后，可添加 `--resume-run 原运行目录`。已有 `worker-result.json` 的任务只重新进行外部验收，不重新调用 Agent；尚未创建的任务继续执行。已有仓库但没有 worker 结果时会停止，避免覆盖未确认的尝试。

## 四项任务

| 任务 | 能力 | 特殊验收 |
| --- | --- | --- |
| config-precedence | 跨模块理解与修改 | defaults/file/env 优先级、布尔解析、端口范围、错误传播 |
| pagination-diagnosis | 错误诊断与边界恢复 | 空页、有效 falsy 游标、重复游标保护、网络异常传播 |
| invoice-cli | 跨文件功能实现 | Decimal 舍入、数量校验、新增 CLI、退出码与 JSON 输出 |
| context-recovery | 压缩后的需求与约束保持 | 注入约 90 KB 历史诊断数据，触发压缩；累加、排序、类型与负数边界 |

这是生成仓库的小规模集成测试，不是 SWE-bench，也不是大型真实开源仓库能力证明。长上下文任务是人为压力输入，应单独解释。

## 两层验证与范围保护

公开测试位于目标仓库 tests/，供 Agent 诊断。独立验收测试只在 worker 停止后生成于仓库外，覆盖公开用例没有验证的边界。验收由外部进程执行，结果不取决于模型宣称。

保护文件的初始 SHA256 用于检查测试、README 等是否被改动；Git diff 和未跟踪文件共同检查改动是否在允许文件集合内。Agent 文件工具不能访问仓库外的验收目录。模型只被授权公开测试命令与少量只读 Git 命令，禁止通过任意 shell 执行外部脚本。

## 指标

- 功能通过：公开测试和独立验收均退出 0，且范围合规。
- 严格通过：功能通过、Goal 状态 completed、无执行错误且未超时。
- Goal 误报：模型目标判断为 completed，但独立功能验收失败；这是相对于完整任务合同的误报，而非隐藏测试被模型读取。
- 耗时：从 worker 启动到退出，包含模型内部重试，不包含后续外部验收时间。
- API 调用：包含主模型、摘要、目标判断、记忆维护及子 Agent，错误请求也计数。
- Token：累计 API 返回的 usage；没有 usage 的失败调用无法估算消耗。报告不推断费用。

基础设施连接失败、执行异常、超时、范围违规、验收失败、Goal 未完成分别标记。单个样例无法估计方差；正式比较应固定模型、设置和任务，多次运行或加入基线消融。

## 产物

每项保留 repository/（最终改动）、agent.log、conversation.json、worker-result.json、initial-tests.txt、public-tests.txt、acceptance-tests.txt、acceptance.py、diff.patch。运行目录生成 report.md 和 report.json；JSON 包含逐调用时延和 usage，但不记录凭据。

这是运行监督与范围检查，不是操作系统沙箱。请不要将验收机制视为防恶意模型的强隔离。目录可能包含源码和对话，清理前检查并保存需要的结果。
