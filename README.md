# GPUbk

**English** | [简体中文](https://github.com/lzzmm/gpubk/blob/main/README.zh-CN.md)

GPUbk is a GPU booking tool for shared Linux servers. The package is named
`gpubk`; the command is the shorter `bk`.

It works offline, stores data in local files, and has no required runtime
dependencies. Users can book GPUs from a plain terminal prompt, a curses TUI,
JSON commands, or an optional local MCP server.

## What It Covers

- Shared and exclusive reservations in 5-minute intervals.
- Automatic queueing, live GPU awareness, and per-GPU VRAM budgets.
- A compact timeline that works on dark and light terminals.
- Scheduled commands with automatic `CUDA_VISIBLE_DEVICES`.
- NVML process monitoring and recent-load history.
- Stable JSON, MCP tools, a bundled Codex Skill, and an optional external allocator.
- Atomic file transactions, UID ownership checks, backups, and an append-only audit log.

GPUbk is a cooperative scheduler. It does not replace Linux device permissions
or stop a user with direct access to `/dev/nvidia*` from bypassing the tool.

## Install

GPUbk requires Python 3.10 or newer.

```bash
python3 -m pip install gpubk          # core CLI and TUI; no dependencies
python3 -m pip install 'gpubk[gpu]'  # add low-overhead NVML telemetry
python3 -m pip install 'gpubk[mcp]'  # add the local MCP server
python3 -m pip install 'gpubk[all]'  # both optional extras
```

Verify the installation:

```bash
bk --version
bk --help
```

## Book GPUs

Shared mode is the default:

```bash
bk 1 30m                         # one GPU for 30 minutes
bk 2 1h30m --mem 12g            # 12 GiB expected VRAM per GPU
bk s 1 2h --gpu 3               # explicit shared mode on GPU 3
bk x 2 4h                        # exclusive mode
bk 1 1h --start 2026-07-12T20:00:00+08:00
```

Manage your reservations with a list number or short ID:

```bash
bk l
bk e 1 --duration 2h
bk d 1
bk doctor                         # read-only ledger checks
```

Scheduling rules are intentionally small:

- Start times and durations use 5-minute boundaries.
- Without `--start`, GPUbk picks the earliest valid slot and prints `queued:`
  when the reservation starts later.
- With `--start`, the time is exact. A conflict returns an error instead of
  silently moving the reservation.
- Shared capacity is counted per overlapping reservation. Exclusive
  reservations cannot overlap anything.
- `--mem` is expected VRAM **per GPU**. Administrators can require it for all
  shared reservations.
- Times shown to users are local. The ledger stores UTC.

Automatic placement considers reservations, physical free VRAM, current GPU
processes, recent load, and near-future booking pressure. A process without a
reservation is reported and avoided when another suitable GPU is free.

## Terminal Interfaces

`bk` opens a normal line-oriented prompt and keeps the terminal background.
`bk t` opens the full-screen TUI.

```bash
bk
bk t
```

Useful TUI keys:

| Key | Action |
| --- | --- |
| `a` / `e` / `d` | Add, edit, or cancel a reservation |
| `Tab`, `↑`, `↓` | Move between reservations and GPU details |
| `←`, `→` | Browse the timeline; move time in Add/Edit |
| `Space` | Toggle the current GPU in Add/Edit |
| `+`, `-` | Change timeline zoom or reservation duration |
| `1`-`9` | Pick a GPU count and jump to the nearest valid slot |
| `s`, `x` | Switch between shared and exclusive in Add/Edit |
| `f`, `g` | Find any suitable GPUs, or keep the selected GPUs fixed |
| `n` | Return to the live `NOW` window |
| `c` | Toggle the dark/light theme |
| `?` | Open the paged help and quick tour |
| `Enter`, `Esc`, `q` | Submit, cancel the current action, or quit |

The timeline can show past reservations, but history is read-only. Add and Edit
always validate the selected interval again inside the locked scheduler
transaction.

## Run a Command at Reservation Time

Put the command after `--`:

```bash
bk 2 1h30m --mem 12g -- python train.py --config exp.yaml
bk j                    # list scheduled jobs
bk w                    # run this user's due jobs
```

The worker sets `CUDA_VISIBLE_DEVICES`, `CUDA_DEVICE_ORDER`,
`BK_RESERVATION_ID`, and `BK_RESERVED_GPUS`. Commands and working directories
stay in UID-owned `0600` job specs; they are not written to the shared ledger.
The worker uses `shell=False`. Use an explicit shell only when shell syntax is
required:

```bash
bk 1 30m -- sh -lc 'python train.py > train.log 2>&1'
```

For unattended jobs, each user can install the bundled systemd user unit:

```bash
bk service install worker
systemctl --user daemon-reload
systemctl --user enable --now bk-worker.service
```

## Monitoring and Placement

Install the `gpu` extra, then run a single sample or a low-overhead monitor:

```bash
bk m --once
bk m
bk u --rollups
```

NVML is initialized once and device handles are reused. The monitor records
bounded load summaries plus process start, stop, and authorization changes; it
does not append a full snapshot every second. Without NVML, GPUbk falls back to
`nvidia-smi` with less process detail.

Process status is based on the process UID and active reservation:
`ok`, `wrong-gpu`, `unreserved`, `unknown`, or `system`. Command lines are
reduced to safe labels before shared logging.

The monitor also has a user service:

```bash
bk service install monitor
systemctl --user daemon-reload
systemctl --user enable --now bk-monitor.service
```

## Agents and MCP

Agents should use the versioned JSON interface instead of parsing terminal text:

```bash
bk agent context --compact
bk agent recommend 2 1h30m --mem 12g --compact
bk 2 1h30m --mem 12g --op-id run-20260712-001 --json
bk agent edit 6e957ef1 --duration 2h --op-id edit-20260712-001 --compact
bk agent cancel 6e957ef1 --compact
```

Create and edit operations require a stable operation ID. An identical retry
returns `status=exists`; reusing the ID for a different write is rejected.
Recommendations are read-only. Identity always comes from the local process UID.

Run the optional stdio MCP server with:

```bash
bk-mcp                       # same as: bk mcp
bk skill install            # installs the bundled Codex Skill
```

The MCP server provides context, recommendation, create, list, edit, cancel,
and private job-log tools. It listens on stdio only; each user runs their own
process. Tool schemas include read-only, idempotent, destructive, and
closed-world annotations.

An administrator may also set `BK_ALLOCATOR_COMMAND` to a trusted local program
that reads `bk.allocator.v1` JSON and returns a GPU ordering. Its output is
advisory: every result still passes the built-in conflict, VRAM, time, UID, and
transaction checks. See the [Agent protocol](https://github.com/lzzmm/gpubk/blob/main/src/bk/data/codex-skill/gpubk/references/protocol.md).

## Shared Server Setup

Create one setgid directory for the lab group:

```bash
sudo install -d -m 2770 -o root -g gpuusers /data2/shared/bk
export BK_DATA_DIR=/data2/shared/bk
```

Put a root-owned `config.json` in that directory:

```json
{
  "gpu_count": 8,
  "max_shared_users": 2,
  "queue_search_hours": 168,
  "ledger_retention_days": 90,
  "require_shared_memory": true,
  "shared_memory_reserve_mb": 512,
  "file_mode": "0660",
  "dir_mode": "2770"
}
```

```bash
sudo chown root:gpuusers /data2/shared/bk/config.json
sudo chmod 0644 /data2/shared/bk/config.json
```

All users and user services must use the same `BK_DATA_DIR`. The first write
binds scheduling and storage policy into the ledger; clients with conflicting
settings fail closed. Verify `flock` and atomic rename on the actual NFS or FUSE
mount before deployment. Every writer must use GPUbk.

See [SECURITY.md](https://github.com/lzzmm/gpubk/blob/main/SECURITY.md) for the supported boundary, file safety, WAL
recovery, private job specs, MCP isolation, and administrator responsibilities.

## Try It Without a GPU

Booking and the TUI can run with simulated GPU count:

```bash
BK_DATA_DIR=/tmp/gpubk-demo BK_GPU_COUNT=4 bk t
BK_DATA_DIR=/tmp/gpubk-demo BK_GPU_COUNT=4 bk 1 30m
```

The cards show unknown hardware metrics, but scheduling, shared capacity, the
timeline, Add/Edit, logs, and Agent JSON remain usable.

## Development

```bash
python3 -m pip install -e '.[mcp,gpu]'
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src python3 benchmarks/scheduler_queue.py
```

Project documents: [Security](https://github.com/lzzmm/gpubk/blob/main/SECURITY.md) ·
[Release process](https://github.com/lzzmm/gpubk/blob/main/RELEASING.md) ·
[Changelog](https://github.com/lzzmm/gpubk/blob/main/CHANGELOG.md) ·
[Apache-2.0 license](https://github.com/lzzmm/gpubk/blob/main/LICENSE)
