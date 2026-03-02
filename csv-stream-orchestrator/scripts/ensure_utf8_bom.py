from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


UTF8_BOM = b"\xef\xbb\xbf"
OTHER_BOMS: list[tuple[str, bytes]] = [
    ("utf-32-le", b"\xff\xfe\x00\x00"),
    ("utf-32-be", b"\x00\x00\xfe\xff"),
    ("utf-16-le", b"\xff\xfe"),
    ("utf-16-be", b"\xfe\xff"),
]


def ensure_utf8_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


ensure_utf8_stdio()


def detect_bom(data: bytes) -> str | None:
    if data.startswith(UTF8_BOM):
        return "utf-8"
    for name, bom in OTHER_BOMS:
        if data.startswith(bom):
            return name
    return None


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{int(time.time() * 1000)}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def to_display_path(path: Path) -> str:
    try:
        return path.resolve().as_posix()
    except Exception:
        return path.as_posix()


def process_file(path: Path, apply: bool) -> dict[str, str]:
    if not path.exists():
        return {"path": to_display_path(path), "status": "error", "message": "文件不存在"}
    if not path.is_file():
        return {"path": to_display_path(path), "status": "error", "message": "不是文件"}

    data = path.read_bytes()
    bom = detect_bom(data)

    if bom == "utf-8":
        return {"path": to_display_path(path), "status": "ok", "message": ""}
    if bom is not None:
        return {
            "path": to_display_path(path),
            "status": "error",
            "message": f"发现非 UTF-8 BOM: {bom}，拒绝修改",
        }

    if not apply:
        return {"path": to_display_path(path), "status": "missing", "message": "缺少 UTF-8 BOM"}

    write_bytes_atomic(path, UTF8_BOM + data)
    return {"path": to_display_path(path), "status": "fixed", "message": "已补齐 UTF-8 BOM"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="确保目标文件包含 UTF-8 BOM（EF BB BF）")
    parser.add_argument("paths", nargs="+", help="文件路径（可多个）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式授权写入：当缺少 BOM 时补齐；默认仅校验",
    )
    args = parser.parse_args(argv)

    file_results: list[dict[str, str]] = []
    counts = {"ok": 0, "fixed": 0, "missing": 0, "error": 0}
    for raw in args.paths:
        path = Path(raw).expanduser()
        try:
            result = process_file(path, args.apply)
        except Exception as exc:
            result = {
                "path": to_display_path(path),
                "status": "error",
                "message": str(exc),
            }
        file_results.append(result)
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    output = {
        "mode": "apply" if args.apply else "check",
        "total": len(file_results),
        "ok_count": counts.get("ok", 0),
        "fixed_count": counts.get("fixed", 0),
        "missing_count": counts.get("missing", 0),
        "error_count": counts.get("error", 0),
        "files": file_results,
    }
    print(json.dumps(output, ensure_ascii=False))

    if output["error_count"] > 0:
        return 1
    if not args.apply and output["missing_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

