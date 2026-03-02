---
name: csv-stream-orchestrator
description: 将需求转为并行探索、计划评审、CSV 子任务分发、流式验收、审查回流重拆分与重分发的执行流程。仅当用户消息以 `$csv-stream-orchestrator` 开头时使用；任何不含此前缀的请求都不要触发本技能。用于手动触发后先等待用户输入需求，再按模板生成计划、CSV 和 worker 指令。
---

# CSV Stream Orchestrator

## 手动触发规则（硬约束）
- 仅当用户消息以 `$csv-stream-orchestrator` 开头时启用本技能。
- 用户只发送 `$csv-stream-orchestrator` 时，先进入等待模式并回复“请提供需求”，不执行探索、不改代码。
- 用户发送 `$csv-stream-orchestrator <需求>` 时，将 `<需求>` 作为本轮唯一需求输入并开始流程。

## 资源与模板
- 执行计划模板：`assets/plan-template.md`
- 任务 CSV 模板：`assets/tasks-template.csv`
- worker 下发模板：`assets/worker-dispatch-template.md`
- worker 回传模板：`assets/worker-result-template.json`
- worker 回传 JSON Schema：`assets/worker-result-schema.json`
- 审查 worker 下发模板：`assets/review-dispatch-template.md`
- 审查 worker 回传模板：`assets/review-result-template.json`
- 审查 worker 回传 JSON Schema：`assets/review-result-schema.json`
- worker 回传校验与 CSV 回写脚本：`scripts/worker_result_to_csv.py`
- 审查回传校验与 CSV 追加脚本（可选）：`scripts/review_result_to_csv.py`（是否调用由主线程决定；`--batch-id` 可省略，缺省写入占位 `batch-unassigned`，后续由主线程统一分配）
- 修改任一 Schema（`assets/*-schema.json`）时，必须同步更新并验证对应 Python 脚本，禁止只改一侧。
- MCP 清理脚本（会话绑定版）：`../worker-mcp-cleanup/scripts/worker_mcp_cleanup.py`

## 任务拆分规范（门控）
在生成任务 CSV 前，先完成“验收清单 -> 任务拆分 -> 冲突分组 -> 拆分自检”。

### 拆分硬标准
- 一个任务只对应一个可验收结果。
- 一个任务只改一组明确文件区域，避免与其他任务冲突。
- `目标路径` 尽量细到文件（或最小冲突单元），避免用过粗目录导致无谓串行。
- `最小验证` 必须可独立执行（单命令或固定检查项）。
- 任务失败后必须能回流（可填写 `错误码`、`错误摘要`、`修复提示`）。
- 任务描述必须上下文自足，worker 单独拿到任务即可执行。
- 单任务目标工时建议在 30-90 分钟。

### 拆分步骤
1. 基于确认后的执行计划，先写主线程验收清单。
2. 每个验收条目拆成 1-2 个候选任务。
3. 按 `目标路径`（尽量细到文件/最小冲突单元）做冲突分组：同组串行、异组并行。
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
- worker 若发现当前任务需要新依赖，必须回传 `exec_state=blocked`、`error_code=WORKER_DEPENDENCY_REQUIRED`，并在 `error_summary` 写清所需依赖与原因。

## 批次并发与内存释放（硬约束）
- 单次会话内采用固定批大小：每批最多 `6` 个 worker（最后一批可少于 6）。
- 当 CSV 任务数 `> 6` 时，必须按批次推进：当前批次未完全结束前，禁止启动下一批。
- 当前批次“完全结束”的判定必须同时满足：该批全部 worker 已 `wait` 完成、已全部 `close_agent`、且本批 `cleanup` 后 `remaining_owner_delta_count=0`。
- 若 `cleanup` 后仍存在 owner 增量进程（`remaining_owner_delta_count>0`），禁止继续起下一批 worker，必须先处理清理异常。
- 回流任务进入后续批次队列，禁止在当前批次中途补位新增 worker。
- 审查阶段固定并发为 `1`：审查 worker 必须在“全部执行 worker 批次完成且已 cleanup 达标”后再启动。
- 审查对象固定为“上一轮全部执行 worker 的结果集合”（不是单个 worker 的抽样结果）。
- 审查 worker 结束后必须单独执行一次 `cleanup`；仅当该次 `remaining_owner_delta_count=0` 时，才允许依据审查结果发起下一轮任务分发。

