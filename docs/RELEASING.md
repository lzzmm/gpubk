# Release Checklist

## One-time trusted-publisher setup

GPUBK publishes without a stored PyPI API token. Before the first release:

1. In GitHub, create the `pypi` environment. Require the `lzzmm` reviewer, restrict it to the `main` branch and `v*.*.*` tags, and keep `main` protected by the complete CI check set.
2. In PyPI's trusted-publisher settings, enter project `gpubk`, owner `lzzmm`, repository `GPUBK`, workflow `release.yml`, and environment `pypi`.
3. Protect `main` with pull requests, strict GitHub Actions checks, linear history, resolved conversations, and force-push/deletion prevention. Protect version tags from update and deletion, and enable private vulnerability reporting.
4. Enable GitHub release immutability before the first GitHub Release. Immutable releases lock the tag and uploaded assets after publication, so all assets must be attached while the release is still a draft.
5. Create the repository Actions variable `PYPI_RELEASE_ENABLED`, initially set to `false`. Set it to `true` only after the trusted publisher and environment protection are verified.

The publish job receives `id-token: write` only inside its protected environment and fails closed behind the release-enabled variable. Do not add `PYPI_API_TOKEN`, `TWINE_PASSWORD`, or a long-lived upload token to repository secrets.

The workflow treats a disabled release gate as an error, not as a successful build-only run.
If a manual release exits immediately with a `*_RELEASE_ENABLED must be true` message,
update the named repository variable after re-confirming GitHub access, then start a new run.

## Every release

1. Confirm the owner-approved Apache-2.0 `LICENSE` is included in both wheel and sdist metadata.
2. Confirm the `lzzmm` author/maintainer metadata and `https://github.com/lzzmm/GPUbk` URLs in `pyproject.toml`.
3. Replace the candidate version in `src/bk/__init__.py` with the final version and replace `Unreleased` in the matching `CHANGELOG.md` heading with the release date. Package metadata reads the version from `bk.__version__`.
4. Run core tests:

   ```bash
   python -m pip install --upgrade pip
   python -m compileall -q src tests benchmarks tools
   ruff check src tests benchmarks tools
   PYTHONPATH=src python benchmarks/scheduler_queue.py
   PYTHONPATH=src python benchmarks/usage_store.py
   coverage run -m unittest discover -s tests -p 'test_*.py'
   coverage combine
   coverage report
   ```

5. Install optional dependencies in a clean environment and run the MCP protocol test:

   ```bash
   python -m pip install '.[mcp,gpu]' build twine pyyaml
   python -m unittest tests.test_mcp_server tests.test_mcp_integration
   python /path/to/skill-creator/scripts/quick_validate.py src/bk/data/codex-skill/gpubk
   ```

6. Build and inspect artifacts:

   ```bash
   rm -rf build dist
   python -m build
   python -m twine check dist/*
   check-wheel-contents dist/*.whl
   validate-pyproject pyproject.toml
   ```

7. Install the wheel into a fresh environment. Verify `bk --version`, core zero-dependency
   installation, `bk skill install`, and `bk-mcp` with the MCP extra. In an isolated no-GPU
   simulation, create a scheduled-command reservation from the wheel, run `bk worker --once`,
   and verify its terminal state, injected GPU environment, private log, and spec cleanup. Also
   install the most recent public GPUBK release in a separate environment, create a real ledger,
   upgrade it with the new wheel, confirm read-only checks do not rewrite the old files, and then
   create a reservation using the new scheduling fields.
8. As the configured `monitor_uid`, run `bk doctor --probe --strict` and bounded read-only
   NVML/context/recommendation checks on a real multi-GPU host with an isolated `BK_DATA_DIR`.
   The `process-identity` probe must demonstrate numeric ownership visibility for a process from
   another UID; create no GPU workload merely for this check. Confirm every GPU reports
   `capabilities.stable_device_identifier=true`; after one bounded `bk monitor --once` sample,
   confirm `collector.stable_device_identifier_gap=[]` and
   `collector.process_identity_gap=[]`. Do not start workloads or services during release
   validation.

   Prefer the local SSH orchestrator. It downloads and verifies the exact PyPI wheelhouse,
   leaves production state untouched, and retrieves a digest-verified report even when an
   acceptance check fails:

   ```bash
   python3 tools/remote_acceptance.py USER@GPU-HOST \
     --remote-python /opt/gpubk/bin/python \
     --system-bk /usr/local/bin/bk \
     --sudo
   ```

   Archive the resulting `acceptance-reports/` directory with the release evidence. Complete
   its four listed manual checks before promoting a release candidate to a final version. For
   the approved live-workload check, activate a CUDA PyTorch environment as an ordinary user and
   run `bk usage demo`; retain its summary, one-minute samples, and process events with the report.

   For a cluster-capable release, also test the exact candidate wheel on at least two distinct
   SSH hosts. This second runner uses simulated GPUs and private temporary ledgers, so it may run
   before the approved live workload and without `sudo`:

   ```bash
   python3 tools/cluster_acceptance.py USER@GPU-HOST-A USER@GPU-HOST-B \
     --wheel dist/gpubk-VERSION-py3-none-any.whl
   ```

   Require a PASS report, distinct stable node IDs, and successful remote cleanup. This proves
   package installation, SSH federation, routing, replay, and cancellation; it does not replace
   the real NVML, second-user authorization, workload, service restart, or reboot checks.
9. Commit and push the release metadata through a pull request, wait for `CI` to pass, and merge it to `main`. Create the annotated tag from that exact `main` commit:

   ```bash
   VERSION=$(PYTHONPATH=src python -c 'from bk import __version__; print(__version__)')
   git tag -a "v$VERSION" -m "GPUBK $VERSION"
   git push origin "v$VERSION"
   ```

   Do not merge another change before the tag workflow starts. Production
   release tags must be annotated and point exactly at the current
   `origin/main` tip; an older commit that merely exists in main history is
   rejected.

10. The `Release` workflow rebuilds and tests the tag, records the wheel and sdist SHA-256
    digests, then requests approval from the protected `pypi` environment before uploading the
    exact artifacts.
11. Approve `pypi` and wait for its automatic digest comparison and installation smoke test.
    The workflow then creates a draft GitHub Release for the same tag, attaches the exact wheel,
    sdist, and checksum file, downloads the draft assets, rejects missing or unexpected names,
    verifies the recorded distribution hashes, and only then publishes the draft. Never rebuild
    or upload an artifact locally for promotion. If this final job is interrupted, use GitHub's
    **Re-run failed jobs** action; it resumes the existing draft and refuses to replace an already
    published release. Release immutability locks the published tag and assets and creates a
    release attestation.

Return `PYPI_RELEASE_ENABLED` to `false` after the release is published.

The workflow rejects a tag that is lightweight, points anywhere other than the
current `main` tip, is a prerelease, does not match the package, or still has an
`Unreleased` changelog heading. It also rejects an already-existing PyPI version
before requesting an upload. A normal final publication therefore starts from a
new final-version tag.
