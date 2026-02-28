from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "review-result-schema.json"
)

CSV_REQUIRED_COLUMNS = {
    "任务ID",
    "来源任务ID",
    "依赖任务ID",
    "批次ID",
    "目标路径",
    "任务说明",
    "最小验证",
    "执行状态",
    "最小验证结果",
    "验收状态",
    "重试次数",
    "最大重试次数",
    "错误码",
    "错误摘要",
    "修复提示",
    "更新时间",
}


def ensure_utf8_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


ensure_utf8_stdio()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str


def load_json_from_path(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_from_stdin() -> Any:
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("STDIN 为空：请通过 --result-path 指定文件，或通过管道传入 review JSON。")
    return json.loads(text)


def read_csv_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError("CSV 缺少表头")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    return fieldnames, rows


def write_csv_rows(
    csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]
) -> None:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        writer.writerows(rows)


def validate_review_result(obj: Any, schema: dict[str, Any]) -> list[ValidationError]:
    if not isinstance(obj, dict):
        return [ValidationError("$", "结果必须是 JSON object")]

    errors: list[ValidationError] = []
    required = [k for k in schema.get("required", []) if isinstance(k, str)]
    properties: dict[str, Any] = schema.get("properties", {}) or {}

    def check_string(value: Any, path: str, field_schema: dict[str, Any]) -> None:
        if not isinstance(value, str):
            errors.append(ValidationError(path, "必须是 string"))
            return
        min_length = field_schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(ValidationError(path, f"长度必须 >= {min_length}"))
        enum_values = field_schema.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            errors.append(ValidationError(path, f"必须为 {enum_values} 之一"))
        pattern = field_schema.get("pattern")
        if isinstance(pattern, str) and re.match(pattern, value) is None:
            errors.append(ValidationError(path, "格式不匹配 schema pattern"))

    def check_integer(value: Any, path: str, field_schema: dict[str, Any]) -> None:
        if not is_int(value):
            errors.append(ValidationError(path, "必须是整数"))
            return
        minimum = field_schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(ValidationError(path, f"范围必须 >= {minimum}"))
        maximum = field_schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(ValidationError(path, f"范围必须 <= {maximum}"))

    for key in required:
        if key not in obj:
            errors.append(ValidationError(f"$.{key}", "缺少必填字段"))

    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for extra_key in sorted(set(obj.keys()) - allowed):
            errors.append(ValidationError(f"$.{extra_key}", "不允许的额外字段"))

    check_string(
        obj.get("review_task_id"),
        "$.review_task_id",
        properties.get("review_task_id", {}) or {},
    )
    check_string(
        obj.get("review_decision"),
        "$.review_decision",
        properties.get("review_decision", {}) or {},
    )
    check_string(obj.get("summary"), "$.summary", properties.get("summary", {}) or {})

    scores = obj.get("scores")
    scores_schema: dict[str, Any] = properties.get("scores", {}) or {}
    if not isinstance(scores, dict):
        errors.append(ValidationError("$.scores", "必须是 object"))
    else:
        score_required = [
            k for k in scores_schema.get("required", []) if isinstance(k, str)
        ]
        score_props: dict[str, Any] = scores_schema.get("properties", {}) or {}
        for key in score_required:
            if key not in scores:
                errors.append(ValidationError(f"$.scores.{key}", "缺少必填字段"))
        if scores_schema.get("additionalProperties") is False:
            allowed_score_keys = set(score_props.keys())
            for extra_key in sorted(set(scores.keys()) - allowed_score_keys):
                errors.append(ValidationError(f"$.scores.{extra_key}", "不允许的额外字段"))
        for key, field_schema in score_props.items():
            if key not in scores:
                continue
            value = scores.get(key)
            field_type = field_schema.get("type")
            if field_type == "integer":
                check_integer(value, f"$.scores.{key}", field_schema)
            elif field_type == "string":
                check_string(value, f"$.scores.{key}", field_schema)

    issues = obj.get("issues")
    issues_schema: dict[str, Any] = properties.get("issues", {}) or {}
    if not isinstance(issues, list):
        errors.append(ValidationError("$.issues", "必须是 array"))
    else:
        issue_item_schema: dict[str, Any] = issues_schema.get("items", {}) or {}
        issue_required = [
            k for k in issue_item_schema.get("required", []) if isinstance(k, str)
        ]
        issue_props: dict[str, Any] = issue_item_schema.get("properties", {}) or {}
        for i, issue in enumerate(issues):
            path = f"$.issues[{i}]"
            if not isinstance(issue, dict):
                errors.append(ValidationError(path, "必须是 object"))
                continue
            for key in issue_required:
                if key not in issue:
                    errors.append(ValidationError(f"{path}.{key}", "缺少必填字段"))
            if issue_item_schema.get("additionalProperties") is False:
                allowed_issue_keys = set(issue_props.keys())
                for extra_key in sorted(set(issue.keys()) - allowed_issue_keys):
                    errors.append(ValidationError(f"{path}.{extra_key}", "不允许的额外字段"))
            for key, field_schema in issue_props.items():
                if key not in issue:
                    continue
                value = issue.get(key)
                field_type = field_schema.get("type")
                if field_type == "string":
                    check_string(value, f"{path}.{key}", field_schema)
                elif field_type == "integer":
                    check_integer(value, f"{path}.{key}", field_schema)

    new_tasks = obj.get("new_tasks")
    new_tasks_schema: dict[str, Any] = properties.get("new_tasks", {}) or {}
    if not isinstance(new_tasks, list):
        errors.append(ValidationError("$.new_tasks", "必须是 array"))
    else:
        task_item_schema: dict[str, Any] = new_tasks_schema.get("items", {}) or {}
        task_required = [
            k for k in task_item_schema.get("required", []) if isinstance(k, str)
        ]
        task_props: dict[str, Any] = task_item_schema.get("properties", {}) or {}
        for i, task in enumerate(new_tasks):
            path = f"$.new_tasks[{i}]"
            if not isinstance(task, dict):
                errors.append(ValidationError(path, "必须是 object"))
                continue
            for key in task_required:
                if key not in task:
                    errors.append(ValidationError(f"{path}.{key}", "缺少必填字段"))
            if task_item_schema.get("additionalProperties") is False:
                allowed_task_keys = set(task_props.keys())
                for extra_key in sorted(set(task.keys()) - allowed_task_keys):
                    errors.append(ValidationError(f"{path}.{extra_key}", "不允许的额外字段"))
            for key, field_schema in task_props.items():
                if key not in task:
                    continue
                value = task.get(key)
                field_type = field_schema.get("type")
                if field_type == "string":
                    check_string(value, f"{path}.{key}", field_schema)
                elif field_type == "integer":
                    check_integer(value, f"{path}.{key}", field_schema)

    check_string(
        obj.get("error_code"), "$.error_code", properties.get("error_code", {}) or {}
    )
    check_string(obj.get("notes"), "$.notes", properties.get("notes", {}) or {})

    return errors


