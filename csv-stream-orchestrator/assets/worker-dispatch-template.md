# Worker 任务单

## 基本信息
- `任务ID`: {{任务ID}}
- `来源任务ID`: {{来源任务ID}}
- `依赖任务ID`: {{依赖任务ID}}
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
6. `exec_state` 允许值：`implemented`、`blocked`、`worker_failed`。
7. `min_verify_state` 允许值：`unknown`、`pass`、`fail`、`skip`。
8. `error_code` 需使用 `WORKER_` 前缀（示例：`WORKER_DEPENDENCY_REQUIRED`、`WORKER_MIN_VERIFY_FAIL`）。
9. 回传 JSON 将被主线程按 Schema 强校验；不符合会被标记为 `WORKER_OUTPUT_SCHEMA_INVALID`，主线程可能会补跑最小验证并回流重试。
10. 若因共享工作区并发冲突（或依赖未就绪）无法执行最小验证，允许设置 `min_verify_state=skip`，并在 `notes` 写明原因（建议固定文案：`共享工作区` / `依赖未就绪`）。
11. `files_changed` 必须使用 **仓库根相对路径** 且分隔符一律使用 **正斜杠 `/`**。
    - 禁止绝对路径（如 `C:/repo/src/a.ts`、`\\\\server\\\\share\\\\a.ts`）。
    - 禁止前缀 `a/`、`b/`、`./`；禁止使用反斜杠 `\\`。

## 最小验证
`{{最小验证}}`

## 输出格式（必须裸 JSON）
请在最终回复中仅输出一段裸 JSON（不要使用 Markdown 代码块，不要附加任何说明文字）；字段名必须与模板一致。

请直接复制并填写以下 JSON 后回传（最终回复不要包含任何代码块标记）：

{
  "task_id": "{{任务ID}}",
  "exec_state": "implemented",
  "min_verify_state": "pass",
  "artifact": {
    "summary": "简述本次实现结果",
    "deliverables": [
      "关键改动点 1",
      "关键改动点 2"
    ]
  },
  "files_changed": [
    "path/to/file"
  ],
  "commands_run": [
    "{{最小验证}}"
  ],
  "error_code": "",
  "error_summary": "",
  "notes": ""
}

依赖阻塞时请使用：
- `exec_state`: `blocked`
- `error_code`: `WORKER_DEPENDENCY_REQUIRED`
- `error_summary`: 写明所需依赖名称与用途

## 完成定义
- 已实现目标或明确失败原因
- 已执行最小验证（若可执行；若 `skip` 必须在 `notes` 写明原因）
- 未引入任何新依赖，未修改依赖清单与锁文件
- 已按 JSON 模板返回结果
