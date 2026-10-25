"""Explicit, content-bound review receipts. No inference of document correctness."""

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import date
from pathlib import Path

MAX_JSON = 8 * 1024 * 1024
MAX_FILES = 10000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


class InputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InputError(message)


def fields(value, expected, label):
    require(type(value) is dict and set(value) == set(expected), f"invalid fields in {label}")


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024, "path must be a nonempty string")
    require(not any(c in value for c in "\\:*?[]\x00"), "use literal relative POSIX paths")
    require(all(ord(c) >= 32 for c in value), "control character in path")
    require(
        all(p not in {"", ".", "..", ".git"} for p in value.split("/")),
        "path must stay below the manifest directory and outside .git",
    )
    return value


def safe_path(root, name):
    relative(name)
    current = root
    for part in name.split("/"):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        require(
            not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400),
            f"symlinks and reparse points are not allowed: {name}",
        )
    require(current.resolve().is_relative_to(root), "path escapes manifest directory")
    return current


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    require(not path.is_symlink() and path.is_file(), "JSON input must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    require(len(raw) <= MAX_JSON, "JSON input exceeds 8 MiB")
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: require(False, "nonfinite JSON number"),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InputError("invalid UTF-8 JSON") from exc


def spec(value):
    fields(value, {"id", "document", "inputs", "max_age_days"}, "runbook")
    require(
        isinstance(value["id"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value["id"]),
        "invalid runbook id",
    )
    relative(value["document"])
    inputs = value["inputs"]
    require(type(inputs) is list and 0 < len(inputs) <= 100, "inputs must contain 1..100 paths")
    for item in inputs:
        relative(item)
    require(len(set(inputs)) == len(inputs), "duplicate input path")
    age = value["max_age_days"]
    require(type(age) is int and 1 <= age <= 3650, "max_age_days must be 1..3650")
    return dict(value, inputs=sorted(inputs))


def parse_date(value):
    require(
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
        "date must be YYYY-MM-DD",
    )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputError("invalid calendar date") from exc


def load_manifest(path):
    path = Path(path).absolute()
    data = read_json(path)
    fields(data, {"version", "runbooks"}, "manifest")
    require(type(data["version"]) is int and data["version"] == 1, "unsupported manifest version")
    require(
        type(data["runbooks"]) is list and 0 < len(data["runbooks"]) <= 1000,
        "manifest needs 1..1000 runbooks",
    )
    specs = {}
    for item in data["runbooks"]:
        item = spec(item)
        require(item["id"] not in specs, "duplicate runbook id")
        specs[item["id"]] = item
    return path.parent.resolve(), specs


def load_receipts(path):
    if not path.exists():
        return dict(version=1, entries={})
    data = read_json(path)
    fields(data, {"version", "entries"}, "receipts")
    require(type(data["version"]) is int and data["version"] == 1, "unsupported receipt version")
    require(
        type(data["entries"]) is dict and len(data["entries"]) <= 1000, "invalid receipt entries"
    )
    for key, item in data["entries"].items():
        fields(item, {"spec", "reviewed_on", "reviewer", "files"}, "receipt entry")
        require(spec(item["spec"])["id"] == key, "receipt id mismatch")
        parse_date(item["reviewed_on"])
        reviewer(item["reviewer"])
        require(
            type(item["files"]) is dict and 0 < len(item["files"]) <= MAX_FILES,
            "invalid receipt file inventory",
        )
        for name, value in item["files"].items():
            relative(name)
            fields(value, {"sha256", "bytes"}, "file digest")
            require(
                isinstance(value["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", value["sha256"]),
                "invalid SHA-256",
            )
            require(
                type(value["bytes"]) is int and 0 <= value["bytes"] <= MAX_FILE_BYTES,
                "invalid recorded byte count",
            )
        require(item["spec"]["document"] in item["files"], "receipt is missing its document")
    return data


def reviewer(value):
    require(
        isinstance(value, str)
        and 0 < len(value.strip()) <= 200
        and all(ord(c) >= 32 for c in value),
        "reviewer must be 1..200 printable characters",
    )
    return value.strip()


def snapshot(root, item, receipt_name):
    """Capture bytes, including directory membership, without following links."""
    names, missing, visited = set(), [], set()
    for name in [item["document"], *item["inputs"]]:
        path = safe_path(root, name)
        if not path.exists():
            missing.append(name)
            continue
        if name == item["document"]:
            require(path.is_file(), "runbook document must be a regular file")
        pending = [path]
        while pending:
            current = pending.pop()
            rel = current.relative_to(root).as_posix()
            current = safe_path(root, rel)
            if rel in visited:
                continue
            visited.add(rel)
            require(len(visited) <= MAX_FILES, "input inventory exceeds 10000 paths")
            require(rel != receipt_name, "receipt file must not be inside watched inputs")
            if current.is_dir():
                # Bound enumeration as well as content hashing, including empty directories.
                with os.scandir(current) as entries:
                    for child in entries:
                        pending.append(Path(child.path))
                        require(
                            len(pending) + len(names) <= MAX_FILES,
                            "input inventory exceeds 10000 paths",
                        )
            else:
                require(current.is_file(), f"input is not a regular file: {rel}")
                names.add(rel)
                require(len(names) <= MAX_FILES, "too many input files")
    files, total = {}, 0
    for name in sorted(names):
        path = safe_path(root, name)
        before = path.stat()
        require(before.st_size <= MAX_FILE_BYTES, f"file exceeds 256 MiB: {name}")
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                require(
                    size <= MAX_FILE_BYTES and total <= MAX_TOTAL_BYTES,
                    "snapshot exceeds byte budget",
                )
                digest.update(chunk)
        after = path.stat()
        require(
            (before.st_size, before.st_mtime_ns, before.st_ino)
            == (after.st_size, after.st_mtime_ns, after.st_ino)
            and size == after.st_size,
            f"file changed while hashing; retry: {name}",
        )
        files[name] = dict(sha256=digest.hexdigest(), bytes=size)
    return files, sorted(set(missing))


def context(manifest, receipt_name):
    root, specs = load_manifest(manifest)
    receipt_path = safe_path(root, receipt_name)
    require(receipt_path != Path(manifest).resolve(), "receipt cannot overwrite manifest")
    # Reject an absent receipt nested under a watched directory, before the first write.
    for item in specs.values():
        for name in [item["document"], *item["inputs"]]:
            watched = safe_path(root, name)
            require(
                receipt_path != watched and not receipt_path.is_relative_to(watched),
                "receipt file must be outside every watched input",
            )
    return root, specs, receipt_path, load_receipts(receipt_path)


def record(manifest, receipt_name, identifier, reviewed_on, by):
    root, specs, path, data = context(manifest, receipt_name)
    require(identifier in specs, "unknown runbook id")
    parse_date(reviewed_on)
    by = reviewer(by)
    item = specs[identifier]
    files, missing = snapshot(root, item, receipt_name)
    require(not missing, f"missing inputs: {', '.join(missing)}")
    data["entries"][identifier] = dict(spec=item, reviewed_on=reviewed_on, reviewer=by, files=files)
    encoded = (json.dumps(data, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    require(len(encoded) <= MAX_JSON, "receipt would exceed 8 MiB")
    require(path.parent.is_dir(), "receipt parent directory must already exist")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(root, receipt_name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dict(schema_version=1, recorded=identifier, files=len(files), reviewed_on=reviewed_on)


def check(manifest, receipt_name, on):
    root, specs, _, data = context(manifest, receipt_name)
    today = parse_date(on)
    results = []
    for identifier, item in sorted(specs.items()):
        findings = []
        receipt = data["entries"].get(identifier)
        if receipt is None:
            findings.append(dict(code="unreviewed"))
        else:
            if item != receipt["spec"]:
                findings.append(dict(code="scope_changed"))
            elapsed = (today - parse_date(receipt["reviewed_on"])).days
            if elapsed < 0:
                findings.append(dict(code="future_review"))
            elif elapsed >= item["max_age_days"]:
                findings.append(dict(code="expired", age_days=elapsed))
            current, missing = snapshot(root, item, receipt_name)
            previous = receipt["files"]
            for name in missing:
                findings.append(dict(code="missing_input", path=name))
            for name in sorted(current.keys() - previous.keys()):
                findings.append(dict(code="added", path=name))
            for name in sorted(previous.keys() - current.keys()):
                findings.append(dict(code="removed", path=name))
            for name in sorted(current.keys() & previous.keys()):
                if current[name] != previous[name]:
                    findings.append(dict(code="changed", path=name))
        results.append(dict(id=identifier, findings=findings, fresh=not findings))
    orphaned = sorted(data["entries"].keys() - specs.keys())
    return dict(
        schema_version=1,
        as_of=on,
        fresh=all(r["fresh"] for r in results) and not orphaned,
        runbooks=results,
        orphaned_receipts=orphaned,
    )