def build_row_from_review_task(
    fieldnames: list[str],
    task: dict[str, Any],
    batch_id: str,
) -> dict[str, str]:
    row = {name: "" for name in fieldnames}
    row["任务ID"] = str(task["task_id"])
    row["来源任务ID"] = str(task["source_task_id"])
    row["依赖任务ID"] = str(task["depends_on_task_id"])
    row["批次ID"] = batch_id
    row["目标路径"] = str(task["target_path"])
    row["任务说明"] = str(task["task_desc"])
    row["最小验证"] = str(task["min_verify"])
    row["执行状态"] = "todo"
    row["最小验证结果"] = "unknown"
    row["验收状态"] = "none"
    row["重试次数"] = "0"
    row["最大重试次数"] = str(task["max_retry"])
    row["错误码"] = ""
    row["错误摘要"] = ""
    row["修复提示"] = ""
    row["更新时间"] = now_iso()
    return row


def collect_results(args: argparse.Namespace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if args.result_path:
        for p in args.result_path:
            obj = load_json_from_path(Path(p))
            if not isinstance(obj, dict):
                raise ValueError(f"结果文件不是 JSON object: {p}")
            results.append(obj)
        return results

    obj = load_json_from_stdin()
    if not isinstance(obj, dict):
        raise ValueError("STDIN 结果不是 JSON object")
    results.append(obj)
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="校验审查 worker 回传 JSON，并在主线程显式授权时追加任务 CSV（UTF-8 BOM）"
    )
    parser.add_argument("--csv-path", required=True, help="任务清单 CSV 路径")
    parser.add_argument(
        "--result-path",
        action="append",
        help="审查 worker 回传 JSON 文件路径（可重复）。若不提供则从 STDIN 读取。",
    )
    parser.add_argument(
        "--schema-path",
        default=str(DEFAULT_SCHEMA_PATH),
        help="审查 worker 回传 JSON Schema 路径",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式授权写入：将 review 的 new_tasks 追加到 CSV；默认仅校验不写入",
    )
    parser.add_argument(
        "--batch-id",
        default="",
        help="追加任务写入的批次ID（仅 --apply 且存在 new_tasks 时必填）",
    )
    args = parser.parse_args(argv)

    schema = load_json_from_path(Path(args.schema_path))
    csv_path = Path(args.csv_path)
    results = collect_results(args)

    fieldnames, rows = read_csv_rows(csv_path)
    missing_cols = sorted(c for c in CSV_REQUIRED_COLUMNS if c not in fieldnames)
    if missing_cols:
        raise RuntimeError(f"CSV 缺少必要列：{', '.join(missing_cols)}")

    blocked_review_ids: list[str] = []
    append_candidates: list[dict[str, Any]] = []
    for result in results:
        errors = validate_review_result(result, schema)
        if errors:
            for e in errors:
                print(f"[schema] {e.path}: {e.message}", file=sys.stderr)
            return 2
        if result.get("review_decision") == "BLOCKED":
            blocked_review_ids.append(str(result.get("review_task_id") or ""))
        if result.get("review_decision") == "NEEDS_IMPROVEMENT":
            for task in result.get("new_tasks", []):
                append_candidates.append(task)

    if blocked_review_ids:
        print(
            json.dumps(
                {
                    "mode": "validate-only",
                    "blocked_review_ids": blocked_review_ids,
                    "message": "存在 BLOCKED 审查结果，主线程需人工处理",
                },
                ensure_ascii=False,
            )
        )
        return 4

    existing_ids = {str((row.get("任务ID") or "")).strip() for row in rows if row.get("任务ID")}
    pending_new_ids: set[str] = set()
    for task in append_candidates:
        task_id = str(task["task_id"]).strip()
        source_task_id = str(task["source_task_id"]).strip()
        if task_id in existing_ids:
            print(f"[csv] 任务ID已存在，无法追加：{task_id}", file=sys.stderr)
            return 3
        if task_id in pending_new_ids:
            print(f"[csv] review new_tasks 内部任务ID重复：{task_id}", file=sys.stderr)
            return 3
        if source_task_id not in existing_ids:
            print(
                f"[csv] 来源任务ID不存在，无法追加：{source_task_id} (new task: {task_id})",
                file=sys.stderr,
            )
            return 3
        pending_new_ids.add(task_id)

    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "validate-only",
                    "review_count": len(results),
                    "append_candidate_count": len(append_candidates),
                    "message": "未写入 CSV；如需追加请由主线程显式传入 --apply",
                },
                ensure_ascii=False,
            )
        )
        return 0

    if append_candidates and not args.batch_id.strip():
        raise RuntimeError("--apply 模式下，当存在可追加任务时必须提供 --batch-id")

    appended_ids: list[str] = []
    for task in append_candidates:
        row = build_row_from_review_task(fieldnames, task, args.batch_id.strip())
        rows.append(row)
        appended_ids.append(str(task["task_id"]))

    if append_candidates:
        write_csv_rows(csv_path, fieldnames, rows)

    print(
        json.dumps(
            {
                "mode": "apply",
                "review_count": len(results),
                "append_candidate_count": len(append_candidates),
                "appended_count": len(appended_ids),
                "appended_task_ids": appended_ids,
                "csv_path": str(csv_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
