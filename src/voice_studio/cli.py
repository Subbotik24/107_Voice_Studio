from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from . import __version__
from .backup import (
    create_backup,
    recover_interrupted_restore,
    restore_backup,
    verify_backup,
)
from .cloud_cleanup import list_ollama_models, propose_cleanup
from .cloud_secrets import (
    delete_openai_api_key,
    get_openai_api_key,
    openai_key_status,
    set_openai_api_key,
)
from .config import cache_dir, data_dir, load_settings, settings_path
from .diagnostics import diagnostics
from .dictionary import TerminologyDictionary
from .exporters import export_transcript
from .hardware import detect_hardware
from .jobs import TranscriptionJobController
from .media import validate_media_file
from .model_catalog import ModelCatalog
from .models import SUPPORTED_COMPUTE_TYPES, SUPPORTED_DEVICES, Settings
from .profiles import apply_profile
from .storage import LocalStore


def _json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _settle_interrupted_restore() -> dict[str, Any]:
    """Settle a half-applied restore before any store is opened.

    A non-trivial outcome is always reported on stderr, so a command that only
    prints its own JSON payload never hides the fact that stored data moved.
    """

    result = recover_interrupted_restore(data_dir(), settings_target=settings_path())
    if result.get("status") != "PASS" or result.get("action") != "none":
        print(
            "restore-journal: " + json.dumps(result, ensure_ascii=False),
            file=sys.stderr,
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="voice-studio",
        description="Privacy-first local desktop transcription.",
    )
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("gui", help="open the desktop application")

    doctor = sub.add_parser("doctor", help="inspect the selected local runtime")
    doctor.add_argument("--json", action="store_true")
    health = sub.add_parser("health", help="machine-readable health status")
    health.add_argument("--json", action="store_true")
    sub.add_parser("settings", help="print effective settings")

    hardware = sub.add_parser("hardware", help="inspect local hardware capabilities")
    hardware_commands = hardware.add_subparsers(dest="hardware_command", required=True)
    hardware_detect = hardware_commands.add_parser("detect", help="detect local runtime support")
    hardware_detect.add_argument("--json", action="store_true")

    workspace = sub.add_parser(
        "init-workspace", help="copy editable starter configs and dictionary"
    )
    workspace.add_argument("directory", type=Path)
    workspace.add_argument("--force", action="store_true")

    validate = sub.add_parser("validate", help="validate an input media file")
    validate.add_argument("file", type=Path)

    transcribe = sub.add_parser("transcribe", help="transcribe an audio or video file")
    transcribe.add_argument("file", type=Path)
    transcribe.add_argument("--language", choices=["auto", "uk", "cs", "en"])
    transcribe.add_argument("--engine", choices=["ollama", "faster-whisper", "openai-cloud"])
    transcribe.add_argument("--model", help="faster-whisper model name or local model path")
    transcribe.add_argument("--ollama-model", help="installed Ollama audio-capable model name")
    transcribe.add_argument("--device", choices=SUPPORTED_DEVICES)
    transcribe.add_argument("--compute-type", choices=SUPPORTED_COMPUTE_TYPES)
    transcribe.add_argument("--retention", choices=["keep", "delete_after_transcription"])
    transcribe.add_argument("--dictionary", type=Path)
    transcribe.add_argument("--export-format", choices=["txt", "md", "json", "srt", "vtt"])
    transcribe.add_argument("--output", type=Path)
    transcribe.add_argument("--timeout", type=int)
    transcribe.add_argument("--allow-cloud-upload", action="store_true")

    cleanup = sub.add_parser("cleanup", help="propose an explicit AI text cleanup")
    cleanup.add_argument("transcript_id")
    cleanup.add_argument("--provider", choices=["ollama", "openai"])
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--allow-cloud-text", action="store_true")
    cleanup.add_argument("--model", help="cleanup model override")
    undo_cleanup = sub.add_parser("cleanup-undo", help="undo the last applied AI cleanup")
    undo_cleanup.add_argument("transcript_id")

    cloud = sub.add_parser("cloud", help="manage explicit cloud-provider credentials")
    cloud_commands = cloud.add_subparsers(dest="cloud_command", required=True)
    cloud_key = cloud_commands.add_parser("key", help="manage the OpenAI key in the OS keychain")
    cloud_key_commands = cloud_key.add_subparsers(dest="cloud_key_command", required=True)
    cloud_key_commands.add_parser("status")
    key_set = cloud_key_commands.add_parser("set")
    key_set.add_argument("--value", help="key value; omit to be prompted on a terminal")
    cloud_key_commands.add_parser("delete")
    cloud_key_commands.add_parser("test")

    diagnostics_export = sub.add_parser("diagnostics", help="inspect or export a redacted report")
    diagnostics_export.add_argument("--export", type=Path)

    history = sub.add_parser("history", help="list local transcript history")
    history.add_argument("--query", default="")
    history.add_argument("--limit", type=int, default=100)

    show = sub.add_parser("show", help="show one transcript")
    show.add_argument("transcript_id")

    export = sub.add_parser("export", help="export a stored transcript")
    export.add_argument("transcript_id")
    export.add_argument("--format", choices=["txt", "md", "json", "srt", "vtt"], required=True)
    export.add_argument("--output", type=Path)

    delete = sub.add_parser("delete", help="delete a transcript record")
    delete.add_argument("transcript_id")
    delete.add_argument("--delete-audio", action="store_true")

    models = sub.add_parser("models", help="manage local faster-whisper models")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    model_commands.add_parser("list", help="list installed models")
    model_commands.add_parser("reconcile", help="repair local model catalog state")
    install_model = model_commands.add_parser("install", help="install a model explicitly")
    install_model.add_argument("model_id")
    install_model.add_argument("--from-directory", type=Path)
    install_model.add_argument("--revision")
    install_model.add_argument(
        "--registry",
        help="HTTPS model-registry-v1.json URL from the project's GitHub Release",
    )
    install_model.add_argument("--timeout", type=int, default=1_800)
    verify_installed = model_commands.add_parser("verify", help="verify an installed model")
    verify_installed.add_argument("model_id")
    remove_model = model_commands.add_parser("remove", help="remove a managed model")
    remove_model.add_argument("model_id")
    remove_model.add_argument("--yes", action="store_true")

    backup = sub.add_parser("backup", help="create, verify, or restore a local backup")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_commands.add_parser("create", help="create a versioned backup")
    backup_create.add_argument("output", type=Path)
    backup_create.add_argument("--without-audio", action="store_true")
    backup_verify = backup_commands.add_parser("verify", help="verify a backup")
    backup_verify.add_argument("file", type=Path)
    backup_restore = backup_commands.add_parser("restore", help="restore a verified backup")
    backup_restore.add_argument("file", type=Path)

    storage = sub.add_parser("storage", help="audit managed local storage")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("audit", help="check SQLite and managed sources")
    storage_repair = storage_commands.add_parser(
        "repair-missing",
        help="detach a transcript from a confirmed missing managed source",
    )
    storage_repair.add_argument("transcript_id")
    storage_repair.add_argument("--expected-path")
    storage_repair.add_argument("--yes", action="store_true")
    storage_cleanup = storage_commands.add_parser(
        "cleanup-orphans",
        help="remove verified unreferenced managed copies",
    )
    storage_cleanup.add_argument("--yes", action="store_true")

    benchmark = sub.add_parser("benchmark", help="measure a licensed local test manifest")
    benchmark.add_argument("manifest", type=Path)
    benchmark.add_argument("--engine", choices=["faster-whisper"])
    benchmark.add_argument("--model")
    benchmark.add_argument("--device", choices=SUPPORTED_DEVICES)
    benchmark.add_argument("--compute-type", choices=SUPPORTED_COMPUTE_TYPES)
    benchmark.add_argument("--output", type=Path)

    return root


