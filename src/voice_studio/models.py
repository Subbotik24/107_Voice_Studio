from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, ClassVar

SUPPORTED_LANGUAGES = ("auto", "uk", "cs", "en")
SUPPORTED_UI_LANGUAGES = ("uk", "cs", "en")
SUPPORTED_PROFILES = ("ollama-local", "whisper-local", "openai-cloud")
SUPPORTED_ENGINES = ("ollama", "faster-whisper", "openai-cloud")
SUPPORTED_CLEANUP_PROVIDERS = ("none", "ollama", "openai")
RETENTION_POLICIES = ("keep", "delete_after_transcription")
SUPPORTED_DEVICES = ("auto", "cpu", "cuda")
SUPPORTED_COMPUTE_TYPES = (
    "default",
    "auto",
    "int8",
    "int8_float32",
    "int8_float16",
    "int8_bfloat16",
    "int16",
    "float16",
    "float32",
    "bfloat16",
)
PROFILE_INVARIANTS = {
    "ollama-local": ("ollama", "ollama", True, True),
    "whisper-local": ("faster-whisper", "none", False, True),
    "openai-cloud": ("openai-cloud", "openai", False, False),
}


def validate_hardware_options(device: str, compute_type: str) -> None:
    """Validate the supported faster-whisper/CTranslate2 option vocabulary."""

    for field_name, value, allowed in (
        ("device", device, SUPPORTED_DEVICES),
        ("compute_type", compute_type, SUPPORTED_COMPUTE_TYPES),
    ):
        if value not in allowed:
            rendered = ", ".join(allowed)
            raise ValueError(
                f"unsupported {field_name} '{value}'; allowed values: {rendered}"
            )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Segment:
    start: float
    end: float
    text: str
    corrected_text: str | None = None
    language: str | None = None
    confidence: float | None = None

    @property
    def display_text(self) -> str:
        return self.corrected_text if self.corrected_text is not None else self.text

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        allowed = cls.__dataclass_fields__.keys()
        values = {key: value for key, value in data.items() if key in allowed}
        return cls(**values)


