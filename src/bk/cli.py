from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import sys
from datetime import timedelta
from typing import List, Optional

from .advisor import GpuAdvice, build_gpu_advice
from .config import Config, load_config
from .gpu import snapshot
from .monitor import UsageAuditStore, run_monitor
from .models import MODE_EXCLUSIVE, MODE_SHARED, Actor, BookingError, BookingRequest, EditRequest
from .scheduler import (
    add_booking,
    cancel_booking,
    edit_booking,
    find_policy_violations,
    list_active,
    shared_memory_headroom_for_reservation,
)
from .storage import LedgerStore
from .timeparse import (
    format_local,
    format_local_range,
    parse_duration_seconds,
    parse_iso,
    parse_memory_mb,
    parse_start,
    utc_now,
)
from .tui import run_tui
from .usage import classify_process_usage
from .worker import job_log_path, retry_job, run_worker

try:
    import readline  # noqa: F401
except ImportError:
    pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config()
        store = LedgerStore(
            config.data_dir,
            config.lock_timeout_seconds,
            config.backup_keep,
            config.file_mode,
            config.dir_mode,
        )
        if not argv:
            return _interactive_shell(config, store)

        head = argv[0]
        if _looks_like_auto_request(argv):
            return _book_command(argv, MODE_SHARED, config, store)
        if head in {"auto", "shared", "s"}:
            return _book_command(argv[1:], MODE_SHARED, config, store)
        if head in {"exclusive", "x"}:
            return _book_command(argv[1:], MODE_EXCLUSIVE, config, store)
        if head in {"tui", "t"}:
            return run_tui(config, store)
        if head in {"monitor", "m"}:
            return _monitor_command(argv[1:], config, store)
        if head in {"usage", "u"}:
            return _usage_command(argv[1:], config)
        if head in {"worker", "w"}:
            return _worker_command(argv[1:], config, store)
        if head in {"jobs", "j"}:
            return _jobs_command(store)
        if head in {"job-log", "jl"}:
            return _job_log_command(argv[1:], config, store)
        if head in {"job-retry", "jr"}:
            return _job_retry_command(argv[1:], store)
        if head in {"status", "timeline", "st"}:
            _print_status(config, store)
            return 0
        if head in {"add", "a"}:
            return _add_interactive(config, store)
        if head in {"edit", "e"}:
            return _edit_command(argv[1:], config, store)
        if head in {"del", "delete", "d", "rm"}:
            return _delete_command(argv[1:], store)
        if head == "reset":
            return _reset_command(argv[1:], config, store)
        if head in {"log", "lg"}:
            return _log_command(config, store)
        if head in {"doctor", "dr"}:
            return _doctor_command(config, store)
        if head in {"list", "ls", "l"}:
            return _list_command(store)
        if head in {"-h", "--help", "help"}:
            _print_help()
            return 0
        print(f"未知命令: {head}", file=sys.stderr)
        _print_help(file=sys.stderr)
        return 2
    except (BookingError, ValueError, TimeoutError, OSError) as exc:
        print(f"bk: {exc}", file=sys.stderr)
        return 2


def _looks_like_auto_request(argv: List[str]) -> bool:
    return len(argv) >= 2 and argv[0].isdigit()