## 审查回流阶段（硬约束）
- 审查 worker 仅用于“变更审查 + 任务重拆分建议”，禁止修改代码、禁止改 CSV、禁止做最终验收判定。
- 审查 worker 必须使用只读上下文（上一轮全部执行 worker 的任务行状态、任务回传 JSON、验收日志、变更文件清单），主线程只接收结构化 JSON。
- 审查范围仅限本轮变更与对应任务产物；不扩展到历史无关代码，但必须覆盖上一轮全部执行 worker 的结果集合。
- 禁止仅基于单个 worker 结果给出整轮审查结论。
- 主线程不做实际代码质量判断，只按验收门槛执行（通常为编译/构建通过）；审查建议仅用于决定是否追加重拆分任务。
- 审查结果若 `review_decision=NEEDS_IMPROVEMENT` 且 `new_tasks` 非空，主线程必须将 `new_tasks` 追加写入原 CSV，并按常规批次流程继续分发。
- 主线程可选择调用 `scripts/review_result_to_csv.py` 完成审查结果校验与任务追加；是否调用由主线程按当前批次情况决定，不是强制步骤（`--batch-id` 可省略，缺省写入占位 `batch-unassigned`，后续由主线程统一分配）。
- 追加任务必须填写 `来源任务ID`（映射到被审查任务），且初始化状态固定为：`执行状态=todo`、`最小验证结果=unknown`、`验收状态=none`、`重试次数=0`。
- 审查结果若 `review_decision=PASS` 或 `new_tasks` 为空，视为本轮不追加任务，直接进入收口。
- 审查结果若 `review_decision=BLOCKED`，主线程写入 `REVIEW_BLOCKED` 并停止自动重分发，转人工处理。

## Worker 观察与超时（硬约束）
- 主线程必须在计划中为每个任务（或任务分组）设置两个时间参数：`最短观察时长（分钟）` 与 `最大执行时长（分钟）`。
- 主线程对单个 worker 的首次状态检查，不得早于该任务的 `最短观察时长`。
- 未达到 `最大执行时长` 前，禁止提前结束 worker（禁止因等待焦虑执行 `interrupt`、`close_agent` 或其他提前终止动作）。
- 当 worker 达到 `最大执行时长` 仍未终态时，主线程才可判定超时并回写 `错误码=WORKER_TIMEOUT`，随后执行回流重分发。
- `最大执行时长` 允许按任务难度分级设置（例如轻量任务更短、复杂任务更长），但必须在分发前写入计划并固定。

## 执行流程
1. 使用 `multi_tool_use.parallel` 做只读并行探索，收集实现上下文。
2. 按 `assets/plan-template.md` 输出计划，等待用户确认。
3. 用户确认后，主线程先完成依赖引入与基础验证，再按“任务拆分规范（门控）”生成任务清单，并在项目根目录（目标代码仓库根目录；优先用 `git rev-parse --show-toplevel` 定位）创建 `docs/csv/YYYY-MM-DD-<topic>/`（以 CSV 为主体的批次目录），然后落盘以下文件：
   - 分发前确保项目 `.gitignore` 已忽略 `docs/`（文档仅本地使用，不提交）。
   - `docs/plans/YYYY-MM-DD-<topic>-design.md`（写入用户确认后的执行计划）
   - `docs/csv/YYYY-MM-DD-<topic>/YYYY-MM-DD-<topic>.csv`（按 `assets/tasks-template.csv` 生成任务清单；回写命令/脚本必须显式使用 UTF-8 BOM）
   - `docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json`（由主线程在每批分发前写入该批 MCP 基线快照）
   - `docs/csv/YYYY-MM-DD-<topic>/artifacts/`（可选；本批次非 baseline 辅助产物目录，避免文件散落在 `docs/csv` 根目录）
