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


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


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
    required = schema.get("required", [])
    properties: dict[str, Any] = schema.get("properties", {}) or {}

    for key in required:
        if key not in obj:
            errors.append(ValidationError(f"$.{key}", "缺少必填字段"))

    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for extra_key in sorted(set(obj.keys()) - allowed):
            errors.append(ValidationError(f"$.{extra_key}", "不允许的额外字段"))

    review_task_id = obj.get("review_task_id")
    if not isinstance(review_task_id, str) or not review_task_id.strip():
        errors.append(ValidationError("$.review_task_id", "必须是非空字符串"))

    review_decision = obj.get("review_decision")
    allowed_decisions = (
        (properties.get("review_decision") or {}).get("enum")
        or ["PASS", "NEEDS_IMPROVEMENT", "BLOCKED"]
    )
    if review_decision not in allowed_decisions:
        errors.append(ValidationError("$.review_decision", f"必须为 {allowed_decisions} 之一"))

    if not isinstance(obj.get("summary"), str):
        errors.append(ValidationError("$.summary", "必须是 string"))

    scores = obj.get("scores")
    score_keys = [
        "root_cause_resolution",
        "code_quality",
        "side_effects",
        "edge_cases",
        "test_coverage",
        "total",
    ]
    if not isinstance(scores, dict):
        errors.append(ValidationError("$.scores", "必须是 object"))
    else:
        for k in score_keys:
            v = scores.get(k)
            if not is_int(v):
                errors.append(ValidationError(f"$.scores.{k}", "必须是整数"))
        for k in score_keys[:-1]:
            v = scores.get(k)
            if is_int(v) and (v < 0 or v > 20):
                errors.append(ValidationError(f"$.scores.{k}", "范围必须在 0-20"))
        total = scores.get("total")
        if is_int(total) and (total < 0 or total > 100):
            errors.append(ValidationError("$.scores.total", "范围必须在 0-100"))
        if all(is_int(scores.get(k)) for k in score_keys):
            subtotal = sum(int(scores[k]) for k in score_keys[:-1])
            if int(scores["total"]) != subtotal:
                errors.append(
                    ValidationError(
                        "$.scores.total",
                        f"应等于前五项之和（期望 {subtotal}，实际 {scores['total']}）",
                    )
                )

    issues = obj.get("issues")
    if not isinstance(issues, list):
        errors.append(ValidationError("$.issues", "必须是 array"))
    else:
        allowed_issue_severity = {"critical", "major", "minor", "info"}
        allowed_issue_category = {
            "security",
            "code_quality",
            "performance",
            "reliability",
            "compatibility",
            "test_coverage",
            "other",
        }
        for i, issue in enumerate(issues):
            path = f"$.issues[{i}]"
            if not isinstance(issue, dict):
                errors.append(ValidationError(path, "必须是 object"))
                continue
            required_issue_fields = [
                "source_task_id",
                "severity",
                "category",
                "evidence",
                "reason",
                "fix_hint",
            ]
            for key in required_issue_fields:
                if key not in issue:
                    errors.append(ValidationError(f"{path}.{key}", "缺少必填字段"))
            sid = issue.get("source_task_id")
            if not isinstance(sid, str) or not sid.strip():
                errors.append(ValidationError(f"{path}.source_task_id", "必须是非空字符串"))
            if issue.get("severity") not in allowed_issue_severity:
                errors.append(
                    ValidationError(
                        f"{path}.severity",
                        "必须为 critical/major/minor/info 之一",
                    )
                )
            if issue.get("category") not in allowed_issue_category:
                errors.append(ValidationError(f"{path}.category", "类型不在允许范围"))
            for key in ["evidence", "reason", "fix_hint"]:
                if not isinstance(issue.get(key), str):
                    errors.append(ValidationError(f"{path}.{key}", "必须是 string"))

    new_tasks = obj.get("new_tasks")
    if not isinstance(new_tasks, list):
        errors.append(ValidationError("$.new_tasks", "必须是 array"))
    else:
        seen_new_ids: set[str] = set()
        for i, task in enumerate(new_tasks):
            path = f"$.new_tasks[{i}]"
            if not isinstance(task, dict):
                errors.append(ValidationError(path, "必须是 object"))
                continue
            required_task_fields = [
                "task_id",
                "source_task_id",
                "depends_on_task_id",
                "target_path",
                "task_desc",
                "min_verify",
                "max_retry",
            ]
            for key in required_task_fields:
                if key not in task:
                    errors.append(ValidationError(f"{path}.{key}", "缺少必填字段"))
            task_id = task.get("task_id")
            source_task_id = task.get("source_task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                errors.append(ValidationError(f"{path}.task_id", "必须是非空字符串"))
            if not isinstance(source_task_id, str) or not source_task_id.strip():
                errors.append(ValidationError(f"{path}.source_task_id", "必须是非空字符串"))
            if isinstance(task_id, str):
                if task_id in seen_new_ids:
                    errors.append(ValidationError(f"{path}.task_id", "在 new_tasks 中重复"))
                seen_new_ids.add(task_id)
            for key in ["depends_on_task_id", "target_path", "task_desc", "min_verify"]:
                if not isinstance(task.get(key), str):
                    errors.append(ValidationError(f"{path}.{key}", "必须是 string"))
            max_retry = task.get("max_retry")
            if not is_int(max_retry):
                errors.append(ValidationError(f"{path}.max_retry", "必须是整数"))
            elif int(max_retry) < 1 or int(max_retry) > 10:
                errors.append(ValidationError(f"{path}.max_retry", "范围必须在 1-10"))

    error_code = obj.get("error_code")
    if not isinstance(error_code, str) or not re.match(r"^(|REVIEW_[A-Z0-9_]+)$", error_code):
        errors.append(ValidationError("$.error_code", "必须为空或 REVIEW_ 前缀的大写下划线编码"))

    if not isinstance(obj.get("notes"), str):
        errors.append(ValidationError("$.notes", "必须是 string"))

    if review_decision == "PASS" and isinstance(new_tasks, list) and new_tasks:
        errors.append(ValidationError("$.new_tasks", "review_decision=PASS 时不应提供 new_tasks"))
    if review_decision == "BLOCKED" and isinstance(new_tasks, list) and new_tasks:
        errors.append(ValidationError("$.new_tasks", "review_decision=BLOCKED 时不应提供 new_tasks"))

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
        if task_id in existing_ids:
            print(f"[csv] 任务ID已存在，无法追加：{task_id}", file=sys.stderr)
            return 3
        if task_id in pending_new_ids:
            print(f"[csv] review new_tasks 内部任务ID重复：{task_id}", file=sys.stderr)
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
