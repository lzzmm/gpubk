from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .advisor import GpuAdvice
from .config import Config
from .models import Actor
from .scheduler import list_active
from .storage import LedgerStore
from .timeparse import to_iso


ALLOCATOR_SCHEMA_VERSION = "bk.allocator.v1"
MAX_ALLOCATOR_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class AllocatorDecision:
    order: List[int]
    scores: Dict[int, float]
    source: str
    reason: str = ""
    warning: str = ""


def apply_external_allocator(
    config: Config,
    store: LedgerStore,
    actor: Actor,
    advice: GpuAdvice,
    *,
    count: int,
    duration_seconds: int,
    start_at: datetime,
    mode: str,
    expected_memory_mb: Optional[int],
) -> AllocatorDecision:
    if not config.allocator_command:
        return AllocatorDecision(list(advice.order), dict(advice.scores), "builtin")
    payload = _allocator_payload(
        config,
        store,
        actor,
        advice,
        count=count,
        duration_seconds=duration_seconds,
        start_at=start_at,
        mode=mode,
        expected_memory_mb=expected_memory_mb,
    )
    try:
        completed = subprocess.run(
            list(config.allocator_command),
            input=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=config.allocator_timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1][:200] if completed.stderr.strip() else "no stderr"
            raise ValueError(f"allocator exited {completed.returncode}: {detail}")
        if len(completed.stdout.encode("utf-8")) > MAX_ALLOCATOR_OUTPUT_BYTES:
            raise ValueError("allocator output exceeded 64 KiB")
        response = json.loads(completed.stdout)
        order, reason = _validate_allocator_response(response, config.gpu_count)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return AllocatorDecision(
            list(advice.order),
            dict(advice.scores),
            "builtin-fallback",
            warning=f"external allocator ignored: {exc}",
        )

    adjusted_scores = dict(advice.scores)
    for rank, gpu in enumerate(order):
        adjusted_scores[gpu] = round(adjusted_scores[gpu] + rank * config.allocator_weight, 3)
    return AllocatorDecision(order, adjusted_scores, "external", reason=reason)


def _allocator_payload(
    config: Config,
    store: LedgerStore,
    actor: Actor,
    advice: GpuAdvice,
    *,
    count: int,
    duration_seconds: int,
    start_at: datetime,
    mode: str,
    expected_memory_mb: Optional[int],
) -> dict:
    reservations = list_active(store.load(), start_at)
    return {
        "schema_version": ALLOCATOR_SCHEMA_VERSION,
        "kind": "allocation_request",
        "generated_at": to_iso(advice.generated_at),
        "actor": {"uid": actor.uid},
        "request": {
            "count": count,
            "duration_seconds": duration_seconds,
            "start_at": to_iso(start_at),
            "mode": mode,
            "expected_memory_mb_per_gpu": expected_memory_mb,
        },
        "policy": {
            "gpu_count": config.gpu_count,
            "max_shared_reservations_per_gpu": config.max_shared_users,
            "shared_memory_reserve_mb": config.shared_memory_reserve_mb,
            "local_score_is_authoritative_for_safety": True,
            "allocator_weight": config.allocator_weight,
        },
        "builtin_advice": advice.as_dict(),
        "reservations": [
            {
                "uid": item.get("uid"),
                "gpus": list(item.get("gpus", [])),
                "mode": item.get("mode"),
                "start_at": item.get("start_at"),
                "end_at": item.get("end_at"),
                "expected_memory_mb_per_gpu": item.get("expected_memory_mb"),
            }
            for item in reservations
        ],
        "response_contract": {
            "schema_version": ALLOCATOR_SCHEMA_VERSION,
            "gpu_order": "permutation of every configured GPU index",
            "reason": "optional privacy-safe text, max 500 chars",
        },
    }


def _validate_allocator_response(response: object, gpu_count: int) -> tuple[List[int], str]:
    if not isinstance(response, dict):
        raise ValueError("allocator response must be a JSON object")
    if response.get("schema_version") != ALLOCATOR_SCHEMA_VERSION:
        raise ValueError("allocator schema version mismatch")
    raw_order = response.get("gpu_order")
    if not isinstance(raw_order, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_order):
        raise ValueError("gpu_order must be an integer array")
    if raw_order != list(dict.fromkeys(raw_order)) or sorted(raw_order) != list(range(gpu_count)):
        raise ValueError("gpu_order must be a permutation of configured GPUs")
    reason = str(response.get("reason", "")).strip()
    if len(reason) > 500 or any(ord(char) < 32 and char not in "\t" for char in reason):
        raise ValueError("allocator reason is invalid")
    return list(raw_order), reason
