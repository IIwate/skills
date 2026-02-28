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


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def validate_worker_result(obj: Any, schema: dict[str, Any]) -> list[ValidationError]:
    if not isinstance(obj, dict):
        return [ValidationError("$", "结果必须是 JSON object")]

    errors: list[ValidationError] = []

    required = schema.get("required", [])
    for key in required:
        if key not in obj:
            errors.append(ValidationError(f"$.{key}", "缺少必填字段"))

    properties: dict[str, Any] = schema.get("properties", {}) or {}
    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for extra_key in sorted(set(obj.keys()) - allowed):
            errors.append(ValidationError(f"$.{extra_key}", "不允许的额外字段"))

    task_id = obj.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        errors.append(ValidationError("$.task_id", "必须是非空字符串"))

    exec_state = obj.get("exec_state")
    allowed_exec = (
        (properties.get("exec_state") or {}).get("enum")
        or ["implemented", "blocked", "worker_failed"]
    )
    if exec_state not in allowed_exec:
        errors.append(ValidationError("$.exec_state", f"必须为 {allowed_exec} 之一"))

    min_verify_state = obj.get("min_verify_state")
    allowed_min_verify = (
        (properties.get("min_verify_state") or {}).get("enum")
        or ["unknown", "pass", "fail", "skip"]
    )
    if min_verify_state not in allowed_min_verify:
        errors.append(
            ValidationError("$.min_verify_state", f"必须为 {allowed_min_verify} 之一")
        )

    artifact = obj.get("artifact")
    if not isinstance(artifact, dict):
        errors.append(ValidationError("$.artifact", "必须是 object"))
    else:
        artifact_schema: dict[str, Any] = properties.get("artifact", {}) or {}
        artifact_props: dict[str, Any] = artifact_schema.get("properties", {}) or {}
        if artifact_schema.get("additionalProperties") is False:
            allowed_artifact = set(artifact_props.keys())
            for extra_key in sorted(set(artifact.keys()) - allowed_artifact):
                errors.append(
                    ValidationError(f"$.artifact.{extra_key}", "不允许的额外字段")
                )

        if "summary" not in artifact:
            errors.append(ValidationError("$.artifact.summary", "缺少必填字段"))
        elif not isinstance(artifact.get("summary"), str):
            errors.append(ValidationError("$.artifact.summary", "必须是 string"))

        if "deliverables" not in artifact:
            errors.append(ValidationError("$.artifact.deliverables", "缺少必填字段"))
        elif not is_string_list(artifact.get("deliverables")):
            errors.append(ValidationError("$.artifact.deliverables", "必须是 string 数组"))

    if not is_string_list(obj.get("files_changed")):
        errors.append(ValidationError("$.files_changed", "必须是 string 数组"))

    if not is_string_list(obj.get("commands_run")):
        errors.append(ValidationError("$.commands_run", "必须是 string 数组"))

    error_code = obj.get("error_code")
    pattern = re.compile(r"^(|WORKER_[A-Z0-9_]+)$")
    if not isinstance(error_code, str) or not pattern.match(error_code):
        errors.append(ValidationError("$.error_code", "必须为空或 WORKER_ 前缀的大写下划线编码"))

    if not isinstance(obj.get("error_summary"), str):
        errors.append(ValidationError("$.error_summary", "必须是 string"))

    if not isinstance(obj.get("notes"), str):
        errors.append(ValidationError("$.notes", "必须是 string"))

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
    row["最小验证结果"] = str(result.get("min_verify_state", "") or "")
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

    required_cols = {"任务ID", "执行状态", "最小验证结果", "错误码", "错误摘要", "更新时间"}
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
