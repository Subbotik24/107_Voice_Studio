# models-v1 build runbook

Build the Tiny/Small `models-v1` release assets on a trusted developer
machine. The build script deliberately performs no downloads, and model
archives are never committed to Git; this runbook is the manual procedure it
expects. A cloud/CI environment without Hugging Face egress cannot run steps
1-2 — the 2026-09-01 session verified exactly that and stopped here.

## 1. Pin the upstream revisions

```bash
curl -sS https://huggingface.co/api/models/Systran/faster-whisper-tiny | python -c "import json,sys; print(json.load(sys.stdin)['sha'])"
curl -sS https://huggingface.co/api/models/Systran/faster-whisper-small | python -c "import json,sys; print(json.load(sys.stdin)['sha'])"
```

Record both commit hashes; they become the `--revision` values and part of the
archive names.

## 2. Download the pinned snapshots (HTTPS only)

```bash
pip install "huggingface_hub>=0.23"
python - <<'PY'
from huggingface_hub import snapshot_download
for model, revision in (("tiny", "<TINY_SHA>"), ("small", "<SMALL_SHA>")):
    path = snapshot_download(
        f"Systran/faster-whisper-{model}",
        revision=revision,
        local_dir=f"upstream/{model}",
    )
    print(model, path)
PY
```

Verify each directory contains `model.bin`, `config.json`, the tokenizer
files, and the upstream `README.md` (model card) — the archive must ship the
license/model card alongside the weights. Do not edit any downloaded file.

## 3. Build the archives, inventory and checksums

```bash
python scripts/build_model_release.py \
  --model tiny=upstream/tiny --revision tiny=<TINY_SHA> \
  --model small=upstream/small --revision small=<SMALL_SHA> \
  --repository Subbotik24/107_voice_studio \
  --output dist/models-v1
```

The output directory holds the two ZIP archives, `SHA256SUMS.txt` and
`model-registry-v1.json` (per-file SHA-256 inventory, sizes, provenance and
final download URLs).

## 4. Publish

1. Create the `models-v1` GitHub release (no source tag semantics — it is a
   data release) and attach both archives, `SHA256SUMS.txt` and
   `model-registry-v1.json`. Each asset must stay under GitHub's 2 GiB limit.
2. Re-download each archive and confirm its SHA-256 against `SHA256SUMS.txt`
   before announcing anything.
3. Never add the archives or upstream snapshots to Git; only this runbook and
   release-side metadata live in the repository.

The in-app catalog verifies the exact SHA-256 from the registry before an
install, requires HTTPS, validates the ZIP safely and installs atomically —
so a checksum mismatch at step 4.2 means the release assets must be replaced,
not the registry edited.
