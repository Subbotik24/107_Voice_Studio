from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from dataclasses import dataclass, replace
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
from .cloud_cleanup import list_ollama_models, propose_cleanup
from .cloud_secrets import (
    delete_openai_api_key,
    get_openai_api_key,
    openai_key_status,
    set_openai_api_key,
)
from .config import cache_dir, data_dir, load_settings, save_settings, settings_path
from .dictionary import TerminologyDictionary
from .editor_state import snapshot_editor
from .exporters import export_transcript
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
from .models import Settings, Transcript
from .profiles import (
    apply_profile,
    discover_ollama_audio_models,
    with_preferred_ollama_model,
)
from .recorder import AudioRecorder
from .storage import LocalStore

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


class VoiceStudioApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VOICE Studio")
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
        self.hotkey: GlobalHotkey | None = None
        self.current: Transcript | None = None
        self._editor_baseline = snapshot_editor("", {})
        self._cleanup_snapshot = None
        self._cleanup_transcript_id: str | None = None
        self._cleanup_provider = "openai"
        self._cleanup_model = self.settings.openai_cleanup_model
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._history_items: list[Transcript] = []
        self._busy = False
        self._continuous_recording = False
        self._pending_microphone_files: set[Path] = set()
        self._active_recording_path: Path | None = None
        self._ambiguous_microphone_files: set[Path] = set()
        self._recording_residue_diagnostics: list[str] = []
        self._cancel_event = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._help_window: tk.Toplevel | None = None
        self._help_images: list[tk.PhotoImage] = []
        self._installed_ollama_audio_models: list[str] = []
        self._ollama_discovery_error = ""
        self._ollama_discovery_thread: threading.Thread | None = None
        self._settings_ollama_combo: ttk.Combobox | None = None
        self._settings_info_var: tk.StringVar | None = None
        self._build_ui()
        self._report_restore_recovery()
        self._refresh_history()
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
        """Finish or undo an interrupted restore. Never blocks application start."""

        try:
            return recover_interrupted_restore(
                data_dir(), settings_target=settings_path()
            )
        except Exception as exc:  # startup must survive any journal defect
            return {"status": "FAIL", "action": "none", "error": str(exc)}

    def _report_restore_recovery(self) -> None:
        result = self._restore_recovery
        action = result.get("action", "none")
        if result.get("status") != "PASS":
            message = self._t(
                "restore_recovery_failed", error=result.get("error", "unknown")
            )
            self.status.set(message)
            self.after(
                250, lambda: messagebox.showwarning(self._t("backup"), message)
            )
            return
        key = {
            "completed": "restore_recovered",
            "settings_completed": "restore_recovered",
            "rolled_back": "restore_rolled_back",
            "staging_discarded": "restore_staging_discarded",
        }.get(action)
        if key is None:
            return
        values = {"records": result.get("records") or 0} if key == "restore_recovered" else {}
        self.status.set(self._t(key, **values))

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
                models = discover_ollama_audio_models()
                self.events.put(("ollama_models", {"models": models, "error": ""}))
            except Exception as exc:
                self.events.put(
                    ("ollama_models", {"models": [], "error": str(exc)[:500]})
                )

        self._ollama_discovery_thread = threading.Thread(
            target=discover,
            daemon=True,
            name="ollama-model-discovery",
        )
        self._ollama_discovery_thread.start()

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
        ttk.Label(
            self.brand_details, text="VOICE Studio", style="Brand.TLabel"
        ).pack(anchor="w")
        self.brand_subtitle_label = ttk.Label(
            self.brand_details,
            text=self._t("studio_subtitle"),
            style="SidebarMuted.TLabel",
        )
        self.brand_subtitle_label.pack(anchor="w", pady=(2, 0))

        navigation = ttk.Frame(
            self.sidebar, padding=(18, 12, 18, 12), style="Sidebar.TFrame"
        )
        navigation.grid(row=1, column=0, sticky="nsew")
        self.studio_button = ttk.Button(
            navigation,
            text=self._t("studio_nav"),
            command=lambda: self.editor.focus_set(),
            style="SidebarActive.TButton",
        )
        self.studio_button.pack(fill="x", pady=(0, 6))
        self.history_nav_button = ttk.Button(
            navigation,
            text=self._t("history"),
            command=lambda: self.history.focus_set(),
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
            command=self._settings_dialog,
            style="Sidebar.TButton",
        )
        self.settings_button.pack(fill="x", pady=6)
        self.help_button = ttk.Button(
            navigation,
            text=self._t("help"),
            command=self._help_dialog,
            style="Sidebar.TButton",
        )
        self.help_button.pack(fill="x", pady=6)
        self._sidebar_buttons = (
            (self.studio_button, "studio_nav", "●"),
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
        ttk.Label(
            topbar, textvariable=self.engine_label, style="TopbarMuted.TLabel"
        ).pack(side="right")
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

        self.main_area = ttk.Frame(self.workspace_body, style="Canvas.TFrame")
        main_area = self.main_area
        main_area.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
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

        self.status = tk.StringVar(value=self._t("ready_local"))
        status_bar = ttk.Frame(main_area, padding=(12, 8), style="Status.TFrame")
        status_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(status_bar, text="●", style="StatusDot.TLabel").pack(side="left")
        ttk.Label(status_bar, textvariable=self.status, style="Status.TLabel").pack(
            side="left", padx=(7, 0)
        )

        self.editor_frame = ttk.Labelframe(
            main_area, text=self._t("transcript"), padding=14, style="Card.TLabelframe"
        )
        self.editor_frame.grid(row=3, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self.editor_frame)
        self.notebook.pack(fill="both", expand=True)
        corrected_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        raw_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        details_frame = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(corrected_frame, text=self._t("corrected_text"))
        self.notebook.add(raw_frame, text=self._t("raw"))
        self.notebook.add(details_frame, text=self._t("data"))

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
        self.editor.pack(fill="both", expand=True)
        self.editor.tag_configure("bold", font=(theme.ui_font, 11, "bold"))
        self.editor.tag_configure("italic", font=(theme.ui_font, 11, "italic"))
        self.editor.bind("<Return>", self._insert_editor_newline)
        self.editor.bind("<Control-Return>", self._insert_editor_newline)
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

        export_bar = ttk.Frame(self.editor_frame, style="Card.TFrame")
        export_bar.pack(fill="x", pady=(10, 0))
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

        self.history_frame = ttk.Labelframe(
            main_area,
            text=self._t("history"),
            padding=12,
            style="Card.TLabelframe",
        )
        self.history_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))

        search_row = ttk.Frame(self.history_frame, style="Card.TFrame")
        search_row.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", lambda _event: self._refresh_history())
        self.search_button = ttk.Button(
            search_row, text=self._t("search"), command=self._refresh_history
        )
        self.search_button.pack(
            side="left", padx=(5, 0)
        )

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

        self.readiness_frame = ttk.Frame(
            self.workspace_body, width=214, padding=18, style="CardBorder.TFrame"
        )
        self.readiness_frame.grid(row=0, column=1, sticky="nsew")
        self.readiness_frame.grid_propagate(False)
        self.readiness_title_label = ttk.Label(
            self.readiness_frame, text=self._t("readiness"), style="CardTitle.TLabel"
        )
        self.readiness_title_label.pack(anchor="w")
        ready_box = ttk.Frame(
            self.readiness_frame, padding=(12, 10), style="ReadyBox.TFrame"
        )
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

        self._studio_layout: StudioLayout | None = None
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.bind_all("<F1>", lambda _event: self._help_dialog(), add="+")
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
        if layout.show_readiness:
            self.readiness_frame.grid()
            self.workspace_body.grid_columnconfigure(1, minsize=214)
            self.main_area.grid_configure(padx=(0, panel_gap))
        else:
            self.readiness_frame.grid_remove()
            self.workspace_body.grid_columnconfigure(1, minsize=0)
            self.main_area.grid_configure(padx=0)
        for button, key, symbol in self._sidebar_buttons:
            label = symbol if layout.compact_sidebar else f"●  {self._t(key)}"
            button.configure(text=label)

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
            background=[("active", theme.accent_soft), ("disabled", theme.surface)],
            foreground=[("active", theme.ink), ("disabled", theme.muted_ink)],
            bordercolor=[("focus", theme.accent)],
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
        help_window = getattr(self, "_help_window", None)
        if help_window is not None and help_window.winfo_exists():
            help_window.title(self._t("help_title"))
            self._help_title_label.configure(text=self._t("help_title"))
            self._help_intro_label.configure(text=self._t("help_intro"))
            self._help_search_label.configure(text=self._t("help_search"))
            self._help_search_button.configure(text=self._t("help_search_action"))
            self._help_close_button.configure(text=self._t("help_close"))
        self._apply_studio_layout(self.winfo_width(), force=True)
        self._update_engine_label()

    def _update_engine_label(self) -> None:
        if self.settings.engine == "ollama":
            model = self.settings.ollama_model or self._t("not_selected")
        elif self.settings.engine == "openai-cloud":
            model = self.settings.openai_transcription_model
        else:
            model = self.settings.model
        self.engine_label.set(
            self._t("engine_status", engine=self.settings.engine, model=model)
        )
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

    def _start_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        try:
            self.hotkey = GlobalHotkey(
                self.settings.hotkey,
                lambda: self.events.put(("record_start", None)),
                lambda: self.events.put(("record_stop", None)),
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
            getattr(error, "cleanup_error", None) is not None
            or getattr(error, "residue_paths", ())
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
                    paths=", ".join(
                        str(path) for path in sorted(unambiguous_pending, key=str)
                    ),
                )
            )
        messagebox.showerror(
            self._t("recording_cleanup_title"), "\n\n".join(details), parent=self
        )

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
                    if not self._try_show_result(transcript, copy=True):
                        self._refresh_history(
                            select_id=self.current.id if self.current else None
                        )
                    self._report_automatic_cleanup_warning(transcript)
                elif event == "error":
                    error, cleanup = value
                    self._cleanup_temp(cleanup)
                    self._set_busy(False)
                    self.status.set(self._t("processing_error"))
                    messagebox.showerror(self._t("error"), str(error))
                elif event == "model_progress":
                    downloaded, expected = value
                    percent = min(99, round(downloaded * 100 / max(expected, 1)))
                    self.status.set(self._t("model_download_progress", percent=percent))
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
                    self._ollama_discovery_thread = None
                    models = [str(item) for item in value.get("models", []) if str(item)]
                    self._installed_ollama_audio_models = models
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
                        choices = list(models)
                        if self.settings.ollama_model not in choices:
                            choices.insert(0, self.settings.ollama_model)
                        combo.configure(values=tuple(item for item in choices if item))
                        if self.settings.ollama_model:
                            combo.set(self.settings.ollama_model)
                    info = self._settings_info_var
                    if info is not None:
                        if self._ollama_discovery_error:
                            info.set(self._ollama_discovery_error)
                        elif models:
                            info.set(self._t("ollama_found", count=len(models)))
                        else:
                            info.set(self._t("ollama_missing"))
                elif event == "job_cancelled":
                    cleanup = value
                    self._cleanup_temp(cleanup)
                    self._set_busy(False)
                    self.status.set(self._t("task_cancelled"))
                elif event == "backup_done":
                    action, result = value
                    self._maintenance_thread = None
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
                elif event == "backup_error":
                    action, error = value
                    self._maintenance_thread = None
                    if action == "restore":
                        self._reload_after_restore()
                    self._set_busy(False)
                    self.status.set(self._t("backup_error"))
                    messagebox.showerror(self._t("backup"), str(error))
                elif event == "cleanup_proposal":
                    transcript, proposal = value
                    self._set_busy(False)
                    preview = self._t(
                        "cleanup_preview",
                        before=transcript.corrected_text[:1000],
                        after=proposal.corrected_text[:1000],
                    )
                    if messagebox.askyesno(
                        self._t("cleanup_preview_title"), preview, parent=self
                    ):
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
                    else:
                        self.status.set(self._t("cleanup_not_applied"))
                elif event == "cleanup_error":
                    self._set_busy(False)
                    self.status.set(self._t("cleanup_error"))
                    messagebox.showerror(self._t("cleanup_error"), str(value), parent=self)
        except queue.Empty:
            pass
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
            self._register_recorder_residues(exc)
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
            self._report_recorder_error(
                RuntimeError(self._t("recording_path_mismatch"))
            )
            return

        limit_reached = bool(getattr(result, "limit_reached", False) or limit_forced)
        if getattr(result, "degraded", False):
            warning = getattr(result, "warning", "") or (
                self._t("recording_damage_default")
            )
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
                self._t("recording_processing")
                if started
                else self._t("processing_not_started")
            )
            self.status.set(self._t("recording_limit_status", result=result_text))

    def _choose_file(self) -> None:
        if self._busy:
            return
        name = filedialog.askopenfilename(filetypes=MEDIA_FILETYPES)
        if name:
            self._process(Path(name), cleanup=False)

    def _process(self, source: Path, *, cleanup: bool) -> bool:
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
                    progress=lambda phase, elapsed: self.events.put(
                        ("job_progress", (phase, elapsed))
                    ),
                )
                self.events.put(("done", (transcript, source if cleanup else None)))
            except JobCancelled:
                self.events.put(("job_cancelled", source if cleanup else None))
            except Exception as exc:
                self.events.put(("error", (exc, source if cleanup else None)))

        threading.Thread(target=work, daemon=True, name="transcription-worker").start()
        return True

    def _cancel_current(self) -> None:
        if self._busy:
            self._cancel_event.set()
            self.status.set(self._t("cancel_running"))

    def _show_result(
        self, transcript: Transcript, *, copy: bool = False, refresh: bool = True
    ) -> None:
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
        if refresh:
            self._refresh_history(select_id=transcript.id)
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

    def _refresh_history(self, *, select_id: str | None = None) -> None:
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        self._history_items = self.store.list(query=query, limit=250)
        self.history.delete(0, "end")
        selected_index: int | None = None
        for index, item in enumerate(self._history_items):
            label = f"{item.created_at[:16]}  [{item.language}]  {item.source_name}"
            self.history.insert("end", label)
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
            self.current = None
            self.editor.delete("1.0", "end")
            self._apply_editor_formatting({})
            self._editor_baseline = snapshot_editor("", {})
            self._set_readonly_text(self.raw_editor, "")
            self._set_readonly_text(self.details, "")
        self._refresh_history()
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
        return choice is False

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
                self.events.put(("cleanup_proposal", (transcript, proposal)))
            except Exception as exc:
                self.events.put(("cleanup_error", exc))

        threading.Thread(target=work, daemon=True, name="ai-cleanup").start()

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

    def _raise_existing_help(self) -> bool:
        window = getattr(self, "_help_window", None)
        if window is None or not window.winfo_exists():
            self._help_window = None
            return False
        window.deiconify()
        window.lift()
        window.focus_force()
        return True

    def _localized_help_topics(self, help_root: Path) -> tuple[HelpTopic, ...]:
        return load_help_topics(help_root, self.settings.ui_language)

    def _close_help_window(self) -> str:
        window = getattr(self, "_help_window", None)
        self._help_window = None
        self._help_images = []
        if window is not None and window.winfo_exists():
            window.destroy()
        return "break"

    def _help_dialog(self) -> None:
        if self._raise_existing_help():
            return
        try:
            help_root = resolve_help_root()
            topics = self._localized_help_topics(help_root)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                self._t("help"), self._t("help_unavailable", error=exc), parent=self
            )
            return

        theme = VOICE_STUDIO_THEME
        dialog = tk.Toplevel(self)
        self._help_window = dialog
        self._help_images = []
        dialog.title(self._t("help_title"))
        dialog.geometry("1040x720")
        dialog.minsize(820, 560)
        dialog.transient(self)
        dialog.configure(background=theme.canvas)
        dialog.grid_rowconfigure(1, weight=1)
        dialog.grid_columnconfigure(0, weight=1)

        def close_help(_event: Any = None) -> str:
            return self._close_help_window()

        dialog.protocol("WM_DELETE_WINDOW", close_help)
        dialog.bind("<Escape>", close_help)

        header = ttk.Frame(dialog, padding=(28, 22, 28, 18), style="Topbar.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        self._help_title_label = ttk.Label(header, text=self._t("help_title"), style="Title.TLabel")
        self._help_title_label.pack(anchor="w")
        self._help_intro_label = ttk.Label(
            header, text=self._t("help_intro"), style="TopbarMuted.TLabel"
        )
        self._help_intro_label.pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(dialog, padding=(24, 20), style="Canvas.TFrame")
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

        footer = ttk.Frame(dialog, padding=(24, 0, 24, 18), style="Canvas.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        self._help_close_button = ttk.Button(footer, text=self._t("help_close"), command=close_help)
        self._help_close_button.pack(side="right")
        populate_topics()
        search_entry.focus_set()

    def _close_settings_dialog(self, dialog: tk.Toplevel) -> None:
        """Finish Tk teardown before starting the native keyboard listener."""

        self._settings_ollama_combo = None
        self._settings_info_var = None
        dialog.grab_release()
        dialog.destroy()
        self.after_idle(self._start_hotkey)

    def _refresh_after_settings_save(self, previous_ui_language: str) -> None:
        self.job_controller.close()
        if previous_ui_language != self.settings.ui_language:
            self._close_help_window()
        self._refresh_ui_text()

    def _settings_dialog(self) -> None:
        # Do not let the currently configured global shortcut start a recording
        # while the user is choosing a new shortcut in this modal dialog.
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        dialog = tk.Toplevel(self)
        dialog.title(self._t("settings_title"))
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("980x700")
        dialog.minsize(900, 620)
        dialog.resizable(True, True)
        dialog.configure(background=VOICE_STUDIO_THEME.canvas)
        dialog.grid_rowconfigure(1, weight=1)
        dialog.grid_columnconfigure(0, weight=1)

        language_labels = dict(UI_LANGUAGE_CHOICES)
        language_codes = {label: code for code, label in UI_LANGUAGE_CHOICES}
        installed_ollama_models = list(self._installed_ollama_audio_models)
        if self._ollama_discovery_error:
            ollama_status = self._ollama_discovery_error
        elif installed_ollama_models:
            ollama_status = self._t("ollama_found", count=len(installed_ollama_models))
        else:
            ollama_status = self._t("ollama_checking")
        if (
            self.settings.ollama_model
            and self.settings.ollama_model not in installed_ollama_models
        ):
            installed_ollama_models.insert(0, self.settings.ollama_model)
        selected_ollama_model = self.settings.ollama_model or (
            installed_ollama_models[0] if installed_ollama_models else ""
        )
        variables: dict[str, tk.Variable] = {
            "profile": tk.StringVar(value=self.settings.profile),
            "engine": tk.StringVar(value=self.settings.engine),
            "language": tk.StringVar(value=self.settings.language),
            "ui_language": tk.StringVar(value=language_labels[self.settings.ui_language]),
            "model": tk.StringVar(value=self.settings.model),
            "device": tk.StringVar(value=self.settings.device),
            "compute_type": tk.StringVar(value=self.settings.compute_type),
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
        }
        info = tk.StringVar(value=ollama_status)
        self._settings_info_var = info

        header = ttk.Frame(dialog, padding=(28, 22, 28, 18), style="SettingsHeader.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=self._t("settings_title"), style="CardTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text=self._t("settings_intro"),
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(dialog)
        notebook.grid(row=1, column=0, sticky="nsew", padx=24, pady=(18, 12))
        profiles_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        general_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        recognition_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        local_ai_page = ttk.Frame(notebook, padding=22, style="Card.TFrame")
        notebook.add(profiles_page, text=self._t("profiles_settings"))
        notebook.add(general_page, text=self._t("general_settings"))
        notebook.add(recognition_page, text=self._t("recognition_settings"))
        notebook.add(local_ai_page, text=self._t("local_ai_settings"))
        for page in (general_page, recognition_page, local_ai_page):
            page.grid_columnconfigure(0, weight=1, uniform="settings")
            page.grid_columnconfigure(1, weight=1, uniform="settings")

        for column in range(3):
            profiles_page.grid_columnconfigure(column, weight=1, uniform="profiles")

        def activate_profile() -> None:
            preset = apply_profile(self.settings, str(variables["profile"].get()))
            variables["engine"].set(preset.engine)
            variables["cleanup_provider"].set(preset.cleanup_provider)
            variables["automatic_cleanup"].set(preset.automatic_cleanup)
            variables["offline_only"].set(preset.offline_only)
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
            ttk.Label(container, text=label, style="CardMuted.TLabel").pack(
                anchor="w", pady=(0, 6)
            )
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

        hotkey_field = field(
            general_page, 1, 0, self._t("hotkey"), columnspan=2
        )
        hotkey_row = ttk.Frame(hotkey_field, style="Card.TFrame")
        hotkey_row.pack(fill="x")
        ttk.Entry(hotkey_row, textvariable=variables["hotkey"]).pack(
            side="left", fill="x", expand=True
        )

        def choose_dictionary() -> None:
            path = filedialog.askopenfilename(parent=dialog, filetypes=[("JSON", "*.json")])
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
            dialog.focus_set()

        dialog.bind("<KeyPress>", capture_hotkey)
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

        engine_field = field(recognition_page, 0, 0, self._t("engine"))
        ttk.Label(
            engine_field,
            textvariable=variables["engine"],
            style="CardTitle.TLabel",
        ).pack(anchor="w")

        language_field = field(
            recognition_page, 0, 1, self._t("transcription_language")
        )
        ttk.Combobox(
            language_field,
            textvariable=variables["language"],
            values=("auto", "uk", "cs", "en"),
            state="readonly",
        ).pack(fill="x")

        model_field = field(recognition_page, 1, 0, self._t("model"))
        ttk.Entry(model_field, textvariable=variables["model"]).pack(fill="x")

        device_field = field(recognition_page, 1, 1, self._t("device"))
        ttk.Entry(device_field, textvariable=variables["device"]).pack(fill="x")

        compute_field = field(recognition_page, 2, 0, self._t("compute_type"))
        ttk.Entry(compute_field, textvariable=variables["compute_type"]).pack(fill="x")

        openai_stt_field = field(recognition_page, 2, 1, self._t("openai_stt_model"))
        ttk.Entry(
            openai_stt_field, textvariable=variables["openai_transcription_model"]
        ).pack(fill="x")

        dictionary_field = field(
            recognition_page, 3, 0, self._t("dictionary_json"), columnspan=2
        )
        dictionary_row = ttk.Frame(dictionary_field, style="Card.TFrame")
        dictionary_row.pack(fill="x")
        ttk.Entry(dictionary_row, textvariable=variables["dictionary_path"]).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(dictionary_row, text=self._t("browse"), command=choose_dictionary).pack(
            side="left", padx=(8, 0)
        )

        provider_field = field(local_ai_page, 0, 0, self._t("cleanup_provider"))
        ttk.Label(
            provider_field,
            textvariable=variables["cleanup_provider"],
            style="CardTitle.TLabel",
        ).pack(anchor="w")

        ollama_field = field(local_ai_page, 0, 1, self._t("ollama_model"))
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
            self._start_ollama_model_discovery()

        ttk.Button(
            ollama_row,
            text=self._t("refresh_models"),
            command=refresh_ollama_models,
        ).pack(side="left", padx=(8, 0))
        self._start_ollama_model_discovery()

        openai_cleanup_field = field(
            local_ai_page, 1, 0, self._t("openai_cleanup_model"), columnspan=2
        )
        ttk.Entry(
            openai_cleanup_field, textvariable=variables["openai_cleanup_model"]
        ).pack(fill="x")

        def set_cloud_key() -> None:
            value = simpledialog.askstring(
                "OpenAI API key",
                self._t("key_prompt"),
                show="*",
                parent=dialog,
            )
            if value is None:
                return
            try:
                set_openai_api_key(value)
                info.set(self._t("key_saved"))
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=dialog)

        def delete_cloud_key() -> None:
            try:
                removed = delete_openai_api_key()
                info.set(self._t("key_removed") if removed else self._t("key_missing"))
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=dialog)

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

        key_field = field(local_ai_page, 2, 0, "OpenAI", columnspan=2)
        key_row = ttk.Frame(key_field, style="Card.TFrame")
        key_row.pack(fill="x")
        ttk.Button(key_row, text=self._t("set_key"), command=set_cloud_key).pack(
            side="left"
        )
        ttk.Button(key_row, text=self._t("delete_key"), command=delete_cloud_key).pack(
            side="left", padx=5
        )
        ttk.Button(key_row, text=self._t("status"), command=key_status).pack(
            side="left", padx=5
        )
        ttk.Button(key_row, text=self._t("test_connection"), command=test_cloud_key).pack(
            side="left", padx=5
        )
        ttk.Label(
            local_ai_page,
            text=self._t("cloud_consent"),
            style="CardMuted.TLabel",
            wraplength=760,
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        footer = ttk.Frame(dialog, padding=(24, 12, 24, 18), style="SettingsHeader.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            footer,
            textvariable=info,
            wraplength=640,
            style="CardMuted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        footer_actions = ttk.Frame(footer, style="SettingsHeader.TFrame")
        footer_actions.pack(side="right", padx=(18, 0))

        def close_without_saving() -> None:
            self._close_settings_dialog(dialog)

        def save() -> None:
            previous_ui_language = self.settings.ui_language
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
                )
                updated = apply_profile(updated, updated.profile)
                updated.validate()
                if updated.dictionary_path:
                    TerminologyDictionary.load(updated.dictionary_path)
                save_settings(updated)
                self.settings = updated
            except Exception as exc:
                messagebox.showerror(self._t("settings"), str(exc), parent=dialog)
                return
            self._refresh_after_settings_save(previous_ui_language)
            self.status.set(self._t("settings_saved"))
            self._close_settings_dialog(dialog)

        ttk.Button(
            footer_actions, text=self._t("cancel"), command=close_without_saving
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            footer_actions, text=self._t("save"), command=save, style="Primary.TButton"
        ).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", close_without_saving)

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
            source = filedialog.askdirectory(
                parent=dialog, title=self._t("model_directory_title")
            )
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
                        progress=lambda done, total: self.events.put(
                            ("model_progress", (done, total))
                        ),
                    )
                    self.events.put(("model_done", entry))
                except Exception as exc:
                    self.events.put(("model_error", exc))

            threading.Thread(target=work, daemon=True, name="model-download").start()

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

    def _backup_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self._t("backup"))
        dialog.transient(self)
        dialog.resizable(False, False)
        include_audio = tk.BooleanVar(value=True)
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

        def start_operation(action: str, callback: Any) -> None:
            self._set_busy(True)
            self.status.set(self._t("backup_running"))
            dialog.destroy()

            def work() -> None:
                try:
                    self.events.put(("backup_done", (action, callback())))
                except Exception as exc:
                    self.events.put(("backup_error", (action, exc)))

            thread = threading.Thread(
                target=work,
                daemon=False,
                name=f"backup-{action}",
            )
            self._maintenance_thread = thread
            thread.start()

        def create() -> None:
            destination = filedialog.asksaveasfilename(
                parent=dialog,
                defaultextension=".voice-backup",
                filetypes=[("VOICE Studio backup", "*.voice-backup")],
            )
            if destination:
                include_audio_value = bool(include_audio.get())
                start_operation(
                    "create",
                    lambda: create_backup(
                        self.store,
                        Path(destination),
                        settings_file=settings_path(),
                        include_audio=include_audio_value,
                    ),
                )

        def verify() -> None:
            source = filedialog.askopenfilename(
                parent=dialog,
                filetypes=[("VOICE Studio backup", "*.voice-backup")],
            )
            if source:
                start_operation("verify", lambda: verify_backup(Path(source)))

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
            row=2, column=0, padx=12, pady=12
        )
        ttk.Button(dialog, text=self._t("verify"), command=verify).grid(
            row=2, column=1, padx=6, pady=12
        )
        ttk.Button(dialog, text=self._t("restore"), command=restore).grid(
            row=2, column=2, padx=12, pady=12
        )

    def _queue_restore(self, source: Path, start_operation: Any) -> bool:
        if not self._confirm_editor_transition():
            return False
        self.job_controller.close()
        start_operation(
            "restore",
            lambda: restore_backup(
                source,
                data_dir(),
                settings_target=settings_path(),
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

    def _restart_runtime(self) -> None:
        self.store = LocalStore(data_dir())
        self.job_controller = TranscriptionJobController(self.store, cache_dir())

    def _reload_after_restore(self) -> None:
        self._restart_runtime()
        self.settings = load_settings()
        self._clear_current_transcript_view()
        self._refresh_history()
        self._refresh_ui_text()
        self._start_hotkey()

    def _close(self) -> None:
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
        if self.hotkey:
            self.hotkey.stop()
        try:
            self.recorder.cancel()
        except Exception as exc:
            self._report_recorder_error(exc)
        self._active_recording_path = None
        self._cancel_event.set()
        self.job_controller.close()
        for path in list(self.__dict__.get("_pending_microphone_files", set())):
            self._cleanup_temp(path)
        self._report_recording_residues()
        self.destroy()


def main() -> None:
    VoiceStudioApp().mainloop()


if __name__ == "__main__":
    main()
