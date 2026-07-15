from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import List, Optional, Sequence

from .cluster import load_cluster_config
from .cluster_transport import run_bounded_command
from .models import MODE_EXCLUSIVE, MODE_SHARED, BookingError


MIN_CLUSTER_MCP_TIMEOUT_SECONDS = 30.0
MAX_CLUSTER_MCP_TIMEOUT_SECONDS = 600.0
MAX_CLUSTER_MCP_ERROR_CHARS = 1000


class ClusterMcpBackend:
    """MCP facade over the versioned cluster CLI and its routing guarantees."""

    def context(self) -> dict:
        return self._call(["status", "--json"])

    def check(self, require_jobs: bool = False) -> dict:
        arguments = ["check"]
        if require_jobs:
            arguments.append("--jobs")
        arguments.append("--json")
        return self._call(arguments)

    def recommend(
        self,
        count: int,
        duration: str,
        mode: str = "shared",
        start: Optional[str] = None,
        gpus: Optional[List[int]] = None,
        exclude_gpus: Optional[List[int]] = None,
        expected_memory: Optional[str] = None,
        share: Optional[int] = None,
    ) -> dict:
        arguments = [
            "recommend",
            str(count),
            duration,
            "--mode",
            _normalize_mode(mode),
        ]
        _append_booking_options(
            arguments,
            start=start,
            gpus=gpus,
            exclude_gpus=exclude_gpus,
            expected_memory=expected_memory,
            share=share,
        )
        arguments.append("--json")
        return self._call(arguments)

    def book(
        self,
        count: int,
        duration: str,
        operation_id: str,
        mode: str = "shared",
        start: Optional[str] = None,
        gpus: Optional[List[int]] = None,
        exclude_gpus: Optional[List[int]] = None,
        expected_memory: Optional[str] = None,
        share: Optional[int] = None,
        command: Optional[List[str]] = None,
    ) -> dict:
        _require_operation_id(operation_id)
        arguments = [
            "book",
            str(count),
            duration,
            "--mode",
            _normalize_mode(mode),
            "--op-id",
            operation_id,
        ]
        _append_booking_options(
            arguments,
            start=start,
            gpus=gpus,
            exclude_gpus=exclude_gpus,
            expected_memory=expected_memory,
            share=share,
        )
        arguments.append("--json")
        if command is not None:
            if not command or any(not isinstance(item, str) or not item for item in command):
                raise BookingError("command must be a non-empty argv list")
            arguments += ["--", *command]
        return self._call(arguments)

    def usage(
        self,
        since: str = "24h",
        resolution: str = "auto",
        limit: int = 1000,
    ) -> dict:
        return self._call(
            [
                "usage",
                "--since",
                since,
                "--resolution",
                resolution,
                "--limit",
                str(limit),
                "--json",
                "--compact",
            ]
        )

    def edit(
        self,
        reservation_id: str,
        operation_id: str,
        duration: Optional[str] = None,
        mode: Optional[str] = None,
        start: Optional[str] = None,
        gpus: Optional[List[int]] = None,
        exclude_gpus: Optional[List[int]] = None,
        count: Optional[int] = None,
        expected_memory: Optional[str] = None,
        allow_queue: bool = False,
        share: Optional[int] = None,
    ) -> dict:
        _require_qualified_reservation_id(reservation_id)
        _require_operation_id(operation_id)
        if not allow_queue and all(
            value is None
            for value in (
                duration,
                mode,
                start,
                gpus,
                exclude_gpus,
                count,
                expected_memory,
                share,
            )
        ):
            raise BookingError("cluster edit requires at least one changed field")
        arguments = ["edit", reservation_id, "--op-id", operation_id]
        for flag, value in (
            ("--duration", duration),
            ("--start", start),
            ("--count", str(count) if count is not None else None),
            ("--mode", _normalize_mode(mode) if mode is not None else None),
            ("--mem", expected_memory),
            ("--share", str(share) if share is not None else None),
        ):
            if value is not None:
                arguments += [flag, value]
        _append_gpu_options(arguments, gpus, exclude_gpus)
        if allow_queue:
            arguments.append("--queue")
        arguments.append("--json")
        return self._call(arguments)

    def cancel(self, reservation_id: str, operation_id: str) -> dict:
        _require_qualified_reservation_id(reservation_id)
        _require_operation_id(operation_id)
        return self._call(
            [
                "cancel",
                reservation_id,
                "--op-id",
                operation_id,
                "--json",
            ]
        )

    def _call(self, arguments: Sequence[str]) -> dict:
        command = [sys.executable, "-m", "bk", "cluster", *arguments]
        environment = dict(os.environ)
        environment["NO_COLOR"] = "1"
        try:
            returncode, stdout, stderr = run_bounded_command(
                command,
                environment=environment,
                timeout_seconds=self._timeout_seconds(),
            )
        except subprocess.TimeoutExpired as exc:
            raise BookingError("cluster MCP request timed out") from exc
        except ValueError as exc:
            raise BookingError("cluster MCP response exceeded the safe output limit") from exc
        except OSError as exc:
            raise BookingError(f"cannot start cluster MCP request: {exc}") from exc
        payload = _decode_payload(stdout)
        if returncode == 3 and payload is not None and payload.get("kind") in {
            "cluster-check",
            "cluster-context",
            "cluster-usage",
        }:
            return payload
        if returncode != 0:
            detail = _safe_error(stderr or stdout)
            raise BookingError(
                f"cluster request failed{f': {detail}' if detail else ''}"
            )
        if payload is None:
            _decode_payload(stdout, raise_on_error=True)
            raise BookingError("cluster request returned invalid JSON")
        return payload

    @staticmethod
    def _timeout_seconds() -> float:
        config = load_cluster_config()
        node_timeout = max(
            (node.timeout_seconds for node in config.enabled_nodes),
            default=MIN_CLUSTER_MCP_TIMEOUT_SECONDS,
        )
        return min(
            MAX_CLUSTER_MCP_TIMEOUT_SECONDS,
            max(MIN_CLUSTER_MCP_TIMEOUT_SECONDS, node_timeout * 5 + 10),
        )