def _load_effective_settings(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    selected_engine = getattr(args, "engine", None)
    if selected_engine is not None:
        profile = {
            "ollama": "ollama-local",
            "faster-whisper": "whisper-local",
            "openai-cloud": "openai-cloud",
        }[selected_engine]
        settings = apply_profile(settings, profile)
    overrides: dict[str, Any] = {}
    for argument, field in (
        ("language", "language"),
        ("model", "model"),
        ("ollama_model", "ollama_model"),
        ("device", "device"),
        ("compute_type", "compute_type"),
        ("retention", "retention"),
    ):
        value = getattr(args, argument, None)
        if value is not None:
            overrides[field] = value
    if getattr(args, "dictionary", None) is not None:
        overrides["dictionary_path"] = str(args.dictionary.expanduser())
    effective = replace(settings, **overrides)
    effective.validate()
    return effective


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "gui":
            from .app import main as gui_main

            gui_main()
            return 0

        if args.command in {"doctor", "health"}:
            result = diagnostics(load_settings())
            if args.json or args.command == "health":
                _json(result if args.json else {"status": result["status"]})
            else:
                for key, value in result.items():
                    print(f"{key}: {value}")
            return 0 if result["status"] == "ok" else 2

        if args.command == "settings":
            _json(load_settings().to_dict())
            return 0

        if args.command == "hardware":
            if args.hardware_command != "detect":
                parser.error("unsupported hardware command")
            result = detect_hardware()
            if args.json:
                _json(asdict(result))
            else:
                print(f"{result.status}: {result.detail}")
                print(f"fallback: {result.fallback[0]}/{result.fallback[1]}")
            return 0

        if args.command == "diagnostics":
            result = diagnostics(load_settings())
            if args.export:
                from .diagnostics import export_redacted_diagnostics

                print(export_redacted_diagnostics(result, args.export))
            else:
                _json(result)
            return 0

        if args.command == "cloud":
            if args.cloud_command != "key":
                parser.error("unsupported cloud command")
            if args.cloud_key_command == "status":
                _json(openai_key_status())
                return 0
            if args.cloud_key_command == "set":
                value = args.value
                if value is None:
                    import getpass

                    value = getpass.getpass("OpenAI API key: ")
                set_openai_api_key(value)
                _json({"configured": True, "source": "keychain"})
                return 0
            if args.cloud_key_command == "delete":
                _json({"deleted": delete_openai_api_key()})
                return 0
            if args.cloud_key_command == "test":
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise RuntimeError("Install voice-studio[cloud] to test OpenAI") from exc
                # Explicit command: a lightweight authenticated network request is intended.
                OpenAI(api_key=get_openai_api_key(), timeout=30.0, max_retries=0).models.list()
                _json({"status": "ok", "provider": "openai"})
                return 0

        if args.command == "init-workspace":
            from .workspace import initialize_workspace

            _json(initialize_workspace(args.directory, overwrite=args.force))
            return 0

        if args.command == "validate":
            validate_media_file(args.file)
            _json(
                {
                    "valid": True,
                    "path": str(args.file.resolve()),
                    "size": args.file.stat().st_size,
                }
            )
            return 0

        if args.command == "backup":
            if args.backup_command == "verify":
                _json(verify_backup(args.file))
                return 0
            if args.backup_command == "restore":
                recovered = _settle_interrupted_restore()
                restored = restore_backup(
                    args.file, data_dir(), settings_target=settings_path()
                )
                restored["recovered_interrupted_restore"] = recovered
                _json(restored)
                return 0
            _settle_interrupted_restore()
            store = LocalStore(data_dir())
            _json(
                create_backup(
                    store,
                    args.output,
                    settings_file=settings_path(),
                    include_audio=not args.without_audio,
                )
            )
            return 0

        if args.command == "storage" and args.storage_command == "audit":
            _json(LocalStore.audit_existing(data_dir()))
            return 0

        _settle_interrupted_restore()
        store = LocalStore(data_dir())

        if args.command == "models":
            catalog = ModelCatalog(store.models)
            reconciliation = catalog.reconcile()
            if reconciliation["status"] != "PASS" or reconciliation["action"] != "none":
                print(
                    "model-catalog:" + json.dumps(reconciliation, ensure_ascii=False),
                    file=sys.stderr,
                )
            if args.models_command == "reconcile":
                _json(reconciliation)
                return 0 if reconciliation["status"] == "PASS" else 2
            if args.models_command == "list":
                _json(catalog.list())
                return 0
            if args.models_command == "install":
                if args.from_directory:
                    _json(catalog.import_local(args.model_id, args.from_directory))
                else:
                    settings = load_settings()
                    _json(
                        catalog.install(
                            args.model_id,
                            revision=args.revision,
                            offline_only=settings.offline_only,
                            timeout_seconds=args.timeout,
                            registry=args.registry,
                        )
                    )
                return 0
            if args.models_command == "verify":
                _json(catalog.verify(args.model_id))
                return 0
            if args.models_command == "remove":
                _json(catalog.remove(args.model_id, confirmed=args.yes))
                return 0

        if args.command == "storage":
            if args.storage_command == "repair-missing":
                try:
                    repaired = store.repair_missing_source(
                        args.transcript_id,
                        confirmed=args.yes,
                        expected_path=args.expected_path,
                    )
                except KeyError as exc:
                    print(exc.args[0], file=sys.stderr)
                    return 3
                _json(repaired)
                return 0
            if args.storage_command == "cleanup-orphans":
                _json(store.cleanup_orphans(confirmed=args.yes))
                return 0

        if args.command == "cleanup-undo":
            _json(store.undo_last_ai_cleanup(args.transcript_id).to_dict())
            return 0

        if args.command == "cleanup":
            settings = load_settings()
            provider = args.provider or settings.cleanup_provider
            if provider == "openai" and not args.allow_cloud_text:
                raise ValueError(
                    "AI cleanup requires --allow-cloud-text; corrected text is never "
                    "uploaded silently"
                )
            if provider == "openai" and settings.offline_only:
                raise ValueError("offline_only blocks AI cleanup")
            if provider == "ollama":
                local_models = list_ollama_models()
                model = args.model or settings.ollama_model or (
                    local_models[0] if local_models else ""
                )
                if not model:
                    raise ValueError("no local Ollama model is installed")
            else:
                model = args.model or settings.openai_cleanup_model
            transcript = store.get(args.transcript_id)
            if transcript is None:
                print("transcript not found", file=sys.stderr)
                return 3
            proposal = propose_cleanup(
                transcript,
                provider=provider,
                model=model,
            )
            result = proposal.to_dict()
            if args.apply:
                result["transcript"] = store.apply_ai_cleanup(
                    transcript.id,
                    result,
                    provider=provider,
                    model=model,
                ).to_dict()
            _json(result)
            return 0

        if args.command == "benchmark":
            from .benchmark import run_benchmark

            settings = _load_effective_settings(args)
            result = run_benchmark(
                args.manifest,
                settings,
                cache_directory=cache_dir(),
                model_directory=store.models,
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            _json(result)
            return 0

        if args.command == "history":
            _json([item.to_dict() for item in store.list(args.query, args.limit)])
            return 0

        if args.command == "show":
            transcript = store.get(args.transcript_id)
            if transcript is None:
                print("transcript not found", file=sys.stderr)
                return 3
            _json(transcript.to_dict())
            return 0

        if args.command == "export":
            transcript = store.get(args.transcript_id)
            if transcript is None:
                print("transcript not found", file=sys.stderr)
                return 3
            output = args.output or (store.exports / f"{transcript.id}.{args.format}")
            print(export_transcript(transcript, args.format, output))
            return 0

        if args.command == "delete":
            deleted = store.delete(args.transcript_id, delete_audio=args.delete_audio)
            if not deleted:
                print("transcript not found", file=sys.stderr)
                return 3
            _json({"deleted": True, "id": args.transcript_id})
            return 0

        if args.command == "transcribe":
            settings = _load_effective_settings(args)
            if settings.engine == "openai-cloud":
                if not args.allow_cloud_upload:
                    raise ValueError(
                        "OpenAI transcription requires --allow-cloud-upload; audio is never "
                        "uploaded silently"
                    )
                if settings.offline_only:
                    raise ValueError("offline_only blocks cloud transcription")
                from .engines.openai_cloud import OpenAICloudEngine

                # Validate consent and file limits before LocalStore reads/copies the source.
                OpenAICloudEngine.validate_upload(args.file)
            dictionary = TerminologyDictionary.load(settings.dictionary_path)
            controller = TranscriptionJobController(store, cache_dir())
            try:
                transcript = controller.run(
                    args.file,
                    settings,
                    dictionary,
                    timeout_seconds=args.timeout or settings.task_timeout_seconds,
                    progress=lambda phase, elapsed: print(
                        f"{phase}: {elapsed:.1f}s",
                        file=sys.stderr,
                    ),
                )
            finally:
                controller.close()
            if args.export_format:
                output = args.output or (store.exports / f"{transcript.id}.{args.export_format}")
                export_transcript(transcript, args.export_format, output)
                _json({"transcript": transcript.to_dict(), "export": str(output.resolve())})
            else:
                _json(transcript.to_dict())
            return 0

    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