@dataclass
class Transcript:
    id: str
    created_at: str
    source_name: str
    source_sha256: str
    language: str
    engine: str
    model: str
    raw_text: str
    corrected_text: str
    segments: list[Segment] = field(default_factory=list)
    dictionary_version: str = "none"
    audio_retained: bool = True
    source_path: str | None = None
    status: str = "completed"
    audio_seconds: float = 0.0
    real_time_factor: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["segments"] = [segment.to_dict() for segment in self.segments]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        values = dict(data)
        values["segments"] = [
            Segment.from_dict(item) for item in values.get("segments", [])
        ]
        # Compatibility with Local Voice & Transcribe 0.1 payloads.
        values.setdefault("engine", "faster-whisper")
        values.setdefault("audio_seconds", 0.0)
        values.setdefault("real_time_factor", None)
        values.setdefault("error", None)
        values.setdefault("metadata", {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass
class Settings:
    STRING_FIELDS: ClassVar[tuple[str, ...]] = (
        "profile",
        "language",
        "ui_language",
        "engine",
        "model",
        "device",
        "compute_type",
        "hotkey",
        "retention",
        "dictionary_path",
        "cloud_provider",
        "cleanup_provider",
        "ollama_model",
        "openai_transcription_model",
        "openai_cleanup_model",
        "sync_folder",
    )
    BOOLEAN_FIELDS: ClassVar[tuple[str, ...]] = (
        "auto_copy",
        "offline_only",
        "automatic_cleanup",
        "vad_filter",
        "sync_enabled",
        "sync_include_audio",
    )

    profile: str = "ollama-local"
    language: str = "uk"
    ui_language: str = "uk"
    engine: str = "ollama"
    model: str = "small"
    device: str = "auto"
    compute_type: str = "default"
    hotkey: str = "<f13>"
    retention: str = "keep"
    dictionary_path: str = ""
    auto_copy: bool = False
    offline_only: bool = True
    automatic_cleanup: bool = True
    vad_filter: bool = True
    task_timeout_seconds: int = 7_200
    cloud_provider: str = "openai"
    cleanup_provider: str = "ollama"
    ollama_model: str = ""
    openai_transcription_model: str = "gpt-transcribe"
    openai_cleanup_model: str = "gpt-4.1-mini-2025-04-14"
    sync_folder: str = ""
    sync_enabled: bool = False
    sync_include_audio: bool = False

    def _validate_types(self) -> None:
        for name in self.STRING_FIELDS:
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"settings.{name} must be a string")
        for name in self.BOOLEAN_FIELDS:
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"settings.{name} must be a boolean")
        if type(self.task_timeout_seconds) is not int:
            raise ValueError("settings.task_timeout_seconds must be an integer")

    def validate_profile_invariants(self) -> None:
        if self.profile not in SUPPORTED_PROFILES:
            raise ValueError(f"unsupported profile: {self.profile}")
        relevant_values = (self.profile, self.engine, self.cleanup_provider)
        if not all(isinstance(value, str) for value in relevant_values) or any(
            type(value) is not bool for value in (self.automatic_cleanup, self.offline_only)
        ):
            raise ValueError(f"inconsistent settings for profile: {self.profile}")
        actual_profile_fields = (
            self.engine,
            self.cleanup_provider,
            self.automatic_cleanup,
            self.offline_only,
        )
        if actual_profile_fields != PROFILE_INVARIANTS[self.profile]:
            raise ValueError(f"inconsistent settings for profile: {self.profile}")

    def validate(self) -> None:
        self._validate_types()
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {self.language}")
        if self.ui_language not in SUPPORTED_UI_LANGUAGES:
            raise ValueError(f"unsupported interface language: {self.ui_language}")
        if self.profile not in SUPPORTED_PROFILES:
            raise ValueError(f"unsupported profile: {self.profile}")
        if self.engine not in SUPPORTED_ENGINES:
            raise ValueError(f"unsupported engine: {self.engine}")
        if self.retention not in RETENTION_POLICIES:
            raise ValueError(f"unsupported retention policy: {self.retention}")
        if not self.model.strip():
            raise ValueError("model cannot be empty")
        if not self.device.strip():
            raise ValueError("device cannot be empty")
        if not self.compute_type.strip():
            raise ValueError("compute_type cannot be empty")
        validate_hardware_options(self.device, self.compute_type)
        if not self.hotkey.strip():
            raise ValueError("hotkey cannot be empty")
        if not 60 <= self.task_timeout_seconds <= 86_400:
            raise ValueError("task_timeout_seconds must be between 60 and 86400")
        if self.cloud_provider != "openai":
            raise ValueError(f"unsupported cloud provider: {self.cloud_provider}")
        if self.cleanup_provider not in SUPPORTED_CLEANUP_PROVIDERS:
            raise ValueError(f"unsupported cleanup provider: {self.cleanup_provider}")
        if self.offline_only and self.engine == "openai-cloud":
            raise ValueError("offline_only blocks the OpenAI cloud engine")
        self.validate_profile_invariants()
        if not self.openai_transcription_model.strip() or not self.openai_cleanup_model.strip():
            raise ValueError("OpenAI model identifiers cannot be empty")
        if self.sync_enabled and not self.sync_folder.strip():
            raise ValueError("sync_folder must be set when sync_enabled is true")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        data = dict(data)
        if "profile" not in data:
            legacy_engine = data.get("engine")
            legacy_profiles = {
                "faster-whisper": "whisper-local",
                "openai-cloud": "openai-cloud",
                "ollama": "ollama-local",
            }
            profile = (
                legacy_profiles.get(legacy_engine)
                if isinstance(legacy_engine, str)
                else None
            )
            if legacy_engine is None:
                profile = "ollama-local"
            data["profile"] = profile or "ollama-local"
            if profile is not None:
                engine, cleanup_provider, automatic_cleanup, offline_only = (
                    PROFILE_INVARIANTS[profile]
                )
                invariants = {
                    "engine": engine,
                    "cleanup_provider": cleanup_provider,
                    "automatic_cleanup": automatic_cleanup,
                    "offline_only": offline_only,
                }
                if legacy_engine is None:
                    for key, value in invariants.items():
                        data.setdefault(key, value)
                else:
                    data.update(invariants)
        if data.get("hotkey") == "<ctrl>+<alt>+space":
            data["hotkey"] = "<ctrl>+<alt>+<space>"
        allowed = {item.name for item in fields(cls) if item.init}
        settings = cls(**{key: value for key, value in data.items() if key in allowed})
        settings.validate()
        return settings
