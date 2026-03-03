# AGENTS.md

## Cursor Cloud specific instructions

This is a single-file Python 3 CLI tool (`patch_tool.py`) for git patch management. It has **zero external dependencies** (stdlib only).

### Runtime requirements

- **Python 3** (>=3.6, type hints used) — pre-installed
- **Git CLI** — pre-installed
- A git repository with history is needed to exercise any subcommand

### Linting

No project-level lint config exists. Use `ruff` (installed via `pip install ruff`):

```
python3 -m ruff check patch_tool.py
```

There are 3 pre-existing F841 warnings (unused variables in `cmd_sync_meta`) — these are known and intentional (destructuring assignment).

### Running / testing

The tool provides 3 subcommands: `check`, `cherry-pick`, `sync-meta`. Run with:

```
python3 patch_tool.py --help
python3 patch_tool.py <subcommand> --help
```

- `cherry-pick` is interactive (blocks on `input()` when conflicts occur) — not suitable for non-interactive CI.
- `sync-meta` rewrites git history; always use `--dry-run` first for safe testing.
- `check` is the safest subcommand for automated testing — reads commit titles from a file, writes a report.

### No build step

There is no build step, no `pyproject.toml`, no `requirements.txt`, and no test suite in the repo.
