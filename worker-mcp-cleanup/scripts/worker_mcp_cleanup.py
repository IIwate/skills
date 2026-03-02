import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any


ACE_TOOL_PATTERN = re.compile(r"ace-tool", re.IGNORECASE)
CODE_INDEX_PATTERN = re.compile(r"code-index-mcp(\.exe)?", re.IGNORECASE)
CODEX_NODE_PATTERN = re.compile(r"node_modules[/\\]@openai[/\\]codex[/\\]bin[/\\]codex\.js", re.IGNORECASE)


PROCESS_NOT_FOUND_PATTERNS = [
    re.compile(r"\bnot\s+found\b", re.IGNORECASE),
    re.compile(r"\bno\s+running\s+instance\b", re.IGNORECASE),
    re.compile(r"未找到进程"),
    re.compile(r"找不到进程"),
    re.compile(r"没有运行的任务实例"),
]


def ensure_utf8_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


ensure_utf8_stdio()


def is_process_not_found_message(message: str) -> bool:
    if not message:
        return False
    text = message.strip()
    if not text:
        return False
    return any(pattern.search(text) is not None for pattern in PROCESS_NOT_FOUND_PATTERNS)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def run_pwsh(command: str) -> str:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "PowerShell 执行失败")
    return result.stdout.strip()


def get_all_processes() -> list[dict[str, Any]]:
    command = r"""
$items = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate
$items | ConvertTo-Json -Compress -Depth 6
"""
    raw = run_pwsh(command)
    if not raw:
        return []

    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]

    processes: list[dict[str, Any]] = []
    for item in data:
        pid = item.get("ProcessId")
        ppid = item.get("ParentProcessId")
        if pid is None:
            continue
        processes.append(
            {
                "ProcessId": int(pid),
                "ParentProcessId": int(ppid) if ppid is not None else 0,
                "Name": item.get("Name", "") or "",
                "CommandLine": item.get("CommandLine", "") or "",
                "CreationDate": item.get("CreationDate", "") or "",
            }
        )
    return processes


def is_mcp_process(proc: dict[str, Any]) -> bool:
    name = str(proc.get("Name", "")).lower()
    cmd = str(proc.get("CommandLine", ""))
    if name == "code-index-mcp.exe":
        return True
    if name == "node.exe" and ACE_TOOL_PATTERN.search(cmd):
        return True
    if name in {"uv.exe", "uvx.exe"} and CODE_INDEX_PATTERN.search(cmd):
        return True
    if name == "python.exe" and CODE_INDEX_PATTERN.search(cmd):
        return True
    return False


def build_process_map(processes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(proc["ProcessId"]): proc for proc in processes}


def walk_ancestor_chain(start_pid: int, process_map: dict[int, dict[str, Any]], max_depth: int = 64) -> list[int]:
    chain: list[int] = []
    current = int(start_pid)
    visited: set[int] = set()

    for _ in range(max_depth):
        if current <= 0 or current in visited or current not in process_map:
            break
        visited.add(current)
        chain.append(current)
        parent = int(process_map[current].get("ParentProcessId") or 0)
        if parent == current:
            break
        current = parent
    return chain


def detect_owner_pid(process_map: dict[int, dict[str, Any]]) -> int | None:
    chain = walk_ancestor_chain(os.getpid(), process_map)
    for pid in chain:
        proc = process_map.get(pid)
        if not proc:
            continue
        name = str(proc.get("Name", "")).lower()
        cmd = str(proc.get("CommandLine", ""))
        if name == "codex.exe":
            return pid
        if name == "node.exe" and CODEX_NODE_PATTERN.search(cmd):
            return pid
    return None


def is_descendant_of_owner(pid: int, owner_pid: int, process_map: dict[int, dict[str, Any]], max_depth: int = 64) -> bool:
    if owner_pid <= 0:
        return False
    current = int(pid)
    visited: set[int] = set()

    for _ in range(max_depth):
        if current <= 0 or current in visited or current not in process_map:
            return False
        if current == owner_pid:
            return True
        visited.add(current)
        parent = int(process_map[current].get("ParentProcessId") or 0)
        if parent == current:
            return False
        current = parent
    return False