4. 将可执行任务按调度顺序切分为批次队列：每批最多 `6` 个任务（最后一批可少于 6）；回流任务写回 CSV 后进入后续批次队列，禁止当前批次中途补位。
5. 每批启动前，主线程先调用 `worker-mcp-cleanup` 的 `snapshot`（或执行 `python ../worker-mcp-cleanup/scripts/worker_mcp_cleanup.py --mode snapshot --snapshot-path docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json`）记录该批基线。
6. 主线程仅为当前批次创建 worker（最多 6 个），并按 `assets/worker-dispatch-template.md` 渲染任务单后立即下发（首次分发可直接作为 `spawn_agent` 的 `message`；回流重试再用 `send_input`）。
7. 主线程维护当前批次的 `pending_worker_ids` 收敛循环并做流式验收：
   - `while pending_worker_ids 非空`：按任务配置的 `最短观察时长` 调用 `wait(ids=pending_worker_ids, timeout_ms=...)` 轮询已终态 worker（分钟换算为毫秒）。
   - 若 `wait` 超时且未到相关任务 `最大执行时长`，继续等待，禁止提前结束该 worker。
   - 异步通知（例如 `subagent_notification`）只做记录，不做状态迁移、不执行 `close_agent`、不改动 `pending_worker_ids`。
   - 仅按本次 `wait` 返回中的已终态 worker 执行打印与回传处理：先做 JSON Schema 强校验（推荐用 `scripts/worker_result_to_csv.py` 自动校验并回写 CSV）。
     - 不通过：优先要求同一 worker “仅重发 JSON”（不重做实现），最多 2 次。
     - 仍不通过：写入 `错误码=WORKER_OUTPUT_SCHEMA_INVALID`、`错误摘要` 以 `[worker] ` 开头；并将 `执行状态=worker_failed`、`最小验证结果=unknown` 回写到任务 CSV，后续由主线程补跑 `最小验证` 决定回流与否。
   - 对 Schema 校验通过的任务立即进入流式验收（逐任务、快速失败）：先对 `最小验证结果 != pass` 的任务补跑 `最小验证`，仅当 `pass` 时才进入正式验收。
   - 若任务达到 `最大执行时长` 仍未终态：主线程回写 `执行状态=worker_failed`、`最小验证结果=unknown`、`验收状态=accept_fail`、`错误码=WORKER_TIMEOUT`、`错误摘要=[worker] 超时未完成`，并执行回流重分发。
   - 对本次 `wait` 返回的已终态 worker 执行 `close_agent` 并从 `pending_worker_ids` 移除；当前批次禁止新建补位 worker。
8. 当前批次全部 worker 完成并 `close_agent` 后，主线程立即执行一次 `worker-mcp-cleanup` 的 `cleanup`（建议先 `--dry-run` 再正式清理）回收本批新增 MCP 进程。
   - `cleanup` 后必须检查 `remaining_owner_delta_count`；仅当其为 `0` 才允许启动下一批。
   - 基线文件默认临时：`cleanup` 成功后脚本自动删除 `mcp-baseline.json`。
   - 仅在 `cleanup` 失败或显式传入 `--keep-baseline` 时保留基线文件。
9. 按“批次启动 -> 收敛验收 -> close -> cleanup 验证”的顺序循环，直到当前队列中的所有执行 worker 批次（含回流批次）处理完毕。
10. 执行审查 worker 阶段（单 worker）：
   - 在启动审查 worker 前，主线程先执行 `snapshot` 写入同目录 `mcp-baseline.json`。
   - 主线程先汇总“上一轮全部执行 worker”的输入包（CSV 任务状态、回传 JSON、验收日志、变更文件清单）。
   - 使用 `assets/review-dispatch-template.md` 组装审查任务单，明确审查对象为该全量输入包，并要求审查 worker 按 `assets/review-result-schema.json` 回传裸 JSON。
   - 主线程等待审查 worker 终态后执行 `close_agent`，随后立即执行一次 `cleanup`，并检查 `remaining_owner_delta_count=0`。
