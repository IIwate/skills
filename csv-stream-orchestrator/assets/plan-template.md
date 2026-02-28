# 执行计划（流式验收版）

## 1. 需求与目标
- 需求摘要：{{requirement_summary}}
- 成功标准：{{success_criteria}}

## 2. 并行探索范围（只读）
- 模块/目录：{{explore_scope}}
- 关键入口：{{entry_points}}
- 风险点：{{known_risks}}

## 3. 任务拆分策略
- 拆分原则：任务强独立、避免同文件同区域冲突
- 预计任务数：{{task_count}}
- 每项最小验证：{{min_verify_policy}}

### 拆分自检清单
- [ ] 一个任务只对应一个可验收结果
- [ ] 每个任务的 `目标路径` 与其他并发任务无冲突
- [ ] 每个任务都有可独立执行的 `最小验证`
- [ ] 每个任务失败后可回流（可填写错误码、错误摘要、修复提示）
- [ ] 单任务目标工时在 30-90 分钟

## 4. 验收策略
- 验收执行：主线程流式验收（worker 完成即验收）
- 快速失败：单任务首个未达标点即停止该任务剩余测试
- 失败回流：回写任务 CSV 的 `错误码/错误摘要/修复提示`，按需回流重分发

## 5. 批次参数
- `批次ID`：{{batch_id}}
- `最大并发`：{{max_concurrency}}
- `最大重试次数`：{{max_attempt}}
- `依赖策略`：主线程先完成依赖引入，子线程禁止引入依赖
- `调度方式`：`spawn_agent` + `wait`（主线程强校验 worker 回传 JSON）
- `通知与回收规则`：异步通知只记录；完成打印与 `close_agent` 仅由 `wait` 返回结果驱动
- `MCP 回收策略`：每轮分发前 `snapshot`；本轮全部任务（含回流任务）完成 `wait + close_agent` 后执行一次 `cleanup`（会话绑定，先 dry-run；默认成功后自动删基线，失败或 `--keep-baseline` 时保留）
- `MCP 快照路径`：{{mcp_snapshot_path}}
- `CSV 目录`：{{csv_dir}}
- `目录规约`：以 `{{csv_dir}}` 为主体；`mcp-baseline.json` 与 CSV 同目录；非 baseline 辅助产物放 `{{csv_artifacts_dir}}`

## 6. 输出物
- 设计文档：{{design_path}}
- 任务 CSV：{{csv_path}}
- MCP 快照：{{mcp_snapshot_path}}
- 辅助产物目录：{{csv_artifacts_dir}}
