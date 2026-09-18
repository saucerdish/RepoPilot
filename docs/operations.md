# 运行与排错

## 首次使用

在项目目录创建 .venv 并安装 requirements.txt，再配置 .env。Windows 使用 `.venv/Scripts/python.exe`，Linux 使用 `.venv/bin/python`。`main.py --help` 和离线测试无需模型依赖；真正运行 Agent 需要 openai 与 python-dotenv。测试必须从项目根目录运行。

## 典型任务

先让 Agent 阅读 README 与配置，再识别测试命令。多步任务先 todo_write；跨会话任务用 create_task/get_task/list_tasks。创建任务得到 ID 后再 update_task 添加依赖。检查 Git diff 与测试输出后才确认成果，模型的最后一段总结不替代实际验证。

并行工作先决定职责与任务边界，必要时创建 worktree，再 spawn_teammate。require_plan=true 时队友只读调查并 submit_plan，Lead 用 review_plan 审批。所有异步 shell 命令须事先通过 `--allow-command` 精确授权；不在名单内的异步命令会被拒绝。worktree 分支合并与清理需用户检查后进行。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| APIConnectionError / 超时 | 检查 OPENAI_BASE_URL、网络和服务状态；有界重试失败时不会计为完成 |
| JSON 判断失败 | 确认模型或兼容服务支持 response_format=json_object；目标保留 active 可继续 |
| shell Permission denied | 前台回答 y；异步轮次配置完全相同的 allow-command，不能用模糊前缀 |
| Task has incomplete dependencies | get_task 查看 blockedBy，完成依赖后再认领 |
| Owner already has an active task | 完成或通过宿主释放旧任务，不能抢占第二项 |
| Blocked: submit_plan | 提交计划，并由 Lead 审批对应 request_id |
| worktree 创建失败 | 检查 git worktree list 和 codex/ 分支，保留部分创建内容，不自动删除 |
| Workflow resume 锁存在 | 确认旧进程已停止，检查快照后手动处理残留锁 |
| Goal incomplete / limited | 查看 /goal，补充证据或重新执行验证；达到限制不代表完成 |
| Cron 不触发 | 保持交互进程运行；使用本地时间，停机期间不会补跑 |

## 存储维护

`.repopilot/state.sqlite3` 包含持久状态，SQLite 会生成 WAL/SHM 文件；不要在进程运行中直接覆盖。退出后再备份整个目录。归档和记忆可能包含源代码，按仓库要求处理。不支持自动销毁或自动恢复整段会话。不要删除尚有未合并任务分支或改动的 worktree。

若需要外部机器使用项目，重新建立虚拟环境，不能复制旧 .venv 的启动器。GitHub Actions 只执行无密钥测试与 demo；真实模型评测在本地运行。课程机制已集成不意味着每个复杂仓库任务都会成功，需要持续扩展评测与诊断数据。