def _interactive_shell(config: Config, store: LedgerStore) -> int:
    print("bk GPU booking")
    print(f"data: {config.data_dir}")
    print(f"shared limit: {config.max_shared_users}")
    print("Type 'help' for commands. Type 'quit' to exit.")
    print()
    _print_status(config, store)

    while True:
        try:
            line = input("bk> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        line = line.strip()
        if not line:
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            print(f"bk: {exc}")
            continue
        if not args:
            continue

        try:
            keep_running = _dispatch_shell_command(args, config, store)
        except SystemExit as exc:
            if exc.code not in (0, None):
                print("bk: invalid command arguments")
            keep_running = True
        except (BookingError, ValueError, TimeoutError, OSError) as exc:
            print(f"bk: {exc}")
            keep_running = True

        if not keep_running:
            return 0


def _dispatch_shell_command(args: List[str], config: Config, store: LedgerStore) -> bool:
    head = args[0]
    if head in {"q", "quit", "exit"}:
        return False
    if head in {"h", "help", "?"}:
        _print_shell_help()
        return True
    if head in {"status", "refresh", "r", "timeline", "st"}:
        _print_status(config, store)
        return True
    if head in {"list", "ls", "l"}:
        _list_command(store)
        return True
    if head in {"log", "logs", "lg"}:
        _log_command(config, store)
        return True
    if head in {"doctor", "dr"}:
        _doctor_command(config, store)
        return True
    if head in {"del", "delete", "cancel", "d", "rm"}:
        _delete_command(args[1:], store)
        return True
    if head in {"edit", "e"}:
        _edit_command(args[1:], config, store)
        return True
    if head == "reset":
        _reset_command(args[1:], config, store)
        return True
    if head in {"add", "a"}:
        _add_interactive(config, store)
        return True
    if head in {"tui", "t"}:
        run_tui(config, store)
        return True
    if head in {"monitor", "m"}:
        _monitor_command(args[1:], config, store)
        return True
    if head in {"usage", "u"}:
        _usage_command(args[1:], config)
        return True
    if head in {"worker", "w"}:
        _worker_command(args[1:], config, store)
        return True
    if head in {"jobs", "j"}:
        _jobs_command(store)
        return True
    if head in {"job-log", "jl"}:
        _job_log_command(args[1:], config, store)
        return True
    if head in {"job-retry", "jr"}:
        _job_retry_command(args[1:], store)
        return True
    if _looks_like_auto_request(args):
        _book_command(args, MODE_SHARED, config, store)
        return True
    if head in {"auto", "shared", "s"}:
        _book_command(args[1:], MODE_SHARED, config, store)
        return True
    if head in {"exclusive", "x"}:
        _book_command(args[1:], MODE_EXCLUSIVE, config, store)
        return True
    print(f"未知命令: {head}")
    _print_shell_help()
    return True


def _book_command(argv: List[str], mode: str, config: Config, store: LedgerStore) -> int:
    booking_argv, command_argv = _split_job_command(argv)
    parser = argparse.ArgumentParser(prog=f"bk {'exclusive' if mode == MODE_EXCLUSIVE else ''}".strip())
    parser.add_argument("count", type=int)
    parser.add_argument("duration")
    parser.add_argument("--start", help="ISO time; omitted means now with automatic queueing")
    parser.add_argument("--gpu", help="comma separated GPU indexes, for example 0,1")
    parser.add_argument("--mem", help="expected memory on each GPU, for example 12g or 4096m")
    parser.add_argument("--op-id", help="idempotency key for agents and retry-safe scripts")
    args = parser.parse_args(booking_argv)

    start_raw = args.start or "now"
    preferred = _parse_gpu_list(args.gpu) if args.gpu else None
    advice = build_gpu_advice(config)
    expected_memory_mb = parse_memory_mb(args.mem) if args.mem else None
    request = BookingRequest(
        actor=_current_actor(),
        count=args.count,
        duration_seconds=parse_duration_seconds(args.duration),
        start_at=parse_start(start_raw),
        mode=mode,
        preferred_gpus=preferred,
        gpu_order=advice.order,
        gpu_scores=advice.scores,
        allow_queue=args.start is None,
        op_id=args.op_id,
        command_argv=command_argv,
        working_directory=os.getcwd() if command_argv is not None else None,
        expected_memory_mb=expected_memory_mb,
        gpu_memory_capacity_mb=advice.memory_capacities_mb,
    )
    result = add_booking(store, config, request)
    reservation = result.reservation
    if not result.created:
        status = "exists"
    elif result.queued:
        status = "queued"
    else:
        status = "created"
    gpus = ",".join(str(item) for item in reservation["gpus"])
    print(
        f"{status}: {_short_id(reservation)} mode={reservation['mode']} "
        f"gpu={gpus} {format_local_range(reservation['start_at'], reservation['end_at'])}"
    )
    _print_booking_advice(config, store, reservation, advice, expected_memory_mb)
    if isinstance(reservation.get("job"), dict):
        command_text = shlex.join(reservation["job"].get("argv", []))
        print(f"job: {reservation['job'].get('status')} command={command_text}")
        print("worker: keep `bk w` running before the scheduled start")
    return 0


def _monitor_command(argv: List[str], config: Config, store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk monitor")
    parser.add_argument("--interval", type=float, default=2.0, help="sampling interval in seconds (default: 2)")
    parser.add_argument("--rollup", type=int, default=60, help="rollup window in seconds (default: 60)")
    parser.add_argument("--once", action="store_true", help="collect one sample and exit")
    parser.add_argument("--samples", type=int, help="collect a bounded number of samples and exit")
    parser.add_argument("--verbose", action="store_true", help="print every sample instead of state changes only")
    args = parser.parse_args(argv)
    return run_monitor(
        config,
        store,
        interval_seconds=args.interval,
        rollup_seconds=args.rollup,
        once=args.once,
        max_samples=1 if args.once else args.samples,
        verbose=args.verbose,
    )


def _usage_command(argv: List[str], config: Config) -> int:
    parser = argparse.ArgumentParser(prog="bk usage")
    parser.add_argument("--rollups", action="store_true", help="show utilization rollups instead of events")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.limit < 1:
        raise ValueError("--limit must be >= 1")
    store = UsageAuditStore(
        config.data_dir,
        config.lock_timeout_seconds,
        config.file_mode,
        config.dir_mode,
    )
    records = store.recent_rollups(args.limit) if args.rollups else store.recent_events(args.limit)
    if not records:
        print("no usage records")
        return 0
    for record in records:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def _worker_command(argv: List[str], config: Config, store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk worker")
    parser.add_argument("--once", action="store_true", help="run due jobs, wait for them, then exit")
    parser.add_argument("--poll", type=float, help="poll interval in seconds")
    parser.add_argument("--max-parallel", type=int, help="maximum child jobs for this worker")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    summary = run_worker(
        config,
        store,
        _current_actor(),
        once=args.once,
        poll_seconds=args.poll,
        max_parallel=args.max_parallel,
        quiet=args.quiet,
    )
    return 0 if summary.failed == 0 else 1


def _jobs_command(store: LedgerStore) -> int:
    actor = _current_actor()
    reservations = _own_job_reservations(store, actor)
    if not reservations:
        print("No jobs.")
        return 0
    print("#  ID       State        GPU       Start                  Command")
    for index, reservation in enumerate(reservations, 1):
        job = reservation["job"]
        command = shlex.join(job.get("argv", []))
        if len(command) > 48:
            command = command[:45] + "..."
        print(
            f"{index:<2} {_short_id(reservation):<8} {str(job.get('status', '?')):<12} "
            f"{','.join(map(str, reservation.get('gpus', []))):<9} "
            f"{format_local(reservation['start_at']):<22} {command}"
        )
    return 0


def _job_log_command(argv: List[str], config: Config, store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk job-log")
    parser.add_argument("reservation_id", nargs="?")
    args = parser.parse_args(argv)
    actor = _current_actor()
    reservations = _own_job_reservations(store, actor)
    if not reservations:
        raise BookingError("you have no jobs")
    token = args.reservation_id or input("job number or short id: ").strip()
    reservation = _resolve_job_reservation(reservations, token)
    path = job_log_path(config, str(reservation["id"]))
    if not path.exists():
        print(f"job log not created yet: {path}")
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        sys.stdout.write(fh.read())
    return 0


def _job_retry_command(argv: List[str], store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk job-retry")
    parser.add_argument("reservation_id")
    parser.add_argument(
        "--accept-duplicate-risk",
        action="store_true",
        help="required for an uncertain claim that might already be running",
    )
    args = parser.parse_args(argv)
    actor = _current_actor()
    reservation = _resolve_job_reservation(_own_job_reservations(store, actor), args.reservation_id)
    updated = retry_job(
        store,
        actor,
        str(reservation["id"]),
        accept_duplicate_risk=args.accept_duplicate_risk,
    )
    print(f"job retry queued: {_short_id(updated)}")
    return 0


def _add_interactive(config: Config, store: LedgerStore) -> int:
    actor = _current_actor()
    mode_raw = input("mode [shared/exclusive] (shared): ").strip() or MODE_SHARED
    if mode_raw not in {MODE_SHARED, MODE_EXCLUSIVE}:
        raise ValueError("mode must be shared or exclusive")
    count = int(input("gpu count: ").strip())
    duration = parse_duration_seconds(input("duration (30m/1h30m/1d): ").strip())
    start_raw = input("start ISO or now (now): ").strip()
    allow_queue = start_raw in {"", "now"}
    start = parse_start(start_raw or "now")
    gpu_raw = input("gpu indexes optional, for example 0,1: ").strip()
    preferred = _parse_gpu_list(gpu_raw) if gpu_raw else None
    memory_raw = input("expected memory per GPU optional, for example 12g: ").strip()
    expected_memory_mb = parse_memory_mb(memory_raw) if memory_raw else None
    command_raw = input("command optional, for example python train.py: ").strip()
    command_argv = shlex.split(command_raw) if command_raw else None
    advice = build_gpu_advice(config)
    result = add_booking(
        store,
        config,
        BookingRequest(
            actor=actor,
            count=count,
            duration_seconds=duration,
            start_at=start,
            mode=mode_raw,
            preferred_gpus=preferred,
            gpu_order=advice.order,
            gpu_scores=advice.scores,
            allow_queue=allow_queue,
            command_argv=command_argv,
            working_directory=os.getcwd() if command_argv is not None else None,
            expected_memory_mb=expected_memory_mb,
            gpu_memory_capacity_mb=advice.memory_capacities_mb,
        ),
    )
    reservation = result.reservation
    print(f"{'queued' if result.queued else 'created'}: {_short_id(reservation)} {format_local_range(reservation['start_at'], reservation['end_at'])}")
    _print_booking_advice(config, store, reservation, advice, expected_memory_mb)
    if isinstance(reservation.get("job"), dict):
        print(f"job: pending command={shlex.join(reservation['job'].get('argv', []))}")
        print("worker: keep `bk w` running before the scheduled start")
    return 0


def _delete_command(argv: List[str], store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk del")
    parser.add_argument("reservation_id", nargs="?")
    args = parser.parse_args(argv)
    actor = _current_actor()
    reservation_id = args.reservation_id or _prompt_reservation_token(store, actor, "delete")
    resolved = _resolve_own_reservation_id(store, reservation_id, actor)
    reservation = cancel_booking(store, resolved, actor)
    print(f"cancelled: {_short_id(reservation)}")
    return 0


def _edit_command(argv: List[str], config: Config, store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk edit")
    parser.add_argument("reservation_id", nargs="?")
    parser.add_argument("--duration")
    parser.add_argument("--start")
    parser.add_argument("--gpu", help="comma separated GPU indexes; use with --count to change GPU count")
    parser.add_argument("--count", type=int)
    parser.add_argument("--mode", choices=[MODE_SHARED, MODE_EXCLUSIVE])
    parser.add_argument("--queue", action="store_true", help="allow moving to the next available slot")
    args = parser.parse_args(argv)

    actor = _current_actor()
    token = args.reservation_id or _prompt_reservation_token(store, actor, "edit")
    reservation_id = _resolve_own_reservation_id(store, token, actor)
    if not any([args.duration, args.start, args.gpu, args.count, args.mode, args.queue]):
        return _edit_interactive(config, store, reservation_id, actor)

    preferred = _parse_gpu_list(args.gpu) if args.gpu else None
    result = edit_booking(
        store,
        config,
        EditRequest(
            actor=actor,
            reservation_id=reservation_id,
            start_at=parse_start(args.start) if args.start else None,
            duration_seconds=parse_duration_seconds(args.duration) if args.duration else None,
            mode=args.mode,
            preferred_gpus=preferred,
            count=args.count,
            allow_queue=args.queue,
        ),
    )
    _print_edit_result(result.reservation, result)
    return 0


def _edit_interactive(config: Config, store: LedgerStore, reservation_id: str, actor: Actor) -> int:
    reservation = _get_reservation(store, reservation_id)
    print(f"editing {_short_id(reservation)}")
    print(f"current: mode={reservation['mode']} gpu={','.join(map(str, reservation.get('gpus', [])))} {format_local_range(reservation['start_at'], reservation['end_at'])}")
    mode = input(f"mode [{reservation['mode']}]: ").strip() or None
    if mode and mode not in {MODE_SHARED, MODE_EXCLUSIVE}:
        raise ValueError("mode must be shared or exclusive")
    duration = input("duration (blank keep, e.g. 30m/4h): ").strip()
    start = input("start ISO/now (blank keep): ").strip()
    gpu_raw = input("gpu list (blank keep, e.g. 0,1): ").strip()
    count_raw = input("gpu count for auto-pick (blank keep): ").strip()
    queue_raw = input("queue if conflict? [y/N]: ").strip().lower()
    preferred = _parse_gpu_list(gpu_raw) if gpu_raw else None
    result = edit_booking(
        store,
        config,
        EditRequest(
            actor=actor,
            reservation_id=reservation_id,
            start_at=parse_start(start) if start else None,
            duration_seconds=parse_duration_seconds(duration) if duration else None,
            mode=mode,
            preferred_gpus=preferred,
            count=int(count_raw) if count_raw else None,
            allow_queue=queue_raw in {"y", "yes"},
        ),
    )
    _print_edit_result(result.reservation, result)
    return 0


def _print_edit_result(reservation: dict, result) -> None:
    status = "queued" if result.queued else "updated"
    print(
        f"{status}: {_short_id(reservation)} mode={reservation['mode']} "
        f"gpu={','.join(map(str, reservation.get('gpus', [])))} "
        f"{format_local_range(reservation['start_at'], reservation['end_at'])}"
    )


def _log_command(config: Config, store: LedgerStore) -> int:
    uid = _current_actor().uid
    store.ensure()
    if not store.log_path.exists():
        return 0
    with store.log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line)
            if int(item.get("uid")) != uid:
                continue
            when = format_local(item["ts"]) if item.get("ts") else ""
            time_range = ""
            if item.get("start_at") and item.get("end_at"):
                time_range = f" {format_local_range(item['start_at'], item['end_at'])}"
            print(
                f"{when} {item['action']} {item['result']} "
                f"{str(item.get('reservation_id', ''))[:8]} mode={item.get('mode')} gpu={','.join(map(str, item.get('gpus', [])))}{time_range}"
            )
    return 0


def _list_command(store: LedgerStore) -> int:
    active = list_active(store.load())
    if not active:
        print("No active reservations.")
        return 0
    actor = _current_actor()
    mine = _own_active_reservations(store, actor)
    mine_index = {reservation["id"]: index + 1 for index, reservation in enumerate(mine)}
    for reservation in active:
        gpus = ",".join(str(item) for item in reservation.get("gpus", []))
        index = mine_index.get(reservation["id"], "-")
        print(
            f"{index:>2} {_short_id(reservation)} {reservation['mode']} uid={reservation['uid']} "
            f"user={reservation['username']} gpu={gpus} "
            f"job={reservation.get('job', {}).get('status', '-')} "
            f"{format_local_range(reservation['start_at'], reservation['end_at'])}"
        )
    return 0


def _doctor_command(config: Config, store: LedgerStore) -> int:
    storage_issues = store.health_issues()
    issues = find_policy_violations(store.load(), config.max_shared_users)
    if not issues and not storage_issues:
        print("No policy issues found.")
        return 0
    print(f"Found {len(storage_issues)} storage issue(s), {len(issues)} policy issue(s):")
    for issue in storage_issues:
        details = " ".join(
            f"{key}={value}"
            for key, value in issue.items()
            if key not in {"type", "message"}
        )
        print(f"{issue['type']} {details} {issue.get('message', '')}".rstrip())
    for issue in issues:
        if issue["type"] == "shared-capacity":
            print(
                "shared-capacity "
                f"gpu={issue['gpu']} count={issue['count']} limit={issue['limit']} "
                f"{format_local_range(issue['start_at'], issue['end_at'])} "
                f"ids={','.join(str(item)[:8] for item in issue['reservation_ids'])}"
            )
        elif issue["type"] == "exclusive-overlap":
            print(
                "exclusive-overlap "
                f"gpu={issue['gpu']} "
                f"{str(issue['left_id'])[:8]}[{format_local_range(issue['left_start_at'], issue['left_end_at'])}] "
                f"overlaps {str(issue['right_id'])[:8]}[{format_local_range(issue['right_start_at'], issue['right_end_at'])}]"
            )
    return 0


def _reset_command(argv: List[str], config: Config, store: LedgerStore) -> int:
    parser = argparse.ArgumentParser(prog="bk reset")
    parser.add_argument("--yes", action="store_true", help="required to reset without an interactive confirmation")
    args = parser.parse_args(argv)
    if not args.yes:
        answer = input(f"Clear all bk data in {store.data_dir}? Type reset to continue: ").strip()
        if answer != "reset":
            print("reset cancelled")
            return 1
    audit_store = UsageAuditStore(
        config.data_dir,
        config.lock_timeout_seconds,
        config.file_mode,
        config.dir_mode,
    )
    with audit_store.lock():
        result = store.reset()
        usage_result = audit_store.clear_unlocked()
    print(
        f"reset: removed {result['reservations']} reservation record(s), "
        f"{result['logs']} log line(s), {result['backups']} backup file(s), "
        f"{usage_result['usage_events']} usage event(s), "
        f"{usage_result['usage_rollups']} usage rollup(s)"
    )
    return 0


def _parse_gpu_list(value: str) -> List[int]:
    gpus = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        gpus.append(int(part))
    if not gpus:
        raise ValueError("--gpu must contain at least one GPU index")
    return gpus


def _split_job_command(argv: List[str]) -> tuple[List[str], Optional[List[str]]]:
    if "--" not in argv:
        return argv, None
    separator = argv.index("--")
    command = argv[separator + 1 :]
    if not command:
        raise ValueError("-- must be followed by a job command")
    return argv[:separator], command


def _print_booking_advice(
    config: Config,
    store: LedgerStore,
    reservation: dict,
    advice: GpuAdvice,
    expected_memory_mb: Optional[int],
) -> None:
    selected = [int(gpu) for gpu in reservation.get("gpus", [])]
    has_telemetry = any(item.source != "none" for item in advice.snapshots)
    has_history = any(item.sample_count for item in advice.historical_loads.values())
    if has_telemetry or has_history:
        parts = []
        for gpu in selected:
            state = advice.live_states[gpu]
            recent = advice.historical_loads[gpu]
            recent_text = f"{recent.predicted_percent:.0f}%" if recent.sample_count else "n/a"
            parts.append(f"GPU {gpu} score={advice.scores[gpu]:.1f} now={state.status} recent={recent_text}")
        print("selection: " + "; ".join(parts))

    busy_selected = [gpu for gpu in selected if advice.live_states[gpu].status == "busy"]
    busy_avoided = [
        gpu
        for gpu in advice.order
        if gpu not in selected and advice.live_states[gpu].status == "busy"
    ]
    if busy_selected:
        details = ", ".join(f"GPU {gpu} ({advice.live_states[gpu].reason})" for gpu in busy_selected)
        print(f"warning: selected GPU currently busy: {details}", file=sys.stderr)
    elif busy_avoided:
        details = ", ".join(f"GPU {gpu} ({advice.live_states[gpu].reason})" for gpu in busy_avoided)
        print(f"note: avoided currently busy {details}")

    capacities = advice.memory_capacities_mb
    if not capacities:
        if reservation.get("mode") == MODE_SHARED and expected_memory_mb is None:
            print("note: GPU memory telemetry unavailable; shared memory admission used record limit only")
        return

    snapshots = {item.index: item for item in advice.snapshots}
    headroom = shared_memory_headroom_for_reservation(
        list_active(store.load(), parse_iso(reservation["start_at"])),
        reservation,
        capacities,
        config.max_shared_users,
        config.shared_memory_reserve_mb,
    )
    memory_parts = []
    for gpu in selected:
        item = snapshots.get(gpu)
        if item is None or not item.memory_total_mb:
            continue
        now_free = max(0, item.memory_total_mb - item.memory_used_mb)
        projected = headroom.get(gpu)
        projected_text = f", projected-headroom={_format_memory_mb(projected)}" if projected is not None else ""
        memory_parts.append(f"GPU {gpu} now-free={_format_memory_mb(now_free)}{projected_text}")
    if memory_parts:
        print("memory: " + "; ".join(memory_parts))
    if reservation.get("mode") == MODE_SHARED and expected_memory_mb is None:
        assumptions = [
            max(1, (capacities[gpu] - config.shared_memory_reserve_mb) // config.max_shared_users)
            for gpu in selected
            if gpu in capacities
        ]
        if assumptions:
            print(
                f"note: --mem omitted; assumed {_format_memory_mb(min(assumptions))} per GPU "
                f"from shared limit {config.max_shared_users}"
            )


def _format_memory_mb(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    if value >= 1024:
        return f"{value / 1024:.1f}GiB"
    return f"{value}MiB"


def _current_actor() -> Actor:
    return Actor(uid=os.getuid(), username=getpass.getuser())


def _short_id(reservation: dict) -> str:
    return str(reservation.get("id", ""))[:8]


def _own_active_reservations(store: LedgerStore, actor: Actor) -> List[dict]:
    return [item for item in list_active(store.load()) if int(item.get("uid")) == actor.uid]


def _own_job_reservations(store: LedgerStore, actor: Actor) -> List[dict]:
    result = [
        item
        for item in store.load().get("reservations", [])
        if int(item.get("uid", -1)) == actor.uid and isinstance(item.get("job"), dict)
    ]
    return sorted(result, key=lambda item: (str(item.get("start_at", "")), str(item.get("id", ""))))


def _resolve_job_reservation(reservations: List[dict], token: str) -> dict:
    if not token:
        raise BookingError("job number or short id is required")
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(reservations):
            return reservations[index - 1]
    matches = [item for item in reservations if str(item.get("id", "")).startswith(token)]
    if not matches:
        raise BookingError(f"job not found for current user: {token}")
    if len(matches) > 1:
        raise BookingError(f"ambiguous job id {token}")
    return matches[0]


def _prompt_reservation_token(store: LedgerStore, actor: Actor, action: str) -> str:
    mine = _own_active_reservations(store, actor)
    if not mine:
        raise BookingError("you have no active reservations")
    print("Your active reservations:")
    for index, reservation in enumerate(mine, 1):
        print(
            f"  {index}. {_short_id(reservation)} {reservation['mode']} "
            f"GPU={','.join(map(str, reservation.get('gpus', [])))} "
            f"{format_local_range(reservation['start_at'], reservation['end_at'])}"
        )
    token = input(f"reservation to {action} (number or short id): ").strip()
    if not token:
        raise BookingError("reservation id is required")
    return token


def _resolve_own_reservation_id(store: LedgerStore, token: str, actor: Actor) -> str:
    mine = _own_active_reservations(store, actor)
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(mine):
            return mine[index - 1]["id"]
    matches = [item for item in mine if str(item.get("id", "")).startswith(token)]
    if not matches:
        raise BookingError(f"reservation not found for current user: {token}")
    if len(matches) > 1:
        choices = ", ".join(_short_id(item) for item in matches)
        raise BookingError(f"ambiguous reservation id {token}; matches: {choices}")
    return matches[0]["id"]


def _get_reservation(store: LedgerStore, reservation_id: str) -> dict:
    for reservation in list_active(store.load()):
        if reservation.get("id") == reservation_id:
            return reservation
    raise BookingError("reservation not found")


def _print_help(file=None) -> None:
    file = file or sys.stdout
    print(
        """usage:
  bk
  bk <count> <duration> [--gpu 0,1] [--start ISO]
  bk <count> <duration> -- <command> [args...]
  bk s <count> <duration>       shared (shared/auto also accepted)
  bk x <count> <duration>       exclusive
  bk t                          TUI
  bk m [--once]                 monitor
  bk u [--rollups]              usage records
  bk w                          run this UID's scheduled jobs
  bk j                          list this UID's jobs
  bk jl <number_or_short_id>    show a job log
  bk jr <number_or_short_id>    retry a failed job
  bk a                          guided add
  bk e [number_or_short_id]     edit
  bk d <number_or_short_id>     delete
  bk l                          list
  bk reset --yes
  bk list
  bk log
  bk doctor

duration examples: 30m, 1h30m, 1d
shared memory: --mem 12g (expected memory per GPU)
default mode: shared
omitted --start: queue to the earliest available slot
explicit --start: exact time, no automatic move
default interaction: plain prompt, no fullscreen terminal takeover
""",
        file=file,
    )


def _print_shell_help() -> None:
    print(
        """Commands:
  st | status               show GPU summary and active reservations
  1 4h [--gpu 0]            shared booking, default mode
  s 1 4h [--gpu 0]          shared booking
  x 1 4h [--gpu 0]          exclusive booking
  a | add                   guided booking prompts
  e <number|short_id>       modify your reservation
  d <number|short_id>       cancel your reservation
  l | list                  list active reservations
  lg | log                  show your operation log
  dr | doctor               report policy violations in the ledger
  m | monitor               continuously audit GPU process usage
  u [--rollups]             show recent usage events or minute rollups
  w | worker                execute only this UID's due jobs
  j | jobs                  list scheduled job states
  jl <number|short_id>      show a job log
  jr <number|short_id>      retry a failed job
  reset --yes               clear ledger, logs, and backups in this data dir
  t | tui                   open full-screen TUI
  quit                      exit
"""
    )


def _print_status(config: Config, store: LedgerStore) -> None:
    now = utc_now()
    active = list_active(store.load(), now)
    gpu_snapshots = snapshot(config)
    usage_by_gpu = classify_process_usage(gpu_snapshots, active, now)
    print("GPU summary")
    for gpu in gpu_snapshots:
        if gpu.memory_total_mb:
            mem = f"{gpu.memory_used_mb}/{gpu.memory_total_mb} MiB"
        else:
            mem = "unknown"
        util = f"{gpu.utilization_percent}%" if gpu.utilization_percent is not None else "unknown"
        rows = usage_by_gpu.get(gpu.index, [])
        violations = sum(1 for item in rows if item.violation)
        print(
            f"  GPU {gpu.index}: {gpu.name} util={util} mem={mem} "
            f"processes={len(rows)} violations={violations} source={gpu.source}"
        )
        for item in rows:
            process = item.process
            sm = f"{process.sm_utilization_percent}%" if process.sm_utilization_percent is not None else "-"
            print(
                f"    pid={process.pid} uid={process.uid if process.uid is not None else '?'} "
                f"user={process.username} sm={sm} mem={process.gpu_memory_mb}MiB "
                f"state={item.status} cmd={process.command or '?'}"
            )

    print()
    _print_timeline(config, store)
    print("Active reservations")
    if not active:
        print("  none")
    else:
        actor = _current_actor()
        mine = _own_active_reservations(store, actor)
        mine_index = {reservation["id"]: index + 1 for index, reservation in enumerate(mine)}
        for reservation in active:
            gpus = ",".join(str(item) for item in reservation.get("gpus", []))
            index = mine_index.get(reservation["id"], "-")
            print(
                f"  {index:>2} {_short_id(reservation)} {reservation['mode']:<9} "
                f"GPU={gpus:<7} {reservation['username']} "
                f"job={reservation.get('job', {}).get('status', '-'):<10} "
                f"{format_local_range(reservation['start_at'], reservation['end_at'])}"
            )
    print()


def _print_timeline(config: Config, store: LedgerStore) -> None:
    now = utc_now()
    hours = min(config.timeline_hours, 24)
    active = list_active(store.load(), now)
    actor = _current_actor()
    print(f"Timeline (next {hours}h, local)")
    label = "      " + " ".join((now + timedelta(hours=i)).astimezone().strftime("%H") for i in range(hours))
    print(label)
    for gpu in range(config.gpu_count):
        cells = []
        for offset in range(hours):
            slot_start = now + timedelta(hours=offset)
            slot_end = slot_start + timedelta(hours=1)
            overlapping = [
                item
                for item in active
                if gpu in item.get("gpus", [])
                and parse_iso(item["start_at"]) < slot_end
                and slot_start < parse_iso(item["end_at"])
            ]
            if not overlapping:
                cells.append(".")
            elif any(int(item.get("uid")) == actor.uid for item in overlapping):
                cells.append("M")
            elif any(item.get("mode") == MODE_EXCLUSIVE for item in overlapping):
                cells.append("X")
            else:
                records = [item for item in overlapping if item.get("mode") == MODE_SHARED]
                cells.append(str(min(len(records), 9)))
        print(f"GPU{gpu:<2} " + " ".join(cells))
    print("Legend: . free, M mine, X exclusive, 1-9 shared record count")
    print()
