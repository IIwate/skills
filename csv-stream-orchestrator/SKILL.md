---
name: csv-stream-orchestrator
description: 将需求转为并行探索、计划评审、CSV 子任务分发、流式验收和失败回流重分发的执行流程。仅当用户消息以 `$csv-stream` 开头时使用；任何不含此前缀的请求都不要触发本技能。用于手动触发后先等待用户输入需求，再按模板生成计划、CSV 和 worker 指令。
---

# CSV Stream Orchestrator

## 手动触发规则（硬约束）
- 仅当用户消息以 `$csv-stream` 开头时启用本技能。
- 用户只发送 `$csv-stream`（或 `$csv-stream start`）时，先进入等待模式并回复“请提供需求”，不执行探索、不改代码。
- 用户发送 `$csv-stream <需求>` 时，将 `<需求>` 作为本轮唯一需求输入并开始流程。

## 资源与模板
- 执行计划模板：`assets/plan-template.md`
- 任务 CSV 模板：`assets/tasks-template.csv`
- worker 下发模板：`assets/worker-dispatch-template.md`
- worker 回传模板：`assets/worker-result-template.json`

## 任务拆分规范（门控）
在生成任务 CSV 前，先完成“验收清单 -> 任务拆分 -> 冲突分组 -> 拆分自检”。

### 拆分硬标准
- 一个任务只对应一个可验收结果。
- 一个任务只改一组明确文件区域，避免与其他任务冲突。
- `最小验证` 必须可独立执行（单命令或固定检查项）。
- 任务失败后必须能回流（可填写 `错误码`、`错误摘要`、`修复提示`）。
- 任务描述必须上下文自足，worker 单独拿到任务即可执行。
- 单任务目标工时建议在 30-90 分钟。

### 拆分步骤
1. 基于确认后的执行计划，先写主线程验收清单。
2. 每个验收条目拆成 1-2 个候选任务。
3. 按 `目标路径` 做冲突分组：同组串行、异组并行。
4. 为每个任务补齐 `任务说明`、`最小验证`、`最大重试次数`。
5. 做快速失败预演：首个未达标点出现时，能否立即判定 `accept_fail` 并回流。
6. 未通过上述任一检查时，返回第 2 步重拆，不写入 CSV。

### 禁止拆分方式
- 按目录平均切分但无清晰验收边界。
- 一个任务绑定多个互不相关目标。
- 多个任务并发修改同一文件同一区域。
- 让 worker 做最终验收判定。

## 依赖治理（硬约束）
- 依赖引入只允许主线程执行，worker 禁止新增/升级/删除依赖。
- 主线程在任务分发前完成依赖变更与锁文件更新，并做基础可用性验证。
- worker 禁止修改依赖清单与锁文件（例如 `package.json`、`pnpm-lock.yaml`、`package-lock.json`、`yarn.lock`、`Cargo.toml`、`Cargo.lock`、`requirements*.txt`、`poetry.lock`）。
- worker 若发现当前任务需要新依赖，必须回传 `exec_state=blocked`、`error_code=DEPENDENCY_REQUIRED`，并在 `error_summary` 写清所需依赖与原因。

## 执行流程
1. 使用 `multi_tool_use.parallel` 做只读并行探索，收集实现上下文。
2. 按 `assets/plan-template.md` 输出计划，等待用户确认。
3. 用户确认后，主线程先完成依赖引入与基础验证，再按“任务拆分规范（门控）”生成任务清单，并在项目根目录创建以下两个文件：
   - `docs/plans/YYYY-MM-DD-<topic>-design.md`（写入用户确认后的执行计划）
   - `docs/csv/YYYY-MM-DD-<topic>.csv`（按 `assets/tasks-template.csv` 生成任务清单）
4. 使用 `spawn_agents_on_csv` 分发任务，worker 仅做实现和最小验证。
5. 任一 worker 完成即进入验收队列，主线程立即验收，不等待全量返回。
6. 单任务验收采用快速失败：首个未达标点即停止该任务剩余测试并标记失败。
7. 将失败原因与修复提示追加回 CSV，重新分配给 worker。
8. 全部任务闭环后统一收口：冲突处理、全量验证、交付说明。

## 路径与命名规则
- `YYYY-MM-DD` 使用当前本地日期（例如 `2026-02-27`）。
- `<topic>` 使用需求主题的短标识，建议小写短横线风格（示例：`reader-cache-fix`）。
- 若 `docs/plans` 或 `docs/csv` 不存在，先创建目录再写文件。

## CSV 字段（中文列名）
- `任务ID`
- `来源任务ID`
- `批次ID`
- `目标路径`
- `任务说明`
- `最小验证`
- `执行状态`
- `最小验证结果`
- `验收状态`
- `重试次数`
- `最大重试次数`
- `错误码`
- `错误摘要`
- `修复提示`
- `更新时间`

## 状态机约束
### 执行状态列 `执行状态`（状态值）
- `todo`
- `dispatched`
- `working`
- `implemented`
- `worker_failed`
- `blocked`
- `requeued`
- `terminal_fail`

### 验收状态列 `验收状态`（状态值）
- `none`
- `accept_queued`
- `accepting`
- `accept_pass`
- `accept_fail`

## 角色边界
### worker 只做
- 实现任务目标
- 执行 `最小验证`
- 按统一 JSON 回传结果

### 主线程只做
- 维护状态机与 CSV
- 执行正式验收与快速失败
- 做失败归因与回流重分发

## 输出要求
- worker 回传必须匹配 `assets/worker-result-template.json` 的字段结构。
- 主线程为状态文件唯一写入者，禁止 worker 直接改状态 CSV。
- 当 `重试次数 >= 最大重试次数` 时置为 `terminal_fail` 并进入人工处理。
- 所有生成或回写的 CSV 文件统一使用 `UTF-8 BOM` 编码，避免 Windows 终端和表格工具乱码。
- 任务清单 CSV 的标准落盘路径为 `docs/csv/YYYY-MM-DD-<topic>.csv`。
- 对应设计文档的标准落盘路径为 `docs/plans/YYYY-MM-DD-<topic>-design.md`。
