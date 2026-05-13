# AGENTS.md

## Repository instructions

This repository contains small Python CLI tools for backport and patch workflows:

- `patch_tool.py`: git patch management with `check`, `cherry-pick`, and `sync-meta`.
- `pr_tool.py`: PR statistics collection for gitee, gitcode.net, gitcode.com, and atomgit URLs.
- `file_check_tool.py`: basename-based file presence checks across system directories.
- `excel_tool.py`: Excel row splitting and `Commit信息` commit-list export.
- `mcp_server.py`: MCP server exposing the patch, PR, file-check, and Excel commit export capabilities.

Most core CLI tools are stdlib-only. `mcp_server.py` and `excel_tool.py` require dependencies listed in `requirements.txt`.

When project tool behavior or public interfaces change, always check whether `mcp_server.py` needs a matching tool, schema, documentation, or validation update.

### Runtime requirements

- Python 3 (>=3.6 for the core CLIs; MCP dependency may require a newer Python).
- Git CLI for `patch_tool.py` operations.
- A git repository with history is needed to exercise patch checks, cherry-picks, or metadata sync.
- Network access and optional `GITEE_TOKEN` / `GITCODE_TOKEN` may be needed for private or rate-limited PR stats.
- `openpyxl` is needed for `excel_tool.py` to read and write `.xlsx` / `.xlsm` files.

### Linting

No project-level lint config exists. If `ruff` is installed, lint touched Python files directly:

```
python3 -m ruff check patch_tool.py pr_tool.py file_check_tool.py excel_tool.py mcp_server.py
```

`patch_tool.py` has known pre-existing F841 warnings in `cmd_sync_meta`; they are intentional destructuring assignments.

### Running / testing

Use the built-in help for each tool:

```
python3 patch_tool.py --help
python3 patch_tool.py <subcommand> --help
python3 pr_tool.py --help
python3 file_check_tool.py --help
python3 excel_tool.py --help
```

For MCP, prefer the virtual environment when available:

```
.venv/bin/python mcp_server.py --help
.venv/bin/python mcp_server.py
.venv/bin/python mcp_server.py --transport http --host 127.0.0.1 --port 8000
```

Useful non-destructive validation:

```
python3 -m py_compile patch_tool.py pr_tool.py file_check_tool.py excel_tool.py mcp_server.py
python3 patch_tool.py --help
python3 pr_tool.py --help
python3 file_check_tool.py --help
python3 excel_tool.py --help
```

### Tool notes

- `patch_tool.py check` is the safest patch subcommand for automated testing; it reads commit titles, hash+title lines, or hash-only entries and writes a report. The current output format is `title|commit_id|status|git_describe|commit_time|lines_changed`, where `lines_changed` is the sum of added and deleted lines for that commit on the checked branch.
- `patch_tool.py cherry-pick` can block on `input()` when conflicts occur, so avoid the CLI in unattended CI. Use the MCP `cherry_pick` tool for non-interactive automation.
- `patch_tool.py cherry-pick` accepts both the old 5-column `check` output and the current 6-column output with trailing `lines_changed`.
- `patch_tool.py sync-meta` rewrites git history when not in dry-run mode. Always test with `--dry-run` first and do not run destructive history rewrites unless explicitly requested.
- `pr_tool.py` performs live HTTP requests; avoid it when only validating local interfaces.
- `file_check_tool.py` reads file names from `-i/--input`, matches by exact basename, prints a table, and can write CSV with `--csv`.
- `file_check_tool.py --roots` fully overrides default search roots.
- Without `--roots`, `file_check_tool.py --os-family {auto,generic,debian,rpm}` controls default roots. `auto` reads `/etc/os-release`; `rpm` covers CentOS/RHEL/openEuler-style layouts; `debian` adds Debian multiarch library directories.
- `excel_tool.py INPUT OUTPUT` processes all worksheets in `.xlsx` / `.xlsm` files. It splits rows only by the `Commit信息` column, ignores blank commit segments, and fills other columns from the original row.
- `excel_tool.py export-commits INPUT OUTPUT` treats each `Commit信息` entry as semicolon-delimited fields, keeps only field 1 as commit hash and field 2 as commit title, and exports text lines formatted as `12-char-hash    commit title` (four spaces between hash and title). Entries without at least a 12-character hash and non-empty title are skipped.

### No build step

There is no build step, no `pyproject.toml`, and no test suite in the repo.
