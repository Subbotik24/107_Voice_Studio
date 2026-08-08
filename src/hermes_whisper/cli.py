from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .bundle import create_model_bundle, verify_model_bundle
from .checkpoint import load_model_checkpoint, verify_checkpoint
from .config import ExperimentConfig
from .decoding import transcribe_file
from .evaluation import evaluate_records
from .manifest import load_manifest, summarize_manifest
from .smoke import run_smoke_training
from .tokenizer import HermesTokenizer
from .trainer import Trainer


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _command_doctor(_args: argparse.Namespace) -> int:
    modules = {
        name: bool(importlib.util.find_spec(name))
        for name in ("numpy", "torch", "soundfile", "safetensors")
    }
    payload = {
        "status": "PASS" if modules["numpy"] and modules["torch"] else "INCOMPLETE",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": modules,
        "cuda_available": False,
        "mps_available": False,
    }
    if modules["torch"]:
        import torch

        payload["torch"] = torch.__version__
        payload["cuda_available"] = torch.cuda.is_available()
        payload["mps_available"] = bool(
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        )
    _print_json(payload)
    return 0 if payload["status"] == "PASS" else 2


def _command_init_tokenizer(args: argparse.Namespace) -> int:
    texts: list[str] = []
    for corpus in args.corpus:
        texts.extend(Path(corpus).read_text(encoding="utf-8").splitlines())
    for manifest_path in args.manifest:
        records = load_manifest(
            manifest_path,
            allowed_languages=tuple(args.languages),
            require_audio_exists=False,
        )
        texts.extend(record.text for record in records)
    tokenizer = HermesTokenizer.train(
        texts,
        target_text_vocab_size=args.text_vocab_size,
        min_pair_frequency=args.min_pair_frequency,
        languages=tuple(args.languages),
        timestamp_resolution=args.timestamp_resolution,
        max_timestamp_seconds=args.max_timestamp_seconds,
    )
    tokenizer.save(args.output)
    _print_json(
        {
            "output": str(Path(args.output).resolve()),
            "text_vocab_size": tokenizer.text_vocab_size,
            "total_vocab_size": tokenizer.vocab_size,
            "languages": tokenizer.languages,
        }
    )
    return 0


def _command_validate_manifest(args: argparse.Namespace) -> int:
    records = load_manifest(
        args.manifest,
        allowed_languages=tuple(args.languages),
        require_audio_exists=not args.skip_audio_check,
    )
    _print_json({"status": "PASS", **summarize_manifest(records)})
    return 0


def _command_inspect(args: argparse.Namespace) -> int:
    config = ExperimentConfig.load(args.config)
    tokenizer = HermesTokenizer.load(args.tokenizer) if args.tokenizer else None
    if tokenizer is not None:
        if tokenizer.languages != config.model.languages:
            raise ValueError("tokenizer languages differ from config languages")
        config = replace(config, model=config.model.with_vocab_size(tokenizer.vocab_size))
    payload: dict[str, Any] = {
        "config_fingerprint": config.fingerprint(),
        "model": config.model.name,
        "languages": config.model.languages,
        "vocab_size": config.model.vocab_size,
        "parameter_estimate": config.model.estimated_parameter_count(config.audio.n_mels),
        "max_audio_seconds": config.audio.max_audio_seconds,
    }
    if importlib.util.find_spec("torch") and tokenizer is not None:
        from .model import HermesSpeechModel

        model = HermesSpeechModel(config.audio, config.model, pad_id=tokenizer.pad_id)
        payload["parameter_count"] = model.parameter_count
    _print_json(payload)
    return 0


def _command_train(args: argparse.Namespace) -> int:
    config = ExperimentConfig.load(args.config)
    tokenizer = HermesTokenizer.load(args.tokenizer)
    train_records = load_manifest(
        args.train_manifest,
        allowed_languages=config.model.languages,
        require_audio_exists=True,
    )
    validation_records = load_manifest(
        args.validation_manifest,
        allowed_languages=config.model.languages,
        require_audio_exists=True,
    )
    trainer = Trainer(
        config,
        tokenizer,
        train_records,
        validation_records,
        args.run_directory,
        device=args.device,
    )
    if args.resume:
        trainer.resume(args.resume)
    checkpoint = trainer.train()
    if trainer.is_primary:
        _print_json(
            {
                "status": "PASS",
                "step": trainer.global_step,
                "checkpoint": str(checkpoint.resolve()) if checkpoint else None,
                "parameters": trainer.unwrapped_model.parameter_count,
            }
        )
    return 0


