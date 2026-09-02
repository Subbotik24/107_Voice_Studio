from __future__ import annotations

import json
import queue
import re
import threading
import time
import tkinter as tk
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as day_time
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .backup import (
    create_backup,
    recover_interrupted_restore,
    restore_backup,
    verify_backup,
)
from .batch import BatchQueue
from .cloud_cleanup import list_ollama_models, propose_cleanup
from .cloud_secrets import (
    delete_openai_api_key,
    get_openai_api_key,
    openai_key_status,
    set_openai_api_key,
)
from .config import cache_dir, config_dir, data_dir, load_settings, save_settings, settings_path
from .dashboard import HistoryFilter
from .dictionary import DictionaryMergePreview, DictionaryRule, TerminologyDictionary, merge_preview
from .dictionary_store import DictionaryRepository
from .editor_state import snapshot_editor
from .editor_tools import (
    ConfidenceEntry,
    FillerMatch,
    TextMatch,
    confidence_entries,
    find_filler_matches,
    find_matches,
    remove_matches,
    segment_spans,
)
from .exporters import export_transcript
from .hardware import HardwareDetectionResult, detect_hardware
from .help_content import (
    HelpTopic,
    help_anchor,
    load_help_topics,
    parse_markdown,
    resolve_help_asset,
    resolve_help_root,
    search_help_topics,
    split_help_target,
)
from .hotkey import GlobalHotkey, hotkey_from_tk_event
from .i18n import UI_LANGUAGE_CHOICES, translate
from .jobs import JobCancelled, TranscriptionJobController
from .model_catalog import ModelCatalog
from .models import (
    SUPPORTED_COMPUTE_TYPES,
    SUPPORTED_DEVICES,
    SUPPORTED_ENGINES,
    SUPPORTED_LANGUAGES,
    Segment,
    Settings,
    Transcript,
)
from .playback import SUPPORTED_SPEEDS, AudioPlayer
from .profiles import (
    apply_profile,
    discover_ollama_model_catalog,
    with_preferred_ollama_model,
)
from .recorder import AudioRecorder
from .smart_text import (
    MAX_PARAGRAPH_GAP_SECONDS,
    MAX_PARAGRAPH_SECONDS,
    MIN_PARAGRAPH_SECONDS,
    SmartTextOptions,
    format_timestamp,
    render_markdown,
    render_plain,
    speaker_labels_from_metadata,
)
from .storage import LocalStore
from .subtitles import editable_text
from .sync_folder import (
    SyncFolderError,
    SyncSummary,
    mirror_all,
    mirror_transcript,
    validate_sync_root,
)

_BACKUP_PASSPHRASE_REQUIRED = "backup is encrypted; a passphrase is required"

# Visible build stamp in the window title so an outdated launch is obvious.
APP_BUILD = "2026-09-02.3"
EDITOR_FIND_TAG = "editor_find"
EDITOR_CONFIDENCE_TAG = "editor_confidence"
FILLER_CONTEXT_WIDTH = 30
CONFIDENCE_SNIPPET_WIDTH = 48
DEFAULT_CONFIDENCE_THRESHOLD = "0.60"
SMART_TEXT_SNIPPET_WIDTH = 40
DEFAULT_SMART_TEXT_GAP = "2.0"
DEFAULT_SMART_TEXT_MAX = "90"
BATCH_COLUMNS = (
    ("file", "batch_column_file", 240),
    ("status", "batch_column_status", 120),
    ("seconds", "batch_column_seconds", 90),
    ("error", "batch_column_error", 280),
)

_ERROR_TYPE_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Cancelled):\s+")


def _plain_error_text(error: object) -> str:
    """Drop the ``SomeError:`` prefix the job worker adds for diagnostics."""

    return _ERROR_TYPE_PREFIX.sub("", str(error), count=1).strip() or str(error)


def _write_text_atomically(destination: Path, content: str) -> Path:
    """Write one exported text file the way ``exporters`` does: never partial."""

    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


