# 实现顺序与依赖

当前 s01–s08 已完成。后续按依赖实现：s09 Memory → s10 Task DAG → s11 Background → s12 Cron → s13 Teams/worktree；s14 MCP 从基础工具与权限独立扩展；s15 集成所有服务 → s16 Workflow → s17 Goal。Workflow 与 Goal 可独立使用，集成时前者结果作为后者证据。

## s09 持久记忆

使用目标仓库 `.repopilot/state.sqlite3` 存储索引与正文，SQLite 事务用于并发和整理失败回滚。`save_memory` 显式写入，`recall_memory` 关键词召回最多五条、8,000 字符。CLI 在请求开始时召回，在结束后通过独立模型调用提取候选；提取失败不影响任务结果。只允许四类持久信息，拒绝明显的临时任务约束。当前关键词相关性属于基线方案，不是向量检索。