def _command_transcribe(args: argparse.Namespace) -> int:
    from .decoding import select_device

    device = select_device(args.device)
    model, config, tokenizer, metadata = load_model_checkpoint(
        args.checkpoint,
        device=device,
    )
    result = transcribe_file(
        model,
        config,
        tokenizer,
        args.audio,
        language=args.language,
        device=device,
        overlap_seconds=args.overlap_seconds,
    )
    payload = {
        "checkpoint_step": metadata["step"],
        **result.to_dict(),
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _print_json(payload)
    return 0


def _command_evaluate(args: argparse.Namespace) -> int:
    from .decoding import select_device

    device = select_device(args.device)
    model, config, tokenizer, metadata = load_model_checkpoint(
        args.checkpoint,
        device=device,
    )
    records = load_manifest(
        args.manifest,
        allowed_languages=config.model.languages,
        require_audio_exists=True,
    )
    metrics = evaluate_records(
        model,
        config,
        tokenizer,
        records,
        device=device,
        output_jsonl=args.predictions,
        language_mode=args.language_mode,
    )
    _print_json({"checkpoint_step": metadata["step"], **metrics})
    return 0


def _command_verify_checkpoint(args: argparse.Namespace) -> int:
    _print_json({"status": "PASS", **verify_checkpoint(args.checkpoint)})
    return 0


def _command_bundle(args: argparse.Namespace) -> int:
    _print_json(create_model_bundle(args.checkpoint, args.output))
    return 0


def _command_verify_bundle(args: argparse.Namespace) -> int:
    _print_json({"status": "PASS", **verify_model_bundle(args.bundle)})
    return 0


def _command_smoke(args: argparse.Namespace) -> int:
    _print_json(
        run_smoke_training(
            args.work_directory,
            clean=not args.keep,
            device=args.device,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-whisper",
        description="Train and run an independent Ukrainian/Czech speech model.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check the local runtime")
    doctor.set_defaults(handler=_command_doctor)

    tokenizer = subparsers.add_parser("init-tokenizer", help="train a new UTF-8 BPE")
    tokenizer.add_argument("--corpus", action="append", default=[])
    tokenizer.add_argument("--manifest", action="append", default=[])
    tokenizer.add_argument("--languages", nargs="+", default=["uk", "cs"])
    tokenizer.add_argument("--text-vocab-size", type=int, default=8192)
    tokenizer.add_argument("--min-pair-frequency", type=int, default=10)
    tokenizer.add_argument("--timestamp-resolution", type=float, default=0.02)
    tokenizer.add_argument("--max-timestamp-seconds", type=float, default=30.0)
    tokenizer.add_argument("--output", required=True)
    tokenizer.set_defaults(handler=_command_init_tokenizer)

    manifest = subparsers.add_parser("validate-manifest", help="validate JSONL data")
    manifest.add_argument("manifest")
    manifest.add_argument("--languages", nargs="+", default=["uk", "cs"])
    manifest.add_argument("--skip-audio-check", action="store_true")
    manifest.set_defaults(handler=_command_validate_manifest)

    inspect = subparsers.add_parser("inspect", help="inspect model dimensions")
    inspect.add_argument("--config", required=True)
    inspect.add_argument("--tokenizer")
    inspect.set_defaults(handler=_command_inspect)

    train = subparsers.add_parser("train", help="train from random initialization")
    train.add_argument("--config", required=True)
    train.add_argument("--tokenizer", required=True)
    train.add_argument("--train-manifest", required=True)
    train.add_argument("--validation-manifest", required=True)
    train.add_argument("--run-directory", required=True)
    train.add_argument("--resume")
    train.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    train.set_defaults(handler=_command_train)

    transcribe = subparsers.add_parser("transcribe", help="transcribe with trained weights")
    transcribe.add_argument("--checkpoint", required=True)
    transcribe.add_argument("--audio", required=True)
    transcribe.add_argument("--language", default="auto")
    transcribe.add_argument("--device", default="auto")
    transcribe.add_argument("--overlap-seconds", type=float, default=1.0)
    transcribe.add_argument("--output")
    transcribe.set_defaults(handler=_command_transcribe)

    evaluate = subparsers.add_parser("evaluate", help="calculate WER, CER, and RTF")
    evaluate.add_argument("--checkpoint", required=True)
    evaluate.add_argument("--manifest", required=True)
    evaluate.add_argument("--device", default="auto")
    evaluate.add_argument(
        "--language-mode",
        choices=("auto", "reference"),
        default="auto",
    )
    evaluate.add_argument("--predictions")
    evaluate.set_defaults(handler=_command_evaluate)

    checkpoint = subparsers.add_parser("verify-checkpoint", help="verify checkpoint hashes")
    checkpoint.add_argument("checkpoint")
    checkpoint.set_defaults(handler=_command_verify_checkpoint)

    bundle = subparsers.add_parser("bundle", help="create a distributable .hws model")
    bundle.add_argument("--checkpoint", required=True)
    bundle.add_argument("--output", required=True)
    bundle.set_defaults(handler=_command_bundle)

    verify_bundle = subparsers.add_parser("verify-bundle", help="verify a .hws model")
    verify_bundle.add_argument("bundle")
    verify_bundle.set_defaults(handler=_command_verify_bundle)

    smoke = subparsers.add_parser("smoke-test", help="run two real optimization steps")
    smoke.add_argument("--work-directory", default="artifacts/smoke")
    smoke.add_argument("--keep", action="store_true")
    smoke.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    smoke.set_defaults(handler=_command_smoke)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-tokenizer" and not (args.corpus or args.manifest):
        parser.error("init-tokenizer requires --corpus or --manifest")
    try:
        return int(args.handler(args))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
