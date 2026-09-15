# RepoPilot

RepoPilot 是面向真实 Git 仓库的软件工程 Agent 学习项目，按 *learn-claude-code* 的章节逐步实现。目前完成 s01 Agent Loop、s02 Tool Use、s03 Permission 和 s04 Hooks。长期目标是让 Agent 理解仓库、规划任务、搜索与修改代码、运行测试和诊断错误，并加入 Context Management、MCP 与 Agent Evaluation。

## 运行

安装依赖：`pip install -r requirements.txt`。设置环境变量 `OPENAI_API_KEY` 和 `MODEL_ID`，可选设置 `OPENAI_BASE_URL`；也可以放在本地 `.env`。运行：

```sh
python main.py D:/path/to/git/repository
```

省略路径时使用当前目录。输入任务后，Agent 会把模型的工具调用按原始顺序执行；输入 `q` 退出。可以先试“阅读 README，找出测试命令”，再试“找到相关文件并修复失败测试”。任务结束后建议查看 `git diff`。

无需模型 API 的本地验证：

```sh
python -m unittest discover -s tests -v
```

## 目前的工具

`bash` 在目标仓库目录执行命令；`read_file` 读 UTF-8 文件；`write_file` 创建或覆盖文件；`edit_file` 只在旧文本恰好出现一次时替换；`glob` 按模式查找文件（`**` 表示递归）。文件工具把路径解析到目标仓库内，越界路径会被拒绝。

## s03：执行前权限判断

权限策略在 [PermissionHook](repopilot/agent/permission.py) 中实现，并作为 `PreToolUse` 注册。硬拒绝规则先处理明显危险的 shell 命令，例如 `rm -rf /`、`shutdown` 和 `mkfs`；删除命令（`rm`、`del` 等）、`chmod 777` 等操作会暂停并询问用户，默认拒绝。文件工具的仓库外路径直接拒绝，因为这些工具本身也不支持越界访问。被拒绝的调用不会执行，但拒绝原因会作为工具结果回传给模型。

这是学习用的规则匹配策略，**不是 shell 沙箱**。命令可能通过其他程序、拼接方式或子进程绕开简单的字符串规则。现阶段只应在可信仓库中运行；更严格的 shell 隔离和完整权限策略仍需后续实现。

## s04：类封装的 Hook

[HookManager](repopilot/agent/hooks.py) 为每个 Agent 实例保存一个事件到回调列表的映射，用 `register(event, callback)` 注册、`trigger(event, *args)` 触发。不同 Agent 的 Hook 不共享状态。事件包括：

| 事件 | 触发位置 | 返回值作用 |
| --- | --- | --- |
| `UserPromptSubmit` | 用户输入后、模型调用前 | 当前忽略返回值，适合记录或校验输入 |
| `PreToolUse` | 每个工具调用前 | 第一个非 `None` 返回值阻止执行，并成为工具结果 |
| `PostToolUse` | 工具实际执行后 | 当前忽略返回值，适合日志或输出检查 |
| `Stop` | 模型不再调用工具、Agent 即将结束时 | 非空返回值作为新的用户消息让 Agent 继续；每次任务最多续行三次 |

例如新增一个执行后日志，不需要改 Agent 循环：

```python
hooks.register("PostToolUse", lambda name, args, output: print(name, len(output)))
```

当前启动流程在 [main.py](main.py) 注册权限 Hook 与大输出提示 Hook。你可以继续在 `build_agent()` 中注册自己的 Hook。`PreToolUse` 回调接收 `(tool_name, args)`，`PostToolUse` 接收 `(tool_name, args, output)`，`Stop` 接收消息历史。Hook 可以用类实例（如 `PermissionHook`）或普通函数实现。

## 后续方向

下一阶段是 s05 的任务清单，然后完善仓库搜索和测试诊断。再往后加入上下文压缩、MCP 工具和可重复的 Agent 评测。每个阶段都应围绕真实仓库任务验证完成率，而不只是增加工具数量。
