# Changelog

## 0.1.1

- Include the complete runnable example, contribution/security guidance and CI
  configuration in source distributions.

## 0.1.0

- Record explicit per-runbook reviews with SHA-256 inventories and declared scope.
- Detect changed, added, removed and missing inputs, changed scope and review expiry.
- Preserve other runbooks' receipts during atomic writes; report orphaned receipts.
- Provide offline text/JSON commands, strict input validation and bounded hashing.

Limits: self-declared metadata rather than signed attestations, literal local paths,
byte-level checks, no semantic or remote-service verification, serialized writers.