MEDIA_FILETYPES = [
    ("Audio/video", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac *.mp4 *.mov *.mkv *.webm"),
    ("All files", "*.*"),
]


@dataclass(frozen=True)
class StudioTheme:
    canvas: str
    surface: str
    surface_muted: str
    accent: str
    accent_hover: str
    accent_soft: str
    ink: str
    muted_ink: str
    primary: str
    primary_hover: str
    border: str
    disabled: str
    selection: str
    ui_font: str
    ui_font_fallback: str
    mono_font: str
    mono_font_fallback: str


VOICE_STUDIO_THEME = StudioTheme(
    canvas="#f6eddc",
    surface="#fffaf1",
    surface_muted="#efe3cd",
    accent="#e99016",
    accent_hover="#d9800c",
    accent_soft="#f7e6cd",
    ink="#2a2119",
    muted_ink="#7c6b5d",
    primary="#5b4332",
    primary_hover="#483225",
    border="#dfcaa9",
    disabled="#eadfc8",
    selection="#f2c77e",
    ui_font="Bahnschrift",
    ui_font_fallback="Segoe UI",
    mono_font="Cascadia Mono",
    mono_font_fallback="Consolas",
)


def studio_icon_pixel(x: int, y: int, *, size: int = 32, radius: int = 8) -> bool:
    """Return whether a pixel belongs to the rounded VOICE Studio app mark."""

    if not (0 <= x < size and 0 <= y < size):
        return False
    nearest_x = min(max(x, radius), size - radius - 1)
    nearest_y = min(max(y, radius), size - radius - 1)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius**2


@dataclass(frozen=True)
class StudioLayout:
    sidebar_width: int
    compact_sidebar: bool
    show_readiness: bool


def studio_layout_for_width(width: int) -> StudioLayout:
    """Apply the approved full and compact sidebar proportions."""

    if width >= 1040:
        return StudioLayout(250, False, True)
    if width >= 760:
        return StudioLayout(250, False, False)
    return StudioLayout(88, True, False)


def initial_window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    """Fit the reference layout on the current display without shrinking its menu."""

    width = max(900, min(1320, screen_width - 16))
    height = max(640, min(820, screen_height - 32))
    return width, height


def studio_content_metrics(width: int) -> tuple[tuple[int, int, int, int], int, int]:
    """Return body padding, panel gap, and subtitle wrap for the active width."""

    if 1040 <= width < 1200:
        return (16, 18, 16, 20), 12, 340
    return (28, 22, 28, 24), 18, 560


DASHBOARD_EMPTY_VALUE = "—"
DASHBOARD_RECENT_LIMIT = 5
DASHBOARD_ACTIVITY_DAYS = 14


def format_audio_duration(seconds: float) -> str:
    """Render accumulated audio length as H:MM:SS."""

    total = max(0, int(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def format_speed_multiplier(value: float | None) -> str:
    return DASHBOARD_EMPTY_VALUE if value is None else f"×{value:.1f}"


def format_count_ranking(counts: tuple[tuple[str, int], ...], *, top: int = 3) -> str:
    if not counts:
        return DASHBOARD_EMPTY_VALUE
    return "\n".join(f"{name} — {count}" for name, count in counts[:top])


def history_day_bounds(value: str, *, end_of_day: bool) -> datetime | None:
    """Turn a YYYY-MM-DD entry into an inclusive UTC day bound, or None if invalid."""

    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    moment = day_time(23, 59, 59, 999999) if end_of_day else day_time(0, 0, 0)
    return datetime.combine(day, moment, tzinfo=UTC)


class VoiceStudioApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"VOICE Studio · {APP_BUILD}")
        self._install_window_icon()
        width, height = initial_window_size(
            self.winfo_screenwidth(),
            self.winfo_screenheight(),
        )
        self.geometry(f"{width}x{height}")
        self.minsize(900, 640)
        try:
            self.settings = load_settings()
            settings_error = ""
        except ValueError as exc:
            self.settings = Settings()
            settings_error = str(exc)
        self._configure_theme()
        # A restore interrupted by a process death must be settled before the
        # store is opened, otherwise an empty LocalStore would be created beside
        # the two directories that hold the real data.
        self._restore_recovery = self._settle_interrupted_restore()
        self.store = LocalStore(data_dir())
        self.job_controller = TranscriptionJobController(self.store, cache_dir())
        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self._playback_ticker: str | None = None
        self._playback_error_reported: str | None = None
        self.hotkey: GlobalHotkey | None = None
        self.current: Transcript | None = None
        self._editor_baseline = snapshot_editor("", {})
        self._cleanup_snapshot = None
        self._cleanup_transcript_id: str | None = None
        self._cleanup_provider = "openai"
        self._cleanup_model = self.settings.openai_cleanup_model
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._shutdown_event = threading.Event()
        self._worker_lock = threading.RLock()
        self._worker_threads: dict[str, threading.Thread] = {}
        self._closing = False
        self._shutdown_residue_threads: tuple[str, ...] = ()
        self._history_items: list[Transcript] = []
        self._busy = False
        self.batch_queue = BatchQueue()
        self._batch_owned = False
        self._batch_started: float | None = None
        self._batch_last_transcript: Transcript | None = None
        self._smart_text_rendered = ""
        self._continuous_recording = False
        self._pending_microphone_files: set[Path] = set()
        self._active_recording_path: Path | None = None
        self._ambiguous_microphone_files: set[Path] = set()
        self._recording_residue_diagnostics: list[str] = []
        self._cancel_event = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._help_page_built = False
        self._help_images: list[tk.PhotoImage] = []
        self._installed_ollama_audio_models: list[str] = []
        self._installed_ollama_all_models: list[str] = []
        self._ollama_discovery_error = ""
        self._ollama_discovery_thread: threading.Thread | None = None
        self._settings_ollama_combo: ttk.Combobox | None = None
        self._settings_hardware_device_combo: ttk.Combobox | None = None
        self._settings_hardware_compute_combo: ttk.Combobox | None = None
        self._settings_info_var: tk.StringVar | None = None
        self._settings_ollama_status_var: tk.StringVar | None = None
        self._settings_variables: dict[str, tk.Variable] = {}
        self._settings_baseline: dict[str, Any] = {}
        self._settings_save: Callable[[], bool] = lambda: False
        self._settings_capture_binding: str | None = None
        self._settings_return_page = "dashboard"
        self.dictionary_repository = DictionaryRepository(config_dir())
        self.dictionary = TerminologyDictionary()
        self.dictionary_read_only = False
        self._dictionary_dirty = False
        self._build_ui()
        self._report_restore_recovery()
        self._settle_model_catalog()
        self._refresh_history()
        self._refresh_dashboard()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._start_hotkey()
        self._start_ollama_model_discovery()
        if settings_error:
            self.after(
                250,
                lambda: messagebox.showwarning(
                    self._t("settings"),
                    self._t("settings_file_error", error=settings_error),
                ),
            )

    def _settle_interrupted_restore(self) -> dict[str, Any]:
        """Finish or undo an interrupted restore without preventing startup.

        An encrypted restore interrupted with its settings payload pending
        needs the passphrase once, here on the main thread. Cancel keeps the
        sidecar and journal untouched and startup continues; the passphrase
        is passed straight to the backup API and never stored.
        """

        try:
            result = recover_interrupted_restore(data_dir(), settings_target=settings_path())
            if result.get("action") != "passphrase_required":
                return result
            passphrase = simpledialog.askstring(
                self._t("backup"),
                self._t("backup_passphrase_required"),
                show="*",
                parent=self,
            )
            if passphrase is None:
                return result
            try:
                return recover_interrupted_restore(
                    data_dir(), settings_target=settings_path(), passphrase=passphrase
                )
            finally:
                del passphrase
        except Exception as exc:  # startup must survive any journal defect
            return {"status": "FAIL", "action": "none", "error": str(exc)}

    def _report_restore_recovery(self) -> None:
        result = self._restore_recovery
        action = result.get("action", "none")
        if result.get("status") != "PASS":
            message = self._t("restore_recovery_failed", error=result.get("error", "unknown"))
            self.status.set(message)
            self.after(250, lambda: messagebox.showwarning(self._t("backup"), message))
            return
        key = {
            "completed": "restore_recovered",
            "settings_completed": "restore_recovered",
            "rolled_back": "restore_rolled_back",
            "staging_discarded": "restore_staging_discarded",
            "passphrase_required": "restore_passphrase_required",
        }.get(action)
        if key is None:
            return
        values = {"records": result.get("records") or 0} if key == "restore_recovered" else {}
        self.status.set(self._t(key, **values))

    def _settle_model_catalog(self) -> dict[str, Any]:
        """Reconcile local model state without preventing the GUI from starting."""

        try:
            result = ModelCatalog(self.store.models).reconcile()
        except Exception as exc:  # startup must survive any catalog defect
            result = {"status": "FAIL", "action": "attention", "error": str(exc)}

        status = result.get("status")
        action = result.get("action", "none")
        if status == "FAIL":
            message = self._t("model_catalog_repair_failed", error=result.get("error", "unknown"))
            self.status.set(message)
            self.after(250, lambda: messagebox.showwarning(self._t("models"), message))
            return result

        if action == "repaired" or action == "attention":
            if result.get("catalog_quarantined"):
                message = self._t("model_catalog_rebuilt", path=result["catalog_quarantined"])
            elif action == "repaired":
                message = self._t(
                    "model_catalog_repaired",
                    adopted=", ".join(str(item) for item in result.get("adopted") or []),
                    dropped=", ".join(str(item) for item in result.get("dropped") or []),
                )
            else:
                details = (
                    "; ".join(
                        f"{item.get('id', '?')}: {item.get('reason', 'unknown')}"
                        if isinstance(item, dict)
                        else str(item)
                        for item in result.get("blocked") or []
                    )
                    or "unknown model catalog issue"
                )
                message = self._t("model_catalog_attention", details=details)
            self.status.set(message)
            if action == "attention":
                self.after(250, lambda: messagebox.showwarning(self._t("models"), message))
        return result

    def _install_window_icon(self) -> None:
        icon = tk.PhotoImage(master=self, width=32, height=32)
        for y in range(32):
            for x in range(32):
                if studio_icon_pixel(x, y):
                    icon.put(VOICE_STUDIO_THEME.accent, to=(x, y))
        self._window_icon = icon
        self.iconphoto(True, icon)

    def _start_ollama_model_discovery(self) -> None:
        running = self._ollama_discovery_thread
        if running is not None and running.is_alive():
            return

        def discover() -> None:
            try:
                catalog = discover_ollama_model_catalog()
                self._post_event(
                    "ollama_models",
                    {
                        "models": catalog.get("audio", []),
                        "all_models": catalog.get("all", []),
                        "error": "",
                    },
                )
            except Exception as exc:
                self._post_event(
                    "ollama_models",
                    {"models": [], "all_models": [], "error": str(exc)[:500]},
                )

        thread = self._start_worker("ollama-model-discovery", discover)
        self._assign_worker_alias("ollama-model-discovery", thread, "_ollama_discovery_thread")

    def _start_worker(
        self,
        role: str,
        target: Callable[[], None],
        *,
        daemon: bool = True,
    ) -> threading.Thread | None:
        """Start and retain one named GUI worker until it has really stopped."""

        shutdown = self.__dict__.setdefault("_shutdown_event", threading.Event())
        lock = self.__dict__.setdefault("_worker_lock", threading.RLock())
        workers = self.__dict__.setdefault("_worker_threads", {})
        with lock:
            if shutdown.is_set():
                return None
            previous = workers.get(role)
            if previous is not None and previous.is_alive():
                raise RuntimeError(f"worker '{role}' is already running")
            holder: dict[str, threading.Thread] = {}

            def run() -> None:
                try:
                    target()
                finally:
                    with lock:
                        if workers.get(role) is holder.get("thread"):
                            workers.pop(role, None)
                        if role == "maintenance" and self.__dict__.get(
                            "_maintenance_thread"
                        ) is holder.get("thread"):
                            self._maintenance_thread = None
                        if role == "ollama-model-discovery" and self.__dict__.get(
                            "_ollama_discovery_thread"
                        ) is holder.get("thread"):
                            self._ollama_discovery_thread = None

            thread = threading.Thread(
                target=run,
                daemon=daemon,
                name=f"voice-studio-{role}",
            )
            holder["thread"] = thread
            workers[role] = thread
            try:
                thread.start()
            except BaseException:
                if workers.get(role) is thread:
                    workers.pop(role, None)
                raise
            return thread

    def _post_event(self, event: str, value: Any) -> bool:
        """Publish worker output only while the Tk window accepts events."""

        shutdown = self.__dict__.setdefault("_shutdown_event", threading.Event())
        lock = self.__dict__.setdefault("_worker_lock", threading.RLock())
        with lock:
            if shutdown.is_set():
                return False
            self.events.put((event, value))
        return True

    def _assign_worker_alias(
        self,
        role: str,
        thread: threading.Thread | None,
        attribute: str,
    ) -> None:
        """Publish a compatibility handle only while its registry entry owns it."""

        lock = self.__dict__.setdefault("_worker_lock", threading.RLock())
        workers = self.__dict__.setdefault("_worker_threads", {})
        with lock:
            if thread is not None and workers.get(role) is thread:
                setattr(self, attribute, thread)
            elif self.__dict__.get(attribute) is thread:
                setattr(self, attribute, None)

    def _join_workers(self, timeout_seconds: float = 3.0) -> tuple[str, ...]:
        """Join daemon workers using one monotonic shutdown budget."""

        lock = self.__dict__.setdefault("_worker_lock", threading.RLock())
        workers = self.__dict__.setdefault("_worker_threads", {})
        with lock:
            snapshot = tuple(workers.items())
        current = threading.current_thread()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for _role, thread in snapshot:
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                if thread.is_alive():
                    thread.join(timeout=remaining)
            except (RuntimeError, OSError):
                continue
        return tuple(
            sorted(role for role, thread in snapshot if thread is not current and thread.is_alive())
        )

    def _build_ui(self) -> None:
        theme = VOICE_STUDIO_THEME
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar = ttk.Frame(self, width=250, style="Sidebar.TFrame")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(1, weight=1)

        brand = ttk.Frame(self.sidebar, padding=(18, 24, 18, 18), style="Sidebar.TFrame")
        brand.grid(row=0, column=0, sticky="ew")
        self.brand_mark = tk.Canvas(
            brand,
            width=34,
            height=34,
            background=theme.surface,
            borderwidth=0,
            highlightthickness=0,
        )
        self.brand_mark.create_polygon(
            9,
            1,
            25,
            1,
            33,
            9,
            33,
            25,
            25,
            33,
            9,
            33,
            1,
            25,
            1,
            9,
            smooth=True,
            splinesteps=12,
            fill=theme.accent,
            outline=theme.accent,
        )
        self.brand_mark.create_text(
            17,
            17,
            text="VO",
            fill=theme.ink,
            font=(theme.ui_font, 10, "bold"),
        )
        self.brand_mark.grid(row=0, column=0, sticky="w")
        self.brand_details = ttk.Frame(brand, style="Sidebar.TFrame")
        self.brand_details.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(self.brand_details, text="VOICE Studio", style="Brand.TLabel").pack(anchor="w")
        self.brand_subtitle_label = ttk.Label(
            self.brand_details,
            text=self._t("studio_subtitle"),
            style="SidebarMuted.TLabel",
        )
        self.brand_subtitle_label.pack(anchor="w", pady=(2, 0))

        navigation = ttk.Frame(self.sidebar, padding=(18, 12, 18, 12), style="Sidebar.TFrame")
        navigation.grid(row=1, column=0, sticky="nsew")
        self.dashboard_button = ttk.Button(
            navigation,
            text=self._t("dashboard"),
            command=lambda: self._show_page("dashboard"),
            style="Sidebar.TButton",
        )
        self.dashboard_button.pack(fill="x", pady=(0, 6))
        self.studio_button = ttk.Button(
            navigation,
            text=self._t("studio_nav"),
            command=lambda: self._show_page("studio"),
            style="Sidebar.TButton",
        )
        self.studio_button.pack(fill="x", pady=(0, 6))
        self.dictionary_button = ttk.Button(
            navigation,
            text=self._t("dictionary"),
            command=lambda: self._show_page("dictionary"),
            style="Sidebar.TButton",
        )
        self.dictionary_button.pack(fill="x", pady=6)
        self.history_nav_button = ttk.Button(
            navigation,
            text=self._t("history"),
            command=lambda: self._show_page("history"),
            style="Sidebar.TButton",
        )
        self.history_nav_button.pack(fill="x", pady=6)
        self.models_button = ttk.Button(
            navigation,
            text=self._t("models"),
            command=self._models_dialog,
            style="Sidebar.TButton",
        )
        self.models_button.pack(fill="x", pady=6)
        self.backup_button = ttk.Button(
            navigation,
            text=self._t("backup"),
            command=self._backup_dialog,
            style="Sidebar.TButton",
        )
        self.backup_button.pack(fill="x", pady=6)
        self.settings_button = ttk.Button(
            navigation,
            text=self._t("settings"),
            command=lambda: self._show_page("settings"),
            style="Sidebar.TButton",
        )
        self.settings_button.pack(fill="x", pady=6)
        self.help_button = ttk.Button(
            navigation,
            text=self._t("help"),
            command=lambda: self._show_page("help"),
            style="Sidebar.TButton",
        )
        self.help_button.pack(fill="x", pady=6)
        self._page_buttons = {
            "dashboard": self.dashboard_button,
            "studio": self.studio_button,
            "dictionary": self.dictionary_button,
            "history": self.history_nav_button,
            "settings": self.settings_button,
            "help": self.help_button,
        }
        self._sidebar_buttons = (
            (self.dashboard_button, "dashboard", "⌂"),
            (self.studio_button, "studio_nav", "●"),
            (self.dictionary_button, "dictionary", "≡"),
            (self.history_nav_button, "history", "▤"),
            (self.models_button, "models", "◆"),
            (self.backup_button, "backup", "↻"),
            (self.settings_button, "settings", "⚙"),
            (self.help_button, "help", "?"),
        )

        self.sidebar_footer = ttk.Frame(
            self.sidebar, padding=(18, 14, 18, 22), style="Sidebar.TFrame"
        )
        self.sidebar_footer.grid(row=2, column=0, sticky="ew")
        self.local_boundary_label = ttk.Label(
            self.sidebar_footer,
            text=self._t("local_boundary"),
            style="SidebarFooterTitle.TLabel",
        )
        self.local_boundary_label.pack(anchor="w")
        self.local_boundary_detail_label = ttk.Label(
            self.sidebar_footer,
            text=self._t("local_boundary_detail"),
            style="SidebarMuted.TLabel",
            wraplength=200,
        )
        self.local_boundary_detail_label.pack(anchor="w", pady=(4, 0))

        self.workspace = ttk.Frame(self, style="Canvas.TFrame")
        self.workspace.grid(row=0, column=1, sticky="nsew")
        self.workspace.grid_rowconfigure(1, weight=1)
        self.workspace.grid_columnconfigure(0, weight=1)

        topbar = ttk.Frame(self.workspace, padding=(28, 16), style="Topbar.TFrame")
        topbar.grid(row=0, column=0, sticky="ew")
        self.engine_label = tk.StringVar()
        ttk.Label(topbar, textvariable=self.engine_label, style="TopbarMuted.TLabel").pack(
            side="right"
        )
        self.topbar_context_label = ttk.Label(
            topbar, text=self._t("workspace_context"), style="TopbarTitle.TLabel"
        )
        self.topbar_context_label.pack(side="left")

        self.workspace_body = ttk.Frame(
            self.workspace, padding=(28, 22, 28, 24), style="Canvas.TFrame"
        )
        self.workspace_body.grid(row=1, column=0, sticky="nsew")
        self.workspace_body.grid_rowconfigure(0, weight=1)
        self.workspace_body.grid_columnconfigure(0, weight=1)
        self.workspace_body.grid_columnconfigure(1, minsize=214)

        self.page_host = ttk.Frame(self.workspace_body, style="Canvas.TFrame")
        self.page_host.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)

        self.status = tk.StringVar(value=self._t("ready_local"))
        self.status_bar = ttk.Frame(self.workspace_body, padding=(12, 8), style="Status.TFrame")
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(self.status_bar, text="●", style="StatusDot.TLabel").pack(side="left")
        ttk.Label(self.status_bar, textvariable=self.status, style="Status.TLabel").pack(
            side="left", padx=(7, 0)
        )
        self.status_progress = ttk.Progressbar(
            self.status_bar, mode="indeterminate", length=110, maximum=100
        )
        self.status_batch_var = tk.StringVar(value="")
        self.status_batch_label = ttk.Label(
            self.status_bar, textvariable=self.status_batch_var, style="Status.TLabel"
        )
        self.status_batch_label.pack(side="right", padx=(0, 10))

        self.dashboard_page = ttk.Frame(self.page_host, padding=28, style="Canvas.TFrame")
        self.dashboard_page.grid(row=0, column=0, sticky="nsew")
        self.dashboard_page.grid_columnconfigure(0, weight=1)
        self.dashboard_title_label = ttk.Label(
            self.dashboard_page, text=self._t("dashboard_title"), style="Title.TLabel"
        )
        self.dashboard_title_label.grid(row=0, column=0, sticky="w")

        self.dashboard_kpi_captions: dict[str, ttk.Label] = {}
        self.dashboard_kpi_values: dict[str, ttk.Label] = {}
        kpi_area = ttk.Frame(self.dashboard_page, style="Canvas.TFrame")
        kpi_area.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        for row, keys in enumerate(
            (
                ("total", "completed", "failed", "words"),
                ("duration", "speed", "retained", "last_7_days"),
            )
        ):
            for column, key in enumerate(keys):
                kpi_area.grid_columnconfigure(column, weight=1, uniform="dashboard_kpi")
                card = ttk.Frame(kpi_area, padding=(12, 10), style="Card.TFrame")
                card.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
                caption = ttk.Label(
                    card, text=self._t(f"dashboard_{key}"), style="CardMuted.TLabel"
                )
                caption.pack(anchor="w")
                value = ttk.Label(card, text=DASHBOARD_EMPTY_VALUE, style="CardTitle.TLabel")
                value.pack(anchor="w", pady=(3, 0))
                self.dashboard_kpi_captions[key] = caption
                self.dashboard_kpi_values[key] = value
        activity_card = ttk.Frame(kpi_area, padding=(12, 10), style="Card.TFrame")
        activity_card.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        self.dashboard_kpi_captions["last_30_days"] = ttk.Label(
            activity_card, text=self._t("dashboard_last_30_days"), style="CardMuted.TLabel"
        )
        self.dashboard_kpi_captions["last_30_days"].pack(anchor="w")
        self.dashboard_kpi_values["last_30_days"] = ttk.Label(
            activity_card, text=DASHBOARD_EMPTY_VALUE, style="CardTitle.TLabel"
        )
        self.dashboard_kpi_values["last_30_days"].pack(anchor="w", pady=(3, 0))

        self.dashboard_top_captions: dict[str, ttk.Label] = {}
        self.dashboard_top_values: dict[str, ttk.Label] = {}
        top_area = ttk.Frame(self.dashboard_page, style="Canvas.TFrame")
        top_area.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for column, key in enumerate(("languages", "engines", "models")):
            top_area.grid_columnconfigure(column, weight=1, uniform="dashboard_top")
            card = ttk.Frame(top_area, padding=(12, 10), style="Card.TFrame")
            card.grid(row=0, column=column, sticky="new", padx=(0, 8))
            caption = ttk.Label(
                card, text=self._t(f"dashboard_top_{key}"), style="CardMuted.TLabel"
            )
            caption.pack(anchor="w")
            value = ttk.Label(
                card, text=DASHBOARD_EMPTY_VALUE, style="CardValue.TLabel", justify="left"
            )
            value.pack(anchor="w", pady=(3, 0))
            self.dashboard_top_captions[key] = caption
            self.dashboard_top_values[key] = value

        self.dashboard_invalid_frame = ttk.Frame(
            self.dashboard_page, padding=(12, 8), style="Card.TFrame"
        )
        self.dashboard_invalid_frame.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.dashboard_invalid_label = ttk.Label(
            self.dashboard_invalid_frame,
            text="",
            style="CardMuted.TLabel",
            wraplength=620,
        )
        self.dashboard_invalid_label.pack(anchor="w")
        self.dashboard_invalid_frame.grid_remove()

        recent_card = ttk.Frame(self.dashboard_page, padding=(12, 10), style="Card.TFrame")
        recent_card.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.dashboard_recent_caption = ttk.Label(
            recent_card, text=self._t("dashboard_recent"), style="CardMuted.TLabel"
        )
        self.dashboard_recent_caption.grid(row=0, column=0, sticky="w")
        recent_card.grid_columnconfigure(0, weight=1)
        self._dashboard_recent_items: list[Transcript] = []
        self.dashboard_recent_buttons: list[ttk.Button] = []
        for index in range(DASHBOARD_RECENT_LIMIT):
            button = ttk.Button(
                recent_card,
                text="",
                command=partial(self._open_dashboard_recent, index),
            )
            button.grid(row=index + 1, column=0, sticky="ew", pady=(4, 0))
            button.grid_remove()
            self.dashboard_recent_buttons.append(button)
        self.dashboard_recent_empty_label = ttk.Label(
            recent_card, text=self._t("dashboard_recent_empty"), style="CardValue.TLabel"
        )
        self.dashboard_recent_empty_label.grid(row=DASHBOARD_RECENT_LIMIT + 1, column=0, sticky="w")

        dynamics_card = ttk.Frame(self.dashboard_page, padding=(12, 10), style="Card.TFrame")
        dynamics_card.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        dynamics_card.grid_columnconfigure(0, weight=1, uniform="dashboard_dynamics")
        dynamics_card.grid_columnconfigure(1, weight=1, uniform="dashboard_dynamics")
        self.dashboard_dynamics_caption = ttk.Label(
            dynamics_card, text=self._t("dashboard_dynamics"), style="CardMuted.TLabel"
        )
        self.dashboard_dynamics_caption.grid(row=0, column=0, columnspan=2, sticky="w")
        self.dashboard_activity_caption = ttk.Label(
            dynamics_card, text=self._t("dashboard_activity_14d"), style="CardMuted.TLabel"
        )
        self.dashboard_activity_caption.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.dashboard_distribution_caption = ttk.Label(
            dynamics_card, text=self._t("dashboard_distribution"), style="CardMuted.TLabel"
        )
        self.dashboard_distribution_caption.grid(
            row=1, column=1, sticky="w", pady=(6, 0), padx=(12, 0)
        )
        self.dashboard_activity_canvas = tk.Canvas(
            dynamics_card,
            height=120,
            background=theme.surface,
            highlightthickness=0,
        )
        self.dashboard_activity_canvas.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.dashboard_distribution_canvas = tk.Canvas(
            dynamics_card,
            height=120,
            background=theme.surface,
            highlightthickness=0,
        )
        self.dashboard_distribution_canvas.grid(
            row=2, column=1, sticky="ew", pady=(4, 0), padx=(12, 0)
        )
        self._dashboard_activity_data: tuple[tuple[str, int], ...] = ()
        self._dashboard_language_data: tuple[tuple[str, int], ...] = ()
        self._dashboard_engine_data: tuple[tuple[str, int], ...] = ()
        self.dashboard_activity_canvas.bind(
            "<Configure>", lambda _event: self._redraw_dashboard_activity_chart()
        )
        self.dashboard_distribution_canvas.bind(
            "<Configure>", lambda _event: self._redraw_dashboard_distribution_chart()
        )

        self.studio_page = ttk.Frame(self.page_host, style="Canvas.TFrame")
        self.main_area = self.studio_page
        main_area = self.main_area
        main_area.grid(row=0, column=0, sticky="nsew")
        main_area.grid_rowconfigure(3, weight=1)
        main_area.grid_columnconfigure(0, weight=1)

        heading = ttk.Frame(main_area, style="Canvas.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        self.file_button = ttk.Button(
            heading,
            text=self._t("transcribe_file"),
            command=self._choose_file,
            style="PrimaryLarge.TButton",
        )
        self.file_button.pack(side="right", padx=(18, 0))
        self.batch_button = ttk.Button(
            heading,
            text=self._t("batch_button"),
            command=self._toggle_batch_panel,
        )
        self.batch_button.pack(side="right", padx=(12, 0))
        heading_copy = ttk.Frame(heading, style="Canvas.TFrame")
        heading_copy.pack(side="left", fill="x", expand=True)
        self.workspace_kicker_label = ttk.Label(
            heading_copy, text=self._t("workspace_kicker"), style="Kicker.TLabel"
        )
        self.workspace_kicker_label.pack(anchor="w")
        self.workspace_title_label = ttk.Label(
            heading_copy, text=self._t("workspace_title"), style="Title.TLabel"
        )
        self.workspace_title_label.pack(anchor="w", pady=(3, 2))
        self.workspace_subtitle_label = ttk.Label(
            heading_copy,
            text=self._t("workspace_subtitle"),
            style="Subtitle.TLabel",
            wraplength=560,
        )
        self.workspace_subtitle_label.pack(anchor="w")

        toolbar = ttk.Frame(main_area, padding=(10, 10), style="ActionBar.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.record_button = ttk.Button(
            toolbar,
            text=self._t("hold_record"),
            style="Record.TButton",
        )
        self.record_button.pack(side="left")
        self.record_button.bind("<ButtonPress-1>", lambda _event: self._record_start())
        self.record_button.bind("<ButtonRelease-1>", lambda _event: self._record_stop())
        self.continuous_record_button = ttk.Button(
            toolbar,
            text=self._t("continuous_record"),
            command=self._toggle_continuous_recording,
            style="CompactAction.TButton",
        )
        self.continuous_record_button.pack(side="left", padx=(8, 0))
        self.cancel_button = ttk.Button(
            toolbar,
            text=self._t("cancel"),
            command=self._cancel_current,
            state="disabled",
            style="CompactAction.TButton",
        )
        self.cancel_button.pack(side="right", padx=(8, 0))
        self.copy_button = ttk.Button(
            toolbar,
            text=self._t("copy_text"),
            command=self._copy_current,
            style="CompactAction.TButton",
        )
        self.copy_button.pack(side="right")

        self.batch_panel = ttk.Labelframe(
            main_area,
            text=self._t("batch_panel_title"),
            padding=12,
            style="Card.TLabelframe",
        )
        self.batch_panel.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.batch_panel.grid_remove()
        self.batch_panel_visible = False
        self.batch_panel.grid_columnconfigure(0, weight=1)
        batch_list_frame = ttk.Frame(self.batch_panel, style="Card.TFrame")
        batch_list_frame.grid(row=0, column=0, sticky="ew")
        self.batch_tree = ttk.Treeview(
            batch_list_frame,
            columns=tuple(column for column, _key, _width in BATCH_COLUMNS),
            show="headings",
            height=4,
            selectmode="extended",
        )
        for column, key, width in BATCH_COLUMNS:
            self.batch_tree.heading(column, text=self._t(key))
            self.batch_tree.column(column, width=width, stretch=column == "error")
        batch_scrollbar = ttk.Scrollbar(
            batch_list_frame, orient="vertical", command=self.batch_tree.yview
        )
        self.batch_tree.configure(yscrollcommand=batch_scrollbar.set)
        self.batch_tree.pack(side="left", fill="both", expand=True)
        batch_scrollbar.pack(side="right", fill="y")
        # Two rows: sources on the first, run control on the second, so the
        # panel never clips its buttons at the default window width.
        batch_sources = ttk.Frame(self.batch_panel, style="Card.TFrame")
        batch_sources.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        batch_actions = ttk.Frame(self.batch_panel, style="Card.TFrame")
        batch_actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self._batch_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("batch_add_files", self._batch_add_files),
            ("batch_add_folder", self._batch_add_folder),
        ):
            button = ttk.Button(batch_sources, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._batch_button_keys[button] = key
        self.batch_recursive_var = tk.BooleanVar(value=False)
        self.batch_recursive_check = ttk.Checkbutton(
            batch_sources,
            text=self._t("batch_recursive"),
            variable=self.batch_recursive_var,
        )
        self.batch_recursive_check.pack(side="left", padx=(10, 0))
        start_button = ttk.Button(
            batch_actions,
            text=self._t("batch_start"),
            command=self._batch_start,
            style="Primary.TButton",
        )
        start_button.pack(side="left", padx=(0, 5))
        self._batch_button_keys[start_button] = "batch_start"
        self.batch_pause_button = ttk.Button(
            batch_actions,
            text=self._t("batch_pause"),
            command=self._batch_toggle_pause,
        )
        self.batch_pause_button.pack(side="left", padx=(0, 5))
        for key, command in (
            ("batch_skip", self._batch_skip_selected),
            ("batch_clear_finished", self._batch_clear_finished),
            ("batch_clear", self._batch_clear),
        ):
            button = ttk.Button(batch_actions, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._batch_button_keys[button] = key

        self.editor_frame = ttk.Labelframe(
            main_area, text=self._t("transcript"), padding=14, style="Card.TLabelframe"
        )
        self.editor_frame.grid(row=3, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self.editor_frame)
        # Packed after the export bar (see below) so the bar keeps its place
        # at the bottom of the card when the window is short.
        corrected_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        raw_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        details_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        smart_text_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(corrected_frame, text=self._t("corrected_text"))
        self.notebook.add(raw_frame, text=self._t("raw"))
        self.notebook.add(details_frame, text=self._t("data"))
        self.notebook.add(smart_text_frame, text=self._t("smart_text_tab"))

        format_bar = ttk.Frame(corrected_frame, padding=(0, 7, 0, 7), style="Card.TFrame")
        format_bar.pack(fill="x")
        self.format_label = ttk.Label(
            format_bar, text=self._t("formatting"), style="CardMuted.TLabel"
        )
        self.format_label.pack(side="left")
        ttk.Button(
            format_bar, text="B", width=3, command=lambda: self._toggle_editor_tag("bold")
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            format_bar, text="I", width=3, command=lambda: self._toggle_editor_tag("italic")
        ).pack(side="right")
        self.editor_find_button = ttk.Button(
            format_bar,
            text=self._t("editor_find_button"),
            command=self._toggle_find_panel,
        )
        self.editor_find_button.pack(side="left", padx=(12, 0))
        self.editor_add_rule_button = ttk.Button(
            format_bar,
            text=self._t("editor_add_rule_button"),
            command=self._add_selection_to_dictionary,
        )
        self.editor_add_rule_button.pack(side="left", padx=(6, 0))
        self.editor_filler_button = ttk.Button(
            format_bar,
            text=self._t("editor_filler_button"),
            command=self._open_filler_dialog,
        )
        self.editor_filler_button.pack(side="left", padx=(6, 0))
        self.editor_confidence_button = ttk.Button(
            format_bar,
            text=self._t("editor_confidence_button"),
            command=self._toggle_confidence_panel,
        )
        self.editor_confidence_button.pack(side="left", padx=(6, 0))
        self.editor = tk.Text(
            corrected_frame,
            wrap="word",
            undo=True,
            font=(theme.ui_font, 11),
            background=theme.surface,
            foreground=theme.ink,
            insertbackground=theme.primary,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            relief="flat",
            padx=14,
            pady=12,
            height=9,
        )
        self.editor.tag_configure("bold", font=(theme.ui_font, 11, "bold"))
        self.editor.tag_configure("italic", font=(theme.ui_font, 11, "italic"))
        self.editor.tag_configure(
            EDITOR_FIND_TAG, background=theme.selection, foreground=theme.ink
        )
        self.editor.tag_configure(
            EDITOR_CONFIDENCE_TAG, background=theme.accent_soft, foreground=theme.ink
        )
        self.editor.bind("<Return>", self._insert_editor_newline)
        self.editor.bind("<Control-Return>", self._insert_editor_newline)

        # Bottom-packed before the editor text: a short window shrinks the
        # text, never the transport controls.
        self.playback_bar = ttk.Frame(corrected_frame, padding=(0, 7, 0, 0), style="Card.TFrame")
        self.playback_bar.pack(side="bottom", fill="x")
        self.editor.pack(fill="both", expand=True)
        playback_controls_row = ttk.Frame(self.playback_bar, style="Card.TFrame")
        playback_controls_row.pack(fill="x")
        self.playback_toggle_button = ttk.Button(
            playback_controls_row,
            text=self._t("playback_play"),
            command=self._toggle_playback,
        )
        self.playback_toggle_button.pack(side="left")
        self._playback_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("playback_stop", self._stop_playback),
            ("playback_back_5", lambda: self._seek_playback(-5.0)),
            ("playback_forward_5", lambda: self._seek_playback(5.0)),
        ):
            button = ttk.Button(playback_controls_row, text=self._t(key), command=command)
            button.pack(side="left", padx=(5, 0))
            self._playback_button_keys[button] = key
        self.playback_speed_label = ttk.Label(
            playback_controls_row, text=self._t("playback_speed"), style="CardMuted.TLabel"
        )
        self.playback_speed_label.pack(side="left", padx=(12, 4))
        self.playback_speed_var = tk.StringVar(value="1×")
        speed_combo = ttk.Combobox(
            playback_controls_row,
            textvariable=self.playback_speed_var,
            values=[f"{speed:g}×" for speed in SUPPORTED_SPEEDS],
            state="readonly",
            width=6,
        )
        speed_combo.pack(side="left")
        speed_combo.bind("<<ComboboxSelected>>", self._set_playback_speed)
        self.playback_position_var = tk.StringVar(value="0:00 / —")
        ttk.Label(
            playback_controls_row,
            textvariable=self.playback_position_var,
            style="CardMuted.TLabel",
        ).pack(side="right")
        playback_seek_row = ttk.Frame(self.playback_bar, style="Card.TFrame")
        playback_seek_row.pack(fill="x", pady=(6, 0))
        self.playback_seek_label = ttk.Label(
            playback_seek_row, text=self._t("playback_seek"), style="CardMuted.TLabel"
        )
        self.playback_seek_label.pack(side="left", padx=(0, 8))
        self.playback_seek_var = tk.DoubleVar(value=0.0)
        self._playback_seek_dragging = False
        self.playback_seek_scale = ttk.Scale(
            playback_seek_row,
            from_=0,
            to=1000,
            orient="horizontal",
            variable=self.playback_seek_var,
        )
        self.playback_seek_scale.configure(state="disabled")
        self.playback_seek_scale.pack(side="left", fill="x", expand=True)
        self.playback_seek_scale.bind("<ButtonPress-1>", self._press_playback_seek)
        self.playback_seek_scale.bind("<ButtonRelease-1>", self._release_playback_seek)

        self.find_panel = ttk.Frame(corrected_frame, padding=(0, 7, 0, 0), style="Card.TFrame")
        self.find_panel_visible = False
        self.find_panel.grid_columnconfigure(1, weight=1)
        self.find_panel.grid_columnconfigure(3, weight=1)
        self.editor_find_caption = ttk.Label(
            self.find_panel, text=self._t("editor_find_label"), style="CardMuted.TLabel"
        )
        self.editor_find_caption.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.editor_find_var = tk.StringVar()
        find_entry = ttk.Entry(self.find_panel, textvariable=self.editor_find_var)
        find_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        find_entry.bind("<Return>", lambda _event: self._find_in_editor())
        self.editor_replace_caption = ttk.Label(
            self.find_panel, text=self._t("editor_replace_label"), style="CardMuted.TLabel"
        )
        self.editor_replace_caption.grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.editor_replace_var = tk.StringVar()
        ttk.Entry(self.find_panel, textvariable=self.editor_replace_var).grid(
            row=0, column=3, sticky="ew"
        )
        self.editor_find_case_var = tk.BooleanVar(value=False)
        self.editor_find_case_check = ttk.Checkbutton(
            self.find_panel,
            text=self._t("editor_find_case"),
            variable=self.editor_find_case_var,
        )
        self.editor_find_case_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.editor_find_word_var = tk.BooleanVar(value=False)
        self.editor_find_word_check = ttk.Checkbutton(
            self.find_panel,
            text=self._t("editor_find_whole_word"),
            variable=self.editor_find_word_var,
        )
        self.editor_find_word_check.grid(row=1, column=2, columnspan=2, sticky="w", pady=(5, 0))
        self.editor_find_count_var = tk.StringVar()
        ttk.Label(
            self.find_panel,
            textvariable=self.editor_find_count_var,
            style="CardMuted.TLabel",
        ).grid(row=1, column=4, sticky="e", padx=(10, 0))
        find_actions = ttk.Frame(self.find_panel, style="Card.TFrame")
        find_actions.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self._editor_find_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("editor_find_action", self._find_in_editor),
            ("editor_find_replace_one", self._replace_one_in_editor),
            ("editor_find_replace_all", self._replace_all_in_editor),
            ("editor_find_close", self._close_find_panel),
        ):
            button = ttk.Button(find_actions, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._editor_find_button_keys[button] = key

        self.confidence_panel = ttk.Frame(
            corrected_frame, padding=(0, 7, 0, 0), style="Card.TFrame"
        )
        self.confidence_panel_visible = False
        self.confidence_panel.grid_columnconfigure(2, weight=1)
        self._confidence_entries: tuple[ConfidenceEntry, ...] = ()
        self.editor_confidence_caption = ttk.Label(
            self.confidence_panel,
            text=self._t("editor_confidence_threshold"),
            style="CardMuted.TLabel",
        )
        self.editor_confidence_caption.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.confidence_threshold_var = tk.StringVar(value=DEFAULT_CONFIDENCE_THRESHOLD)
        threshold_spin = ttk.Spinbox(
            self.confidence_panel,
            from_=0.0,
            to=1.0,
            increment=0.05,
            width=6,
            textvariable=self.confidence_threshold_var,
            command=self._refresh_confidence_panel,
        )
        threshold_spin.grid(row=0, column=1, sticky="w")
        threshold_spin.bind("<Return>", lambda _event: self._refresh_confidence_panel())
        self.confidence_count_var = tk.StringVar()
        ttk.Label(
            self.confidence_panel,
            textvariable=self.confidence_count_var,
            style="CardMuted.TLabel",
        ).grid(row=0, column=2, sticky="e", padx=(10, 0))
        confidence_list_frame = ttk.Frame(self.confidence_panel, style="Card.TFrame")
        confidence_list_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.confidence_list = tk.Listbox(
            confidence_list_frame,
            height=5,
            activestyle="dotbox",
            background=theme.surface,
            foreground=theme.ink,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            highlightbackground=theme.border,
            highlightcolor=theme.accent,
            relief="flat",
            borderwidth=1,
            font=(theme.ui_font, 10),
        )
        confidence_scrollbar = ttk.Scrollbar(
            confidence_list_frame, orient="vertical", command=self.confidence_list.yview
        )
        self.confidence_list.configure(yscrollcommand=confidence_scrollbar.set)
        self.confidence_list.pack(side="left", fill="both", expand=True)
        confidence_scrollbar.pack(side="right", fill="y")
        self.confidence_list.bind("<<ListboxSelect>>", self._select_confidence_entry)
        confidence_actions = ttk.Frame(self.confidence_panel, style="Card.TFrame")
        confidence_actions.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._editor_confidence_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("editor_confidence_play", self._play_selected_segment),
            ("editor_confidence_close", self._close_confidence_panel),
        ):
            button = ttk.Button(confidence_actions, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._editor_confidence_button_keys[button] = key
        self.raw_editor = tk.Text(
            raw_frame,
            wrap="word",
            font=(theme.ui_font, 11),
            state="disabled",
            background=theme.surface,
            foreground=theme.ink,
            relief="flat",
            padx=14,
            pady=12,
        )
        self.raw_editor.pack(fill="both", expand=True)
        self.details = tk.Text(
            details_frame,
            wrap="word",
            font=(theme.mono_font, 9),
            state="disabled",
            background=theme.surface,
            foreground=theme.ink,
            relief="flat",
            padx=14,
            pady=12,
        )
        self.details.pack(fill="both", expand=True)

        smart_options = ttk.Frame(smart_text_frame, padding=(0, 7, 0, 7), style="Card.TFrame")
        smart_options.pack(fill="x")
        self.smart_text_gap_label = ttk.Label(
            smart_options, text=self._t("smart_text_gap"), style="CardMuted.TLabel"
        )
        self.smart_text_gap_label.pack(side="left")
        self.smart_gap_var = tk.StringVar(value=DEFAULT_SMART_TEXT_GAP)
        smart_gap_spin = ttk.Spinbox(
            smart_options,
            from_=0.0,
            to=MAX_PARAGRAPH_GAP_SECONDS,
            increment=0.5,
            width=6,
            textvariable=self.smart_gap_var,
            command=self._refresh_smart_text,
        )
        smart_gap_spin.pack(side="left", padx=(6, 14))
        smart_gap_spin.bind("<Return>", lambda _event: self._refresh_smart_text())
        self.smart_text_max_label = ttk.Label(
            smart_options, text=self._t("smart_text_max"), style="CardMuted.TLabel"
        )
        self.smart_text_max_label.pack(side="left")
        self.smart_max_var = tk.StringVar(value=DEFAULT_SMART_TEXT_MAX)
        smart_max_spin = ttk.Spinbox(
            smart_options,
            from_=MIN_PARAGRAPH_SECONDS,
            to=MAX_PARAGRAPH_SECONDS,
            increment=5.0,
            width=6,
            textvariable=self.smart_max_var,
            command=self._refresh_smart_text,
        )
        smart_max_spin.pack(side="left", padx=(6, 14))
        smart_max_spin.bind("<Return>", lambda _event: self._refresh_smart_text())
        self.smart_timestamps_var = tk.BooleanVar(value=True)
        self.smart_timestamps_check = ttk.Checkbutton(
            smart_options,
            text=self._t("smart_text_timestamps"),
            variable=self.smart_timestamps_var,
            command=self._refresh_smart_text,
        )
        self.smart_timestamps_check.pack(side="left")
        self.smart_speakers_var = tk.BooleanVar(value=True)
        self.smart_speakers_check = ttk.Checkbutton(
            smart_options,
            text=self._t("smart_text_speakers"),
            variable=self.smart_speakers_var,
            command=self._refresh_smart_text,
        )
        self.smart_speakers_check.pack(side="left", padx=(10, 0))
        # Actions sit on their own row: with the options they clip at the
        # default window width.
        smart_actions = ttk.Frame(smart_text_frame, padding=(0, 0, 0, 7), style="Card.TFrame")
        smart_actions.pack(fill="x")
        self._smart_text_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("smart_text_refresh", self._refresh_smart_text),
            ("smart_text_copy", self._copy_smart_text),
            ("smart_text_export_md", partial(self._export_smart_text, "md")),
            ("smart_text_export_txt", partial(self._export_smart_text, "txt")),
        ):
            button = ttk.Button(smart_actions, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._smart_text_button_keys[button] = key
        # Preview and segment list share the height side by side: stacked,
        # the list would squeeze the preview out of a small window.
        smart_body = ttk.Frame(smart_text_frame, style="Card.TFrame")
        smart_body.pack(fill="both", expand=True)
        smart_speakers = ttk.Frame(smart_body, padding=(10, 0, 0, 0), style="Card.TFrame")
        smart_speakers.pack(side="right", fill="y")
        self.smart_text_view = tk.Text(
            smart_body,
            wrap="word",
            height=5,
            width=40,
            font=(theme.ui_font, 11),
            state="disabled",
            background=theme.surface,
            foreground=theme.ink,
            relief="flat",
            padx=14,
            pady=12,
        )
        self.smart_text_view.pack(side="left", fill="both", expand=True)
        self.smart_speaker_caption = ttk.Label(
            smart_speakers,
            text=self._t("smart_text_speaker_list"),
            style="CardMuted.TLabel",
        )
        self.smart_speaker_caption.pack(anchor="w")
        smart_speaker_button = ttk.Button(
            smart_speakers,
            text=self._t("smart_text_assign_speaker"),
            command=self._assign_smart_speaker,
        )
        smart_speaker_button.pack(fill="x", pady=(4, 4))
        self._smart_text_button_keys[smart_speaker_button] = "smart_text_assign_speaker"
        smart_speaker_frame = ttk.Frame(smart_speakers, style="Card.TFrame")
        smart_speaker_frame.pack(fill="both", expand=True)
        self.smart_speaker_list = tk.Listbox(
            smart_speaker_frame,
            height=3,
            width=44,
            activestyle="dotbox",
            background=theme.surface,
            foreground=theme.ink,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            highlightbackground=theme.border,
            highlightcolor=theme.accent,
            relief="flat",
            borderwidth=1,
            font=(theme.ui_font, 10),
        )
        smart_speaker_scrollbar = ttk.Scrollbar(
            smart_speaker_frame, orient="vertical", command=self.smart_speaker_list.yview
        )
        self.smart_speaker_list.configure(yscrollcommand=smart_speaker_scrollbar.set)
        self.smart_speaker_list.pack(side="left", fill="both", expand=True)
        smart_speaker_scrollbar.pack(side="right", fill="y")

        export_bar = ttk.Frame(self.editor_frame, style="Card.TFrame")
        export_bar.pack(side="bottom", fill="x", pady=(10, 0))
        self.notebook.pack(fill="both", expand=True)
        self.save_edits_button = ttk.Button(
            export_bar, text=self._t("save_edits"), command=self._save_edits
        )
        self.save_edits_button.pack(side="left")
        self.cleanup_button = ttk.Button(
            export_bar, text=self._t("cleanup"), command=self._ai_cleanup
        )
        self.cleanup_button.pack(side="left", padx=6)
        self.undo_cleanup_button = ttk.Button(
            export_bar, text=self._t("undo_cleanup"), command=self._undo_ai_cleanup
        )
        self.undo_cleanup_button.pack(side="left")
        for fmt in ("TXT", "MD", "JSON", "SRT", "VTT"):
            ttk.Button(
                export_bar,
                text=fmt,
                command=partial(self._export, fmt.lower()),
            ).pack(side="right", padx=2)

        self.history_page = ttk.Frame(self.page_host, padding=28, style="Canvas.TFrame")
        self.history_page.grid(row=0, column=0, sticky="nsew")
        self.history_page.grid_rowconfigure(0, weight=1)
        self.history_page.grid_columnconfigure(0, weight=1)
        self.history_frame = ttk.Labelframe(
            self.history_page,
            text=self._t("history"),
            padding=12,
            style="Card.TLabelframe",
        )
        self.history_frame.grid(row=0, column=0, sticky="nsew")

        search_row = ttk.Frame(self.history_frame, style="Card.TFrame")
        search_row.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", lambda _event: self._refresh_history())
        self.search_button = ttk.Button(
            search_row, text=self._t("search"), command=self._refresh_history
        )
        self.search_button.pack(side="left", padx=(5, 0))

        filter_row = ttk.Frame(self.history_frame, style="Card.TFrame")
        filter_row.pack(fill="x", pady=(0, 6))
        filter_row.grid_columnconfigure(1, weight=1)
        filter_row.grid_columnconfigure(3, weight=1)
        self._history_filter_vars: dict[str, tk.StringVar] = {}
        self._history_filter_combos: dict[str, ttk.Combobox] = {}
        self._history_filter_labels: dict[str, dict[str, object]] = {}
        self.history_filter_captions: dict[str, ttk.Label] = {}
        for name, row, column in (
            ("language", 0, 0),
            ("engine", 0, 2),
            ("status", 1, 0),
            ("retained", 1, 2),
        ):
            caption = ttk.Label(
                filter_row, text=self._t(f"history_filter_{name}"), style="CardMuted.TLabel"
            )
            caption.grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(0, 4))
            variable = tk.StringVar()
            combo = ttk.Combobox(filter_row, textvariable=variable, state="readonly", width=14)
            combo.grid(row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=(0, 4))
            self.history_filter_captions[name] = caption
            self._history_filter_vars[name] = variable
            self._history_filter_combos[name] = combo
            self._apply_history_filter_choices(name, reset=True)
        for name, row, column in (
            ("model", 2, 0),
            ("from", 2, 2),
            ("to", 3, 0),
        ):
            caption = ttk.Label(
                filter_row, text=self._t(f"history_filter_{name}"), style="CardMuted.TLabel"
            )
            caption.grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(0, 4))
            self.history_filter_captions[name] = caption
        self.history_model_var = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.history_model_var, width=14).grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 4)
        )
        self.history_from_var = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.history_from_var, width=14).grid(
            row=2, column=3, sticky="ew", padx=(0, 8), pady=(0, 4)
        )
        self.history_to_var = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.history_to_var, width=14).grid(
            row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 4)
        )
        self.history_reset_button = ttk.Button(
            filter_row, text=self._t("history_filter_reset"), command=self._reset_history_filters
        )
        self.history_reset_button.grid(row=3, column=3, sticky="e", pady=(0, 4))

        list_frame = ttk.Frame(self.history_frame, style="Card.TFrame")
        list_frame.pack(fill="both", expand=True)
        self.history = tk.Listbox(
            list_frame,
            width=34,
            activestyle="dotbox",
            background=theme.surface,
            foreground=theme.ink,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            highlightbackground=theme.border,
            highlightcolor=theme.accent,
            relief="flat",
            borderwidth=1,
            font=(theme.ui_font, 10),
            height=4,
        )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=scrollbar.set)
        self.history.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.history.bind("<<ListboxSelect>>", self._select_history)
        history_actions = ttk.Frame(self.history_frame, style="Card.TFrame")
        history_actions.pack(fill="x", pady=(7, 0))
        self.rename_history_button = ttk.Button(
            history_actions, text=self._t("rename"), command=self._rename_selected_history
        )
        self.rename_history_button.pack(side="left")
        self.delete_history_button = ttk.Button(
            history_actions, text=self._t("delete"), command=self._delete_selected_history
        )
        self.delete_history_button.pack(side="right")

        self.dictionary_page = ttk.Frame(self.page_host, padding=28, style="Canvas.TFrame")
        self.dictionary_page.grid(row=0, column=0, sticky="nsew")
        self.dictionary_page.grid_rowconfigure(3, weight=1)
        self.dictionary_page.grid_columnconfigure(0, weight=1)
        self.dictionary_title_label = ttk.Label(
            self.dictionary_page, text=self._t("dictionary_title"), style="Title.TLabel"
        )
        self.dictionary_title_label.grid(row=0, column=0, sticky="w")
        self.dictionary_detail_label = ttk.Label(
            self.dictionary_page,
            text=self._t("dictionary_detail"),
            style="Subtitle.TLabel",
            wraplength=760,
        )
        self.dictionary_detail_label.grid(row=1, column=0, sticky="w", pady=(8, 4))
        self.dictionary_banner_var = tk.StringVar()
        ttk.Label(
            self.dictionary_page, textvariable=self.dictionary_banner_var, style="CardMuted.TLabel"
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        dictionary_actions = ttk.Frame(self.dictionary_page, style="Canvas.TFrame")
        dictionary_actions.grid(row=3, column=0, sticky="nsew")
        dictionary_actions.grid_rowconfigure(1, weight=1)
        dictionary_actions.grid_columnconfigure(0, weight=1)
        search_row = ttk.Frame(dictionary_actions, style="Canvas.TFrame")
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        search_row.grid_columnconfigure(0, weight=1)
        self.dictionary_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.dictionary_search_var)
        search_entry.grid(row=0, column=0, sticky="ew")
        search_entry.bind("<KeyRelease>", lambda _event: self._dictionary_refresh_widgets())
        self.dictionary_search_button = ttk.Button(
            search_row, text=self._t("search"), command=self._dictionary_refresh_widgets
        )
        self.dictionary_search_button.grid(row=0, column=1, padx=(6, 0))
        self.dictionary_table = ttk.Treeview(
            dictionary_actions,
            columns=("source", "target", "case", "whole", "hint"),
            show="headings",
            height=9,
        )
        self._dictionary_heading_keys: dict[str, str] = {}
        for key, width in (
            ("source", 170),
            ("target", 170),
            ("case", 70),
            ("whole", 70),
            ("hint", 70),
        ):
            self.dictionary_table.heading(key, text=self._t(f"dictionary_{key}"))
            self.dictionary_table.column(key, width=width, anchor="w")
            self._dictionary_heading_keys[key] = f"dictionary_{key}"
        self.dictionary_table.grid(row=1, column=0, sticky="nsew")
        controls = ttk.Frame(dictionary_actions, style="Canvas.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.dictionary_edit_buttons: list[ttk.Button] = []
        self._dictionary_button_keys: dict[ttk.Button, str] = {}
        for key, command in (
            ("dictionary_add", self._dictionary_add_dialog),
            ("dictionary_edit", self._dictionary_edit_dialog),
            ("dictionary_delete", self._dictionary_delete),
            ("dictionary_up", lambda: self._dictionary_move(-1)),
            ("dictionary_down", lambda: self._dictionary_move(1)),
            ("save", self._save_dictionary),
            ("dictionary_import", self._dictionary_import_dialog),
            ("export", self._dictionary_export_dialog),
        ):
            button = ttk.Button(controls, text=self._t(key), command=command)
            button.pack(side="left", padx=(0, 5))
            self._dictionary_button_keys[button] = key
            if key in {
                "dictionary_add",
                "dictionary_edit",
                "dictionary_delete",
                "dictionary_up",
                "dictionary_down",
                "save",
            }:
                self.dictionary_edit_buttons.append(button)
        test_box = ttk.Frame(dictionary_actions, style="Canvas.TFrame")
        test_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        test_box.grid_columnconfigure(0, weight=1)
        self.dictionary_test_var = tk.StringVar()
        ttk.Entry(test_box, textvariable=self.dictionary_test_var).grid(
            row=0, column=0, sticky="ew"
        )
        self.dictionary_test_button = ttk.Button(
            test_box, text=self._t("dictionary_test"), command=self._dictionary_test
        )
        self.dictionary_test_button.grid(row=0, column=1, padx=(6, 0))
        self.dictionary_test_result_var = tk.StringVar()
        ttk.Label(
            test_box, textvariable=self.dictionary_test_result_var, style="CardMuted.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.settings_page = ttk.Frame(self.page_host, style="Canvas.TFrame")
        self.settings_page.grid(row=0, column=0, sticky="nsew")
        self.settings_page.grid_rowconfigure(1, weight=1)
        self.settings_page.grid_columnconfigure(0, weight=1)

        self.help_page = ttk.Frame(self.page_host, style="Canvas.TFrame")
        self.help_page.grid(row=0, column=0, sticky="nsew")
        self.help_page.grid_rowconfigure(1, weight=1)
        self.help_page.grid_columnconfigure(0, weight=1)

        self._page_frames = {
            "dashboard": self.dashboard_page,
            "studio": self.studio_page,
            "dictionary": self.dictionary_page,
            "history": self.history_page,
            "settings": self.settings_page,
            "help": self.help_page,
        }

        self.readiness_frame = ttk.Frame(
            self.workspace_body, width=214, padding=18, style="CardBorder.TFrame"
        )
        self.readiness_frame.grid(row=0, column=1, sticky="nsew")
        self.readiness_frame.grid_propagate(False)
        self.readiness_title_label = ttk.Label(
            self.readiness_frame, text=self._t("readiness"), style="CardTitle.TLabel"
        )
        self.readiness_title_label.pack(anchor="w")
        ready_box = ttk.Frame(self.readiness_frame, padding=(12, 10), style="ReadyBox.TFrame")
        ready_box.pack(fill="x", pady=(14, 18))
        self.ready_status_label = ttk.Label(
            ready_box, text=self._t("ready_to_work"), style="Ready.TLabel"
        )
        self.ready_status_label.pack(anchor="w")
        self.local_processing_label = ttk.Label(
            ready_box, text=self._t("local_processing"), style="ReadyMuted.TLabel"
        )
        self.local_processing_label.pack(anchor="w", pady=(2, 0))

        self.readiness_engine_caption = ttk.Label(
            self.readiness_frame, text=self._t("engine"), style="CardMuted.TLabel"
        )
        self.readiness_engine_caption.pack(anchor="w")
        self.readiness_engine_value = tk.StringVar()
        ttk.Label(
            self.readiness_frame,
            textvariable=self.readiness_engine_value,
            style="CardValue.TLabel",
            wraplength=178,
        ).pack(anchor="w", pady=(2, 12))
        self.readiness_model_caption = ttk.Label(
            self.readiness_frame, text=self._t("active_model"), style="CardMuted.TLabel"
        )
        self.readiness_model_caption.pack(anchor="w")
        self.readiness_model_value = tk.StringVar()
        ttk.Label(
            self.readiness_frame,
            textvariable=self.readiness_model_value,
            style="CardValue.TLabel",
            wraplength=178,
        ).pack(anchor="w", pady=(2, 12))
        self.readiness_language_caption = ttk.Label(
            self.readiness_frame,
            text=self._t("interface_language"),
            style="CardMuted.TLabel",
        )
        self.readiness_language_caption.pack(anchor="w")
        self.readiness_language_value = tk.StringVar()
        ttk.Label(
            self.readiness_frame,
            textvariable=self.readiness_language_value,
            style="CardValue.TLabel",
        ).pack(anchor="w", pady=(2, 12))
        self.readiness_ai_caption = ttk.Label(
            self.readiness_frame,
            text=self._t("ai_cleanup_short"),
            style="CardMuted.TLabel",
        )
        self.readiness_ai_caption.pack(anchor="w")
        self.readiness_ai_value = tk.StringVar()
        ttk.Label(
            self.readiness_frame,
            textvariable=self.readiness_ai_value,
            style="CardValue.TLabel",
            wraplength=178,
        ).pack(anchor="w", pady=(2, 12))

        ttk.Separator(self.readiness_frame).pack(fill="x", pady=(4, 14))
        self.privacy_note_label = ttk.Label(
            self.readiness_frame,
            text=self._t("privacy_note"),
            style="CardMuted.TLabel",
            wraplength=178,
            justify="left",
        )
        self.privacy_note_label.pack(anchor="w")

        self._current_page = "dashboard"
        self._studio_layout: StudioLayout | None = None
        self._show_page("dashboard")
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.bind_all("<F1>", lambda _event: self._show_page("help"), add="+")
        self.after_idle(lambda: self._apply_studio_layout(self.winfo_width(), force=True))
        self._update_engine_label()

    def _on_window_configure(self, event: tk.Event[Any]) -> None:
        if event.widget is self:
            self._apply_studio_layout(int(event.width))

    def _apply_studio_layout(self, width: int, *, force: bool = False) -> None:
        layout = studio_layout_for_width(width)
        if not force and layout == self._studio_layout:
            return
        self._studio_layout = layout
        self.sidebar.configure(width=layout.sidebar_width)
        if layout.compact_sidebar:
            self.brand_details.grid_remove()
            self.sidebar_footer.grid_remove()
            self.workspace_body.configure(padding=(20, 18, 20, 20))
            self.workspace_subtitle_label.configure(wraplength=340)
        else:
            self.brand_details.grid()
            self.sidebar_footer.grid()
            body_padding, panel_gap, subtitle_wrap = studio_content_metrics(width)
            self.workspace_body.configure(padding=body_padding)
            self.workspace_subtitle_label.configure(wraplength=subtitle_wrap)
        if layout.show_readiness and self._current_page == "studio":
            self.readiness_frame.grid()
            self.workspace_body.grid_columnconfigure(1, minsize=214)
            self.page_host.grid_configure(padx=(0, panel_gap))
        else:
            self.readiness_frame.grid_remove()
            self.workspace_body.grid_columnconfigure(1, minsize=0)
            self.page_host.grid_configure(padx=0)
        for button, key, symbol in self._sidebar_buttons:
            label = symbol if layout.compact_sidebar else f"●  {self._t(key)}"
            button.configure(text=label)

    def _show_page(self, page: str) -> bool:
        if page not in self._page_frames:
            raise ValueError(f"unknown central page: {page}")
        entering = self._current_page != page
        if page == "settings" and entering and self.__dict__.get("_busy", False):
            self.status.set(self._t("task_already_running"))
            return False
        if page == "dictionary" and entering:
            if not self._reload_dictionary():
                return False
        if self._current_page == "studio" and page != "studio":
            if not self._confirm_editor_transition():
                return False
        if self._current_page == "dictionary" and page != "dictionary":
            if not self._confirm_dictionary_transition():
                return False
        if self._current_page == "settings" and page != "settings":
            if not self._confirm_settings_transition():
                return False
        if page == "settings" and entering:
            self._settings_return_page = self._current_page
            self._build_settings_page()
        if page == "help" and entering:
            self._build_help_page()
        if self._current_page == "studio" and page != "studio":
            self._stop_playback()
        if self._current_page == "settings" and page != "settings":
            self._leave_settings_page()
        for page_id, frame in self._page_frames.items():
            if page_id == page:
                frame.grid()
            else:
                frame.grid_remove()
        self._current_page = page
        for page_id, button in self._page_buttons.items():
            button.configure(
                style="SidebarActive.TButton" if page_id == page else "Sidebar.TButton"
            )
        if page == "studio":
            self.readiness_frame.grid()
        else:
            self.readiness_frame.grid_remove()
        self._apply_studio_layout(self.winfo_width(), force=True)
        if page == "dashboard":
            self._refresh_dashboard()
        return True

    def _reload_dictionary(self) -> bool:
        path = self.settings.dictionary_path
        try:
            if path and self.dictionary_repository.is_managed(path) and not Path(path).exists():
                dictionary = TerminologyDictionary()
            else:
                dictionary = self.dictionary_repository.load(path)
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_load_error", error=str(exc)))
            return False
        self.dictionary = dictionary
        self.dictionary_read_only = bool(path) and not self.dictionary_repository.is_managed(path)
        self._dictionary_dirty = False
        self._dictionary_refresh_widgets()
        return True

    def _dictionary_can_edit(self) -> bool:
        return not self.dictionary_read_only

    def _dictionary_changed(self) -> None:
        self._dictionary_dirty = True
        self._dictionary_refresh_widgets()

    def _add_dictionary_rule(self, rule: DictionaryRule) -> bool:
        if not self._dictionary_can_edit():
            return False
        self.dictionary.rules.append(rule)
        self._dictionary_changed()
        return True

    def _edit_dictionary_rule(self, index: int, rule: DictionaryRule) -> bool:
        if not self._dictionary_can_edit() or not 0 <= index < len(self.dictionary.rules):
            return False
        self.dictionary.rules[index] = rule
        self._dictionary_changed()
        return True

    def _delete_dictionary_rule(self, index: int) -> bool:
        if not self._dictionary_can_edit() or not 0 <= index < len(self.dictionary.rules):
            return False
        del self.dictionary.rules[index]
        self._dictionary_changed()
        return True

    def _move_dictionary_rule(self, index: int, delta: int) -> bool:
        destination = index + delta
        if (
            not self._dictionary_can_edit()
            or not 0 <= index < len(self.dictionary.rules)
            or not 0 <= destination < len(self.dictionary.rules)
        ):
            return False
        self.dictionary.rules[index], self.dictionary.rules[destination] = (
            self.dictionary.rules[destination],
            self.dictionary.rules[index],
        )
        self._dictionary_changed()
        return True

    def _filtered_dictionary_rules(self, query: str) -> list[DictionaryRule]:
        needle = query.casefold().strip()
        if not needle:
            return list(self.dictionary.rules)
        return [
            rule
            for rule in self.dictionary.rules
            if needle in rule.source.casefold() or needle in rule.target.casefold()
        ]

    def _apply_dictionary_test_sentence(self, text: str) -> str:
        return self.dictionary.apply(text)

    def _save_dictionary(self) -> bool:
        if self.dictionary_read_only:
            self._dictionary_status(self._t("dictionary_external_read_only"))
            return False
        try:
            self.dictionary_repository.save_managed(self.dictionary)
            updated_settings = replace(
                self.settings, dictionary_path=str(self.dictionary_repository.managed_path)
            )
            save_settings(updated_settings)
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_save_error", error=str(exc)))
            return False
        self.settings = updated_settings
        self._dictionary_dirty = False
        self._dictionary_status(self._t("dictionary_saved"))
        self._dictionary_refresh_widgets()
        return True

    def _prepare_dictionary_import(self, path: str | Path, mode: str) -> DictionaryMergePreview:
        suffix = Path(path).suffix.lower()
        if suffix == ".csv":
            incoming = self.dictionary_repository.load_csv(path)
        elif suffix == ".json":
            incoming = self.dictionary_repository.load(path)
        else:
            raise ValueError(f"unsupported dictionary format: {suffix or 'no suffix'}")
        if mode == "replace":
            return DictionaryMergePreview(incoming, len(incoming.rules), 0, 0, ())
        if mode != "merge":
            raise ValueError(f"unknown dictionary import mode: {mode}")
        return merge_preview(self.dictionary, incoming)

    def _commit_dictionary_import(
        self, preview: DictionaryMergePreview, mode: str, *, confirmed: bool
    ) -> bool:
        if not confirmed or (mode == "merge" and preview.conflicts):
            return False
        try:
            self.dictionary_repository.save_managed(preview.merged)
            updated_settings = replace(
                self.settings, dictionary_path=str(self.dictionary_repository.managed_path)
            )
            save_settings(updated_settings)
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_import_error", error=str(exc)))
            return False
        self.settings = updated_settings
        self.dictionary = preview.merged
        self.dictionary_read_only = False
        self._dictionary_dirty = False
        self._dictionary_status(self._t("dictionary_imported"))
        self._dictionary_refresh_widgets()
        return True

    def _export_dictionary(self, destination: str | Path) -> bool:
        try:
            target = Path(destination)
            if target.suffix.lower() == ".csv":
                self.dictionary_repository.export_csv(self.dictionary, target)
            elif target.suffix.lower() == ".json":
                self.dictionary_repository.export_json(self.dictionary, target)
            else:
                raise ValueError(
                    f"unsupported dictionary format: {target.suffix.lower() or 'no suffix'}"
                )
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_export_error", error=str(exc)))
            return False
        self._dictionary_status(self._t("dictionary_exported"))
        return True

    def _confirm_dictionary_transition(self) -> bool:
        if not self.__dict__.get("_dictionary_dirty", False):
            return True
        choice = messagebox.askyesnocancel(
            self._t("dictionary"), self._t("dictionary_unsaved"), parent=self
        )
        if choice is None:
            return False
        if choice:
            return self._save_dictionary()
        return self._reload_dictionary()

    def _settings_page_is_dirty(self) -> bool:
        baseline = self.__dict__.get("_settings_baseline") or {}
        variables = self.__dict__.get("_settings_variables") or {}
        return any(variable.get() != baseline.get(name) for name, variable in variables.items())

    def _confirm_settings_transition(self) -> bool:
        if not self._settings_page_is_dirty():
            return True
        choice = messagebox.askyesnocancel(
            self._t("settings"), self._t("settings_unsaved"), parent=self
        )
        if choice is None:
            return False
        if choice:
            return bool(self._settings_save())
        self._build_settings_page()
        return True

    def _validate_settings_for_save(self, updated: Settings) -> None:
        """Raise if ``updated`` cannot be saved.

        Runs the ordinary field validation, then — only when Sync is
        enabled — also confirms the folder is a real, safe mirror target
        (not missing, a symlink, or inside/around the app data folder).
        """

        updated.validate()
        if updated.sync_enabled:
            validate_sync_root(Path(updated.sync_folder), data_root=data_dir())

    def _apply_settings_update(self, updated: Settings) -> bool:
        previous_ui_language = self.settings.ui_language
        path_changed = updated.dictionary_path != self.settings.dictionary_path
        if path_changed and self._dictionary_dirty and not self._confirm_dictionary_transition():
            return False
        try:
            if updated.dictionary_path:
                self.dictionary_repository.load(updated.dictionary_path)
            save_settings(updated)
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_load_error", error=str(exc)))
            return False
        self.settings = updated
        if path_changed and not self._reload_dictionary():
            return False
        self._refresh_after_settings_save(previous_ui_language)
        return True

    def _dictionary_status(self, message: str) -> None:
        if hasattr(self, "dictionary_banner_var"):
            self.dictionary_banner_var.set(message)
        if hasattr(self, "status"):
            self.status.set(message)

    def _dictionary_refresh_widgets(self) -> None:
        if not hasattr(self, "dictionary_table"):
            return
        query = self.dictionary_search_var.get() if hasattr(self, "dictionary_search_var") else ""
        for item in self.dictionary_table.get_children():
            self.dictionary_table.delete(item)
        visible = self._filtered_dictionary_rules(query)
        for index, rule in enumerate(self.dictionary.rules):
            if rule in visible:
                self.dictionary_table.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        rule.source,
                        rule.target,
                        rule.case_sensitive,
                        rule.whole_word,
                        rule.use_as_hint,
                    ),
                )
        if hasattr(self, "dictionary_banner_var"):
            self.dictionary_banner_var.set(
                self._t("dictionary_external_read_only")
                if self.dictionary_read_only
                else self._t("dictionary_ready")
            )
        for button in getattr(self, "dictionary_edit_buttons", []):
            button.configure(state="disabled" if self.dictionary_read_only else "normal")

    def _selected_dictionary_index(self) -> int | None:
        selection = self.dictionary_table.selection()
        if not selection:
            return None
        return int(selection[0])

    def _dictionary_test(self) -> None:
        self.dictionary_test_result_var.set(
            self._apply_dictionary_test_sentence(self.dictionary_test_var.get())
        )

    def _dictionary_add_dialog(self) -> None:
        self._dictionary_rule_dialog(None)

    def _dictionary_edit_dialog(self) -> None:
        index = self._selected_dictionary_index()
        if index is not None:
            self._dictionary_rule_dialog(index)

    def _dictionary_rule_dialog(self, index: int | None) -> None:
        if not self._dictionary_can_edit():
            return
        existing = self.dictionary.rules[index] if index is not None else DictionaryRule(" ", "")
        dialog = tk.Toplevel(self)
        dialog.title(self._t("dictionary_edit"))
        source, target = (
            tk.StringVar(value=existing.source.strip()),
            tk.StringVar(value=existing.target),
        )
        case, whole, hint = (
            tk.BooleanVar(value=existing.case_sensitive),
            tk.BooleanVar(value=existing.whole_word),
            tk.BooleanVar(value=existing.use_as_hint),
        )
        for row, (label, variable) in enumerate(
            ((self._t("dictionary_source"), source), (self._t("dictionary_target"), target))
        ):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=12, pady=5, sticky="w")
            ttk.Entry(dialog, textvariable=variable).grid(
                row=row, column=1, padx=12, pady=5, sticky="ew"
            )
        for row, (label, variable) in enumerate(
            (
                (self._t("dictionary_case"), case),
                (self._t("dictionary_whole"), whole),
                (self._t("dictionary_hint"), hint),
            ),
            start=2,
        ):
            ttk.Checkbutton(dialog, text=label, variable=variable).grid(
                row=row, column=0, columnspan=2, padx=12, sticky="w"
            )

        def submit() -> None:
            try:
                rule = DictionaryRule(
                    source.get(), target.get(), case.get(), whole.get(), hint.get()
                )
            except ValueError as exc:
                messagebox.showerror(self._t("dictionary"), str(exc), parent=dialog)
                return
            if index is None:
                self._add_dictionary_rule(rule)
            else:
                self._edit_dictionary_rule(index, rule)
            dialog.destroy()

        ttk.Button(dialog, text=self._t("save"), command=submit).grid(
            row=5, column=1, padx=12, pady=12, sticky="e"
        )

    def _dictionary_delete(self) -> None:
        index = self._selected_dictionary_index()
        if index is not None and messagebox.askyesno(
            self._t("dictionary"), self._t("dictionary_delete_confirm"), parent=self
        ):
            self._delete_dictionary_rule(index)

    def _dictionary_move(self, delta: int) -> None:
        index = self._selected_dictionary_index()
        if index is not None:
            self._move_dictionary_rule(index, delta)

    def _dictionary_import_dialog(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[("Dictionary", "*.json *.csv")])
        if not path:
            return
        mode = (
            "merge"
            if messagebox.askyesno(
                self._t("dictionary_import"), self._t("dictionary_import_merge_prompt"), parent=self
            )
            else "replace"
        )
        try:
            preview = self._prepare_dictionary_import(path, mode)
        except Exception as exc:
            self._dictionary_status(self._t("dictionary_import_error", error=str(exc)))
            return
        if preview.conflicts:
            detail = "; ".join(
                f"{item.incoming.source}: {item.existing.target} / {item.incoming.target}"
                for item in preview.conflicts
            )
            messagebox.showerror(
                self._t("dictionary_import"),
                self._t("dictionary_import_conflicts", conflicts=detail),
                parent=self,
            )
            return
        detail = self._t(
            "dictionary_import_preview",
            added=preview.added_count,
            skipped=preview.exact_skipped_count,
            hints=preview.hint_update_count,
            count=len(preview.merged.rules),
        )
        self._commit_dictionary_import(
            preview,
            mode,
            confirmed=messagebox.askyesno(self._t("dictionary_import"), detail, parent=self),
        )

    def _dictionary_export_dialog(self) -> None:
        format_choice = messagebox.askyesnocancel(
            self._t("dictionary_export_format_title"),
            self._t("dictionary_export_format_prompt"),
            parent=self,
        )
        if format_choice is None:
            return
        suffix = ".json" if format_choice else ".csv"
        label = "JSON" if format_choice else "CSV"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=suffix,
            filetypes=[(label, f"*{suffix}")],
        )
        if path:
            self._export_dictionary(Path(path).with_suffix(suffix))

    def _t(self, key: str, **values: Any) -> str:
        language = getattr(self.settings, "ui_language", "uk")
        return translate(language, key, **values)

    def _configure_theme(self) -> None:
        theme = VOICE_STUDIO_THEME
        self.configure(background=theme.canvas)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(theme.ui_font, 10), background=theme.canvas)
        style.configure("TFrame", background=theme.canvas)
        style.configure("Canvas.TFrame", background=theme.canvas)
        style.configure("Topbar.TFrame", background=theme.surface)
        style.configure("Sidebar.TFrame", background=theme.surface)
        style.configure("Header.TFrame", background=theme.primary)
        style.configure("Toolbar.TFrame", background=theme.surface_muted)
        style.configure(
            "ActionBar.TFrame",
            background=theme.canvas,
            borderwidth=0,
        )
        style.configure(
            "Status.TFrame",
            background=theme.surface_muted,
            bordercolor=theme.border,
            relief="solid",
            borderwidth=1,
        )
        style.configure("Card.TFrame", background=theme.surface)
        style.configure(
            "CardBorder.TFrame",
            background=theme.surface,
            bordercolor=theme.border,
            relief="solid",
            borderwidth=1,
        )
        style.configure("ReadyBox.TFrame", background=theme.surface_muted)
        style.configure("SettingsHeader.TFrame", background=theme.surface)
        style.configure("TLabel", background=theme.canvas, foreground=theme.ink)
        style.configure(
            "Brand.TLabel",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 14, "bold"),
        )
        style.configure(
            "HeaderMuted.TLabel",
            background=theme.primary,
            foreground=theme.surface,
        )
        style.configure(
            "SidebarMuted.TLabel",
            background=theme.surface,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 9),
        )
        style.configure(
            "SidebarFooterTitle.TLabel",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 10, "bold"),
        )
        style.configure(
            "TopbarTitle.TLabel",
            background=theme.surface,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 10),
        )
        style.configure(
            "TopbarMuted.TLabel",
            background=theme.surface,
            foreground=theme.muted_ink,
            font=(theme.mono_font, 9),
        )
        style.configure(
            "Kicker.TLabel",
            background=theme.canvas,
            foreground=theme.accent,
            font=(theme.ui_font, 9, "bold"),
        )
        style.configure(
            "Title.TLabel",
            background=theme.canvas,
            foreground=theme.ink,
            font=(theme.ui_font, 25, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=theme.canvas,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 10),
        )
        style.configure(
            "Muted.TLabel",
            background=theme.surface_muted,
            foreground=theme.muted_ink,
        )
        style.configure(
            "Status.TLabel",
            background=theme.surface_muted,
            foreground=theme.ink,
            font=(theme.ui_font, 9),
        )
        style.configure(
            "StatusDot.TLabel",
            background=theme.surface_muted,
            foreground=theme.accent,
            font=(theme.ui_font, 9),
        )
        style.configure(
            "CardTitle.TLabel",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 14, "bold"),
        )
        style.configure(
            "CardMuted.TLabel",
            background=theme.surface,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 9),
        )
        style.configure(
            "CardValue.TLabel",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 10),
        )
        style.configure(
            "Ready.TLabel",
            background=theme.surface_muted,
            foreground=theme.ink,
            font=(theme.ui_font, 10, "bold"),
        )
        style.configure(
            "ReadyMuted.TLabel",
            background=theme.surface_muted,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 9),
        )
        style.configure(
            "TButton",
            background=theme.surface,
            foreground=theme.ink,
            bordercolor=theme.border,
            lightcolor=theme.surface,
            darkcolor=theme.surface,
            padding=(10, 7),
            relief="flat",
            font=(theme.ui_font, 10),
        )
        style.map(
            "TButton",
            background=[("active", theme.accent_soft), ("disabled", theme.disabled)],
            foreground=[("disabled", theme.muted_ink)],
            bordercolor=[("focus", theme.accent)],
        )
        style.configure(
            "Primary.TButton",
            background=theme.primary,
            foreground=theme.surface,
            bordercolor=theme.primary,
            font=(theme.ui_font, 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", theme.primary_hover), ("disabled", theme.disabled)],
            foreground=[("disabled", theme.muted_ink)],
        )
        style.configure(
            "PrimaryLarge.TButton",
            background=theme.primary,
            foreground=theme.surface,
            bordercolor=theme.primary,
            font=(theme.ui_font, 10, "bold"),
            padding=(16, 11),
        )
        style.map(
            "PrimaryLarge.TButton",
            background=[("active", theme.primary_hover), ("disabled", theme.disabled)],
            foreground=[("disabled", theme.muted_ink)],
        )
        style.configure(
            "Sidebar.TButton",
            background=theme.surface,
            foreground=theme.ink,
            bordercolor=theme.surface,
            lightcolor=theme.surface,
            darkcolor=theme.surface,
            padding=(13, 11),
            anchor="w",
            font=(theme.ui_font, 10),
        )
        style.map(
            "Sidebar.TButton",
            background=[("active", theme.selection), ("disabled", theme.surface)],
            foreground=[("active", theme.ink), ("disabled", theme.muted_ink)],
            bordercolor=[("focus", theme.accent)],
            cursor=[("active", "hand2"), ("disabled", "")],
        )
        style.configure(
            "SidebarActive.TButton",
            background=theme.accent_soft,
            foreground=theme.ink,
            bordercolor=theme.accent_soft,
            lightcolor=theme.accent_soft,
            darkcolor=theme.accent_soft,
            padding=(13, 11),
            anchor="w",
            font=(theme.ui_font, 10, "bold"),
        )
        style.map(
            "SidebarActive.TButton",
            background=[("active", theme.accent_soft)],
            bordercolor=[("focus", theme.accent)],
            cursor=[("active", "hand2")],
        )
        style.configure(
            "Record.TButton",
            background=theme.accent,
            foreground=theme.ink,
            bordercolor=theme.accent,
            font=(theme.ui_font, 10, "bold"),
            padding=(12, 8),
        )
        style.map(
            "Record.TButton",
            background=[("active", theme.accent_hover), ("disabled", theme.disabled)],
            foreground=[("disabled", theme.muted_ink)],
        )
        style.configure(
            "Card.TLabelframe",
            background=theme.surface,
            bordercolor=theme.border,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 11),
        )
        style.configure(
            "TEntry",
            fieldbackground=theme.surface,
            foreground=theme.ink,
            bordercolor=theme.border,
            lightcolor=theme.surface,
            darkcolor=theme.surface,
            padding=6,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", theme.accent)],
            lightcolor=[("focus", theme.accent)],
            darkcolor=[("focus", theme.accent)],
        )
        style.configure(
            "TCombobox",
            fieldbackground=theme.surface,
            background=theme.surface,
            foreground=theme.ink,
            arrowcolor=theme.primary,
            bordercolor=theme.border,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", theme.surface)],
            foreground=[("readonly", theme.ink), ("disabled", theme.muted_ink)],
            bordercolor=[("focus", theme.accent)],
        )
        style.configure("CompactAction.TButton", padding=(6, 7))
        style.configure(
            "TCheckbutton",
            background=theme.surface,
            foreground=theme.ink,
            font=(theme.ui_font, 10),
        )
        style.map(
            "TCheckbutton",
            background=[("active", theme.surface)],
            indicatorcolor=[("selected", theme.accent)],
        )
        style.configure("TNotebook", background=theme.surface, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(15, 9),
            background=theme.canvas,
            foreground=theme.muted_ink,
            font=(theme.ui_font, 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", theme.surface), ("active", theme.accent_soft)],
            foreground=[("selected", theme.ink), ("active", theme.ink)],
        )
        style.configure(
            "TScrollbar",
            background=theme.surface_muted,
            troughcolor=theme.canvas,
            bordercolor=theme.canvas,
            arrowcolor=theme.primary,
        )

    def _refresh_ui_text(self) -> None:
        self.brand_subtitle_label.configure(text=self._t("studio_subtitle"))
        self.topbar_context_label.configure(text=self._t("workspace_context"))
        self.workspace_kicker_label.configure(text=self._t("workspace_kicker"))
        self.workspace_title_label.configure(text=self._t("workspace_title"))
        self.workspace_subtitle_label.configure(text=self._t("workspace_subtitle"))
        self._refresh_dashboard_ui_text()
        self._refresh_history_filter_ui_text()
        self._refresh_dictionary_ui_text()
        self._refresh_editor_tools_ui_text()
        self._refresh_confidence_ui_text()
        self._refresh_playback_ui_text()
        self._refresh_batch_ui_text()
        self._refresh_smart_text_ui_text()
        self.local_boundary_label.configure(text=self._t("local_boundary"))
        self.local_boundary_detail_label.configure(text=self._t("local_boundary_detail"))
        self.record_button.configure(text=self._t("hold_record"))
        self.continuous_record_button.configure(
            text=self._t("stop_record" if self._continuous_recording else "continuous_record")
        )
        self.file_button.configure(text=self._t("transcribe_file"))
        self.cancel_button.configure(text=self._t("cancel"))
        self.copy_button.configure(text=self._t("copy_text"))
        self.history_frame.configure(text=self._t("history"))
        self.editor_frame.configure(text=self._t("transcript"))
        self.search_button.configure(text=self._t("search"))
        self.rename_history_button.configure(text=self._t("rename"))
        self.delete_history_button.configure(text=self._t("delete"))
        self.notebook.tab(0, text=self._t("corrected_text"))
        self.notebook.tab(1, text=self._t("raw"))
        self.notebook.tab(2, text=self._t("data"))
        self.notebook.tab(3, text=self._t("smart_text_tab"))
        self.format_label.configure(text=self._t("formatting"))
        self.save_edits_button.configure(text=self._t("save_edits"))
        self.cleanup_button.configure(text=self._t("cleanup"))
        self.undo_cleanup_button.configure(text=self._t("undo_cleanup"))
        self.readiness_title_label.configure(text=self._t("readiness"))
        self.ready_status_label.configure(text=self._t("ready_to_work"))
        self.local_processing_label.configure(text=self._t("local_processing"))
        self.readiness_engine_caption.configure(text=self._t("engine"))
        self.readiness_model_caption.configure(text=self._t("active_model"))
        self.readiness_language_caption.configure(text=self._t("interface_language"))
        self.readiness_ai_caption.configure(text=self._t("ai_cleanup_short"))
        self.privacy_note_label.configure(text=self._t("privacy_note"))
        self._apply_studio_layout(self.winfo_width(), force=True)
        if self._help_page_built:
            self._help_title_label.configure(text=self._t("help_title"))
            self._help_intro_label.configure(text=self._t("help_intro"))
            self._help_search_label.configure(text=self._t("help_search"))
            self._help_search_button.configure(text=self._t("help_search_action"))
            self._help_close_button.configure(text=self._t("help_close"))
        self._apply_studio_layout(self.winfo_width(), force=True)
        self._update_engine_label()

    def _refresh_dashboard_ui_text(self) -> None:
        self.dashboard_title_label.configure(text=self._t("dashboard_title"))
        for key, label in self.dashboard_kpi_captions.items():
            label.configure(text=self._t(f"dashboard_{key}"))
        for key, label in self.dashboard_top_captions.items():
            label.configure(text=self._t(f"dashboard_top_{key}"))
        self.dashboard_recent_caption.configure(text=self._t("dashboard_recent"))
        self.dashboard_recent_empty_label.configure(text=self._t("dashboard_recent_empty"))
        if "dashboard_dynamics_caption" in self.__dict__:
            self.dashboard_dynamics_caption.configure(text=self._t("dashboard_dynamics"))
            self.dashboard_activity_caption.configure(text=self._t("dashboard_activity_14d"))
            self.dashboard_distribution_caption.configure(
                text=self._t("dashboard_distribution")
            )
        self._refresh_dashboard()

    def _refresh_history_filter_ui_text(self) -> None:
        for name, label in self.history_filter_captions.items():
            label.configure(text=self._t(f"history_filter_{name}"))
        self.history_reset_button.configure(text=self._t("history_filter_reset"))
        for name in self._history_filter_vars:
            self._apply_history_filter_choices(name)

    def _refresh_editor_tools_ui_text(self) -> None:
        self.editor_find_button.configure(text=self._t("editor_find_button"))
        self.editor_add_rule_button.configure(text=self._t("editor_add_rule_button"))
        self.editor_filler_button.configure(text=self._t("editor_filler_button"))
        self.editor_find_caption.configure(text=self._t("editor_find_label"))
        self.editor_replace_caption.configure(text=self._t("editor_replace_label"))
        self.editor_find_case_check.configure(text=self._t("editor_find_case"))
        self.editor_find_word_check.configure(text=self._t("editor_find_whole_word"))
        for button, key in self._editor_find_button_keys.items():
            button.configure(text=self._t(key))
        if self.find_panel_visible:
            self._find_in_editor()
        else:
            self.editor_find_count_var.set("")

    def _refresh_confidence_ui_text(self) -> None:
        self.editor_confidence_button.configure(text=self._t("editor_confidence_button"))
        self.editor_confidence_caption.configure(text=self._t("editor_confidence_threshold"))
        for button, key in self._editor_confidence_button_keys.items():
            button.configure(text=self._t(key))
        if self.confidence_panel_visible:
            self._refresh_confidence_panel()
        else:
            self.confidence_count_var.set("")

    def _refresh_dictionary_ui_text(self) -> None:
        self.dictionary_title_label.configure(text=self._t("dictionary_title"))
        self.dictionary_detail_label.configure(text=self._t("dictionary_detail"))
        self.dictionary_search_button.configure(text=self._t("search"))
        self.dictionary_test_button.configure(text=self._t("dictionary_test"))
        for column, key in self._dictionary_heading_keys.items():
            self.dictionary_table.heading(column, text=self._t(key))
        for button, key in self._dictionary_button_keys.items():
            button.configure(text=self._t(key))
        self._dictionary_refresh_widgets()

    def _update_engine_label(self) -> None:
        if self.settings.engine == "ollama":
            model = self.settings.ollama_model or self._t("not_selected")
        elif self.settings.engine == "openai-cloud":
            model = self.settings.openai_transcription_model
        else:
            model = self.settings.model
        self.engine_label.set(self._t("engine_status", engine=self.settings.engine, model=model))
        profile_labels = {
            "ollama-local": self._t("profile_ollama_title"),
            "whisper-local": self._t("profile_whisper_title"),
            "openai-cloud": self._t("profile_openai_title"),
        }
        self.readiness_engine_value.set(
            profile_labels.get(self.settings.profile, self.settings.engine)
        )
        self.readiness_model_value.set(model)
        language_labels = dict(UI_LANGUAGE_CHOICES)
        self.readiness_language_value.set(
            language_labels.get(self.settings.ui_language, self.settings.ui_language)
        )
        if self.settings.cleanup_provider == "none":
            self.readiness_ai_value.set("—")
        elif self.settings.cleanup_provider == "ollama":
            cleanup_model = self.settings.ollama_model or self._t("not_selected")
            self.readiness_ai_value.set(f"Ollama / {cleanup_model}")
        else:
            self.readiness_ai_value.set(
                f"OpenAI / {self.settings.openai_cleanup_model or self._t('not_selected')}"
            )

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        state = "disabled" if value else "normal"
        self.file_button.configure(state=state)
        self.record_button.configure(state=state)
        self.continuous_record_button.configure(state=state)
        self.settings_button.configure(state=state)
        self.models_button.configure(state=state)
        self.backup_button.configure(state=state)
        self.rename_history_button.configure(state=state)
        self.delete_history_button.configure(state=state)
        self.cleanup_button.configure(state=state)
        self.undo_cleanup_button.configure(state=state)
        self.cancel_button.configure(state="normal" if value else "disabled")
        if "status_progress" in self.__dict__:
            if value:
                self.status_progress.configure(mode="indeterminate")
                self.status_progress.pack(side="right", padx=(0, 10))
                try:
                    self.status_progress.start(12)
                except Exception:
                    pass
            else:
                try:
                    self.status_progress.stop()
                except Exception:
                    pass
                self.status_progress.pack_forget()

    def _start_hotkey(self) -> None:
        if self.hotkey is not None:
            if not self.hotkey.stop():
                self.status.set(
                    self._t(
                        "hotkey_unavailable",
                        error=self._t("hotkey_stop_retry"),
                    )
                )
                return
            self.hotkey = None
        try:
            self.hotkey = GlobalHotkey(
                self.settings.hotkey,
                lambda: self._post_event("record_start", None),
                lambda: self._post_event("record_stop", None),
            )
            self.hotkey.start()
        except Exception as exc:
            self.hotkey = None
            self.status.set(self._t("hotkey_unavailable", error=exc))

    def _recordings_directory(self) -> Path:
        return (Path(cache_dir()) / "recordings").resolve(strict=False)

    def _safe_recording_path(self, path: str | Path | None) -> Path | None:
        if path is None:
            return None
        try:
            lexical_candidate = Path(path).expanduser().absolute()
            recordings_directory = self._recordings_directory()
            if lexical_candidate.parent.resolve(strict=False) != recordings_directory:
                return None
            if lexical_candidate.is_symlink():
                return None
            candidate = lexical_candidate.resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if candidate.parent != recordings_directory:
            return None
        return candidate

    def _lexical_safe_recording_path(self, path: str | Path | None) -> Path | None:
        """Return a direct-child path without resolving its final entry."""

        if path is None:
            return None
        try:
            candidate = Path(path).expanduser().absolute()
            recordings_directory = self._recordings_directory()
            if candidate.parent.resolve(strict=False) != recordings_directory:
                return None
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return candidate

    def _find_pending_recording(self, path: str | Path | None) -> Path | None:
        safe_path = self._safe_recording_path(path)
        if safe_path is None:
            return None
        pending = self.__dict__.get("_pending_microphone_files", set())
        for candidate in pending:
            if self._safe_recording_path(candidate) == safe_path:
                return candidate
        return None

    def _cleanup_temp(self, path: str | Path | None) -> None:
        """Remove only a tracked, direct child of the app recording root."""

        pending = self.__dict__.get("_pending_microphone_files", set())
        tracked = self._find_pending_recording(path)
        if tracked is None:
            return
        safe_path = self._safe_recording_path(tracked)
        if safe_path is None:
            return
        ambiguous = self.__dict__.get("_ambiguous_microphone_files", set())
        if any(self._safe_recording_path(item) == safe_path for item in ambiguous):
            return
        try:
            safe_path.unlink(missing_ok=True)
        except OSError as exc:
            diagnostics = self.__dict__.get("_recording_residue_diagnostics", [])
            diagnostics.append(f"{safe_path}: {exc}")
            self._recording_residue_diagnostics = diagnostics
            if "status" in self.__dict__:
                self.status.set(self._t("temp_record_cleanup_failed", name=safe_path.name))
            messagebox.showerror(
                self._t("recording_cleanup_title"),
                f"{self._t('temp_record_cleanup_failed', name=safe_path)}: {exc}",
                parent=self,
            )
            return
        pending.discard(tracked)
        pending.discard(safe_path)
        ambiguous.discard(tracked)
        ambiguous.discard(safe_path)

    @staticmethod
    def _recorder_error_is_ambiguous(error: BaseException | None) -> bool:
        if error is None:
            return False
        related = [error, getattr(error, "cleanup_error", None)]
        for item in related:
            if item is None:
                continue
            if bool(getattr(item, "identity_ambiguous", False)):
                return True
            diagnostic = getattr(item, "diagnostic", None)
            if isinstance(diagnostic, dict):
                if bool(diagnostic.get("identity_ambiguous")):
                    return True
                if "ambig" in str(diagnostic).lower():
                    return True
            if "ambig" in str(item).lower():
                return True
        return False

    @staticmethod
    def _is_unresolved_writer_timeout(error: BaseException | None) -> bool:
        return (
            isinstance(error, TimeoutError)
            and str(error) == "audio recorder writer did not stop within 2.0 seconds"
        )

    def _retain_unresolved_recorder_path(self, path: str | Path | None) -> Path | None:
        safe_path = self._safe_recording_path(path)
        if safe_path is None:
            return None
        pending = self.__dict__.setdefault("_pending_microphone_files", set())
        ambiguous = self.__dict__.setdefault("_ambiguous_microphone_files", set())
        pending.add(safe_path)
        ambiguous.add(safe_path)
        return safe_path

    def _register_recorder_residues(self, error: BaseException | None = None) -> None:
        owners = [error, getattr(error, "cleanup_error", None), self.recorder]
        raw_paths: list[Path] = []
        for owner in owners:
            if owner is None:
                continue
            for raw_path in getattr(owner, "residue_paths", ()) or ():
                candidate = Path(raw_path)
                if candidate not in raw_paths:
                    raw_paths.append(candidate)
        quarantine = getattr(self.recorder, "quarantine_path", None)
        if quarantine is not None and Path(quarantine) not in raw_paths:
            raw_paths.append(Path(quarantine))

        pending = self.__dict__.get("_pending_microphone_files", set())
        ambiguous = self.__dict__.get("_ambiguous_microphone_files", set())
        is_ambiguous = self._recorder_error_is_ambiguous(error)
        structured_residue = bool(
            getattr(error, "cleanup_error", None) is not None or getattr(error, "residue_paths", ())
        )
        diagnostics = self.__dict__.get("_recording_residue_diagnostics", [])
        for raw_path in raw_paths:
            safe_path = self._safe_recording_path(raw_path)
            if safe_path is None:
                safe_path = self._lexical_safe_recording_path(raw_path)
            if safe_path is None:
                diagnostics.append(f"Залишок recorder поза app root: {raw_path}")
                continue
            pending.add(safe_path)
            if is_ambiguous or structured_residue:
                ambiguous.add(safe_path)
        self._pending_microphone_files = pending
        self._ambiguous_microphone_files = ambiguous
        self._recording_residue_diagnostics = diagnostics

    def _report_recorder_error(
        self,
        error: BaseException,
        *,
        title: str | None = None,
        register_residues: bool = True,
    ) -> None:
        if register_residues:
            self._register_recorder_residues(error)
        message = str(error)
        ambiguous = self.__dict__.get("_ambiguous_microphone_files", set())
        if ambiguous:
            paths = ", ".join(str(path) for path in sorted(ambiguous, key=str))
            message += f"\n\n{self._t('residue_saved', paths=paths)}"
        messagebox.showerror(title or self._t("microphone"), message, parent=self)

    def _report_recording_residues(self) -> None:
        pending = self.__dict__.get("_pending_microphone_files", set())
        ambiguous = self.__dict__.get("_ambiguous_microphone_files", set())
        diagnostics = self.__dict__.get("_recording_residue_diagnostics", [])
        if not pending and not ambiguous and not diagnostics:
            return
        details: list[str] = []
        if ambiguous:
            details.append(
                self._t(
                    "residue_ambiguous",
                    paths=", ".join(str(path) for path in sorted(ambiguous, key=str)),
                )
            )
        if diagnostics:
            details.append(self._t("cleanup_diagnostics", details="; ".join(diagnostics)))
        unambiguous_pending = pending - ambiguous
        if unambiguous_pending:
            details.append(
                self._t(
                    "residue_pending",
                    paths=", ".join(str(path) for path in sorted(unambiguous_pending, key=str)),
                )
            )
        messagebox.showerror(self._t("recording_cleanup_title"), "\n\n".join(details), parent=self)

    def _report_automatic_cleanup_warning(self, transcript: Transcript) -> bool:
        metadata = getattr(transcript, "metadata", {})
        if not isinstance(metadata, dict):
            return False
        warning = str(metadata.get("cleanup_warning", "")).strip()
        if not warning:
            return False
        self.status.set(self._t("cleanup_automatic_failed"))
        messagebox.showwarning(
            self._t("cleanup_automatic_failed_title"),
            self._t("cleanup_automatic_failed_message", error=warning),
            parent=self,
        )
        return True

    def _poll_events(self) -> None:
        if self.__dict__.setdefault("_shutdown_event", threading.Event()).is_set():
            return
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "record_start":
                    self._record_start()
                elif event == "record_stop":
                    self._record_stop()
                elif event == "done":
                    transcript, cleanup = value
                    self._cleanup_temp(cleanup)
                    self._set_busy(False)
                    self._mirror_transcript_quietly(transcript)
                    if self.__dict__.get("_batch_owned", False):
                        self._batch_job_finished(transcript=transcript)
                    else:
                        if not self._try_show_result(transcript, copy=True):
                            self._refresh_history(
                                select_id=self.current.id if self.current else None
                            )
                            self._refresh_dashboard()
                        self._report_automatic_cleanup_warning(transcript)
                elif event == "error":
                    error, cleanup = value
                    self._cleanup_temp(cleanup)
                    self._set_busy(False)
                    if self.__dict__.get("_batch_owned", False):
                        # One failed file must not stop the queue behind a modal.
                        self._batch_job_finished(error=error)
                    else:
                        self.status.set(self._t("processing_error"))
                        messagebox.showerror(self._t("error"), str(error))
                elif event == "model_progress":
                    downloaded, expected = value
                    percent = min(99, round(downloaded * 100 / max(expected, 1)))
                    self.status.set(self._t("model_download_progress", percent=percent))
                    if "status_progress" in self.__dict__:
                        self.status_progress.configure(mode="determinate", maximum=100)
                        self.status_progress.configure(value=percent)
                elif event == "model_done":
                    self._set_busy(False)
                    self.status.set(self._t("model_installed_status", model=value["id"]))
                    messagebox.showinfo(
                        self._t("models"), self._t("model_installed", model=value["id"])
                    )
                elif event == "model_error":
                    self._set_busy(False)
                    self.status.set(self._t("model_install_error"))
                    messagebox.showerror(self._t("models"), str(value))
                elif event == "job_progress":
                    phase, elapsed = value
                    labels = {
                        "importing": self._t("phase_importing"),
                        "loading": self._t("phase_loading"),
                        "transcribing": self._t("phase_transcribing"),
                        "saving": self._t("phase_saving"),
                        "cleaning": self._t("phase_cleaning"),
                        "completed": self._t("phase_completed"),
                    }
                    self.status.set(
                        self._t(
                            "phase_elapsed",
                            phase=labels.get(phase, phase),
                            elapsed=elapsed,
                        )
                    )
                elif event == "ollama_models":
                    models = [str(item) for item in value.get("models", []) if str(item)]
                    all_models = [str(item) for item in value.get("all_models", []) if str(item)]
                    for model in models:
                        if model not in all_models:
                            all_models.append(model)
                    self._installed_ollama_audio_models = models
                    self._installed_ollama_all_models = all_models
                    self._ollama_discovery_error = str(value.get("error", ""))
                    updated = with_preferred_ollama_model(self.settings, models)
                    if updated is not self.settings:
                        try:
                            save_settings(updated)
                        except Exception as exc:
                            self._ollama_discovery_error = str(exc)[:500]
                        else:
                            self.settings = updated
                            self._refresh_ui_text()
                    combo = self._settings_ollama_combo
                    if combo is not None and combo.winfo_exists():
                        choices = list(models or all_models)
                        if self.settings.ollama_model not in choices:
                            choices.insert(0, self.settings.ollama_model)
                        combo.configure(values=tuple(item for item in choices if item))
                        page_variable = self._settings_variables.get("ollama_model")
                        current_value = page_variable.get() if page_variable is not None else ""
                        baseline_value = self._settings_baseline.get("ollama_model")
                        untouched = not current_value or current_value == baseline_value
                        if self.settings.ollama_model and untouched:
                            combo.set(self.settings.ollama_model)
                            self._settings_baseline["ollama_model"] = self.settings.ollama_model
                    if self._ollama_discovery_error:
                        message = self._ollama_discovery_error
                    elif models:
                        message = self._t("ollama_found", count=len(models))
                    elif all_models:
                        message = self._t("ollama_no_audio_models")
                    else:
                        message = self._t("ollama_missing")
                    for variable in (self._settings_info_var, self._settings_ollama_status_var):
                        if variable is not None:
                            variable.set(message)
                elif event == "hardware_detection":
                    result = value
                    if not isinstance(result, HardwareDetectionResult):
                        result = HardwareDetectionResult(
                            "degraded",
                            (),
                            (),
                            ("auto", "default"),
                            self._t(
                                "hardware_detection_degraded",
                                detail=str(value)[:200],
                            ),
                        )
                    info = self._settings_info_var
                    if info is not None:
                        key = (
                            "hardware_detection_success"
                            if result.status == "ok"
                            else "hardware_detection_degraded"
                        )
                        info.set(self._t(key, detail=result.detail))
                    device_values = tuple(
                        item
                        for item in dict.fromkeys(("auto", *result.device_capabilities))
                        if item in SUPPORTED_DEVICES
                    )
                    compute_values = tuple(
                        item
                        for item in dict.fromkeys(("default", "auto", *result.compute_types))
                        if item in SUPPORTED_COMPUTE_TYPES
                    )
                    for combo, values in (
                        (self._settings_hardware_device_combo, device_values),
                        (self._settings_hardware_compute_combo, compute_values),
                    ):
                        if combo is not None and combo.winfo_exists():
                            combo.configure(values=tuple(values))
                elif event == "job_cancelled":
                    cleanup = value
                    self._cleanup_temp(cleanup)
                    self._set_busy(False)
                    self.status.set(self._t("task_cancelled"))
                    if self.__dict__.get("_batch_owned", False):
                        self._batch_job_cancelled()
                elif event == "backup_done":
                    action, result = value
                    self._set_busy(False)
                    if action == "restore":
                        self._reload_after_restore()
                        recovery = result.get("recovery") or "—"
                        message = self._t(
                            "backup_restored",
                            records=result["records"],
                            recovery=recovery,
                        )
                    elif action == "verify":
                        message = self._t(
                            "backup_verified",
                            records=result["records"],
                            members=result["members"],
                        )
                    else:
                        message = self._t(
                            "backup_created",
                            path=result["path"],
                            records=result["records"],
                            audio_files=result["audio_files"],
                        )
                    self.status.set(self._t("backup_complete"))
                    messagebox.showinfo(self._t("backup"), message)
                elif event == "backup_passphrase_required":
                    action, callback = value
                    self._handle_backup_passphrase_required(action, callback)
                elif event == "backup_error":
                    action, error = value
                    if action == "restore":
                        self._reload_after_restore()
                    self._set_busy(False)
                    self.status.set(self._t("backup_error"))
                    messagebox.showerror(self._t("backup"), str(error))
                elif event == "sync_done":
                    self._set_busy(False)
                    if isinstance(value, SyncSummary):
                        self.status.set(
                            self._t(
                                "sync_done",
                                written=value.written,
                                audio=value.audio,
                                failed=len(value.failed),
                            )
                        )
                    else:
                        self.status.set(self._t("sync_failed", error=str(value)))
                        messagebox.showerror(self._t("sync_section"), str(value), parent=self)
                elif event == "cleanup_proposal":
                    transcript, proposal = value
                    self._set_busy(False)
                    preview = self._t(
                        "cleanup_preview",
                        before=transcript.corrected_text[:1000],
                        after=proposal.corrected_text[:1000],
                    )
                    if messagebox.askyesno(self._t("cleanup_preview_title"), preview, parent=self):
                        if not self._confirm_editor_transition():
                            continue
                        if not self._cleanup_result_is_current(transcript):
                            self.status.set(self._t("cleanup_stale"))
                            continue
                        updated = self.store.apply_ai_cleanup(
                            transcript.id,
                            proposal.to_dict(),
                            provider=self.__dict__.get("_cleanup_provider", "openai"),
                            model=self.__dict__.get(
                                "_cleanup_model", self.settings.openai_cleanup_model
                            ),
                        )
                        self._show_result(updated, refresh=True)
                        self.status.set(self._t("cleanup_applied"))
                        self._mirror_transcript_quietly(updated)
                    else:
                        self.status.set(self._t("cleanup_not_applied"))
                elif event == "cleanup_error":
                    self._set_busy(False)
                    self.status.set(self._t("cleanup_error"))
                    messagebox.showerror(self._t("cleanup_error"), str(value), parent=self)
        except queue.Empty:
            pass
        finally:
            # One failing handler must not stop event polling for the rest of
            # the session; the exception still surfaces through Tk reporting.
            if not self._shutdown_event.is_set():
                self.after(100, self._poll_events)

    def _new_recording_temp(self) -> Path:
        recordings_directory = self._recordings_directory()
        try:
            recordings_directory.mkdir(parents=True, exist_ok=True)
            try:
                recordings_directory.chmod(0o700)
            except OSError:
                pass
            path = Path(self.recorder.start(recordings_directory)).resolve(strict=False)
        except Exception as exc:
            self._register_recorder_residues(exc)
            raise
        safe_path = self._safe_recording_path(path)
        if safe_path is None:
            raise RuntimeError("recorder returned a path outside the app recordings directory")
        pending = self.__dict__.setdefault("_pending_microphone_files", set())
        pending.add(safe_path)
        try:
            safe_path.chmod(0o600)
        except OSError:
            pass
        return safe_path

    def _record_start(self, *, continuous: bool = False) -> None:
        if self._busy or self.recorder.recording:
            return
        try:
            self._active_recording_path = self._new_recording_temp()
            self._continuous_recording = continuous
            self.after(250, self._poll_recording_limit, self._active_recording_path)
            if continuous:
                self.continuous_record_button.configure(text=self._t("stop_record"))
                self.status.set(self._t("recording_continuous"))
            else:
                self.status.set(self._t("recording_hold"))
        except Exception as exc:
            self._active_recording_path = None
            self._report_recorder_error(exc, register_residues=False)

    def _toggle_continuous_recording(self) -> None:
        if self.recorder.recording:
            if self._continuous_recording:
                self._record_stop(force=True)
            return
        self._record_start(continuous=True)

    def _poll_recording_limit(self, path: str | Path) -> None:
        active_path = self.__dict__.get("_active_recording_path")
        active_safe_path = self._safe_recording_path(active_path)
        callback_safe_path = self._safe_recording_path(path)
        if active_path is None or active_safe_path != callback_safe_path:
            return
        if not getattr(self.recorder, "limit_reached", False):
            if getattr(self.recorder, "recording", False):
                self.after(250, self._poll_recording_limit, active_path)
            return
        self._record_stop(force=True, limit_forced=True)

    def _record_stop(self, *, force: bool = False, limit_forced: bool = False) -> None:
        active_path = self.__dict__.get("_active_recording_path")
        if not self.recorder.recording and not limit_forced:
            return
        if limit_forced and (
            active_path is None or not getattr(self.recorder, "limit_reached", False)
        ):
            return
        if self._continuous_recording and not force:
            return
        try:
            result = self.recorder.stop()
            self._continuous_recording = False
            self.continuous_record_button.configure(text=self._t("continuous_record"))
            self._active_recording_path = None
        except Exception as exc:
            self._continuous_recording = False
            self.continuous_record_button.configure(text=self._t("continuous_record"))
            self._active_recording_path = None
            writer_timeout = self._is_unresolved_writer_timeout(exc)
            if writer_timeout:
                self._retain_unresolved_recorder_path(active_path)
            self._register_recorder_residues(exc)
            if not writer_timeout:
                self._cleanup_temp(active_path)
            self._report_recorder_error(exc, register_residues=False)
            return

        result_path = self._safe_recording_path(getattr(result, "path", None))
        expected_path = self._safe_recording_path(active_path)
        tracked_path = self._find_pending_recording(result_path)
        if (
            expected_path is None
            or result_path is None
            or result_path != expected_path
            or tracked_path is None
        ):
            self._cleanup_temp(active_path)
            self._report_recorder_error(RuntimeError(self._t("recording_path_mismatch")))
            return

        limit_reached = bool(getattr(result, "limit_reached", False) or limit_forced)
        if getattr(result, "degraded", False):
            warning = getattr(result, "warning", "") or (self._t("recording_damage_default"))
            if not messagebox.askyesno(
                self._t("recording_corrupt_title"),
                self._t("recording_corrupt_prompt", warning=warning),
                parent=self,
                default=messagebox.NO,
            ):
                self._cleanup_temp(result_path)
                if limit_reached:
                    self.status.set(self._t("recording_limit_rejected"))
                else:
                    self.status.set(self._t("recording_corrupt_rejected"))
                return

        started = self._process(result_path, cleanup=True)
        if limit_reached:
            result_text = (
                self._t("recording_processing") if started else self._t("processing_not_started")
            )
            self.status.set(self._t("recording_limit_status", result=result_text))

    def _choose_file(self) -> None:
        if self._busy:
            return
        name = filedialog.askopenfilename(filetypes=MEDIA_FILETYPES)
        if name:
            self._process(Path(name), cleanup=False)

    def _process(self, source: Path, *, cleanup: bool, batch: bool = False) -> bool:
        if self._busy:
            # The source is retained for a later retry; deleting it here would
            # discard a finished recording while another job is still running.
            self.status.set(
                f"{self._t('processing_not_started')} {self._t('task_already_running')}"
            )
            return False
        if self.settings.engine == "openai-cloud":
            if self.settings.offline_only:
                message = self._t("offline_blocks_openai")
                messagebox.showerror("Cloud STT", message)
                if cleanup:
                    self._cleanup_temp(source)
                    self.status.set(f"{self._t('processing_not_started')} {message}")
                return False
            try:
                from .engines.openai_cloud import OpenAICloudEngine

                OpenAICloudEngine.validate_upload(source)
                source_size = source.stat().st_size
            except Exception as exc:
                messagebox.showerror("Cloud STT", str(exc), parent=self)
                if cleanup:
                    self._cleanup_temp(source)
                    self.status.set(f"{self._t('processing_not_started')} {exc}")
                return False
            if not messagebox.askyesno(
                self._t("cloud_audio_title"),
                self._t(
                    "cloud_audio_prompt",
                    name=source.name,
                    size=source_size / 1_000_000,
                ),
                parent=self,
            ):
                if cleanup:
                    self._cleanup_temp(source)
                    self.status.set(self._t("cloud_audio_declined"))
                return False
        self._set_busy(True)
        self._cancel_event.clear()
        # The done/error/cancel handlers need to know whether this job is the
        # queue's, and Tk callbacks are single threaded, so the flag is set
        # before the worker can post its first event.
        self._batch_owned = batch
        self.status.set(self._t("prepare_local"))

        def work() -> None:
            try:
                dictionary = TerminologyDictionary.load(self.settings.dictionary_path)
                transcript = self.job_controller.run(
                    source,
                    self.settings,
                    dictionary,
                    timeout_seconds=self.settings.task_timeout_seconds,
                    cancelled=self._cancel_event.is_set,
                    progress=lambda phase, elapsed: self._post_event(
                        "job_progress", (phase, elapsed)
                    ),
                )
                self._post_event("done", (transcript, source if cleanup else None))
            except JobCancelled:
                self._post_event("job_cancelled", source if cleanup else None)
            except Exception as exc:
                self._post_event("error", (exc, source if cleanup else None))

        try:
            return self._start_worker("transcription", work) is not None
        except RuntimeError:
            # A finished worker can still be alive for an instant after its
            # completion event cleared the busy flag; recover instead of
            # letting the error escape into the Tk callback.
            self._set_busy(False)
            self._batch_owned = False
            self.status.set(
                f"{self._t('processing_not_started')} {self._t('task_already_running')}"
            )
            return False

    def _cancel_current(self) -> None:
        if self._busy:
            self._cancel_event.set()
            self.status.set(self._t("cancel_running"))

    # -- batch transcription queue -----------------------------------------

    def _toggle_batch_panel(self) -> None:
        if self.batch_panel_visible:
            self.batch_panel.grid_remove()
            self.batch_panel_visible = False
            return
        self.batch_panel.grid()
        self.batch_panel_visible = True
        self._batch_refresh_view()

    def _batch_refresh_view(self) -> None:
        """Redraw the queue rows and the pause/resume label from the model."""

        tree = self.__dict__.get("batch_tree")
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for index, item in enumerate(self.batch_queue.items):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item.path.name,
                    self._t(f"batch_status_{item.status}"),
                    f"{item.seconds:.1f}",
                    item.error or "",
                ),
            )
        pause_button = self.__dict__.get("batch_pause_button")
        if pause_button is not None:
            pause_button.configure(
                text=self._t("batch_resume" if self.batch_queue.paused else "batch_pause")
            )
        if "status_batch_var" in self.__dict__:
            summary = self.batch_queue.summary()
            if summary.total > 0:
                done = summary.done + summary.failed + summary.skipped
                self.status_batch_var.set(f"{done}/{summary.total}")
            else:
                self.status_batch_var.set("")

    def _refresh_batch_ui_text(self) -> None:
        self.batch_button.configure(text=self._t("batch_button"))
        self.batch_panel.configure(text=self._t("batch_panel_title"))
        self.batch_recursive_check.configure(text=self._t("batch_recursive"))
        for button, key in self._batch_button_keys.items():
            button.configure(text=self._t(key))
        for column, key, _width in BATCH_COLUMNS:
            self.batch_tree.heading(column, text=self._t(key))
        self._batch_refresh_view()

    def _batch_report(
        self, added: list[Path], rejected: list[tuple[Path, str]]
    ) -> None:
        parts = [self._t("batch_added", count=len(added))]
        if rejected:
            parts.append(self._t("batch_rejected", count=len(rejected)))
        self.status.set(" ".join(parts))
        self._batch_refresh_view()

    def _batch_add_files(self) -> None:
        selection = filedialog.askopenfilenames(filetypes=MEDIA_FILETYPES)
        if isinstance(selection, str):
            selection = self.tk.splitlist(selection) if selection else ()
        if not selection:
            return
        added, rejected = self.batch_queue.add_paths(Path(name) for name in selection)
        self._batch_report(added, rejected)

    def _batch_add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if not folder:
            return
        added, rejected = self.batch_queue.add_folder(
            Path(folder), recursive=bool(self.batch_recursive_var.get())
        )
        self._batch_report(added, rejected)

    def _batch_start(self) -> None:
        if self._busy:
            self.status.set(self._t("batch_busy"))
            return
        self.batch_queue.resume()
        self._batch_refresh_view()
        self._batch_advance()

    def _batch_toggle_pause(self) -> None:
        if self.batch_queue.paused:
            self._batch_start()
            return
        self.batch_queue.pause()
        self._batch_refresh_view()

    def _batch_seconds(self) -> float:
        started = self.__dict__.get("_batch_started")
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def _batch_advance(self) -> None:
        """Start the next pending item, or end the run with a summary."""

        if (
            self._busy
            or self.__dict__.get("_batch_owned", False)
            or self.batch_queue.running() is not None
        ):
            return
        while not self.batch_queue.paused:
            item = self.batch_queue.next_pending()
            if item is None:
                break
            self.batch_queue.mark_running(item.path)
            self._batch_started = time.monotonic()
            self._batch_refresh_view()
            if self._process(item.path, cleanup=False, batch=True):
                self.status.set(self._t("batch_running_file", name=item.path.name))
                return
            # The job never started (busy, refused cloud upload, unreadable
            # source): record why and keep the rest of the queue moving.
            self._batch_owned = False
            self.batch_queue.mark_failed(
                item.path, self._t("processing_not_started"), self._batch_seconds()
            )
            self._batch_started = None
        self._batch_finish()

    def _batch_finish(self) -> None:
        summary = self.batch_queue.summary()
        self._batch_refresh_view()
        transcript = self.__dict__.get("_batch_last_transcript")
        self._batch_last_transcript = None
        if transcript is not None:
            # Only the last result opens in the editor: a queue must not steal
            # the editor away from the user after every single file.
            self._try_show_result(transcript, refresh=False)
        self._refresh_history(select_id=self.current.id if self.current else None)
        self._refresh_dashboard()
        if self.batch_queue.paused and summary.pending:
            # A pause is not an end: say what is still waiting instead of a
            # summary that reads as if the queue were finished.
            self.status.set(self._t("batch_paused", count=summary.pending))
            return
        self.status.set(
            self._t(
                "batch_finished",
                done=summary.done,
                failed=summary.failed,
                skipped=summary.skipped,
            )
        )

    def _batch_job_finished(
        self, *, transcript: Transcript | None = None, error: object | None = None
    ) -> None:
        self._batch_owned = False
        seconds = self._batch_seconds()
        self._batch_started = None
        item = self.batch_queue.running()
        if item is not None:
            if transcript is not None:
                self.batch_queue.mark_done(item.path, transcript.id, seconds)
            else:
                self.batch_queue.mark_failed(item.path, _plain_error_text(error), seconds)
        if transcript is not None:
            self._batch_last_transcript = transcript
        self._batch_refresh_view()
        self.after(0, self._batch_advance)

    def _batch_job_cancelled(self) -> None:
        """Cancel stops the running item and holds the queue where it is."""

        self._batch_owned = False
        seconds = self._batch_seconds()
        self._batch_started = None
        item = self.batch_queue.running()
        self.batch_queue.pause()
        if item is not None:
            self.batch_queue.mark_failed(
                item.path, self._t("batch_status_cancelled"), seconds
            )
        self._batch_refresh_view()

    def _batch_selected_indexes(self) -> list[int]:
        indexes: list[int] = []
        for identifier in self.batch_tree.selection():
            try:
                indexes.append(int(identifier))
            except (TypeError, ValueError):
                continue
        return sorted(indexes)

    def _batch_skip_selected(self) -> None:
        items = self.batch_queue.items
        skipped = 0
        for index in self._batch_selected_indexes():
            if index >= len(items) or items[index].status != "pending":
                continue
            self.batch_queue.mark_skipped(items[index].path)
            skipped += 1
        if not skipped:
            self.status.set(self._t("batch_no_selection"))
            return
        self._batch_refresh_view()

    def _batch_clear_finished(self) -> None:
        removed = self.batch_queue.clear_finished()
        self._batch_refresh_view()
        self.status.set(self._t("batch_removed", count=removed))

    def _batch_clear(self) -> None:
        try:
            self.batch_queue.clear()
        except ValueError:
            self.status.set(self._t("batch_busy"))
            return
        self._batch_refresh_view()

    # -- smart text --------------------------------------------------------

    def _smart_text_options(self) -> SmartTextOptions | None:
        try:
            return SmartTextOptions(
                paragraph_gap_seconds=float(str(self.smart_gap_var.get()).replace(",", ".")),
                max_paragraph_seconds=float(str(self.smart_max_var.get()).replace(",", ".")),
                timestamps=bool(self.smart_timestamps_var.get()),
                speakers=bool(self.smart_speakers_var.get()),
            )
        except (TypeError, ValueError):
            return None

    def _smart_speaker_labels(self) -> dict[int, str]:
        transcript = self.current
        if transcript is None:
            return {}
        return speaker_labels_from_metadata(transcript.metadata)

    def _refresh_smart_text(self) -> None:
        if "smart_text_view" not in self.__dict__:
            return
        transcript = self.current
        if transcript is None:
            self._smart_text_rendered = ""
            self._set_readonly_text(self.smart_text_view, self._t("smart_text_empty"))
            self._refresh_smart_speaker_list()
            return
        options = self._smart_text_options()
        if options is None:
            # A rejected option must not leave a preview that ignores it.
            self._smart_text_rendered = ""
            self._set_readonly_text(self.smart_text_view, "")
            self.status.set(self._t("smart_text_invalid"))
            return
        self._smart_text_rendered = render_plain(transcript, options)
        self._set_readonly_text(self.smart_text_view, self._smart_text_rendered)
        self._refresh_smart_speaker_list()

    def _refresh_smart_speaker_list(self) -> None:
        self.smart_speaker_list.delete(0, "end")
        transcript = self.current
        if transcript is None:
            return
        labels = self._smart_speaker_labels()
        for index, segment in enumerate(transcript.segments):
            snippet = " ".join(editable_text(segment).split())
            if len(snippet) > SMART_TEXT_SNIPPET_WIDTH:
                snippet = snippet[:SMART_TEXT_SNIPPET_WIDTH].rstrip() + "…"
            row = f"{index} · {format_timestamp(segment.start)} · {snippet}"
            name = labels.get(index)
            if name:
                row = f"{row} — {name}"
            self.smart_speaker_list.insert("end", row)

    def _assign_smart_speaker(self) -> None:
        transcript = self.current
        if transcript is None:
            self.status.set(self._t("smart_text_empty"))
            return
        selection = self.smart_speaker_list.curselection()
        if not selection:
            self.status.set(self._t("smart_text_select_segment"))
            return
        index = int(selection[0])
        if index >= len(transcript.segments):
            self.status.set(self._t("smart_text_select_segment"))
            return
        labels = self._smart_speaker_labels()
        answer = simpledialog.askstring(
            self._t("smart_text_assign_speaker"),
            self._t("smart_text_speaker_prompt"),
            initialvalue=labels.get(index, ""),
            parent=self,
        )
        if answer is None:
            return
        name = " ".join(answer.split())
        if name:
            labels[index] = name
        else:
            labels.pop(index, None)
        # Metadata only: raw_text and every segment stay exactly as recognised.
        self.current = self.store.update_speaker_labels(transcript.id, labels)
        self._mirror_transcript_quietly(self.current)
        self._refresh_smart_text()

    def _copy_smart_text(self) -> None:
        if not self._smart_text_rendered:
            self.status.set(self._t("smart_text_empty"))
            return
        self._copy_to_clipboard(self._smart_text_rendered)
        self.status.set(self._t("copied"))

    def _export_smart_text(self, fmt: str) -> None:
        transcript = self.current
        if transcript is None:
            self.status.set(self._t("smart_text_empty"))
            return
        options = self._smart_text_options()
        if options is None:
            self.status.set(self._t("smart_text_invalid"))
            return
        content = (
            render_markdown(transcript, options)
            if fmt == "md"
            else render_plain(transcript, options)
        )
        destination = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            initialfile=f"{Path(transcript.source_name).stem}.{fmt}",
        )
        if not destination:
            return
        written = _write_text_atomically(Path(destination), content)
        self.status.set(self._t("smart_text_exported", name=written.name))

    def _refresh_smart_text_ui_text(self) -> None:
        self.smart_text_gap_label.configure(text=self._t("smart_text_gap"))
        self.smart_text_max_label.configure(text=self._t("smart_text_max"))
        self.smart_timestamps_check.configure(text=self._t("smart_text_timestamps"))
        self.smart_speakers_check.configure(text=self._t("smart_text_speakers"))
        self.smart_speaker_caption.configure(text=self._t("smart_text_speaker_list"))
        for button, key in self._smart_text_button_keys.items():
            button.configure(text=self._t(key))
        self._refresh_smart_text()

    def _show_result(
        self, transcript: Transcript, *, copy: bool = False, refresh: bool = True
    ) -> None:
        if self.current is None or self.current.id != transcript.id:
            self._stop_playback()
        self.current = transcript
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", transcript.corrected_text)
        self._apply_editor_formatting(transcript.metadata.get("editor_formatting", {}))
        self._editor_baseline = snapshot_editor(
            self.editor.get("1.0", "end-1c"), self._editor_formatting()
        )
        self._set_readonly_text(self.raw_editor, transcript.raw_text)
        details = {
            "id": transcript.id,
            "created_at": transcript.created_at,
            "source_name": transcript.source_name,
            "source_sha256": transcript.source_sha256,
            "language": transcript.language,
            "engine": transcript.engine,
            "model": transcript.model,
            "audio_seconds": transcript.audio_seconds,
            "real_time_factor": transcript.real_time_factor,
            "dictionary_version": transcript.dictionary_version,
            "audio_retained": transcript.audio_retained,
            "segments": len(transcript.segments),
            "metadata": transcript.metadata,
        }
        self._set_readonly_text(self.details, json.dumps(details, ensure_ascii=False, indent=2))
        rtf = (
            ""
            if transcript.real_time_factor is None
            else f", RTF {transcript.real_time_factor:.2f}"
        )
        self.status.set(
            self._t(
                "transcription_done",
                language=transcript.language,
                segments=len(transcript.segments),
                rtf=rtf,
            )
        )
        if self.confidence_panel_visible:
            self._refresh_confidence_panel()
        self._refresh_smart_text()
        if refresh:
            self._refresh_history(select_id=transcript.id)
            self._refresh_dashboard()
        if copy and self.settings.auto_copy:
            self._copy_to_clipboard(transcript.corrected_text)

    def _try_show_result(
        self, transcript: Transcript, *, copy: bool = False, refresh: bool = True
    ) -> bool:
        if not self._confirm_editor_transition():
            return False
        self._show_result(transcript, copy=copy, refresh=refresh)
        return True

    @staticmethod
    def _set_readonly_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _insert_editor_newline(self, _event: Any) -> str:
        self.editor.insert("insert", "\n")
        return "break"

    def _toggle_editor_tag(self, tag: str) -> None:
        try:
            start = self.editor.index("sel.first")
            end = self.editor.index("sel.last")
        except tk.TclError:
            self.status.set(self._t("select_formatting_text"))
            return
        covers_selection = any(
            self.editor.compare(range_start, "<=", start)
            and self.editor.compare(range_end, ">=", end)
            for range_start, range_end in zip(
                self.editor.tag_ranges(tag)[::2], self.editor.tag_ranges(tag)[1::2], strict=True
            )
        )
        if covers_selection:
            self.editor.tag_remove(tag, start, end)
        else:
            self.editor.tag_add(tag, start, end)

    def _editor_formatting(self) -> dict[str, list[tuple[str, str]]]:
        return {
            tag: [
                (str(start), str(end))
                for start, end in zip(
                    self.editor.tag_ranges(tag)[::2],
                    self.editor.tag_ranges(tag)[1::2],
                    strict=True,
                )
            ]
            for tag in ("bold", "italic")
        }

    def _apply_editor_formatting(self, formatting: Any) -> None:
        for tag in ("bold", "italic"):
            self.editor.tag_remove(tag, "1.0", "end")
            ranges = formatting.get(tag, []) if isinstance(formatting, dict) else []
            for item in ranges:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                try:
                    self.editor.tag_add(tag, str(item[0]), str(item[1]))
                except tk.TclError:
                    continue

    def _editor_text(self) -> str:
        return self.editor.get("1.0", "end-1c")

    @staticmethod
    def _editor_index(offset: int) -> str:
        return f"1.0+{offset}c"

    def _editor_cursor_offset(self) -> int:
        return len(self.editor.get("1.0", "insert"))

    def _rewrite_editor(self, text: str) -> bool:
        """Replace the whole editor content, keeping the formatting ranges."""

        if text == self._editor_text():
            return False
        formatting = self._editor_formatting()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self._apply_editor_formatting(formatting)
        return True

    def _toggle_find_panel(self) -> None:
        if self.find_panel_visible:
            self._close_find_panel()
            return
        self.find_panel.pack(side="bottom", fill="x")
        self.find_panel_visible = True

    def _close_find_panel(self) -> None:
        self.find_panel.pack_forget()
        self.find_panel_visible = False
        self.editor.tag_remove(EDITOR_FIND_TAG, "1.0", "end")
        self.editor_find_count_var.set("")

    def _find_in_editor(self) -> tuple[TextMatch, ...]:
        query = self.editor_find_var.get()
        matches = find_matches(
            self._editor_text(),
            query,
            case_sensitive=bool(self.editor_find_case_var.get()),
            whole_word=bool(self.editor_find_word_var.get()),
        )
        self.editor.tag_remove(EDITOR_FIND_TAG, "1.0", "end")
        for match in matches:
            self.editor.tag_add(
                EDITOR_FIND_TAG,
                self._editor_index(match.start),
                self._editor_index(match.end),
            )
        self.editor_find_count_var.set(
            "" if not query.strip() else self._t("editor_find_count", count=len(matches))
        )
        return matches

    def _replace_editor_span(self, match: TextMatch, replacement: str) -> None:
        self.editor.delete(
            self._editor_index(match.start), self._editor_index(match.end)
        )
        if replacement:
            self.editor.insert(self._editor_index(match.start), replacement)

    def _replace_one_in_editor(self) -> bool:
        matches = self._find_in_editor()
        if not matches:
            return False
        cursor = self._editor_cursor_offset()
        target = next((match for match in matches if match.start >= cursor), matches[0])
        replacement = self.editor_replace_var.get()
        self._replace_editor_span(target, replacement)
        self.editor.mark_set("insert", self._editor_index(target.start + len(replacement)))
        self._find_in_editor()
        return True

    def _replace_all_in_editor(self) -> int:
        matches = self._find_in_editor()
        if not matches:
            return 0
        replacement = self.editor_replace_var.get()
        for match in reversed(matches):
            self._replace_editor_span(match, replacement)
        self._find_in_editor()
        self.status.set(self._t("editor_find_replaced", count=len(matches)))
        return len(matches)

    def _add_selection_to_dictionary(self) -> None:
        try:
            selection = self.editor.get("sel.first", "sel.last")
        except tk.TclError:
            selection = ""
        source = selection.strip()
        if not source:
            self.status.set(self._t("editor_add_rule_no_selection"))
            return
        if self.dictionary_read_only:
            self.status.set(self._t("editor_add_rule_read_only"))
            return
        if self._dictionary_dirty:
            self.status.set(self._t("editor_add_rule_unsaved"))
            return
        answer = simpledialog.askstring(
            self._t("editor_add_rule_title"),
            self._t("editor_add_rule_prompt", source=source),
            initialvalue=source,
            parent=self,
        )
        if answer is None:
            return
        target = answer.strip()
        if not target:
            self.status.set(self._t("editor_add_rule_empty"))
            return
        rule = DictionaryRule(
            source=source,
            target=target,
            case_sensitive=False,
            whole_word=True,
            # The quick-add flow never exposes the hint checkbox, so it must
            # not silently grow the recognition-hint payload; the Dictionary
            # page is where a rule is promoted to a hint deliberately.
            use_as_hint=False,
        )
        self.dictionary.rules.append(rule)
        if not self._save_dictionary():
            if self.dictionary.rules and self.dictionary.rules[-1] is rule:
                del self.dictionary.rules[-1]
            return
        self._rewrite_editor(TerminologyDictionary([rule]).apply(self._editor_text()))
        self.status.set(self._t("editor_add_rule_saved", source=source, target=target))

    def _editor_filler_language(self) -> str:
        language = self.current.language if self.current else ""
        if not language or language == "auto":
            language = getattr(self.settings, "language", "")
        return "" if language == "auto" else language

    def _collect_filler_matches(self) -> tuple[FillerMatch, ...]:
        language = self._editor_filler_language()
        if not language:
            return ()
        return find_filler_matches(self._editor_text(), language)

    @staticmethod
    def _filler_context(text: str, match: FillerMatch) -> str:
        before = text[max(0, match.start - FILLER_CONTEXT_WIDTH) : match.start]
        after = text[match.end : match.end + FILLER_CONTEXT_WIDTH]
        body = f"…{before}[{text[match.start : match.end]}]{after}…"
        return body.replace("\n", " ")

    def _apply_filler_removal(
        self,
        matches: Sequence[FillerMatch],
        selected: Sequence[bool],
        *,
        snapshot: tuple[str | None, str] | None = None,
    ) -> None:
        if snapshot is not None:
            current_id = self.current.id if self.current else None
            if (current_id, self._editor_text()) != snapshot:
                self.status.set(self._t("editor_filler_stale"))
                return
        chosen = [
            match for match, keep in zip(matches, selected, strict=True) if keep
        ]
        if not chosen:
            return
        if self._rewrite_editor(remove_matches(self._editor_text(), chosen)):
            self.status.set(self._t("editor_filler_removed", count=len(chosen)))

    def _open_filler_dialog(self) -> None:
        matches = self._collect_filler_matches()
        if not matches:
            self.status.set(self._t("editor_filler_none"))
            return
        text = self._editor_text()
        snapshot = (self.current.id if self.current else None, text)
        window = tk.Toplevel(self)
        window.title(self._t("editor_filler_title"))
        window.transient(self)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        variables: list[tk.BooleanVar] = []
        for row, match in enumerate(matches):
            variable = tk.BooleanVar(value=True)
            variables.append(variable)
            ttk.Checkbutton(
                body, text=self._filler_context(text, match), variable=variable
            ).grid(row=row, column=0, sticky="w", pady=(0, 2))
        actions = ttk.Frame(body)
        actions.grid(row=len(matches), column=0, sticky="e", pady=(12, 0))

        def apply_selected() -> None:
            flags = [bool(variable.get()) for variable in variables]
            window.destroy()
            self._apply_filler_removal(matches, flags, snapshot=snapshot)

        ttk.Button(
            actions, text=self._t("editor_filler_apply"), command=apply_selected
        ).pack(side="left")
        ttk.Button(
            actions, text=self._t("editor_filler_cancel"), command=window.destroy
        ).pack(side="left", padx=(6, 0))
        window.grab_set()
        window.wait_window()

    def _toggle_confidence_panel(self) -> None:
        if self.confidence_panel_visible:
            self._close_confidence_panel()
            return
        self.confidence_panel.pack(side="bottom", fill="x")
        self.confidence_panel_visible = True
        self._refresh_confidence_panel()

    def _close_confidence_panel(self) -> None:
        self.confidence_panel.pack_forget()
        self.confidence_panel_visible = False
        self.editor.tag_remove(EDITOR_CONFIDENCE_TAG, "1.0", "end")
        self.confidence_count_var.set("")

    def _confidence_threshold(self) -> float | None:
        try:
            value = float(str(self.confidence_threshold_var.get()).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None

    def _confidence_segments(self) -> Sequence[Segment]:
        return self.current.segments if self.current else ()

    def _confidence_row(self, entry: ConfidenceEntry, segments: Sequence[Segment]) -> str:
        text = editable_text(segments[entry.index]) if entry.index < len(segments) else ""
        snippet = text.replace("\n", " ").strip()
        if len(snippet) > CONFIDENCE_SNIPPET_WIDTH:
            snippet = snippet[:CONFIDENCE_SNIPPET_WIDTH].rstrip() + "…"
        score = (
            self._t("editor_confidence_no_score")
            if entry.confidence is None
            else f"{entry.confidence:.2f}"
        )
        return f"{score} · {snippet}"

    def _refresh_confidence_panel(self) -> None:
        self._confidence_entries = []
        self.confidence_list.delete(0, "end")
        self.editor.tag_remove(EDITOR_CONFIDENCE_TAG, "1.0", "end")
        threshold = self._confidence_threshold()
        if threshold is None:
            self.status.set(self._t("editor_confidence_threshold_invalid"))
            return
        segments = self._confidence_segments()
        entries = confidence_entries(segments, threshold)
        self._confidence_entries = entries
        for entry in entries:
            self.confidence_list.insert("end", self._confidence_row(entry, segments))
        self.confidence_count_var.set(
            self._t("editor_confidence_empty")
            if not entries
            else self._t("editor_confidence_count", count=len(entries))
        )

    def _selected_confidence_entry(self) -> ConfidenceEntry | None:
        selection = self.confidence_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if index >= len(self._confidence_entries):
            return None
        return self._confidence_entries[index]

    def _select_confidence_entry(self, _event: Any = None) -> None:
        entry = self._selected_confidence_entry()
        if entry is not None:
            self._focus_segment(entry.index)

    def _focus_segment(self, segment_index: int) -> None:
        spans = segment_spans(self._editor_text(), self._confidence_segments())
        span = spans[segment_index] if segment_index < len(spans) else None
        if span is None:
            self.status.set(self._t("editor_confidence_focus_missing"))
            return
        start = self._editor_index(span[0])
        self.editor.tag_remove(EDITOR_CONFIDENCE_TAG, "1.0", "end")
        self.editor.tag_add(EDITOR_CONFIDENCE_TAG, start, self._editor_index(span[1]))
        self.editor.mark_set("insert", start)
        self.editor.see(start)
        self.editor.focus_set()

    def _play_selected_segment(self) -> None:
        entry = self._selected_confidence_entry()
        if entry is None:
            self.status.set(self._t("editor_confidence_play_no_selection"))
            return
        self._segment_play_requested(entry.index)

    def _segment_play_requested(self, segment_index: int) -> None:
        """Play the retained managed audio from the segment's own start."""

        transcript = self.current
        if transcript is None or not 0 <= segment_index < len(transcript.segments):
            self.status.set(self._t("playback_no_safe_audio"))
            return
        start = float(transcript.segments[segment_index].start)
        self._start_playback(max(0.0, start))

    def _playable_source_path(self) -> Path | None:
        """Resolve the retained managed copy; external originals are never used."""

        transcript = self.current
        if transcript is None or not transcript.audio_retained or not transcript.source_path:
            return None
        try:
            target = Path(transcript.source_path).expanduser().resolve()
            target.relative_to(self.store.sources.resolve())
        except (OSError, ValueError):
            return None
        return target if target.is_file() else None

    def _selected_playback_speed(self) -> float:
        if "playback_speed_var" not in self.__dict__:
            return 1.0
        value = self.playback_speed_var.get().strip().rstrip("×xX")
        try:
            speed = float(value.replace(",", "."))
        except ValueError:
            return 1.0
        return speed if speed in SUPPORTED_SPEEDS else 1.0

    def _start_playback(self, start: float) -> None:
        player = self.__dict__.get("player")
        if player is None:
            return
        media = self._playable_source_path()
        if media is None:
            self.status.set(self._t("playback_no_safe_audio"))
            return
        self._playback_error_reported = None
        try:
            player.play(media, start=start, speed=self._selected_playback_speed())
        except (RuntimeError, ValueError) as exc:
            self.status.set(self._t("playback_error", error=exc))
            return
        self._sync_playback_toggle()
        self._arm_playback_ticker()

    def _toggle_playback(self) -> None:
        player = self.__dict__.get("player")
        if player is None:
            return
        if player.state == "idle":
            self._start_playback(0.0)
            return
        player.toggle_pause()
        self._sync_playback_toggle()

    def _stop_playback(self) -> None:
        self._cancel_playback_ticker()
        player = self.__dict__.get("player")
        if player is None:
            return
        stopped = False
        try:
            stopped = bool(player.stop())
        except Exception:
            stopped = False
        if not stopped:
            try:
                self.status.set(self._t("playback_stop_timeout"))
            except Exception:
                pass
        self._refresh_playback_label()
        self._sync_playback_toggle()

    def _seek_playback(self, delta: float) -> None:
        player = self.__dict__.get("player")
        if player is None or player.state == "idle":
            return
        try:
            player.seek_by(float(delta))
        except (RuntimeError, ValueError) as exc:
            self.status.set(self._t("playback_error", error=exc))
            return
        self._refresh_playback_label()

    def _press_playback_seek(self, _event: Any = None) -> None:
        self._playback_seek_dragging = True

    def _release_playback_seek(self, _event: Any = None) -> None:
        self._playback_seek_dragging = False
        player = self.__dict__.get("player")
        if player is None or player.state == "idle":
            return
        duration = player.duration
        if duration is None or duration <= 0:
            return
        fraction = max(0.0, min(1000.0, self.playback_seek_var.get())) / 1000.0
        try:
            player.seek_to(fraction * duration)
        except (RuntimeError, ValueError) as exc:
            self.status.set(self._t("playback_error", error=exc))
            return
        self._refresh_playback_label()

    def _set_playback_speed(self, _event: Any = None) -> None:
        player = self.__dict__.get("player")
        if player is None or player.state == "idle":
            return
        try:
            player.set_speed(self._selected_playback_speed())
        except (RuntimeError, ValueError) as exc:
            self.status.set(self._t("playback_error", error=exc))

    def _arm_playback_ticker(self) -> None:
        self._cancel_playback_ticker()
        self._playback_ticker = self.after(250, self._playback_tick)

    def _cancel_playback_ticker(self) -> None:
        ticker = self.__dict__.get("_playback_ticker")
        self._playback_ticker = None
        if ticker is not None:
            try:
                self.after_cancel(ticker)
            except Exception:
                pass

    def _playback_tick(self) -> None:
        self._playback_ticker = None
        self._refresh_playback_label()
        player = self.__dict__.get("player")
        if player is None:
            return
        if player.state == "idle":
            self._sync_playback_toggle()
            error = player.last_error
            if error and error != self._playback_error_reported:
                self._playback_error_reported = error
                self.status.set(self._t("playback_error", error=error))
            return
        self._playback_ticker = self.after(250, self._playback_tick)

    @staticmethod
    def _format_playback_seconds(value: float) -> str:
        total = max(0, int(value))
        return f"{total // 60}:{total % 60:02d}"

    def _refresh_playback_label(self) -> None:
        if "playback_position_var" not in self.__dict__:
            return
        player = self.__dict__.get("player")
        if player is None:
            return
        duration = player.duration
        rendered = "—" if duration is None else self._format_playback_seconds(duration)
        self.playback_position_var.set(
            f"{self._format_playback_seconds(player.position)} / {rendered}"
        )
        self._sync_playback_seek_value(player, duration)

    def _sync_playback_seek_value(self, player: Any, duration: float | None) -> None:
        if "playback_seek_var" not in self.__dict__:
            return
        if self.__dict__.get("_playback_seek_dragging"):
            return
        if duration is None or duration <= 0:
            self.playback_seek_var.set(0.0)
            return
        fraction = max(0.0, min(1.0, player.position / duration))
        self.playback_seek_var.set(fraction * 1000.0)

    def _sync_playback_toggle(self) -> None:
        if "playback_toggle_button" not in self.__dict__:
            return
        player = self.__dict__.get("player")
        key = "playback_pause" if player is not None and player.state == "playing" else (
            "playback_play"
        )
        self.playback_toggle_button.configure(text=self._t(key))
        if "playback_seek_scale" in self.__dict__:
            playable = self._playable_source_path() is not None
            self.playback_seek_scale.configure(state="normal" if playable else "disabled")

    def _shutdown_playback(self, residues: set[str]) -> None:
        """Stop the playback worker at exit; record it when it will not join."""

        self._cancel_playback_ticker()
        player = self.__dict__.get("player")
        if player is None:
            return
        stopped = False
        try:
            stopped = bool(player.stop())
        except Exception:
            stopped = False
        if not stopped:
            residues.add("playback-worker")

    def _refresh_playback_ui_text(self) -> None:
        for button, key in self._playback_button_keys.items():
            button.configure(text=self._t(key))
        self.playback_speed_label.configure(text=self._t("playback_speed"))
        if "playback_seek_label" in self.__dict__:
            self.playback_seek_label.configure(text=self._t("playback_seek"))
        self._sync_playback_toggle()

    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _copy_current(self) -> None:
        if not self.current:
            messagebox.showinfo(self._t("copy_text"), self._t("select_transcript"))
            return
        self._copy_to_clipboard(self.editor.get("1.0", "end-1c"))
        self.status.set(self._t("copied"))

    def _refresh_dashboard(self) -> None:
        if "dashboard_kpi_values" not in self.__dict__:
            return
        statistics = self.store.statistics()
        values = {
            "total": str(statistics.total_records),
            "completed": str(statistics.completed_records),
            "failed": str(statistics.failed_records),
            "words": str(statistics.word_count_total),
            "duration": format_audio_duration(statistics.audio_seconds_total),
            "speed": format_speed_multiplier(statistics.speed_multiplier),
            "retained": str(statistics.retained_audio_records),
            "last_7_days": str(statistics.records_last_7_days),
            "last_30_days": str(statistics.records_last_30_days),
        }
        for key, label in self.dashboard_kpi_values.items():
            label.configure(text=values[key])
        for key, counts in (
            ("languages", statistics.language_counts),
            ("engines", statistics.engine_counts),
            ("models", statistics.model_counts),
        ):
            self.dashboard_top_values[key].configure(text=format_count_ranking(counts))
        if statistics.invalid_records > 0:
            self.dashboard_invalid_label.configure(
                text=self._t("dashboard_invalid_records", count=statistics.invalid_records)
            )
            self.dashboard_invalid_frame.grid()
        else:
            self.dashboard_invalid_frame.grid_remove()
        self._dashboard_recent_items = self.store.list(limit=DASHBOARD_RECENT_LIMIT)
        for index, button in enumerate(self.dashboard_recent_buttons):
            if index < len(self._dashboard_recent_items):
                button.configure(text=self._history_label(self._dashboard_recent_items[index]))
                button.grid()
            else:
                button.grid_remove()
        if self._dashboard_recent_items:
            self.dashboard_recent_empty_label.grid_remove()
        else:
            self.dashboard_recent_empty_label.grid()
        if "dashboard_activity_canvas" in self.__dict__:
            self._dashboard_activity_data = self.store.daily_activity(
                days=DASHBOARD_ACTIVITY_DAYS
            )
            self._dashboard_language_data = statistics.language_counts
            self._dashboard_engine_data = statistics.engine_counts
            self._redraw_dashboard_activity_chart()
            self._redraw_dashboard_distribution_chart()

    def _dashboard_top_with_other(
        self, counts: tuple[tuple[str, int], ...], *, top: int = 5
    ) -> tuple[tuple[str, int], ...]:
        if len(counts) <= top:
            return counts
        head = counts[:top]
        other_total = sum(count for _name, count in counts[top:])
        return (*head, (self._t("dashboard_other"), other_total))

    @staticmethod
    def _dashboard_weekday_label(iso_day: str) -> str:
        try:
            return date.fromisoformat(iso_day).strftime("%a")
        except ValueError:
            return iso_day[-2:]

    def _draw_activity_chart(
        self,
        canvas: Any,
        data: tuple[tuple[str, int], ...],
        *,
        width: int,
        height: int,
    ) -> None:
        """Draw the 14-day activity bar chart. Pure with respect to its arguments."""

        theme = VOICE_STUDIO_THEME
        canvas.delete("all")
        if width <= 0 or height <= 0:
            return
        if not data or all(count == 0 for _day, count in data):
            canvas.create_text(
                width / 2,
                height / 2,
                text=self._t("dashboard_no_activity"),
                fill=theme.muted_ink,
                font=(theme.ui_font, 9),
            )
            return
        top_margin, bottom_margin = 16, 16
        baseline = height - bottom_margin
        plot_height = max(1.0, baseline - top_margin)
        peak = max(count for _day, count in data)
        slot_width = width / len(data)
        bar_width = max(2.0, slot_width * 0.6)
        for index, (iso_day, count) in enumerate(data):
            x_center = slot_width * index + slot_width / 2
            bar_height = 0.0 if peak <= 0 else plot_height * (count / peak)
            y_top = baseline - bar_height
            canvas.create_rectangle(
                x_center - bar_width / 2,
                y_top,
                x_center + bar_width / 2,
                baseline,
                fill=theme.accent,
                outline="",
            )
            if count > 0:
                canvas.create_text(
                    x_center,
                    max(0.0, y_top - 8),
                    text=str(count),
                    fill=theme.ink,
                    font=(theme.ui_font, 8),
                )
            if index % 2 == 0:
                canvas.create_text(
                    x_center,
                    height - bottom_margin / 2,
                    text=self._dashboard_weekday_label(iso_day),
                    fill=theme.muted_ink,
                    font=(theme.ui_font, 7),
                )

    def _draw_distribution_chart(
        self,
        canvas: Any,
        language_counts: tuple[tuple[str, int], ...],
        engine_counts: tuple[tuple[str, int], ...],
        *,
        width: int,
        height: int,
    ) -> None:
        """Draw the language/engine horizontal distribution chart onto ``canvas``."""

        theme = VOICE_STUDIO_THEME
        canvas.delete("all")
        if width <= 0 or height <= 0:
            return
        rows = [
            ("lang", name, count)
            for name, count in self._dashboard_top_with_other(language_counts)
        ] + [
            ("engine", name, count)
            for name, count in self._dashboard_top_with_other(engine_counts)
        ]
        if not rows or all(count == 0 for _kind, _name, count in rows):
            canvas.create_text(
                width / 2,
                height / 2,
                text=self._t("dashboard_no_activity"),
                fill=theme.muted_ink,
                font=(theme.ui_font, 9),
            )
            return
        peak = max((count for _kind, _name, count in rows), default=0) or 1
        row_height = height / len(rows)
        label_width = width * 0.32
        bar_area = max(1.0, width - label_width - 40)
        for index, (kind, name, count) in enumerate(rows):
            y_center = row_height * index + row_height / 2
            canvas.create_text(
                4,
                y_center,
                text=name,
                fill=theme.ink,
                font=(theme.ui_font, 8),
                anchor="w",
            )
            bar_length = bar_area * (count / peak)
            canvas.create_rectangle(
                label_width,
                y_center - row_height * 0.28,
                label_width + bar_length,
                y_center + row_height * 0.28,
                fill=theme.accent if kind == "lang" else theme.primary,
                outline="",
            )
            canvas.create_text(
                label_width + bar_length + 4,
                y_center,
                text=str(count),
                fill=theme.muted_ink,
                font=(theme.ui_font, 8),
                anchor="w",
            )

    def _redraw_dashboard_activity_chart(self) -> None:
        canvas = self.__dict__.get("dashboard_activity_canvas")
        if canvas is None:
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            return
        self._draw_activity_chart(
            canvas,
            self.__dict__.get("_dashboard_activity_data", ()),
            width=width,
            height=height,
        )

    def _redraw_dashboard_distribution_chart(self) -> None:
        canvas = self.__dict__.get("dashboard_distribution_canvas")
        if canvas is None:
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            return
        self._draw_distribution_chart(
            canvas,
            self.__dict__.get("_dashboard_language_data", ()),
            self.__dict__.get("_dashboard_engine_data", ()),
            width=width,
            height=height,
        )

    def _open_dashboard_recent(self, index: int) -> None:
        items = self._dashboard_recent_items
        if index >= len(items):
            return
        if self._try_show_result(items[index], copy=False, refresh=False):
            self._show_page("studio")

    @staticmethod
    def _history_label(transcript: Transcript) -> str:
        return f"{transcript.created_at[:16]}  [{transcript.language}]  {transcript.source_name}"

    def _history_filter_choices(self, name: str) -> tuple[tuple[str, object], ...]:
        head: tuple[tuple[str, object], ...] = ((self._t("history_filter_all"), None),)
        if name == "language":
            return head + tuple((value, value) for value in SUPPORTED_LANGUAGES)
        if name == "engine":
            return head + tuple((value, value) for value in SUPPORTED_ENGINES)
        if name == "status":
            return head + (
                (self._t("history_filter_completed"), "completed"),
                (self._t("history_filter_failed"), "failed"),
            )
        return head + (
            (self._t("history_filter_yes"), True),
            (self._t("history_filter_no"), False),
        )

    def _apply_history_filter_choices(self, name: str, *, reset: bool = False) -> None:
        current = None if reset else self._history_filter_value(name)
        choices = self._history_filter_choices(name)
        self._history_filter_labels[name] = {label: value for label, value in choices}
        self._history_filter_combos[name].configure(values=[label for label, _ in choices])
        selected = next(
            (label for label, value in choices if value == current), choices[0][0]
        )
        self._history_filter_vars[name].set(selected)

    def _history_filter_value(self, name: str) -> object:
        labels = self._history_filter_labels.get(name, {})
        return labels.get(self._history_filter_vars[name].get())

    def _build_history_filter(self) -> HistoryFilter | None:
        text = self.search_var.get().strip() if "search_var" in self.__dict__ else ""
        if "_history_filter_vars" not in self.__dict__:
            return HistoryFilter(text=text)
        bounds: list[datetime | None] = []
        for variable, end_of_day in (
            (self.history_from_var, False),
            (self.history_to_var, True),
        ):
            raw = variable.get().strip()
            if not raw:
                bounds.append(None)
                continue
            bound = history_day_bounds(raw, end_of_day=end_of_day)
            if bound is None:
                self.status.set(self._t("history_filter_date_invalid"))
                return None
            bounds.append(bound)
        language = self._history_filter_value("language")
        engine = self._history_filter_value("engine")
        status = self._history_filter_value("status")
        retained = self._history_filter_value("retained")
        return HistoryFilter(
            text=text,
            created_from=bounds[0],
            created_to=bounds[1],
            language=language if isinstance(language, str) else None,
            engine=engine if isinstance(engine, str) else None,
            model=self.history_model_var.get().strip() or None,
            status=status if isinstance(status, str) else None,
            retained_audio=retained if isinstance(retained, bool) else None,
        )

    def _reset_history_filters(self) -> None:
        if "search_var" in self.__dict__:
            self.search_var.set("")
        self.history_model_var.set("")
        self.history_from_var.set("")
        self.history_to_var.set("")
        for name in self._history_filter_vars:
            self._apply_history_filter_choices(name, reset=True)
        self._refresh_history()

    def _refresh_history(self, *, select_id: str | None = None) -> None:
        history_filter = self._build_history_filter()
        if history_filter is None:
            return
        self._history_items = self.store.list(limit=250, filters=history_filter)
        self.history.delete(0, "end")
        selected_index: int | None = None
        for index, item in enumerate(self._history_items):
            self.history.insert("end", self._history_label(item))
            if select_id and item.id == select_id:
                selected_index = index
        if selected_index is not None:
            self.history.selection_clear(0, "end")
            self.history.selection_set(selected_index)
            self.history.see(selected_index)

    def _select_history(self, _event: Any = None) -> None:
        selection = self.history.curselection()
        if selection:
            selected = self._history_items[selection[0]]
            if self._try_show_result(selected, copy=False, refresh=False):
                if "_page_frames" in self.__dict__:
                    self._show_page("studio")
                return
            self.history.selection_clear(0, "end")
            if self.current:
                for index, item in enumerate(self._history_items):
                    if item.id == self.current.id:
                        self.history.selection_set(index)
                        self.history.see(index)
                        break

    def _selected_history_item(self) -> Transcript | None:
        selection = self.history.curselection()
        return self._history_items[selection[0]] if selection else None

    def _rename_selected_history(self) -> None:
        transcript = self._selected_history_item()
        if transcript is None:
            messagebox.showinfo(self._t("history"), self._t("select_history"))
            return
        if not self._confirm_editor_transition():
            return
        name = simpledialog.askstring(
            self._t("rename_title"),
            self._t("rename_prompt"),
            initialvalue=transcript.source_name,
            parent=self,
        )
        if name is None:
            return
        try:
            renamed = self.store.rename_source_name(transcript.id, name)
        except Exception as exc:
            messagebox.showerror(self._t("rename_error"), str(exc), parent=self)
            return
        self._show_result(renamed, refresh=True)
        self.status.set(self._t("rename_complete"))

    def _delete_selected_history(self) -> None:
        transcript = self._selected_history_item()
        if transcript is None:
            messagebox.showinfo(self._t("history"), self._t("select_history"))
            return
        if not self._confirm_editor_transition():
            return
        if not messagebox.askyesno(
            self._t("delete_transcript_title"),
            self._t("delete_transcript_prompt"),
            parent=self,
        ):
            return
        delete_audio = False
        if transcript.audio_retained:
            choice = messagebox.askyesnocancel(
                self._t("managed_audio_title"),
                self._t("managed_audio_prompt"),
                parent=self,
            )
            if choice is None:
                return
            delete_audio = bool(choice)
        self.store.delete(transcript.id, delete_audio=delete_audio)
        if self.current and self.current.id == transcript.id:
            self._stop_playback()
            self._clear_current_transcript_view()
        self._refresh_history()
        self._refresh_dashboard()
        self.status.set(self._t("delete_complete"))

    def _editor_is_dirty(self) -> bool:
        if not self.current:
            return snapshot_editor(
                self.editor.get("1.0", "end-1c"), self._editor_formatting()
            ) != snapshot_editor("", {})
        current = snapshot_editor(self.editor.get("1.0", "end-1c"), self._editor_formatting())
        return current != self._editor_baseline

    def _confirm_editor_transition(self) -> bool:
        if not self._editor_is_dirty():
            return True
        choice = messagebox.askyesnocancel(
            self._t("unsaved_title"),
            self._t("unsaved_prompt"),
            parent=self,
        )
        if choice is True:
            return self._save_edits()
        if choice is False:
            if self.current is not None:
                self._show_result(self.current, refresh=False)
            else:
                self._clear_current_transcript_view()
            return True
        return False

    def _mirror_transcript_quietly(self, transcript: Transcript) -> None:
        """Mirror the current stored transcript into the sync folder, if enabled.

        A no-op when sync is disabled or no folder is configured. Any failure
        (an invalid or removed folder, a filesystem error, ...) is reported
        on the status bar only — it must never raise into the caller and
        never block or undo whatever save just triggered it.
        """

        try:
            if not self.settings.sync_enabled or not self.settings.sync_folder.strip():
                return
            mirror_transcript(
                transcript,
                Path(self.settings.sync_folder),
                include_audio=self.settings.sync_include_audio,
                sources_root=self.store.sources,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced on the status bar only
            self.status.set(self._t("sync_failed", error=str(exc)))

    def _sync_all_now(self) -> None:
        """Mirror every stored transcript on the maintenance worker."""

        if not self.settings.sync_enabled or not self.settings.sync_folder.strip():
            messagebox.showerror(
                self._t("sync_section"),
                self._t("sync_invalid_folder", error=self._t("sync_folder")),
                parent=self,
            )
            return
        try:
            root = validate_sync_root(Path(self.settings.sync_folder), data_root=data_dir())
        except SyncFolderError as exc:
            messagebox.showerror(self._t("sync_section"), str(exc), parent=self)
            return
        include_audio = self.settings.sync_include_audio
        sources_root = self.store.sources
        self._set_busy(True)
        self.status.set(self._t("sync_running"))

        def work() -> None:
            try:
                transcripts = self.store.list(limit=1_000_000)
                summary = mirror_all(
                    transcripts, root, include_audio=include_audio, sources_root=sources_root
                )
                self._post_event("sync_done", summary)
            except Exception as exc:  # noqa: BLE001 - reported through the event, never raised
                self._post_event("sync_done", exc)

        self._start_worker("sync", work)

    def _save_edits(self) -> bool:
        if not self.current:
            if self._editor_is_dirty():
                messagebox.showerror(
                    self._t("save_edits_error"),
                    self._t("save_edits_no_transcript"),
                    parent=self,
                )
                return False
            return True
        try:
            self.current = self.store.update_editor_state(
                self.current.id,
                self.editor.get("1.0", "end-1c"),
                self._editor_formatting(),
            )
        except Exception as exc:
            messagebox.showerror(self._t("save_edits_error"), str(exc), parent=self)
            return False
        self._editor_baseline = snapshot_editor(
            self.editor.get("1.0", "end-1c"), self._editor_formatting()
        )
        self.status.set(self._t("edits_saved"))
        self._mirror_transcript_quietly(self.current)
        return True

    def _cleanup_result_is_current(self, transcript: Transcript) -> bool:
        if self.current is None or self.current.id != transcript.id:
            return False
        expected = getattr(self, "_cleanup_snapshot", None)
        expected_id = getattr(self, "_cleanup_transcript_id", None)
        if expected is None:
            return False
        if expected_id is not None and expected_id != transcript.id:
            return False
        current = snapshot_editor(self.editor.get("1.0", "end-1c"), self._editor_formatting())
        return current == expected

    def _ai_cleanup(self) -> None:
        if not self.current:
            messagebox.showinfo(self._t("cleanup"), self._t("select_transcript"), parent=self)
            return
        provider = self.settings.cleanup_provider
        if provider == "openai":
            if self.settings.offline_only:
                messagebox.showerror(
                    self._t("cleanup_error"),
                    self._t("offline_blocks_openai"),
                    parent=self,
                )
                return
            if not messagebox.askyesno(
                self._t("cloud_text_title"),
                self._t("cloud_text_prompt"),
                parent=self,
            ):
                return
            model = self.settings.openai_cleanup_model
        else:
            try:
                models = list_ollama_models()
            except Exception as exc:
                messagebox.showerror("Ollama", str(exc), parent=self)
                return
            model = self.settings.ollama_model or (models[0] if models else "")
            if not model:
                messagebox.showerror("Ollama", self._t("ollama_missing"), parent=self)
                return
        if not self._save_edits():
            return
        transcript = self.current
        self._cleanup_provider = provider
        self._cleanup_model = model
        self._cleanup_transcript_id = transcript.id
        self._cleanup_snapshot = snapshot_editor(
            self.editor.get("1.0", "end-1c"), self._editor_formatting()
        )
        self._set_busy(True)

        def work() -> None:
            try:
                proposal = propose_cleanup(
                    transcript,
                    provider=provider,
                    model=model,
                )
                self._post_event("cleanup_proposal", (transcript, proposal))
            except Exception as exc:
                self._post_event("cleanup_error", exc)

        self._start_worker("ai-cleanup", work)

    def _undo_ai_cleanup(self) -> None:
        if not self.current:
            return
        if not self._confirm_editor_transition():
            return
        try:
            updated = self.store.undo_last_ai_cleanup(self.current.id)
            self._show_result(updated, refresh=True)
            self.status.set(self._t("undo_cleanup_complete"))
        except Exception as exc:
            messagebox.showerror(self._t("cleanup_error"), str(exc), parent=self)

    def _export(self, fmt: str) -> None:
        if not self.current:
            messagebox.showinfo(self._t("export"), self._t("select_transcript"))
            return
        if not self._save_edits():
            return
        destination = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            initialfile=f"{Path(self.current.source_name).stem}.{fmt}",
        )
        if destination:
            export_transcript(self.current, fmt, Path(destination))
            self.status.set(self._t("exported", name=Path(destination).name))

    def _localized_help_topics(self, help_root: Path) -> tuple[HelpTopic, ...]:
        return load_help_topics(help_root, self.settings.ui_language)

    def _reset_help_page(self) -> None:
        self._help_page_built = False
        self._help_images = []
        for child in self.help_page.winfo_children():
            child.destroy()

    def _build_help_page(self) -> None:
        if self._help_page_built:
            return
        self._reset_help_page()
        try:
            help_root = resolve_help_root()
            topics = self._localized_help_topics(help_root)
        except (OSError, ValueError) as exc:
            message = self._t("help_unavailable", error=exc)
            self.status.set(message)
            ttk.Label(
                self.help_page,
                text=message,
                style="CardMuted.TLabel",
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, sticky="w", padx=28, pady=28)
            return

        theme = VOICE_STUDIO_THEME
        header = ttk.Frame(self.help_page, padding=(28, 22, 28, 18), style="Canvas.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        self._help_title_label = ttk.Label(header, text=self._t("help_title"), style="Title.TLabel")
        self._help_title_label.pack(anchor="w")
        self._help_intro_label = ttk.Label(
            header, text=self._t("help_intro"), style="Subtitle.TLabel"
        )
        self._help_intro_label.pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(self.help_page, padding=(24, 20), style="Canvas.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        navigation = ttk.Frame(body, width=250, padding=14, style="CardBorder.TFrame")
        navigation.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        navigation.grid_propagate(False)
        navigation.grid_rowconfigure(3, weight=1)
        navigation.grid_columnconfigure(0, weight=1)
        self._help_search_label = ttk.Label(
            navigation, text=self._t("help_search"), style="CardMuted.TLabel"
        )
        self._help_search_label.grid(row=0, column=0, sticky="w")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(navigation, textvariable=search_var)
        search_entry.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        self._help_search_button = ttk.Button(navigation, text=self._t("help_search_action"))
        self._help_search_button.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        topic_list = tk.Listbox(
            navigation,
            activestyle="dotbox",
            background=theme.surface,
            foreground=theme.ink,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            highlightbackground=theme.border,
            highlightcolor=theme.accent,
            relief="flat",
            borderwidth=1,
            font=(theme.ui_font, 10),
            exportselection=False,
        )
        topic_list.grid(row=3, column=0, sticky="nsew")

        article_frame = ttk.Frame(body, padding=1, style="CardBorder.TFrame")
        article_frame.grid(row=0, column=1, sticky="nsew")
        article_frame.grid_rowconfigure(0, weight=1)
        article_frame.grid_columnconfigure(0, weight=1)
        article = tk.Text(
            article_frame,
            wrap="word",
            state="disabled",
            background=theme.surface,
            foreground=theme.ink,
            selectbackground=theme.selection,
            selectforeground=theme.ink,
            relief="flat",
            borderwidth=0,
            padx=24,
            pady=20,
            font=(theme.ui_font, 11),
        )
        article.grid(row=0, column=0, sticky="nsew")
        article_scroll = ttk.Scrollbar(article_frame, orient="vertical", command=article.yview)
        article_scroll.grid(row=0, column=1, sticky="ns")
        article.configure(yscrollcommand=article_scroll.set)
        article.tag_configure(
            "h1", font=(theme.ui_font, 22, "bold"), foreground=theme.primary, spacing3=12
        )
        article.tag_configure(
            "h2",
            font=(theme.ui_font, 16, "bold"),
            foreground=theme.ink,
            spacing1=16,
            spacing3=8,
        )
        article.tag_configure(
            "h3",
            font=(theme.ui_font, 12, "bold"),
            foreground=theme.ink,
            spacing1=12,
            spacing3=5,
        )
        article.tag_configure("body", spacing3=10)
        article.tag_configure("list", spacing3=5, lmargin1=18, lmargin2=32)
        article.tag_configure(
            "code",
            font=(theme.mono_font, 9),
            background=theme.surface_muted,
            foreground=theme.ink,
            lmargin1=12,
            lmargin2=12,
            rmargin=12,
            spacing1=6,
            spacing3=8,
        )
        article.tag_configure("table", font=(theme.mono_font, 9), spacing3=10)
        article.tag_configure("link", foreground=theme.accent_hover, underline=True, spacing3=8)
        article.tag_configure(
            "image_alt", foreground=theme.muted_ink, justify="center", spacing3=12
        )

        visible_topics: tuple[HelpTopic, ...] = topics

        def select_topic(target_topic: HelpTopic, target_anchor: str = "") -> None:
            self._help_images = []
            article.configure(state="normal")
            article.delete("1.0", "end")
            link_index = 0
            heading_positions: dict[str, str] = {}
            for block in parse_markdown(target_topic.markdown):
                if block.kind == "heading":
                    heading_positions.setdefault(help_anchor(block.text), article.index("end-1c"))
                    article.insert("end", block.text + "\n", f"h{min(block.level, 3)}")
                elif block.kind == "paragraph":
                    article.insert("end", block.text + "\n", "body")
                elif block.kind == "bullet":
                    article.insert("end", "• " + block.text + "\n", "list")
                elif block.kind == "numbered":
                    article.insert("end", block.text + "\n", "list")
                elif block.kind == "code":
                    article.insert("end", block.text + "\n", "code")
                elif block.kind == "table":
                    article.insert("end", block.text + "\n", "table")
                elif block.kind == "image" and block.target:
                    try:
                        image_path = resolve_help_asset(
                            help_root, target_topic.source_path, block.target
                        )
                        if not image_path.is_file():
                            raise FileNotFoundError(image_path)
                        help_image = tk.PhotoImage(master=article, file=str(image_path))
                        factor = max(1, (help_image.width() + 679) // 680)
                        if factor > 1:
                            help_image = help_image.subsample(factor, factor)
                        self._help_images.append(help_image)
                        article.image_create("end", image=help_image, pady=8)
                        article.insert("end", "\n" + block.text + "\n", "image_alt")
                    except (OSError, tk.TclError, ValueError):
                        article.insert(
                            "end",
                            self._t("help_image_unavailable", name=block.text) + "\n",
                            "image_alt",
                        )
                elif block.kind == "link" and block.target:
                    tag = f"help-link-{link_index}"
                    link_index += 1
                    article.insert("end", block.text + "\n", ("link", tag))

                    def follow_link(_event: Any, target: str = block.target) -> str:
                        filename, fragment = split_help_target(target)
                        destination = next(
                            (topic for topic in topics if topic.source_path.name == filename),
                            None,
                        )
                        if destination is not None:
                            search_var.set("")
                            populate_topics()
                            index = topics.index(destination)
                            topic_list.selection_clear(0, "end")
                            topic_list.selection_set(index)
                            topic_list.activate(index)
                            select_topic(destination, fragment)
                        return "break"

                    article.tag_bind(tag, "<Button-1>", follow_link)
                else:
                    article.insert("end", block.text + "\n", "body")
            article.configure(state="disabled")
            anchor_position = heading_positions.get(target_anchor)
            if anchor_position is not None:
                article.yview(anchor_position)
            else:
                article.yview_moveto(0.0)

        def on_topic_select(_event: Any = None) -> None:
            selection = topic_list.curselection()
            if selection and selection[0] < len(visible_topics):
                select_topic(visible_topics[selection[0]])

        def populate_topics(_event: Any = None) -> None:
            nonlocal visible_topics
            visible_topics = search_help_topics(topics, search_var.get())
            topic_list.delete(0, "end")
            for topic in visible_topics:
                topic_list.insert("end", topic.title)
            if visible_topics:
                topic_list.selection_set(0)
                topic_list.activate(0)
                select_topic(visible_topics[0])
            else:
                article.configure(state="normal")
                article.delete("1.0", "end")
                article.insert("end", self._t("help_no_results"), "h2")
                article.configure(state="disabled")

        topic_list.bind("<<ListboxSelect>>", on_topic_select)
        search_entry.bind("<Return>", populate_topics)
        self._help_search_button.configure(command=populate_topics)

        footer = ttk.Frame(self.help_page, padding=(24, 0, 24, 18), style="Canvas.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        self._help_close_button = ttk.Button(
            footer,
            text=self._t("help_close"),
            command=lambda: self._show_page("dashboard"),
        )
        self._help_close_button.pack(side="right")
        populate_topics()
        search_entry.focus_set()
        self._help_page_built = True

    def _leave_settings_page(self) -> None:
        """Release the settings page bindings before starting the native listener."""

        self._settings_ollama_combo = None
        self._settings_hardware_device_combo = None
        self._settings_hardware_compute_combo = None
        self._settings_info_var = None
        self._settings_ollama_status_var = None
        if self._settings_capture_binding is not None:
            self.unbind("<KeyPress>", self._settings_capture_binding)
            self._settings_capture_binding = None
        self._hotkey_restart_handle = self.after_idle(self._start_hotkey)

    def _refresh_after_settings_save(self, previous_ui_language: str) -> None:
        self.job_controller.close()
        if previous_ui_language != self.settings.ui_language:
            self._reset_help_page()
        self._refresh_ui_text()

    def _start_hardware_detection(self) -> None:
        """Run advisory local capability detection in the retained GUI worker."""

        info = self._settings_info_var
        if info is not None:
            info.set(self._t("hardware_detection_running"))

        def work() -> None:
            try:
                result = detect_hardware()
            except Exception as exc:
                result = HardwareDetectionResult("degraded", (), (), ("auto", "default"), str(exc))
            self._post_event("hardware_detection", result)

        try:
            self._start_worker("hardware-detection", work)
        except RuntimeError:
            if info is not None:
                info.set(self._t("hardware_detection_busy"))

    def _build_settings_page(self) -> None:
        handle = self.__dict__.get("_hotkey_restart_handle")
        if handle is not None:
            self.after_cancel(handle)
            self._hotkey_restart_handle = None
        # Do not let the currently configured global shortcut start a recording
        # while the user is choosing a new shortcut on this page.
        if self.hotkey is not None and self.hotkey.stop():
            self.hotkey = None
        container = self.settings_page
        for child in container.winfo_children():
            child.destroy()

        language_labels = dict(UI_LANGUAGE_CHOICES)
        language_codes = {label: code for code, label in UI_LANGUAGE_CHOICES}
        audio_ollama_models = list(self._installed_ollama_audio_models)
        installed_ollama_models = list(audio_ollama_models) or list(
            self._installed_ollama_all_models
        )
        if self._ollama_discovery_error:
            ollama_status = self._ollama_discovery_error
        elif audio_ollama_models:
            ollama_status = self._t("ollama_found", count=len(audio_ollama_models))
        elif installed_ollama_models:
            ollama_status = self._t("ollama_no_audio_models")
        else:
            ollama_status = self._t("ollama_checking")
        if self.settings.ollama_model and self.settings.ollama_model not in installed_ollama_models:
            installed_ollama_models.insert(0, self.settings.ollama_model)
        selected_ollama_model = self.settings.ollama_model or (
            audio_ollama_models[0] if audio_ollama_models else ""
        )
        variables: dict[str, tk.Variable] = {
            "profile": tk.StringVar(value=self.settings.profile),
            "engine": tk.StringVar(value=self.settings.engine),
            "language": tk.StringVar(value=self.settings.language),
            "ui_language": tk.StringVar(value=language_labels[self.settings.ui_language]),
            "model": tk.StringVar(value=self.settings.model),
            "device": tk.StringVar(value=self.settings.device),
            "compute_type": tk.StringVar(value=self.settings.compute_type),
            "vad_filter": tk.BooleanVar(value=self.settings.vad_filter),
            "retention": tk.StringVar(value=self.settings.retention),
            "dictionary_path": tk.StringVar(value=self.settings.dictionary_path),
            "hotkey": tk.StringVar(value=self.settings.hotkey),
            "auto_copy": tk.BooleanVar(value=self.settings.auto_copy),
            "offline_only": tk.BooleanVar(value=self.settings.offline_only),
            "automatic_cleanup": tk.BooleanVar(value=self.settings.automatic_cleanup),
            "openai_transcription_model": tk.StringVar(
                value=self.settings.openai_transcription_model
            ),
            "openai_cleanup_model": tk.StringVar(value=self.settings.openai_cleanup_model),
            "cleanup_provider": tk.StringVar(value=self.settings.cleanup_provider),
            "ollama_model": tk.StringVar(value=selected_ollama_model),
            "sync_enabled": tk.BooleanVar(value=self.settings.sync_enabled),
            "sync_folder": tk.StringVar(value=self.settings.sync_folder),
            "sync_include_audio": tk.BooleanVar(value=self.settings.sync_include_audio),
        }
        self._settings_variables = variables
        self._settings_baseline = {name: value.get() for name, value in variables.items()}
        info = tk.StringVar(value=ollama_status)
        self._settings_info_var = info
        ollama_status_var = tk.StringVar(value=ollama_status)
        self._settings_ollama_status_var = ollama_status_var

        header = ttk.Frame(container, padding=(28, 22, 28, 18), style="SettingsHeader.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=self._t("settings_title"), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=self._t("settings_intro"),
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew", padx=24, pady=(18, 12))
        profiles_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        general_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        recognition_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        notebook.add(profiles_page, text=self._t("profiles_settings"))
        notebook.add(general_page, text=self._t("general_settings"))
        notebook.add(recognition_page, text=self._t("recognition_settings"))
        for page in (general_page, recognition_page):
            page.grid_columnconfigure(0, weight=1, uniform="settings")
            page.grid_columnconfigure(1, weight=1, uniform="settings")

        for column in range(3):
            profiles_page.grid_columnconfigure(column, weight=1, uniform="profiles")

        engine_area = ttk.Frame(profiles_page, style="Card.TFrame")
        engine_area.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        engine_area.grid_columnconfigure(0, weight=1)
        engine_pages: dict[str, ttk.Frame] = {}
        for profile in ("ollama-local", "whisper-local", "openai-cloud"):
            engine_page = ttk.Frame(engine_area, padding=(18, 18), style="CardBorder.TFrame")
            engine_page.grid(row=0, column=0, sticky="ew")
            engine_page.grid_columnconfigure(0, weight=1, uniform="settings")
            engine_page.grid_columnconfigure(1, weight=1, uniform="settings")
            engine_pages[profile] = engine_page

        def show_engine_page(profile: str) -> None:
            for name, page in engine_pages.items():
                if name == profile:
                    page.grid()
                else:
                    page.grid_remove()

        def activate_profile() -> None:
            preset = apply_profile(self.settings, str(variables["profile"].get()))
            variables["engine"].set(preset.engine)
            variables["cleanup_provider"].set(preset.cleanup_provider)
            variables["automatic_cleanup"].set(preset.automatic_cleanup)
            variables["offline_only"].set(preset.offline_only)
            show_engine_page(preset.profile)
            info.set(
                f"{self._t('active_profile')}: "
                f"{self._t('profile_' + preset.profile.split('-')[0] + '_title')}"
            )

        profile_cards = (
            (
                "ollama-local",
                "profile_ollama_title",
                "profile_ollama_description",
            ),
            (
                "whisper-local",
                "profile_whisper_title",
                "profile_whisper_description",
            ),
            (
                "openai-cloud",
                "profile_openai_title",
                "profile_openai_description",
            ),
        )
        for column, (profile, title_key, description_key) in enumerate(profile_cards):
            card = ttk.Frame(
                profiles_page,
                padding=(18, 18),
                style="CardBorder.TFrame",
            )
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 12) if column < 2 else 0,
            )
            ttk.Radiobutton(
                card,
                text=self._t(title_key),
                variable=variables["profile"],
                value=profile,
                command=activate_profile,
            ).pack(anchor="w")
            ttk.Label(
                card,
                text=self._t(description_key),
                style="CardMuted.TLabel",
                wraplength=240,
                justify="left",
            ).pack(anchor="w", pady=(10, 0))

        def field(
            parent: ttk.Frame,
            row: int,
            column: int,
            label: str,
            *,
            columnspan: int = 1,
        ) -> ttk.Frame:
            container = ttk.Frame(parent, style="Card.TFrame")
            container.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky="ew",
                padx=(0, 14) if column == 0 and columnspan == 1 else 0,
                pady=(0, 18),
            )
            ttk.Label(container, text=label, style="CardMuted.TLabel").pack(anchor="w", pady=(0, 6))
            return container

        interface_field = field(general_page, 0, 0, self._t("interface_language"))
        ttk.Combobox(
            interface_field,
            textvariable=variables["ui_language"],
            values=tuple(label for _code, label in UI_LANGUAGE_CHOICES),
            state="readonly",
        ).pack(fill="x")

        retention_field = field(general_page, 0, 1, self._t("audio_retention"))
        ttk.Combobox(
            retention_field,
            textvariable=variables["retention"],
            values=("keep", "delete_after_transcription"),
            state="readonly",
        ).pack(fill="x")

        hotkey_field = field(general_page, 1, 0, self._t("hotkey"), columnspan=2)
        hotkey_row = ttk.Frame(hotkey_field, style="Card.TFrame")
        hotkey_row.pack(fill="x")
        ttk.Entry(hotkey_row, textvariable=variables["hotkey"]).pack(
            side="left", fill="x", expand=True
        )

        def choose_dictionary() -> None:
            path = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json")])
            if path:
                variables["dictionary_path"].set(path)

        capture_active = False

        def capture_hotkey(event: Any) -> str | None:
            nonlocal capture_active
            if not capture_active:
                return None
            if event.keysym == "Escape":
                capture_active = False
                info.set(self._t("hotkey_capture_cancelled"))
                return "break"
            captured = hotkey_from_tk_event(event)
            if captured:
                variables["hotkey"].set(captured)
                capture_active = False
                info.set(self._t("hotkey_captured", hotkey=captured))
                return "break"
            return None

        def begin_hotkey_capture() -> None:
            nonlocal capture_active
            capture_active = True
            info.set(self._t("hotkey_capture_prompt"))
            self.focus_set()

        if self._settings_capture_binding is not None:
            self.unbind("<KeyPress>", self._settings_capture_binding)
        self._settings_capture_binding = self.bind("<KeyPress>", capture_hotkey, add="+")
        ttk.Button(
            hotkey_row,
            text=self._t("capture_hotkey"),
            command=begin_hotkey_capture,
        ).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            general_page,
            text=self._t("auto_copy"),
            variable=variables["auto_copy"],
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            general_page,
            text=self._t("offline_only"),
            variable=variables["offline_only"],
            state="disabled",
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        sync_frame = ttk.Labelframe(
            general_page,
            text=self._t("sync_section"),
            padding=14,
            style="Card.TLabelframe",
        )
        sync_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        sync_frame.grid_columnconfigure(0, weight=1)

        ttk.Checkbutton(
            sync_frame,
            text=self._t("sync_enabled"),
            variable=variables["sync_enabled"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        sync_folder_row = ttk.Frame(sync_frame, style="Card.TFrame")
        sync_folder_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Entry(sync_folder_row, textvariable=variables["sync_folder"]).pack(
            side="left", fill="x", expand=True
        )

        def choose_sync_folder() -> None:
            path = filedialog.askdirectory(parent=self, title=self._t("sync_choose_folder"))
            if path:
                variables["sync_folder"].set(path)

        ttk.Button(
            sync_folder_row,
            text=self._t("sync_choose_folder"),
            command=choose_sync_folder,
        ).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            sync_frame,
            text=self._t("sync_include_audio"),
            variable=variables["sync_include_audio"],
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(
            sync_frame,
            text=self._t("sync_caption"),
            style="CardMuted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 8))

        ttk.Button(
            sync_frame,
            text=self._t("sync_all_now"),
            command=self._sync_all_now,
        ).grid(row=4, column=0, sticky="w")

        engine_field = field(recognition_page, 0, 0, self._t("engine"))
        ttk.Label(
            engine_field,
            textvariable=variables["engine"],
            style="CardTitle.TLabel",
        ).pack(anchor="w")

        language_field = field(recognition_page, 0, 1, self._t("transcription_language"))
        ttk.Combobox(
            language_field,
            textvariable=variables["language"],
            values=("auto", "uk", "cs", "en"),
            state="readonly",
        ).pack(fill="x")

        dictionary_field = field(recognition_page, 1, 0, self._t("dictionary_json"), columnspan=2)
        dictionary_row = ttk.Frame(dictionary_field, style="Card.TFrame")
        dictionary_row.pack(fill="x")
        ttk.Entry(dictionary_row, textvariable=variables["dictionary_path"]).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(dictionary_row, text=self._t("browse"), command=choose_dictionary).pack(
            side="left", padx=(8, 0)
        )

        ollama_page = engine_pages["ollama-local"]
        ollama_field = field(ollama_page, 0, 0, self._t("ollama_model"), columnspan=2)
        ollama_row = ttk.Frame(ollama_field, style="Card.TFrame")
        ollama_row.pack(fill="x")
        ollama_combo = ttk.Combobox(
            ollama_row,
            textvariable=variables["ollama_model"],
            values=tuple(installed_ollama_models),
            state="readonly",
        )
        ollama_combo.pack(side="left", fill="x", expand=True)
        self._settings_ollama_combo = ollama_combo

        def refresh_ollama_models() -> None:
            info.set(self._t("ollama_checking"))
            ollama_status_var.set(self._t("ollama_checking"))
            self._start_ollama_model_discovery()

        ttk.Button(
            ollama_row,
            text=self._t("refresh_models"),
            command=refresh_ollama_models,
        ).pack(side="left", padx=(8, 0))

        provider_field = field(ollama_page, 1, 0, self._t("cleanup_provider"), columnspan=2)
        ttk.Label(
            provider_field,
            textvariable=variables["cleanup_provider"],
            style="CardTitle.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            ollama_page,
            textvariable=ollama_status_var,
            style="CardMuted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        self._start_ollama_model_discovery()

        whisper_page = engine_pages["whisper-local"]
        model_field = field(whisper_page, 0, 0, self._t("model"))
        ttk.Entry(model_field, textvariable=variables["model"]).pack(fill="x")

        device_field = field(whisper_page, 0, 1, self._t("device"))
        device_combo = ttk.Combobox(
            device_field,
            textvariable=variables["device"],
            values=SUPPORTED_DEVICES,
            state="readonly",
        )
        device_combo.pack(fill="x")
        self._settings_hardware_device_combo = device_combo

        compute_field = field(whisper_page, 1, 0, self._t("compute_type"))
        compute_combo = ttk.Combobox(
            compute_field,
            textvariable=variables["compute_type"],
            values=SUPPORTED_COMPUTE_TYPES,
            state="readonly",
        )
        compute_combo.pack(fill="x")
        self._settings_hardware_compute_combo = compute_combo

        ttk.Checkbutton(
            whisper_page,
            text=self._t("vad_filter"),
            variable=variables["vad_filter"],
        ).grid(row=2, column=0, sticky="w")
        ttk.Button(
            whisper_page,
            text=self._t("hardware_detect"),
            command=self._start_hardware_detection,
        ).grid(row=2, column=1, sticky="e")

        openai_page = engine_pages["openai-cloud"]
        openai_stt_field = field(openai_page, 0, 0, self._t("openai_stt_model"))
        ttk.Entry(openai_stt_field, textvariable=variables["openai_transcription_model"]).pack(
            fill="x"
        )

        openai_cleanup_field = field(openai_page, 0, 1, self._t("openai_cleanup_model"))
        ttk.Entry(openai_cleanup_field, textvariable=variables["openai_cleanup_model"]).pack(
            fill="x"
        )

        def set_cloud_key() -> None:
            value = simpledialog.askstring(
                "OpenAI API key",
                self._t("key_prompt"),
                show="*",
                parent=self,
            )
            if value is None:
                return
            try:
                set_openai_api_key(value)
                info.set(self._t("key_saved"))
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=self)

        def delete_cloud_key() -> None:
            try:
                removed = delete_openai_api_key()
                info.set(self._t("key_removed") if removed else self._t("key_missing"))
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=self)

        def test_cloud_key() -> None:
            try:
                from openai import OpenAI

                OpenAI(api_key=get_openai_api_key(), timeout=30.0, max_retries=0).models.list()
                info.set(self._t("connection_pass"))
            except Exception as exc:
                info.set(self._t("connection_fail", error=type(exc).__name__))

        def key_status() -> None:
            status = openai_key_status()
            info.set(self._t("key_status", source=status.get("source", "unknown")))

        key_field = field(openai_page, 1, 0, "OpenAI", columnspan=2)
        key_row = ttk.Frame(key_field, style="Card.TFrame")
        key_row.pack(fill="x")
        ttk.Button(key_row, text=self._t("set_key"), command=set_cloud_key).pack(side="left")
        ttk.Button(key_row, text=self._t("delete_key"), command=delete_cloud_key).pack(
            side="left", padx=5
        )
        ttk.Button(key_row, text=self._t("status"), command=key_status).pack(side="left", padx=5)
        ttk.Button(key_row, text=self._t("test_connection"), command=test_cloud_key).pack(
            side="left", padx=5
        )
        ttk.Label(
            openai_page,
            text=self._t("cloud_consent"),
            style="CardMuted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        show_engine_page(str(variables["profile"].get()))

        footer = ttk.Frame(container, padding=(24, 12, 24, 18), style="SettingsHeader.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            footer,
            textvariable=info,
            wraplength=640,
            style="CardMuted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        footer_actions = ttk.Frame(footer, style="SettingsHeader.TFrame")
        footer_actions.pack(side="right", padx=(18, 0))

        def discard_and_leave() -> None:
            self._build_settings_page()
            self._show_page(self._settings_return_page)

        def save() -> bool:
            try:
                updated = replace(
                    self.settings,
                    profile=str(variables["profile"].get()).strip(),
                    engine=str(variables["engine"].get()).strip(),
                    language=str(variables["language"].get()).strip(),
                    ui_language=language_codes[str(variables["ui_language"].get())],
                    model=str(variables["model"].get()).strip(),
                    device=str(variables["device"].get()).strip(),
                    compute_type=str(variables["compute_type"].get()).strip(),
                    vad_filter=bool(variables["vad_filter"].get()),
                    retention=str(variables["retention"].get()).strip(),
                    dictionary_path=str(variables["dictionary_path"].get()).strip(),
                    hotkey=str(variables["hotkey"].get()).strip(),
                    auto_copy=bool(variables["auto_copy"].get()),
                    offline_only=bool(variables["offline_only"].get()),
                    automatic_cleanup=bool(variables["automatic_cleanup"].get()),
                    openai_transcription_model=str(
                        variables["openai_transcription_model"].get()
                    ).strip(),
                    openai_cleanup_model=str(variables["openai_cleanup_model"].get()).strip(),
                    cleanup_provider=str(variables["cleanup_provider"].get()).strip(),
                    ollama_model=str(variables["ollama_model"].get()).strip(),
                    sync_enabled=bool(variables["sync_enabled"].get()),
                    sync_folder=str(variables["sync_folder"].get()).strip(),
                    sync_include_audio=bool(variables["sync_include_audio"].get()),
                )
                updated = apply_profile(updated, updated.profile)
                self._validate_settings_for_save(updated)
            except Exception as exc:
                messagebox.showerror(self._t("settings"), str(exc), parent=self)
                return False
            if not self._apply_settings_update(updated):
                return False
            self.status.set(self._t("settings_saved"))
            self._build_settings_page()
            if self._settings_info_var is not None:
                self._settings_info_var.set(self._t("settings_saved"))
            return True

        self._settings_save = save
        ttk.Button(footer_actions, text=self._t("cancel"), command=discard_and_leave).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            footer_actions, text=self._t("save"), command=save, style="Primary.TButton"
        ).pack(side="left")

    def _models_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self._t("models_title"))
        dialog.transient(self)
        dialog.geometry("680x360")
        catalog = ModelCatalog(self.store.models)
        items: list[dict[str, Any]] = []
        listing = tk.Listbox(dialog)
        listing.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh() -> None:
            nonlocal items
            items = catalog.list()
            listing.delete(0, "end")
            for item in items:
                listing.insert(
                    "end",
                    f"{item['id']} — {item['size'] / 1_000_000:.1f} MB — {item['source']}",
                )

        def selected_id() -> str | None:
            selection = listing.curselection()
            return items[selection[0]]["id"] if selection else None

        def import_local() -> None:
            source = filedialog.askdirectory(parent=dialog, title=self._t("model_directory_title"))
            if not source:
                return
            model_id = simpledialog.askstring(
                "Model ID",
                self._t("model_id_prompt"),
                parent=dialog,
            )
            if not model_id:
                return
            try:
                catalog.import_local(model_id, Path(source))
                refresh()
            except Exception as exc:
                messagebox.showerror(self._t("models"), str(exc), parent=dialog)

        def download() -> None:
            if self.settings.offline_only:
                messagebox.showerror(
                    self._t("models"),
                    self._t("offline_blocks_download"),
                    parent=dialog,
                )
                return
            model_id = simpledialog.askstring(
                self._t("download_model_title"),
                self._t("download_model_prompt"),
                initialvalue="tiny",
                parent=dialog,
            )
            if not model_id:
                return
            self._set_busy(True)
            self._cancel_event.clear()
            dialog.destroy()

            def work() -> None:
                try:
                    entry = catalog.install(
                        model_id,
                        offline_only=self.settings.offline_only,
                        timeout_seconds=self.settings.task_timeout_seconds,
                        cancelled=self._cancel_event.is_set,
                        progress=lambda done, total: self._post_event(
                            "model_progress", (done, total)
                        ),
                    )
                    self._post_event("model_done", entry)
                except Exception as exc:
                    self._post_event("model_error", exc)

            self._start_worker("model-download", work)

        def verify() -> None:
            model_id = selected_id()
            if not model_id:
                return
            try:
                catalog.verify(model_id)
                messagebox.showinfo(
                    self._t("models"),
                    self._t("model_verified", model=model_id),
                    parent=dialog,
                )
            except Exception as exc:
                messagebox.showerror(self._t("models"), str(exc), parent=dialog)

        def remove() -> None:
            model_id = selected_id()
            if not model_id:
                return
            if not messagebox.askyesno(
                self._t("remove_model_title"),
                self._t("remove_model_prompt", model=model_id),
                parent=dialog,
            ):
                return
            try:
                catalog.remove(model_id, confirmed=True)
                refresh()
            except Exception as exc:
                messagebox.showerror(self._t("models"), str(exc), parent=dialog)

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text=self._t("import_local"), command=import_local).pack(side="left")
        ttk.Button(buttons, text=self._t("download"), command=download).pack(side="left", padx=5)
        ttk.Button(buttons, text=self._t("verify"), command=verify).pack(side="left")
        ttk.Button(buttons, text=self._t("remove"), command=remove).pack(side="right")
        refresh()

    def _prompt_new_backup_passphrase(self, parent: Any) -> str | None:
        """Collect a new backup passphrase with two masked prompts.

        Returns ``None`` on cancel, empty input or mismatch (with a localized
        error shown for the latter two). The passphrase stays a local
        variable; it is never stored on self, Settings, events or status.
        """

        passphrase = simpledialog.askstring(
            self._t("backup"),
            self._t("backup_passphrase_enter") + "\n\n" + self._t("backup_encrypt_warning"),
            show="*",
            parent=parent,
        )
        if passphrase is None:
            return None
        if not passphrase:
            messagebox.showerror(
                self._t("backup"), self._t("backup_passphrase_empty"), parent=parent
            )
            return None
        repeated = simpledialog.askstring(
            self._t("backup"),
            self._t("backup_passphrase_repeat"),
            show="*",
            parent=parent,
        )
        if repeated is None:
            return None
        if passphrase != repeated:
            messagebox.showerror(
                self._t("backup"), self._t("backup_passphrase_mismatch"), parent=parent
            )
            return None
        return passphrase

    def _start_backup_operation(
        self, action: str, callback: Any, passphrase: str | None = None
    ) -> None:
        """Run a backup callback on the maintenance worker.

        The callback takes an optional passphrase. Tk dialogs are never
        touched from the worker: a passphrase-required contract error is
        posted as an event and handled on the Tk main thread instead.
        """

        self._set_busy(True)
        self.status.set(self._t("backup_running"))

        def work() -> None:
            try:
                self._post_event("backup_done", (action, callback(passphrase)))
            except Exception as exc:
                if passphrase is None and str(exc) == _BACKUP_PASSPHRASE_REQUIRED:
                    self._post_event("backup_passphrase_required", (action, callback))
                else:
                    self._post_event("backup_error", (action, exc))

        thread = self._start_worker("maintenance", work, daemon=False)
        self._assign_worker_alias("maintenance", thread, "_maintenance_thread")

    def _handle_backup_passphrase_required(self, action: str, callback: Any) -> None:
        """Prompt once on the Tk main thread and retry the operation.

        Cancel finishes cleanly: nothing is retried, and a restore restores
        the runtime it closed before the operation was queued. The passphrase
        is handed to the retry closure only.
        """

        self._set_busy(False)
        passphrase = simpledialog.askstring(
            self._t("backup"),
            self._t("backup_passphrase_enter"),
            show="*",
            parent=self,
        )
        if passphrase is None:
            if action == "restore":
                self._reload_after_restore()
            self.status.set(self._t("backup_cancelled"))
            return
        self._start_backup_operation(action, callback, passphrase=passphrase)

    def _backup_dialog(self) -> None:
        # Maintenance workers are created as ``threading.Thread`` instances
        # by the shared registry, and remain non-daemon until the operation ends.
        dialog = tk.Toplevel(self)
        dialog.title(self._t("backup"))
        dialog.transient(self)
        dialog.resizable(False, False)
        include_audio = tk.BooleanVar(value=True)
        encrypt = tk.BooleanVar(value=False)
        ttk.Label(
            dialog,
            text=self._t("backup_description"),
            wraplength=520,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 8))
        ttk.Checkbutton(
            dialog,
            text=self._t("include_audio"),
            variable=include_audio,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=6)
        ttk.Checkbutton(
            dialog,
            text=self._t("backup_encrypt"),
            variable=encrypt,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))
        ttk.Label(
            dialog,
            text=self._t("backup_encrypt_warning"),
            wraplength=520,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 6))

        def start_operation(action: str, callback: Any, passphrase: str | None = None) -> None:
            dialog.destroy()
            self._start_backup_operation(action, callback, passphrase=passphrase)

        def create() -> None:
            destination = filedialog.asksaveasfilename(
                parent=dialog,
                defaultextension=".voice-backup",
                filetypes=[("VOICE Studio backup", "*.voice-backup")],
            )
            if not destination:
                return
            include_audio_value = bool(include_audio.get())
            passphrase = None
            if encrypt.get():
                passphrase = self._prompt_new_backup_passphrase(dialog)
                if passphrase is None:
                    return  # cancelled or rejected: the operation never starts
            self._queue_backup_create(
                Path(destination),
                include_audio_value,
                passphrase,
                start_operation,
            )

        def verify() -> None:
            source = filedialog.askopenfilename(
                parent=dialog,
                filetypes=[("VOICE Studio backup", "*.voice-backup")],
            )
            if source:
                start_operation(
                    "verify",
                    lambda passphrase=None: verify_backup(Path(source), passphrase=passphrase),
                )

        def restore() -> None:
            source = filedialog.askopenfilename(
                parent=dialog,
                filetypes=[("VOICE Studio backup", "*.voice-backup")],
            )
            if not source:
                return
            if not messagebox.askyesno(
                self._t("restore_backup_title"),
                self._t("restore_backup_prompt"),
                parent=dialog,
            ):
                return
            self._queue_restore(Path(source), start_operation)

        ttk.Button(dialog, text=self._t("create"), command=create).grid(
            row=4, column=0, padx=12, pady=12
        )
        ttk.Button(dialog, text=self._t("verify"), command=verify).grid(
            row=4, column=1, padx=6, pady=12
        )
        ttk.Button(dialog, text=self._t("restore"), command=restore).grid(
            row=4, column=2, padx=12, pady=12
        )

    def _queue_backup_create(
        self,
        destination: Path,
        include_audio: bool,
        passphrase: str | None,
        start_operation: Any,
    ) -> None:
        """Queue backup creation without retaining the secret in its callback."""

        def create(passphrase_for_run: str | None = None) -> dict[str, Any]:
            return create_backup(
                self.store,
                destination,
                settings_file=settings_path(),
                include_audio=include_audio,
                passphrase=passphrase_for_run,
            )

        start_operation("create", create, passphrase)

    def _queue_restore(self, source: Path, start_operation: Any) -> bool:
        if not self._confirm_editor_transition():
            return False
        self.job_controller.close()
        start_operation(
            "restore",
            lambda passphrase=None: restore_backup(
                source,
                data_dir(),
                settings_target=settings_path(),
                passphrase=passphrase,
            ),
        )
        return True

    def _clear_current_transcript_view(self) -> None:
        self.current = None
        self._cleanup_snapshot = None
        self._cleanup_transcript_id = None
        self.editor.delete("1.0", "end")
        self._apply_editor_formatting({})
        self._editor_baseline = snapshot_editor("", {})
        self._set_readonly_text(self.raw_editor, "")
        self._set_readonly_text(self.details, "")
        self._refresh_smart_text()

    def _restart_runtime(self) -> None:
        self.store = LocalStore(data_dir())
        self.job_controller = TranscriptionJobController(self.store, cache_dir())

    def _reload_after_restore(self) -> None:
        self._stop_playback()
        self._restart_runtime()
        previous_ui_language = self.settings.ui_language
        self.settings = load_settings()
        self._clear_current_transcript_view()
        self._refresh_history()
        self._refresh_dashboard()
        if previous_ui_language != self.settings.ui_language:
            self._reset_help_page()
        self._refresh_ui_text()
        if self._current_page == "settings":
            self._build_settings_page()
        else:
            self._start_hotkey()

    def _close(self) -> None:
        if self.__dict__.get("_closing", False):
            return
        maintenance = self.__dict__.get("_maintenance_thread")
        if maintenance is not None and maintenance.is_alive():
            messagebox.showwarning(
                self._t("backup"),
                self._t("wait_backup"),
                parent=self,
            )
            return
        if not self._confirm_editor_transition():
            return
        if not self._confirm_dictionary_transition():
            return
        if (
            self.__dict__.get("_current_page") == "settings"
            and not self._confirm_settings_transition()
        ):
            return
        self._closing = True
        # The queue must not hand out another file while the app shuts down;
        # the running job still goes through the normal cancel path below.
        batch_queue = self.__dict__.get("batch_queue")
        if batch_queue is not None:
            batch_queue.pause()
        shutdown = self.__dict__.setdefault("_shutdown_event", threading.Event())
        lock = self.__dict__.setdefault("_worker_lock", threading.RLock())
        with lock:
            shutdown.set()
        self._cancel_event.set()
        residues: set[str] = set()
        writer_timeout_path: Path | None = None
        try:
            self._shutdown_playback(residues)
            hotkey = self.__dict__.get("hotkey")
            hotkey_stopped = True
            if hotkey is not None:
                try:
                    hotkey_stopped = bool(hotkey.stop())
                except Exception as exc:
                    hotkey_stopped = False
                    try:
                        self.status.set(self._t("hotkey_unavailable", error=exc))
                    except Exception:
                        pass
            if not hotkey_stopped:
                residues.add("global-hotkey")

            try:
                self.recorder.cancel()
            except Exception as exc:
                if self._is_unresolved_writer_timeout(exc):
                    writer_timeout_path = self._retain_unresolved_recorder_path(
                        self._active_recording_path or getattr(self.recorder, "destination", None)
                    )
                try:
                    self._report_recorder_error(exc)
                except Exception:
                    pass
            self._active_recording_path = None

            try:
                self.job_controller.close()
            except Exception as exc:
                try:
                    self.status.set(self._t("processing_error") + f": {exc}")
                except Exception:
                    pass
            try:
                residues.update(self._join_workers())
            except Exception as exc:
                try:
                    self.status.set(self._t("processing_error") + f": {exc}")
                except Exception:
                    pass
        finally:
            self._shutdown_residue_threads = tuple(sorted(residues))
            if self._shutdown_residue_threads:
                try:
                    self.status.set(
                        self._t(
                            "shutdown_residue",
                            workers=", ".join(self._shutdown_residue_threads),
                        )
                    )
                except Exception:
                    pass
            try:
                for path in list(self.__dict__.get("_pending_microphone_files", set())):
                    if (
                        writer_timeout_path is not None
                        and self._safe_recording_path(path) == writer_timeout_path
                    ):
                        continue
                    try:
                        self._cleanup_temp(path)
                    except Exception:
                        pass
                try:
                    self._report_recording_residues()
                except Exception:
                    pass
            finally:
                self.destroy()


def main() -> None:
    VoiceStudioApp().mainloop()


if __name__ == "__main__":
    main()
