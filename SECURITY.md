# Security Policy

## Scope

GPUbk is a cooperative POSIX scheduler. It protects ledger integrity and enforces application-level UID ownership when every writer uses `bk`. It is not a kernel GPU access-control system and cannot prevent a user with direct device permission from launching CUDA outside GPUbk.

Supported security boundaries:

- MCP, CLI, TUI, and worker identity comes from the local process UID.
- Shared-ledger writes use an advisory lock, WAL journal, atomic replacement, and idempotent audit events.
- Shared data files reject symbolic links, FIFOs, devices, and other non-regular leaf files before reading or writing.
- Scheduled command arguments live in UID-owned `0600` specs, not the shared ledger.
- External allocators can advise ordering but cannot bypass deterministic validation.
- External allocator output is bounded and allocator timeouts terminate the isolated process group.
- Telemetry stores only sanitized workload labels and keyed identities, not raw arguments,
  environments, stdout, or absolute script paths.
- Closed telemetry partitions carry record counts and SHA-256 checksums. Unknown future
  fields block compaction instead of being discarded.

Administrator responsibilities:

- Configure a dedicated Unix group and correct setgid directory permissions.
- On a shared deployment, make `config.json` root-owned and not writable by group or other users; GPUbk rejects untrusted configuration files.
- Verify `flock` and atomic rename behavior on the actual NFS/FUSE mount.
- Control `/dev/nvidia*` access separately if hard enforcement is required.
- Run MCP over per-user local stdio unless an authenticated remote transport is deliberately engineered.
- Treat allocator commands as trusted code running with the configuring user's privileges.
- Run exactly one trusted telemetry writer per server. Do not expose `TelemetrySink` as an
  unauthenticated network write endpoint or allow users to submit records for arbitrary UIDs.
- Review generated user units before enabling them. `bk service install` captures absolute data
  and private job-log paths; reinstall the unit after those paths change.
- Back up the complete `usage/` directory, including `workload.key`; losing only that key
  prevents stable workload identity from continuing and intentionally fails closed.

## Reporting

Do not include credentials, private command lines, production ledger contents, or user data in a public issue. Contact the package maintainers through the private security-reporting channel associated with the source repository or distribution. A public repository should enable private vulnerability reporting before the first release.