def load_snapshot(snapshot_path: pathlib.Path) -> dict[str, Any]:
    if not snapshot_path.exists():
        raise FileNotFoundError(f"快照文件不存在: {snapshot_path}")
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def load_baseline_pids(snapshot_payload: dict[str, Any]) -> set[int]:
    raw_pids = snapshot_payload.get("baseline_pids") or [item.get("ProcessId") for item in snapshot_payload.get("processes", [])]
    return {int(pid) for pid in raw_pids if pid is not None}


def calc_delta(current: list[dict[str, Any]], baseline_pids: set[int]) -> list[dict[str, Any]]:
    return [proc for proc in current if int(proc["ProcessId"]) not in baseline_pids]


def filter_owner_bound(
    processes: list[dict[str, Any]],
    owner_pid: int,
    process_map: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owner_bound: list[dict[str, Any]] = []
    non_owner: list[dict[str, Any]] = []
    for proc in processes:
        pid = int(proc["ProcessId"])
        if is_descendant_of_owner(pid, owner_pid, process_map):
            owner_bound.append(proc)
        else:
            non_owner.append(proc)
    return owner_bound, non_owner


def sanitize_process(proc: dict[str, Any]) -> dict[str, Any]:
    return {
        "ProcessId": int(proc.get("ProcessId") or 0),
        "ParentProcessId": int(proc.get("ParentProcessId") or 0),
        "Name": str(proc.get("Name", "") or ""),
    }


def sanitize_process_list(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_process(proc) for proc in processes]


def kill_pid(pid: int) -> tuple[bool, str]:
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout or "").strip()
    return False, message or "taskkill 失败"


def mode_snapshot(snapshot_path: pathlib.Path, owner_pid_arg: int | None) -> dict[str, Any]:
    all_processes = get_all_processes()
    process_map = build_process_map(all_processes)
    owner_pid = int(owner_pid_arg) if owner_pid_arg else detect_owner_pid(process_map)
    if owner_pid is None:
        raise RuntimeError("无法自动识别 owner_pid，请显式传入 --owner-pid。")
    if owner_pid not in process_map:
        raise RuntimeError(f"owner_pid 不存在: {owner_pid}")

    current_mcp = [proc for proc in all_processes if is_mcp_process(proc)]
    owner_bound, non_owner = filter_owner_bound(current_mcp, owner_pid, process_map)

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "worker-mcp-cleanup/v3",
        "created_at": now_iso(),
        "owner_pid": owner_pid,
        "owner_process": {
            "ProcessId": owner_pid,
            "Name": process_map[owner_pid].get("Name", ""),
        },
        "baseline_pids": [int(proc["ProcessId"]) for proc in current_mcp],
        "owner_bound_baseline_pids": [int(proc["ProcessId"]) for proc in owner_bound],
    }
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "mode": "Snapshot",
        "snapshot_path": str(snapshot_path),
        "owner_pid": owner_pid,
        "baseline_count": len(current_mcp),
        "owner_bound_baseline_count": len(owner_bound),
        "non_owner_baseline_count": len(non_owner),
        "captured_at": now_iso(),
    }


