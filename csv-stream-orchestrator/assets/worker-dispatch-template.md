# Worker 任务单

## 基本信息
- `任务ID`: {{任务ID}}
- `来源任务ID`: {{来源任务ID}}
- `批次ID`: {{批次ID}}
- `目标路径`: {{目标路径}}

## 任务目标
{{任务说明}}

## 执行约束
1. 只改与本任务直接相关的文件与区域。
2. 不做最终验收判定，只做最小验证 `最小验证`。
3. 最小验证失败时仍需回传完整结果与错误信息。
4. 不允许引入、升级或删除依赖；不允许修改依赖清单与锁文件。
5. 若任务依赖新包才能完成，直接回传阻塞，不要自行安装依赖。

## 最小验证
`{{最小验证}}`

## 输出格式（必须 JSON）
请严格按下列字段回传：
- `task_id`
- `exec_state`（`implemented` / `worker_failed` / `blocked`）
- `min_verify_state`（`pass` / `fail` / `unknown`）
- `artifact`
- `files_changed`
- `commands_run`
- `error_code`
- `error_summary`
- `notes`

依赖阻塞时请使用：
- `exec_state`: `blocked`
- `error_code`: `DEPENDENCY_REQUIRED`
- `error_summary`: 写明所需依赖名称与用途

## 完成定义
- 已实现目标或明确失败原因
- 已执行最小验证（若可执行）
- 未引入任何新依赖，未修改依赖清单与锁文件
- 已按 JSON 模板返回结果
