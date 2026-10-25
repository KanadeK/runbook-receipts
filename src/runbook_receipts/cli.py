import argparse
import json
import sys
from datetime import UTC, datetime

from .core import InputError, check, record


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record and verify explicit runbook review inputs."
    )
    parser.add_argument("manifest", help="JSON manifest; paths are relative to its directory")
    parser.add_argument(
        "--receipts", default=".runbook-receipts.json", help="relative receipt file"
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--version", action="version", version="runbook-receipts 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("check", help="read-only freshness check")
    stamp = commands.add_parser("record", help="attest that you reviewed the current inputs")
    for command in (inspect, stamp):
        command.add_argument(
            "--on",
            default=datetime.now(UTC).date().isoformat(),
            help="YYYY-MM-DD; default current UTC date",
        )
    stamp.add_argument("id")
    stamp.add_argument("--reviewer", required=True, help="self-declared reviewer label")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            report = record(args.manifest, args.receipts, args.id, args.on, args.reviewer)
        else:
            report = check(args.manifest, args.receipts, args.on)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=True, indent=2))
        elif args.command == "record":
            print(
                f"Recorded {report['recorded']}: {report['files']} files on {report['reviewed_on']}"
            )
        else:
            for item in report["runbooks"]:
                print(f"{item['id']}: {'fresh' if item['fresh'] else 'needs review'}")
                for finding in item["findings"]:
                    print(f"  {finding['code']}: {finding.get('path', '')}")
            for identifier in report["orphaned_receipts"]:
                print(f"orphaned receipt: {identifier}")
        return 0 if report.get("fresh", True) else 1
    except (InputError, OSError) as exc:
        if args.format == "json":
            print(json.dumps(dict(schema_version=1, error=str(exc)), ensure_ascii=True))
        else:
            print(f"runbook-receipts: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
