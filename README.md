# runbook-receipts

Keep a review tied to the files the reviewer actually saw. Record the bytes of a
runbook and its declared local dependencies, then check whether that review still
applies. Useful when handing over restore, deploy or incident procedures.

The tool detects changed documents, changed/added/deleted inputs, changed scope,
missing reviews and expired reviews. It does **not** determine whether a procedure
is correct, execute it, authenticate the reviewer or sign the receipt.

## Install and run a complete example

Python 3.11+, no runtime dependencies, no network access at runtime.

```sh
git clone https://github.com/KanadeK/runbook-receipts.git
cd runbook-receipts
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
runbook-receipts examples/runbooks.json check
```

The first check exits **1** and reports `restore: needs review` / `unreviewed`.
Read the synthetic document and inputs in `examples/`. Record that example review:

```sh
runbook-receipts examples/runbooks.json record restore --reviewer demo-operator
runbook-receipts examples/runbooks.json check
```

The second command exits **0** with `restore: fresh`. Now modify a watched input:

```sh
python -c "from pathlib import Path; p=Path('examples/scripts/restore-plan.txt'); p.write_text('New restore command requires another review.\n', encoding='utf-8')"
runbook-receipts examples/runbooks.json --format json check
```

It exits **1** and reports `changed` for `scripts/restore-plan.txt`. Adding a file
under `scripts/` reports `added`; deleting one reports `removed`. After genuinely
reviewing the changes, run `record` again. Recording is an explicit human assertion;
do not run it automatically to make CI green.

Uninstall: `python -m pip uninstall runbook-receipts`. Remove the virtual environment
and `examples/.runbook-receipts.json` when finished. The tool writes only the chosen
receipt file and a temporary file beside it during atomic replacement. It does not
change your documents, inputs or manifest.

## Manifest

```json
{
  "version": 1,
  "runbooks": [
    {
      "id": "restore",
      "document": "docs/restore.md",
      "inputs": ["scripts", "config/backup.json"],
      "max_age_days": 30
    }
  ]
}
```

Paths are literal POSIX-style paths relative to the manifest's directory. Inputs
may be regular files or directories, recursively including hidden files. There is
no ignore/glob language: choose narrow directories. `document` must be a regular
file. Duplicate IDs/keys, unknown fields, absolute paths, parent traversal, `.git`,
symlinks and Windows reparse points are rejected. Empty directories have no digest;
removal of an explicitly declared directory still produces `missing_input`.

The default receipt is `.runbook-receipts.json` beside the manifest. Override it
with `--receipts reviews.json` **before** the subcommand. Its parent must exist,
and it must sit outside every watched input. Commit the manifest and receipts if
the team wants them to travel with a handover or code review. Input ordering does
not matter; changing the document, paths or review-age policy invalidates the scope.

## Dates, reports and CI

`record` and `check` accept `--on YYYY-MM-DD`; the default is the current **UTC**
date. This override makes historical checks reproducible, not trustworthy proof of
when a review happened. With `max_age_days: 30`, a January 1 review expires on
January 31. A future-dated review is a finding. Reviewer names are self-declared.

```sh
runbook-receipts runbooks.json --format json check --on 2026-10-25
```

Exit codes: **0** successful record/fresh reviews; **1** findings; **2** malformed
input, unsafe path or environment error. JSON output uses `schema_version: 1`,
`fresh`, `runbooks` (per-ID findings) and `orphaned_receipts`. Removed manifest IDs
leave orphaned receipts and fail the check until explicitly removed from the JSON.
An input error contains an `error` string. Argparse errors use stderr.

CI should run `check` only. Receipts contain SHA-256, byte sizes, relative paths,
scope, reviewer and date, never file contents. They are editable metadata, **not
tamper-proof attestations**; repository review controls govern changes to receipts.
Inspect paths and reviewer labels before publishing reports.

## Boundaries

- Per runbook: at most 100 input roots, 10,000 enumerated paths, 256 MiB per file and
  1 GiB of content. JSON files are limited to 8 MiB; at most 1,000 runbooks.
- Exact bytes matter, including line endings. Agree on checkout line endings
  (`.gitattributes`) before sharing receipts between operating systems.
- No permission/mode, empty-subdirectory, remote-service or semantic checks.
- Ordinary modifications during hashing are detected through size/time/inode
  checks. Stop concurrent edits when recording; it is not an adversarial filesystem
  sandbox or a transactional snapshot. Serialize writers to the same receipt file.
- File hashing is streamed; inventory metadata is held in memory. Work scales with
  total declared content, with separate snapshots for each runbook.

Tools such as [Harness](https://github.com/sam-bretz/harness) and
[Drift](https://github.com/pallaprolus/drift) detect documentation/code drift.
This tool provides explicit review receipts for declared local inputs, works
without Git history and does not infer meaning or dependencies. No code from those
projects is bundled. MIT. [Contributing](CONTRIBUTING.md), [security](SECURITY.md).

## 中文说明

为运维文档及其依赖文件保存一次明确的人工复核记录，之后检查文件修改、目录新增/删除、
范围变化和复核过期。只保存哈希与元数据，不保存文件内容，不执行运维命令。
复核人和日期是自行声明的信息，工具不证明文档正确，也不提供签名或防篡改保证。
CI 应只执行 `check`；实际重新复核后再执行 `record`。跨平台需统一换行符。