11. 主线程消费审查结果：严格按“审查回流阶段（硬约束）”执行（含 `NEEDS_IMPROVEMENT/PASS/BLOCKED` 分支与 `--apply` 约束），此处不重复展开。
12. 全部任务闭环后统一收口：冲突处理、全量验证、交付说明。

## 路径与命名规则
- `YYYY-MM-DD` 使用当前本地日期（例如 `2026-02-27`）。
- `<topic>` 使用需求主题的短标识，建议小写短横线风格（示例：`reader-cache-fix`）。
- `docs/` 仅本地使用：必须在项目 `.gitignore` 忽略（至少忽略 `docs/`），避免误提交。
- 若 `docs/plans` 或 `docs/csv` 不存在，先创建目录再写文件。
- 每个任务 CSV 必须使用独立目录：`docs/csv/YYYY-MM-DD-<topic>/`。
- 目录主体固定为 CSV：禁止仅为 `mcp-baseline.json` 单独创建目录或使用 baseline 作为目录名。
- 任务清单 CSV 标准路径：`docs/csv/YYYY-MM-DD-<topic>/YYYY-MM-DD-<topic>.csv`。
- MCP 快照文件标准路径：`docs/csv/YYYY-MM-DD-<topic>/mcp-baseline.json`。
- 非 baseline 辅助产物（日志、中间 JSON、验收记录）统一放 `docs/csv/YYYY-MM-DD-<topic>/artifacts/`。

## CSV 字段（中文列名）
- `任务ID`
- `来源任务ID`
- `依赖任务ID`
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

## CSV 编码落盘（硬约束）
- 所有“新建/覆盖/回写”任务 CSV 的命令或脚本都必须显式指定 `UTF-8 BOM`，禁止依赖默认编码。
- PowerShell 示例（覆盖写入）：
  - `$utf8Bom = New-Object System.Text.UTF8Encoding($true)`
  - `[System.IO.File]::WriteAllText($csvPath, $csvContent, $utf8Bom)`
- Python 示例（覆盖写入）：
  - `with open(csv_path, "w", encoding="utf-8-sig", newline="") as f: ...`
- 回写后可抽样校验 BOM 头：`[System.IO.File]::ReadAllBytes($csvPath)[0..2]` 应为 `EF BB BF`。

## 状态机约束
### 执行状态列 `执行状态`（状态值）
- `todo`
- `dispatched`
- `implemented`
- `worker_failed`
- `blocked`
- `requeued`
- `terminal_fail`

### 最小验证结果列 `最小验证结果`（状态值）
- `unknown`（未执行）
- `pass`
- `fail`
- `skip`（无法执行或不适用，需在 `错误摘要` 写明原因）

### 验收状态列 `验收状态`（状态值）
- `none`
- `accept_queued`
- `accepting`
- `accept_pass`
- `accept_fail`