def _append_booking_options(
    arguments: list[str],
    *,
    start: Optional[str],
    gpus: Optional[List[int]],
    exclude_gpus: Optional[List[int]],
    expected_memory: Optional[str],
    share: Optional[int],
) -> None:
    for flag, value in (
        ("--start", start),
        ("--mem", expected_memory),
        ("--share", str(share) if share is not None else None),
    ):
        if value is not None:
            arguments += [flag, value]
    _append_gpu_options(arguments, gpus, exclude_gpus)


def _append_gpu_options(
    arguments: list[str],
    gpus: Optional[List[int]],
    exclude_gpus: Optional[List[int]],
) -> None:
    if gpus is not None and exclude_gpus is not None:
        raise BookingError("gpus and exclude_gpus are mutually exclusive")
    if gpus is not None:
        arguments += ["--gpu", _gpu_list(gpus)]
    if exclude_gpus is not None:
        arguments += ["--exclude-gpu", _gpu_list(exclude_gpus)]


def _gpu_list(values: List[int]) -> str:
    if not values:
        raise BookingError("GPU list must not be empty")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise BookingError("GPU indexes must be non-negative integers")
    return ",".join(str(value) for value in values)


def _normalize_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"s", MODE_SHARED}:
        return MODE_SHARED
    if normalized in {"x", MODE_EXCLUSIVE}:
        return MODE_EXCLUSIVE
    raise BookingError("mode must be s/shared or x/exclusive")


def _require_operation_id(operation_id: str) -> None:
    if not operation_id:
        raise BookingError("operation_id is required for retry-safe cluster MCP writes")


def _require_qualified_reservation_id(reservation_id: str) -> None:
    node, separator, token = reservation_id.partition("/")
    if not separator or not node or not token:
        raise BookingError("use a node-qualified reservation ID such as gpu-a/1a2b3c")


def _safe_error(raw: bytes) -> str:
    text = raw[-MAX_CLUSTER_MCP_ERROR_CHARS * 4 :].decode("utf-8", errors="replace")
    printable = "".join(character if character.isprintable() else " " for character in text)
    detail = " ".join(printable.split())
    if detail.startswith("bk:"):
        detail = detail[3:].lstrip()
    return detail[-MAX_CLUSTER_MCP_ERROR_CHARS:]


def _decode_payload(raw: bytes, *, raise_on_error: bool = False) -> Optional[dict]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if raise_on_error:
            raise BookingError("cluster request returned invalid JSON") from exc
        return None
    if not isinstance(payload, dict):
        if raise_on_error:
            raise BookingError("cluster request returned a non-object JSON document")
        return None
    return payload