def mode_list_delta(snapshot_path: pathlib.Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    baseline_pids = load_baseline_pids(snapshot)
    owner_pid = int(snapshot.get("owner_pid") or 0)

    all_processes = get_all_processes()
    process_map = build_process_map(all_processes)
    current_mcp = [proc for proc in all_processes if is_mcp_process(proc)]
    delta = calc_delta(current_mcp, baseline_pids)

    owner_alive = owner_pid in process_map
    owner_delta: list[dict[str, Any]] = []
    non_owner_delta: list[dict[str, Any]] = []
    if owner_alive:
        owner_delta, non_owner_delta = filter_owner_bound(delta, owner_pid, process_map)
    else:
        non_owner_delta = delta

    return {
        "mode": "ListDelta",
        "snapshot_path": str(snapshot_path),
        "owner_pid": owner_pid,
        "owner_alive": owner_alive,
        "baseline_count": len(baseline_pids),
        "current_count": len(current_mcp),
        "delta_count": len(delta),
        "owner_delta_count": len(owner_delta),
        "non_owner_delta_count": len(non_owner_delta),
        "owner_delta": sanitize_process_list(owner_delta),
        "non_owner_delta": sanitize_process_list(non_owner_delta),
    }


def mode_cleanup(snapshot_path: pathlib.Path, dry_run: bool, keep_baseline: bool) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    baseline_pids = load_baseline_pids(snapshot)
    owner_pid = int(snapshot.get("owner_pid") or 0)

    all_processes = get_all_processes()
    process_map = build_process_map(all_processes)
    current_mcp = [proc for proc in all_processes if is_mcp_process(proc)]
    delta = calc_delta(current_mcp, baseline_pids)

    owner_alive = owner_pid in process_map
    owner_delta: list[dict[str, Any]] = []
    non_owner_delta: list[dict[str, Any]] = []
    if owner_alive:
        owner_delta, non_owner_delta = filter_owner_bound(delta, owner_pid, process_map)
    else:
        non_owner_delta = delta

    targets = sorted(owner_delta, key=lambda item: int(item["ProcessId"]), reverse=True)

    killed: list[int] = []
    ignored_not_found: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if owner_alive and not dry_run:
        for proc in targets:
            pid = int(proc["ProcessId"])
            ok, error_text = kill_pid(pid)
            if ok:
                killed.append(pid)
            else:
                if is_process_not_found_message(error_text):
                    ignored_not_found.append(
                        {"pid": pid, "name": proc.get("Name", ""), "reason": "process_not_found"}
                    )
                else:
                    failed.append({"pid": pid, "name": proc.get("Name", ""), "error": error_text})

    time.sleep(0.5)
    refreshed_all = get_all_processes()
    refreshed_map = build_process_map(refreshed_all)
    refreshed_mcp = [proc for proc in refreshed_all if is_mcp_process(proc)]
    refreshed_delta = calc_delta(refreshed_mcp, baseline_pids)
    remaining_owner = []
    if owner_pid in refreshed_map:
        remaining_owner, _ = filter_owner_bound(refreshed_delta, owner_pid, refreshed_map)

    result = {
        "mode": "Cleanup",
        "dry_run": dry_run,
        "keep_baseline": keep_baseline,
        "snapshot_path": str(snapshot_path),
        "owner_pid": owner_pid,
        "owner_alive": owner_alive,
        "baseline_count": len(baseline_pids),
        "current_count": len(current_mcp),
        "delta_count": len(delta),
        "owner_delta_count": len(owner_delta),
        "non_owner_delta_count": len(non_owner_delta),
        "killed_count": len(killed),
        "ignored_not_found_count": len(ignored_not_found),
        "failed_count": len(failed),
        "remaining_owner_delta_count": len(remaining_owner),
        "killed_pids": killed,
        "ignored_not_found": ignored_not_found,
        "failed": failed,
        "skipped_non_owner": sanitize_process_list(non_owner_delta),
    }
    cleanup_failed = len(remaining_owner) > 0
    if keep_baseline:
        result["snapshot_retained"] = True
        result["snapshot_retain_reason"] = "explicit_keep_baseline"
        return result
    if dry_run:
        result["snapshot_retained"] = True
        result["snapshot_retain_reason"] = "dry_run"
        return result
    if cleanup_failed:
        result["snapshot_retained"] = True
        result["snapshot_retain_reason"] = "cleanup_failed"
        return result
    try:
        snapshot_path.unlink(missing_ok=True)
        result["snapshot_retained"] = False
        result["snapshot_retain_reason"] = "cleanup_success_pruned"
    except OSError as exc:
        result["snapshot_retained"] = True
        result["snapshot_retain_reason"] = "prune_failed"
        result["snapshot_prune_error"] = str(exc)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按会话 owner PID 绑定，安全清理 worker 新增 MCP 进程")
    parser.add_argument("--mode", required=True, choices=["snapshot", "list-delta", "cleanup"])
    parser.add_argument("--snapshot-path", required=True)
    parser.add_argument("--owner-pid", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-baseline", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    snapshot_path = pathlib.Path(args.snapshot_path).expanduser().resolve()

    if args.mode == "snapshot":
        result = mode_snapshot(snapshot_path, args.owner_pid)
    elif args.mode == "list-delta":
        result = mode_list_delta(snapshot_path)
    else:
        result = mode_cleanup(snapshot_path, args.dry_run, args.keep_baseline)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
