# Contributing

Open an issue with a minimal synthetic reproduction before proposing a behavior change.
Keep real names, private paths and secrets out of reports. Code is MIT-licensed.

```sh
python -m venv .venv
# Activate .venv using your shell's standard activation command.
python -m pip install -e . ruff==0.17.2 build==1.6.1
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Use focused regression tests with independently checked outcomes. Update examples and
limitations when changing behavior. No network access belongs in runtime code or tests.
CI tests and builds only; it never commits changes.
