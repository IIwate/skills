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
    Path(__file__).resolve().parent.parent / "assets" / "worker-result-schema.json"
)

def ensure_utf8_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


ensure_utf8_stdio()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str


def load_json_from_path(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_from_stdin() -> Any:
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("STDIN 为空：请通过 --result-path 指定文件，或通过管道传入 worker JSON。")
    return json.loads(text)


def validate_worker_result(obj: Any, schema: dict[str, Any]) -> list[ValidationError]:
    if not isinstance(obj, dict):
        return [ValidationError("$", "结果必须是 JSON object")]

    errors: list[ValidationError] = []

    required = [k for k in schema.get("required", []) if isinstance(k, str)]
    for key in required:
        if key not in obj:
            errors.append(ValidationError(f"$.{key}", "缺少必填字段"))

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

    def check_string_array(value: Any, path: str, field_schema: dict[str, Any]) -> None:
        if not isinstance(value, list):
            errors.append(ValidationError(path, "必须是 array"))
            return
        item_schema: dict[str, Any] = field_schema.get("items", {}) or {}
        if item_schema.get("type") != "string":
            return
        for i, item in enumerate(value):
            check_string(item, f"{path}[{i}]", item_schema)

    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for extra_key in sorted(set(obj.keys()) - allowed):
            errors.append(ValidationError(f"$.{extra_key}", "不允许的额外字段"))

    check_string(obj.get("task_id"), "$.task_id", properties.get("task_id", {}) or {})
    check_string(
        obj.get("exec_state"),
        "$.exec_state",
        properties.get("exec_state", {}) or {},
    )
    check_string(
        obj.get("min_verify_state"),
        "$.min_verify_state",
        properties.get("min_verify_state", {}) or {},
    )

    artifact = obj.get("artifact")
    if not isinstance(artifact, dict):
        errors.append(ValidationError("$.artifact", "必须是 object"))
    else:
        artifact_schema: dict[str, Any] = properties.get("artifact", {}) or {}
        artifact_required = [
            k for k in artifact_schema.get("required", []) if isinstance(k, str)
        ]
        artifact_props: dict[str, Any] = artifact_schema.get("properties", {}) or {}
        for key in artifact_required:
            if key not in artifact:
                errors.append(ValidationError(f"$.artifact.{key}", "缺少必填字段"))
        if artifact_schema.get("additionalProperties") is False:
            allowed_artifact = set(artifact_props.keys())
            for extra_key in sorted(set(artifact.keys()) - allowed_artifact):
                errors.append(
                    ValidationError(f"$.artifact.{extra_key}", "不允许的额外字段")
                )

        if "summary" in artifact:
            check_string(
                artifact.get("summary"),
                "$.artifact.summary",
                artifact_props.get("summary", {}) or {},
            )
        if "deliverables" in artifact:
            check_string_array(
                artifact.get("deliverables"),
                "$.artifact.deliverables",
                artifact_props.get("deliverables", {}) or {},
            )

    check_string_array(
        obj.get("files_changed"),
        "$.files_changed",
        properties.get("files_changed", {}) or {},
    )
    check_string_array(
        obj.get("commands_run"),
        "$.commands_run",
        properties.get("commands_run", {}) or {},
    )
    check_string(
        obj.get("error_code"), "$.error_code", properties.get("error_code", {}) or {}
    )
    check_string(
        obj.get("error_summary"),
        "$.error_summary",
        properties.get("error_summary", {}) or {},
    )
    check_string(obj.get("notes"), "$.notes", properties.get("notes", {}) or {})

    return errors


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


def apply_result_to_row(row: dict[str, str], result: dict[str, Any]) -> None:
    row["执行状态"] = str(result.get("exec_state", "") or "")
    min_verify_state = str(result.get("min_verify_state", "") or "")
    row["最小验证结果"] = min_verify_state
    if min_verify_state == "fail":
        row["验收状态"] = "accept_fail"
    row["错误码"] = str(result.get("error_code", "") or "")
    row["错误摘要"] = str(result.get("error_summary", "") or "")
    row["更新时间"] = now_iso()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="校验 worker 回传 JSON 并回写任务 CSV（UTF-8 BOM）"
    )
    parser.add_argument("--csv-path", required=True, help="任务清单 CSV 路径")
    parser.add_argument(
        "--result-path",
        action="append",
        help="worker 回传 JSON 文件路径（可重复）。若不提供则从 STDIN 读取。",
    )
    parser.add_argument(
        "--schema-path",
        default=str(DEFAULT_SCHEMA_PATH),
        help="worker 回传 JSON Schema 路径",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验与打印，不写回 CSV")
    args = parser.parse_args(argv)

    schema = load_json_from_path(Path(args.schema_path))
    csv_path = Path(args.csv_path)

    results: list[dict[str, Any]] = []
    if args.result_path:
        for p in args.result_path:
            obj = load_json_from_path(Path(p))
            if not isinstance(obj, dict):
                raise ValueError(f"结果文件不是 JSON object: {p}")
            results.append(obj)
    else:
        obj = load_json_from_stdin()
        if not isinstance(obj, dict):
            raise ValueError("STDIN 结果不是 JSON object")
        results.append(obj)

    fieldnames, rows = read_csv_rows(csv_path)

    required_cols = {
        "任务ID",
        "执行状态",
        "最小验证结果",
        "验收状态",
        "错误码",
        "错误摘要",
        "更新时间",
    }
    missing_cols = sorted(c for c in required_cols if c not in fieldnames)
    if missing_cols:
        raise RuntimeError(f"CSV 缺少必要列：{', '.join(missing_cols)}")

    index_by_id: dict[str, int] = {}
    for i, row in enumerate(rows):
        tid = row.get("任务ID", "") or ""
        if tid and tid not in index_by_id:
            index_by_id[tid] = i

    updated: list[str] = []
    for result in results:
        errors = validate_worker_result(result, schema)
        if errors:
            for e in errors:
                print(f"[schema] {e.path}: {e.message}", file=sys.stderr)
            return 2

        task_id = str(result.get("task_id") or "")
        if task_id not in index_by_id:
            print(f"[csv] 找不到任务ID：{task_id}", file=sys.stderr)
            return 3

        apply_result_to_row(rows[index_by_id[task_id]], result)
        updated.append(task_id)

    if args.dry_run:
        print("dry-run: ok, would update:", ", ".join(updated))
        return 0

    write_csv_rows(csv_path, fieldnames, rows)
    print("updated:", ", ".join(updated))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
