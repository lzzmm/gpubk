# Release Checklist

## One-time trusted-publisher setup

GPUbk publishes without a stored PyPI API token. Before the first release:

1. In GitHub, create environments named `testpypi` and `pypi`. Require a reviewer for `pypi` and restrict it to protected version tags. GitHub Free does not offer required reviewers on private repositories; make the repository public or upgrade before enabling production publishing.
2. In TestPyPI's trusted-publisher settings, add owner `lzzmm`, repository `gpubk`, workflow `release.yml`, and environment `testpypi`.
3. In PyPI's pending trusted-publisher form, enter project `gpubk`, owner `lzzmm`, repository `gpubk`, workflow `release.yml`, and environment `pypi`.
4. Protect `main` and version tags in GitHub, and enable private vulnerability reporting before making the repository public.
5. Create repository Actions variables `TESTPYPI_RELEASE_ENABLED` and `PYPI_RELEASE_ENABLED`, initially set to `false`. Set each to `true` only after its trusted publisher and environment protection are verified.

The publish jobs receive `id-token: write` only inside their protected environments. Both jobs also fail closed behind their corresponding release-enabled variable. Do not add `PYPI_API_TOKEN`, `TWINE_PASSWORD`, or a long-lived upload token to repository secrets.

## Every release

1. Confirm the owner-approved Apache-2.0 `LICENSE` is included in both wheel and sdist metadata.
2. Confirm the `lzzmm` author/maintainer metadata and `https://github.com/lzzmm/gpubk` URLs in `pyproject.toml`.
3. Update `src/bk/__init__.py` and replace `Unreleased` in the matching `CHANGELOG.md` heading with the release date. Package metadata reads the version from `bk.__version__`.
4. Run core tests:

   ```bash
   python -m compileall -q src tests benchmarks
   ruff check src tests benchmarks
   PYTHONPATH=src python benchmarks/scheduler_queue.py
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

7. Install the wheel into a fresh environment. Verify `bk --version`, core zero-dependency installation, `bk skill install`, and `bk-mcp` with the MCP extra.
8. Run bounded read-only NVML/context/recommendation checks on a real multi-GPU host with an isolated `BK_DATA_DIR`. Do not start workloads or services during release validation.
9. Commit and push the release metadata, wait for `CI` to pass, then create and push an annotated version tag:

   ```bash
   git tag -a v0.1.0 -m "GPUbk 0.1.0"
   git push origin v0.1.0
   ```

10. The `Release` workflow rebuilds and tests the tag, uploads that artifact to TestPyPI, and installs it back from TestPyPI. Only then does the protected `pypi` environment request approval to promote the exact same wheel and sdist.
11. Approve `pypi`, wait for the PyPI installation smoke test, then create the GitHub Release from the same tag. Never rebuild an artifact locally for promotion.

Manual `Release` dispatches stop after TestPyPI verification. Use them only with a unique pre-release version such as `0.1.0rc1`; uploaded versions cannot be replaced.
