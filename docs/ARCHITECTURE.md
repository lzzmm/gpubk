# Architecture Rules

GPUBK uses a single source of truth for facts that must change together.

| Fact | Authoritative source | Other representations |
| --- | --- | --- |
| Package and repository metadata | `pyproject.toml` | README and release links are checked against it |
| Worker commands and remediation | `src/bk/worker_guidance.py` | CLI, login notice, cluster checks, JSON, and docs consume or validate it |
| Usage API schema | `src/bk/usage_schema.py` | CLI, MCP, and Agent capabilities import the constant |
| Cluster API schema | `src/bk/cluster.py` | Cluster administration imports the constant |
| Reservation and audit state | Broker-owned versioned ledger | Views derive data through public APIs; they do not edit storage files |

Rules for changes:

1. Do not repeat a behavioral constant merely to make a message convenient.
   Import the domain constant or render a small shared value object.
2. Do not store a value that can be derived safely from authoritative state.
3. When an external format requires duplication, add a contract test that reads
   the authority and validates every mirror.
4. Keep context-specific language local. SSOT applies to facts and behavior, not
   to every sentence shown to users.
5. Do not merge superficially similar concepts. For example, `r` means `run` in
   the normal CLI and `refresh` in the interactive prompt; those alias tables are
   intentionally separate.

`tests/test_architecture.py` enforces the highest-risk boundaries and should be
extended whenever a new public schema, generated command, or mirrored metadata
field is introduced.