### 状态迁移表（触发事件 -> 新状态 -> 回写字段）
| 触发事件 | 新状态 | 回写字段 |
| --- | --- | --- |
| 主线程创建任务行 | `执行状态=todo`，`最小验证结果=unknown`，`验收状态=none` | `执行状态`、`最小验证结果`、`验收状态`、`重试次数=0`、`更新时间` |
| 主线程分发任务（`send_input`） | `执行状态=dispatched` | `执行状态`、`更新时间` |
| worker 回传 `exec_state=implemented` | `执行状态=implemented` | `执行状态`、`最小验证结果=min_verify_state`、`错误码`、`错误摘要`、`更新时间` |
| worker 回传 `exec_state=blocked` | `执行状态=blocked` | `执行状态`、`最小验证结果=min_verify_state`、`错误码`、`错误摘要`、`更新时间` |
| worker 回传 `exec_state=worker_failed` | `执行状态=worker_failed` | `执行状态`、`最小验证结果=min_verify_state`、`错误码`、`错误摘要`、`更新时间` |
| worker 回传 JSON 连续 2 次 Schema 不通过 | `执行状态=worker_failed`，`最小验证结果=unknown` | `执行状态`、`最小验证结果`、`错误码=WORKER_OUTPUT_SCHEMA_INVALID`、`错误摘要=[worker] ...`、`更新时间` |
| 进入正式验收队列 | `验收状态=accept_queued` | `验收状态`、`更新时间` |
| 开始执行正式验收 | `验收状态=accepting` | `验收状态`、`更新时间` |
| 正式验收通过 | `验收状态=accept_pass` | `验收状态`、`错误码`、`错误摘要`、`修复提示`、`更新时间` |
| `min_verify_state=fail` 或补跑最小验证失败 | `验收状态=accept_fail` | `验收状态`、`错误码`、`错误摘要`、`修复提示`、`更新时间` |
| worker 达到 `最大执行时长` 仍未终态 | `执行状态=worker_failed`，`最小验证结果=unknown`，`验收状态=accept_fail` | `执行状态`、`最小验证结果`、`验收状态`、`错误码=WORKER_TIMEOUT`、`错误摘要=[worker] 超时未完成`、`更新时间` |
| `accept_fail` 且 `重试次数 + 1 < 最大重试次数` | `执行状态=requeued` | `执行状态`、`重试次数=重试次数+1`、`更新时间` |
| `accept_fail` 且 `重试次数 + 1 >= 最大重试次数` | `执行状态=terminal_fail` | `执行状态`、`重试次数=重试次数+1`、`错误码`、`错误摘要`、`修复提示`、`更新时间` |

## 回写与判定规则（硬约束）
- 主线程是任务 CSV 的唯一写入者：`执行状态`、`最小验证结果`、`验收状态`、`重试次数`、`更新时间` 均由主线程回写。
- worker 只回传 JSON（见 `assets/worker-result-template.json`），禁止直接修改任务 CSV。
- 审查 worker 只回传 JSON（见 `assets/review-result-template.json`），禁止修改代码与 CSV。
- 字段映射：worker 回传 `exec_state` -> CSV `执行状态`；worker 回传 `min_verify_state` -> CSV `最小验证结果`。
- 直接失败门控：当 `min_verify_state=fail` 时，主线程直接置 `验收状态=accept_fail` 并回流重分发（不执行后续验收命令）。
- 补跑最小验证：
  - 当 `min_verify_state=unknown` 时，主线程必须补跑任务行里的 `最小验证`。
  - 当 `min_verify_state=skip` 且 worker 在 `notes` 写明“共享工作区/依赖未就绪”时，允许延后到批次收口阶段做一次“批次级最小验证”（通常为本批次统一的 build 命令），并批量回写相关任务 `最小验证结果=pass`、`验收状态=accept_pass`。
  - 其他 `skip` 情况：主线程补跑任务行里的 `最小验证`。
- 错误来源标记（worker）：写入 CSV 时，`错误码` 使用 `WORKER_` 前缀；`错误摘要` 以 `[worker] ` 开头。
- 错误来源标记（accept）：写入 CSV 时，`错误码` 使用 `ACCEPT_` 前缀；`错误摘要` 以 `[accept] ` 开头。
- 回传 JSON 强校验：主线程必须按 `assets/worker-result-schema.json` 校验 worker 回传；不通过时先要求 worker 仅重发 JSON，仍不通过则写入 `错误码=WORKER_OUTPUT_SCHEMA_INVALID` 并视为 `min_verify_state=unknown`，由主线程补跑 `最小验证` 决定后续回流与否。
- 审查 JSON 强校验：主线程必须按 `assets/review-result-schema.json` 校验审查 worker 回传；不通过时写入 `错误码=REVIEW_OUTPUT_SCHEMA_INVALID` 并转人工处理。
- 审查回流与追加写入规则统一见“审查回流阶段（硬约束）”；本节不重复定义。
- 批次并发上限、回流排队与“禁止中途补位”统一见“批次并发与内存释放（硬约束）”；本节不重复定义。
- worker 观察超时与 `pending_worker_ids` 状态迁移统一见“Worker 观察与超时（硬约束）”及“执行流程”第 7 步；本节不重复定义。
- `cleanup` 必须按批次执行：每批全部 worker 完成并 `close_agent` 后立即执行一次；`remaining_owner_delta_count=0` 前禁止启动下一批。
- 审查 worker 必须单独执行 `snapshot + wait + close_agent + cleanup`；清理未达标时禁止进入下一轮分发。
- 基线文件默认临时：`cleanup` 成功后自动删除；仅在 `cleanup` 失败或显式 `--keep-baseline` 时保留。
- CSV 回写必须使用显式 BOM 写法（例如 PowerShell `System.Text.UTF8Encoding($true)` 或 Python `encoding="utf-8-sig"`）。

