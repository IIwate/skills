---
name: worker-mcp-cleanup
description: 仅供 `csv-stream-orchestrator` 主线程在其流程内调用的 MCP 进程回收能力。在并发 worker 后，按“会话 owner 绑定 + 基线快照 + 增量清理”回收新增 MCP 相关进程（`ace-tool`、`code-index-mcp`、`uv/uvx/python` 子进程），用于降低长会话内存占用，并避免误杀其他会话或主线程既有进程。
---

# Worker MCP Cleanup

## 手动触发规则（硬约束）

- 本技能不是独立入口；仅允许在 `csv-stream-orchestrator` 主线程流程内被显式调用。
- 若请求未指明来自 orchestrator 的当前批次流程（含批次上下文、快照路径），禁止执行 `cleanup` 杀进程动作。
- `cleanup` 仅允许在 orchestrator 已完成本轮全部任务（含回流任务）`wait + close_agent` 之后执行。
- 未创建基线快照时禁止执行 `cleanup`；必须先执行 `snapshot`。
- 快照路径必须指向对应 CSV 批次目录：`docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json`，禁止为 baseline 单独建目录。
- 基线文件默认是临时文件：`cleanup` 成功后自动删除；仅在 `cleanup` 失败或显式 `--keep-baseline` 时保留。

## 快照与输出最小化（硬约束）

- `snapshot` 文件只保留最小必要字段：`schema`、`created_at`、`owner_pid`、`owner_process(ProcessId/Name)`、`baseline_pids`、`owner_bound_baseline_pids`。
- 快照文件禁止写入主机名、绝对路径命令行、完整进程列表等环境敏感信息。
- `list-delta` / `cleanup` 返回的进程明细仅允许 `ProcessId`、`ParentProcessId`、`Name`。
- 若需要排障细节，优先在本地临时查看，不写入可持久化快照文件。
- 建议将 `docs/csv/**/mcp-baseline.json` 加入 `.gitignore`，避免基线文件被提交或外传。

## 主线程工作流

1. 在本批任务启动前创建基线快照：
   - `python scripts/worker_mcp_cleanup.py --mode snapshot --snapshot-path "<项目根>/docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json"`
   - 需要显式指定 owner 时：`python scripts/worker_mcp_cleanup.py --mode snapshot --snapshot-path "<项目根>/docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json" --owner-pid <owner_pid>`
2. 由 orchestrator 主线程分发并执行 worker（`spawn_agent` 或 `spawn_agents_on_csv`）。
3. worker 返回后，先完成主线程回收动作（例如 `wait`、`close_agent`）。
4. 触发增量清理：
   - `python scripts/worker_mcp_cleanup.py --mode cleanup --snapshot-path "<项目根>/docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json"`
   - 需要保留基线用于排障时：`python scripts/worker_mcp_cleanup.py --mode cleanup --snapshot-path "<项目根>/docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json" --keep-baseline`
5. 读取 JSON 输出中的 `killed_count`、`remaining_owner_delta_count`，确认清理效果。

## 并发与时机约束

- 无论并发数多少，orchestrator 流程内均只在“本轮全部任务（含回流）完成 `wait + close_agent`”后执行一次 `cleanup`。
- 不在仍有活跃 worker 正在调用 MCP 时清理，避免误伤仍在使用的 MCP 进程。

## 脚本说明

- 脚本路径：`scripts/worker_mcp_cleanup.py`
- 脚本会尝试将 stdout/stderr 设为 UTF-8，避免 `--help` 与 JSON 输出出现中文乱码。
- 支持模式：
  - `snapshot`：记录当前 MCP 进程 PID 基线。
  - `list-delta`：只列出相对基线新增的 MCP 进程，不执行结束进程。
  - `cleanup`：仅结束相对基线新增的 MCP 进程。
- 支持参数：
  - `--snapshot-path`：快照文件路径（必填）。
  - `--owner-pid`：会话 owner 进程 PID（可选；默认自动从当前调用链识别 `codex.exe` 或 `codex.js`）。
  - `--dry-run`：预演清理，不实际结束进程。
  - `--keep-baseline`：在 `cleanup` 后保留基线快照（默认仅在 cleanup 失败时保留）。

## 会话绑定机制（关键）

- 快照阶段会记录 `owner_pid`。
- 清理阶段先计算“基线之后新增”的 MCP 进程，再按父子进程链过滤为“属于该 `owner_pid` 的后代进程”。
- 最终仅清理 `owner_delta`，不会清理 `non_owner_delta`（即其他会话新增的 MCP）。
- 若 `owner_pid` 已不存在，清理将进入保护模式：不执行结束进程，仅输出统计与跳过清单。

## 目标进程范围（硬约束）

脚本仅识别以下 MCP 相关进程，不会按“进程名全量匹配”粗暴清理：

- `node.exe` 且命令行包含 `ace-tool`
- `code-index-mcp.exe`
- `uv.exe` / `uvx.exe` 且命令行包含 `code-index-mcp`
- `python.exe` 且命令行包含 `code-index-mcp(.exe)`

最终清理集合 = 当前目标进程集合 - 基线快照 PID 集合。
在会话绑定模式下，最终执行清理集合 = `owner_delta`（属于 `owner_pid` 的增量）。

## 建议集成点

- 在主线程调度循环中增加两个调用点：
  1) 批次开始前调用 `Snapshot`
  2) worker 回收后调用 `Cleanup`（或批次末统一调用）
- 将清理结果 JSON 写入日志，保留 `killed_pids` 与 `failed` 字段便于追踪。

## 输出字段

脚本统一输出 JSON，关键字段：

- `mode`
- `snapshot_path`
- `baseline_count`
- `current_count`
- `delta_count`
- `killed_count`
- `remaining_owner_delta_count`
- `owner_pid`
- `owner_alive`
- `owner_delta_count`
- `non_owner_delta_count`
- `killed_pids`
- `ignored_not_found_count`（可选；taskkill 返回进程不存在时计入忽略，不视为失败）
- `ignored_not_found`（可选；仅用于降噪与追溯）
- `failed`
- `snapshot_retained`
- `snapshot_retain_reason`

补充：
- `owner_delta`、`non_owner_delta`、`skipped_non_owner` 中的进程对象仅包含 `ProcessId`、`ParentProcessId`、`Name`。
