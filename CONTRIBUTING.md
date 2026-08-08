# Contributing

Use Python 3.11 or 3.12. Keep local/private behavior as the default and never
commit audio, transcripts, databases, model weights, API keys or user paths.

Before a pull request run:

```bash
python -m compileall -q src tests
PYTHONPATH=src pytest -q
ruff check src tests
python -m build --wheel
python -m pip check
```

Changes to storage must prove that the original user file remains untouched.
Changes to cloud behavior need tests proving that explicit consent happens before
network access and that credentials do not enter serialized payloads.

Do not claim Hermes accuracy without trained weights, a closed test set and
measured WER/CER. Please use the issue templates and redact diagnostic reports.
