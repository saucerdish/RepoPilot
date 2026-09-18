# 实现顺序与依赖

## s17 目标判断

CLI `/goal 验收条件` 设置并立即执行，`/goal` 查看，`/goal clear` 清除；单次模式也支持。Stop Hook 用独立、无工具模型请求检查具体证据。未完成时注入原因继续，后台或队友尚未返回则 defer，事件回到会话后继续判断。不可能完成、已完成、判断失败、全局轮数与续行限制分开记录；失败和达到限制保留 active goal，不伪装成完成。目标状态保存在 SQLite，重启可查看和手动继续，但完整会话并不自动恢复，因此重启后需要重新提供验证证据。判断器依据会话而非直接读取仓库，结果仍有模型误判可能。

## s16 工作流

宿主注册 async 函数，模型仅提供名称与 schema 校验的参数。提供 agent、parallel 等齐、pipeline 分阶段、phase、log 与一层 workflow 嵌套。子模型输出按支持的 JSON schema 子集校验，失败重试一次。稳定 SHA256 调用键包括工作流名、版本、label、prompt、schema，结果以 SQLite journal 持久化；相同 run_id 续跑复用已完成调用。快照与事件保存于 `.repopilot/workflows/`，排他 lock 防止同时 resume，崩溃后的残留 lock 需人工确认后处理。内置 review-changes 审计并验证两个维度，运行同步等待工作流返回；子调用在线程中执行，不读终端。缓存只适用于同一运行与参数，外部仓库变化后应新建运行。

## s15 集成宿主

AgentHost 用同一锁串行处理用户请求、Cron prompt、团队和后台结果。CLI 采用单一输入读取线程，空闲时每半秒检查事件并唤醒模型；审批回复也从同一输入队列消费，避免多个线程竞争终端。所有 shell 调用都要求确认，只有 `--allow-command` 明确授权的完全相同命令可在异步轮次执行；硬拒绝优先。非交互事件与队友不会读终端。Cron 在首次模型成功响应后 ack，失败保留待交付状态。`--task` 单次模式不审批，退出关闭运行时。提示组装、记忆提取和整理、动态工具池与可恢复上下文在统一模型循环中工作。

## s14 MCP

实现真实 stdio JSON-RPC：initialize、initialized、分页 tools/list、tools/call、超时和进程清理。通过 CLI `--mcp-config` 显式提供宿主 JSON 配置，格式为 `{ "docs": { "command": ["python", "server.py"], "allow_tools": ["search"] } }`。连接启动本地程序需确认；已发现工具统一命名 mcp__server__tool，拒绝名称碰撞与长度超限。allow_tools 是宿主精确授权名单，其他外部工具确认，服务端 hint 不决定权限。当前不支持 HTTP、采样、资源订阅等完整 MCP 能力。传输实现参考官方规范 https://modelcontextprotocol.io/specification/2025-11-25/basic/transports。

## s13 团队与工作目录

持久队友线程在 WORK、IDLE、AWAITING_PLAN、STOPPED 间转换，消息保存在 SQLite 收件箱。空闲队友检查消息后扫描并原子认领 ready task，一名 owner 只能持有一个进行中任务。结果、空闲和错误分开投递；不完整任务释放后停止该队友，避免反复抢占。计划请求带 request_id、任务 ID 和版本，旧任务审批无效；待审批时禁止 shell 和写操作。工作目录绑定任务，worktree 使用 codex/ 分支，创建失败不删除残留。移除及合并由用户检查后手动处理，不提供破坏性清理工具。队友线程不读取终端审批输入。

## s12 本地定时调度

支持五字段 Cron（星号、步长、范围、逗号集合），注册前校验。所有任务持久化，触发前事务写入 pending_delivery；模型成功接收后才 ack，一次性删除、周期任务清标记。同一分钟不重复触发，崩溃恢复至少一次交付。采用本地时区，星期 0 为周日，日和星期同时限制时使用 AND。仅在宿主运行时检查时间，停机期间不补跑；自动唤醒在 s15 宿主接入。

## s11 后台命令

`bash` 显式设置 `run_in_background=true` 后返回占位结果，完成事件在后续模型轮次收集；不会复用原 tool_call_id。结果包含命令与退出码，非零、超时和异常均记失败。命令使用独立进程组，正常结束、超时或运行时关闭时清理子进程。进程组不是沙箱。当前事件自动唤醒由 s15 集成宿主完成。

## s10 持久任务图

任务记录包含状态、owner、blockedBy 和可选工作目录。先创建节点获得真实 ID，再添加依赖；拒绝环、缺失依赖及认领后的结构修改。SQLite BEGIN IMMEDIATE 保证并发认领只有一人成功，完成动作校验宿主绑定的 owner。六个任务工具已接入 Agent；任务清单仍用于单轮步骤，任务图用于跨会话和协作。

当前 s01–s17 已集成。实际实现顺序：s09 Memory → s10 Task DAG → s11 Background → s12 Cron → s13 Teams/worktree；s14 MCP 从基础工具与权限独立扩展；s15 集成所有服务 → s16 Workflow → s17 Goal。Workflow 与 Goal 可独立使用，集成时前者结果作为后者证据。每个新增阶段分别验证、提交和推送；最终收尾提交补充评测、CI 和恢复机制回归测试。

## s09 持久记忆

使用目标仓库 `.repopilot/state.sqlite3` 存储索引与正文，SQLite 事务用于并发和整理失败回滚。`save_memory` 显式写入，`recall_memory` 关键词召回最多五条、8,000 字符。CLI 在请求开始时召回，在结束后通过独立模型调用提取候选；提取失败不影响任务结果。只允许四类持久信息，拒绝明显的临时任务约束。当前关键词相关性属于基线方案，不是向量检索。
