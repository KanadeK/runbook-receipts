import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runbook_receipts.core import InputError, check, record


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "docs/restore.md").write_text("Run the restore procedure.\n", encoding="utf-8")
        (self.root / "scripts/restore.txt").write_bytes(b"restore --dry-run\n")
        self.manifest = self.root / "runbooks.json"
        self.item = dict(
            id="restore", document="docs/restore.md", inputs=["scripts"], max_age_days=30
        )
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(
            json.dumps(dict(version=1, runbooks=[self.item])), encoding="utf-8"
        )

    def record(self, **kwargs):
        return record(self.manifest, "receipts.json", "restore", "2026-01-01", "Ops", **kwargs)

    def check(self, on="2026-01-02"):
        return check(self.manifest, "receipts.json", on)

    def codes(self, on="2026-01-02"):
        return {f["code"] for f in self.check(on)["runbooks"][0]["findings"]}

    def test_record_and_check_hashes(self):
        self.assertEqual(self.record()["files"], 2)
        self.assertTrue(self.check()["fresh"])
        receipt = json.loads((self.root / "receipts.json").read_text())["entries"]["restore"]
        self.assertEqual(
            receipt["files"]["scripts/restore.txt"]["sha256"],
            hashlib.sha256(b"restore --dry-run\n").hexdigest(),
        )
        self.assertNotIn("restore --dry-run", json.dumps(receipt))

    def test_unreviewed(self):
        self.assertEqual(self.codes(), {"unreviewed"})

    def test_document_changes(self):
        self.record()
        (self.root / "docs/restore.md").write_text("Different procedure")
        self.assertEqual(self.codes(), {"changed"})

    def test_input_modified_added_removed(self):
        self.record()
        (self.root / "scripts/restore.txt").unlink()
        (self.root / "scripts/new.txt").write_text("new")
        findings = self.check()["runbooks"][0]["findings"]
        self.assertEqual(
            findings,
            [
                dict(code="added", path="scripts/new.txt"),
                dict(code="removed", path="scripts/restore.txt"),
            ],
        )

    def test_deleted_root_is_visible(self):
        self.record()
        (self.root / "scripts/restore.txt").unlink()
        (self.root / "scripts").rmdir()
        self.assertEqual(self.codes(), {"missing_input", "removed"})

    def test_review_expiration_boundary(self):
        self.record()
        self.assertTrue(self.check("2026-01-30")["fresh"])
        self.assertEqual(self.codes("2026-01-31"), {"expired"})

    def test_future_review(self):
        self.record()
        self.assertEqual(self.codes("2025-12-31"), {"future_review"})

    def test_scope_change_invalidates_even_if_bytes_same(self):
        self.record()
        self.item["inputs"] = ["scripts/restore.txt"]
        self.write_manifest()
        self.assertEqual(self.codes(), {"scope_changed"})

    def test_review_age_policy_change_invalidates(self):
        self.record()
        self.item["max_age_days"] = 60
        self.write_manifest()
        self.assertEqual(self.codes(), {"scope_changed"})

    def test_rereview_replaces_only_selected_entry(self):
        self.record()
        data = json.loads((self.root / "receipts.json").read_text())
        other = json.loads(json.dumps(data["entries"]["restore"]))
        other["spec"]["id"] = "other"
        data["entries"]["other"] = other
        (self.root / "receipts.json").write_text(json.dumps(data))
        (self.root / "scripts/restore.txt").write_text("changed")
        self.record()
        current = json.loads((self.root / "receipts.json").read_text())
        self.assertEqual(current["entries"]["other"], other)
        self.assertEqual(self.check()["orphaned_receipts"], ["other"])
        self.assertFalse(self.check()["fresh"])

    def test_missing_input_cannot_record(self):
        (self.root / "scripts/restore.txt").unlink()
        (self.root / "docs/restore.md").unlink()
        with self.assertRaises(InputError):
            self.record()
        self.assertFalse((self.root / "receipts.json").exists())

    def test_empty_directory_supported(self):
        (self.root / "scripts/restore.txt").unlink()
        self.record()
        self.assertTrue(self.check()["fresh"])

    def test_nested_inputs_and_overlap_deduplicated(self):
        self.item["inputs"].append("scripts/restore.txt")
        self.write_manifest()
        self.assertEqual(self.record()["files"], 2)

    def test_parent_absolute_and_git_paths_rejected(self):
        for value in ("../outside", "/outside", "C:/outside", ".", ".git/config", "a\\b"):
            with self.subTest(value=value):
                self.item["inputs"] = [value]
                self.write_manifest()
                with self.assertRaises(InputError):
                    self.record()

    def test_unknown_manifest_fields(self):
        self.item["max_age_day"] = 10
        self.write_manifest()
        with self.assertRaises(InputError):
            self.check()

    def test_bool_version_rejected(self):
        self.manifest.write_text(json.dumps(dict(version=True, runbooks=[self.item])))
        with self.assertRaises(InputError):
            self.check()

    def test_duplicate_json_rejected(self):
        self.manifest.write_text('{"version":1,"version":1,"runbooks":[]}')
        with self.assertRaises(InputError):
            self.check()

    def test_bad_receipt_structure(self):
        for content in ("[]", '{"version":1,"entries":{"restore":null}}', "{"):
            (self.root / "receipts.json").write_text(content)
            with self.subTest(content=content), self.assertRaises(InputError):
                self.check()

    def test_receipt_cannot_overwrite_manifest(self):
        with self.assertRaises(InputError):
            record(self.manifest, "runbooks.json", "restore", "2026-01-01", "Ops")

    def test_receipt_cannot_watch_itself_before_creation(self):
        with self.assertRaises(InputError):
            record(self.manifest, "scripts/receipt.json", "restore", "2026-01-01", "Ops")
        self.assertFalse((self.root / "scripts/receipt.json").exists())

    def test_invalid_date_and_reviewer(self):
        for day, by in (("2026-02-30", "Ops"), ("20260101", "Ops"), ("2026-01-01", "\n")):
            with self.subTest(day=day, by=by), self.assertRaises(InputError):
                record(self.manifest, "receipts.json", "restore", day, by)

    def test_symlink_input_is_rejected(self):
        try:
            os.symlink(self.root / "docs/restore.md", self.root / "scripts/link")
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with self.assertRaises(InputError):
            self.record()

    def test_bound_on_file_bytes(self):
        with patch("runbook_receipts.core.MAX_FILE_BYTES", 5), self.assertRaises(InputError):
            self.record()

    def test_bound_on_inventory(self):
        with patch("runbook_receipts.core.MAX_FILES", 1), self.assertRaises(InputError):
            self.record()

    def test_hashing_race_detected(self):
        real_sha = hashlib.sha256

        def changing_hash():
            (self.root / "docs/restore.md").write_text("rewritten during snapshot")
            return real_sha()

        with patch("runbook_receipts.core.hashlib.sha256", side_effect=changing_hash):
            with self.assertRaises(InputError):
                self.record()

    def test_atomic_write_failure_preserves_receipt(self):
        self.record()
        before = (self.root / "receipts.json").read_bytes()
        with patch("runbook_receipts.core.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                self.record()
        self.assertEqual((self.root / "receipts.json").read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()),
            ["docs", "receipts.json", "runbooks.json", "scripts"],
        )

    def test_cli_full_flow(self):
        def run(*args):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "runbook_receipts",
                    str(self.manifest),
                    "--receipts",
                    "receipts.json",
                    "--format",
                    "json",
                    *args,
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(run("check", "--on", "2026-01-02").returncode, 1)
        recorded = run("record", "restore", "--reviewer", "Ops", "--on", "2026-01-01")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        result = run("check", "--on", "2026-01-02")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["fresh"])
        (self.root / "scripts/restore.txt").write_text("modified")
        self.assertEqual(run("check", "--on", "2026-01-02").returncode, 1)
        self.assertEqual(run("check", "--on", "invalid").returncode, 2)


if __name__ == "__main__":
    unittest.main()