## 角色边界
### worker 只做
- 实现任务目标
- 执行 `最小验证`
- 按统一 JSON 回传结果

### 审查 worker 只做
- 只读审查上一轮全部执行 worker 的变更结果集合（不改代码）
- 输出结构化评分与问题清单
- 输出可回流的 `new_tasks` 建议（若需要）

### 主线程只做
- 维护状态机与 CSV
- 执行正式验收与快速失败（默认以编译/构建通过作为验收门槛）
- 基于审查 JSON 进行任务追加与回流重分发

## 输出要求
- worker 回传必须匹配 `assets/worker-result-schema.json`（Schema）约束；示例见 `assets/worker-result-template.json`。
- 审查 worker 回传必须匹配 `assets/review-result-schema.json`（Schema）约束；示例见 `assets/review-result-template.json`。
- worker 最终回复必须为一段裸 JSON（不要用 Markdown 代码块，不要附加说明文字）。
- 审查 worker 最终回复必须为一段裸 JSON（不要用 Markdown 代码块，不要附加说明文字）。
- worker 回传 `exec_state` 允许值：`implemented`、`blocked`、`worker_failed`。
- worker 回传 `min_verify_state` 允许值：`unknown`、`pass`、`fail`、`skip`。
- 主线程分发任务时，必须将回传 JSON 模板内联到 `assets/worker-dispatch-template.md` 任务单内容中，不允许只给模板文件路径。
- 主线程分发审查任务时，必须将审查 JSON 模板内联到 `assets/review-dispatch-template.md` 任务单内容中，不允许只给模板文件路径。
- 主线程分发审查任务时，必须提供上一轮全部执行 worker 的输入包；禁止仅提供单个 worker 样本作为整轮结论依据。
- 主线程为状态文件唯一写入者，禁止 worker 直接改状态 CSV。
- 异步通知只记录，不做关闭动作；仅依据 `wait` 返回结果打印完成信息并执行 `close_agent`。
- 主线程在每个批次启动前必须执行 MCP `snapshot`；每批全部任务完成并 `wait + close_agent` 后必须执行一次 MCP `cleanup`，且 `remaining_owner_delta_count=0` 才能启动下一批（推荐先 `--dry-run` 再正式清理，默认成功后自动删基线；需保留时显式 `--keep-baseline`）。
- 审查 worker 也必须遵守 `snapshot + wait + close_agent + cleanup`，且清理达标后才能进入下一轮任务分发或最终收口。
- 主线程必须在计划中明确 `最短观察时长（分钟）` 与 `最大执行时长（分钟）`，并按该配置执行 `wait` 轮询与超时判定。
- 当 `重试次数 >= 最大重试次数` 时置为 `terminal_fail` 并进入人工处理。
- 所有生成或回写的 CSV 文件统一使用 `UTF-8 BOM` 编码，避免 Windows 终端和表格工具乱码。
- 任务清单 CSV 的标准落盘路径为 `docs/csv/YYYY-MM-DD-<topic>/YYYY-MM-DD-<topic>.csv`。
- `mcp-baseline.json` 必须与对应 CSV 同目录，禁止 baseline-only 目录。
- 对应设计文档的标准落盘路径为 `docs/plans/YYYY-MM-DD-<topic>-design.md`。
