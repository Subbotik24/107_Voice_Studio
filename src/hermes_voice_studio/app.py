from __future__ import annotations

import json
import queue
import tempfile
import threading
import tkinter as tk
from dataclasses import replace
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .backup import create_backup, restore_backup, verify_backup
from .cloud_cleanup import propose_cleanup
from .cloud_secrets import (
    delete_openai_api_key,
    get_openai_api_key,
    openai_key_status,
    set_openai_api_key,
)
from .config import cache_dir, data_dir, load_settings, save_settings, settings_path
from .dictionary import TerminologyDictionary
from .exporters import export_transcript
from .hotkey import GlobalHotkey, hotkey_from_tk_event
from .jobs import JobCancelled, TranscriptionJobController
from .model_catalog import ModelCatalog
from .models import Settings, Transcript
from .recorder import AudioRecorder
from .storage import LocalStore

MEDIA_FILETYPES = [
    ("Audio/video", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac *.mp4 *.mov *.mkv *.webm"),
    ("All files", "*.*"),
]


class HermesVoiceApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Hermes Voice Studio")
        self.geometry("1080x720")
        self.minsize(860, 580)
        try:
            self.settings = load_settings()
            settings_error = ""
        except ValueError as exc:
            self.settings = Settings()
            settings_error = str(exc)
        self.store = LocalStore(data_dir())
        self.job_controller = TranscriptionJobController(self.store, cache_dir())
        self.recorder = AudioRecorder()
        self.hotkey: GlobalHotkey | None = None
        self.current: Transcript | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._history_items: list[Transcript] = []
        self._busy = False
        self._continuous_recording = False
        self._cancel_event = threading.Event()
        self._build_ui()
        self._refresh_history()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._start_hotkey()
        self.after(400, self._first_run_model_prompt)
        if settings_error:
            self.after(
                250,
                lambda: messagebox.showwarning(
                    "Налаштування",
                    "Файл налаштувань пошкоджений. Використано безпечні "
                    f"значення за замовчуванням.\n\n{settings_error}",
                ),
            )

    def _first_run_model_prompt(self) -> None:
        """Offer, but never start, a local model download on an empty profile."""

        if self.settings.offline_only or ModelCatalog(self.store.models).list():
            return
        if messagebox.askyesno(
            "Початкове налаштування local AI",
            "Local — Tiny (starter) рекомендовано для першого запуску.\n\n"
            "Виберіть «Так», щоб відкрити Models і явно підтвердити встановлення. "
            "Small доступна там як профіль кращої якості; також можна імпортувати модель offline.",
            parent=self,
        ):
            self._models_dialog()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Record.TButton", font=("", 11, "bold"), padding=(12, 8))

        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="Hermes Voice Studio", font=("", 16, "bold")).pack(side="left")
        self.engine_label = tk.StringVar()
        ttk.Label(header, textvariable=self.engine_label).pack(side="left", padx=14)
        self.settings_button = ttk.Button(
            header, text="Налаштування", command=self._settings_dialog
        )
        self.settings_button.pack(side="right")
        self.models_button = ttk.Button(header, text="Моделі", command=self._models_dialog)
        self.models_button.pack(side="right", padx=(0, 6))
        self.backup_button = ttk.Button(header, text="Резервна копія", command=self._backup_dialog)
        self.backup_button.pack(side="right", padx=(0, 6))

        toolbar = ttk.Frame(self, padding=(12, 0, 12, 10))
        toolbar.pack(fill="x")
        self.record_button = ttk.Button(
            toolbar,
            text="● Утримуйте для запису",
            style="Record.TButton",
        )
        self.record_button.pack(side="left")
        self.record_button.bind("<ButtonPress-1>", lambda _event: self._record_start())
        self.record_button.bind("<ButtonRelease-1>", lambda _event: self._record_stop())
        self.continuous_record_button = ttk.Button(
            toolbar,
            text="● Постійний запис",
            command=self._toggle_continuous_recording,
        )
        self.continuous_record_button.pack(side="left", padx=(0, 8))
        self.file_button = ttk.Button(
            toolbar,
            text="Транскрибувати файл…",
            command=self._choose_file,
        )
        self.file_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(
            toolbar,
            text="Скасувати",
            command=self._cancel_current,
            state="disabled",
        )
        self.cancel_button.pack(side="left")
        ttk.Button(toolbar, text="Копіювати текст", command=self._copy_current).pack(side="left")
        self.status = tk.StringVar(value="Готово — дані обробляються локально")
        ttk.Label(toolbar, textvariable=self.status).pack(side="right")

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        history_frame = ttk.Labelframe(paned, text="Історія", padding=8)
        editor_frame = ttk.Labelframe(paned, text="Транскрипт", padding=8)
        paned.add(history_frame, weight=1)
        paned.add(editor_frame, weight=3)

        search_row = ttk.Frame(history_frame)
        search_row.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", lambda _event: self._refresh_history())
        ttk.Button(search_row, text="Пошук", command=self._refresh_history).pack(
            side="left", padx=(5, 0)
        )

        list_frame = ttk.Frame(history_frame)
        list_frame.pack(fill="both", expand=True)
        self.history = tk.Listbox(list_frame, width=34, activestyle="dotbox")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=scrollbar.set)
        self.history.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.history.bind("<<ListboxSelect>>", self._select_history)
        history_actions = ttk.Frame(history_frame)
        history_actions.pack(fill="x", pady=(7, 0))
        self.rename_history_button = ttk.Button(
            history_actions, text="Перейменувати", command=self._rename_selected_history
        )
        self.rename_history_button.pack(side="left")
        self.delete_history_button = ttk.Button(
            history_actions, text="Видалити", command=self._delete_selected_history
        )
        self.delete_history_button.pack(side="right")

        self.notebook = ttk.Notebook(editor_frame)
        self.notebook.pack(fill="both", expand=True)
        corrected_frame = ttk.Frame(self.notebook)
        raw_frame = ttk.Frame(self.notebook)
        details_frame = ttk.Frame(self.notebook)
        self.notebook.add(corrected_frame, text="Виправлений текст")
        self.notebook.add(raw_frame, text="Raw")
        self.notebook.add(details_frame, text="Дані")

        format_bar = ttk.Frame(corrected_frame, padding=(0, 0, 0, 6))
        format_bar.pack(fill="x")
        ttk.Label(format_bar, text="Оформлення").pack(side="left")
        ttk.Button(
            format_bar, text="B", width=3, command=lambda: self._toggle_editor_tag("bold")
        ).pack(side="right", padx=(3, 0))
        ttk.Button(
            format_bar, text="I", width=3, command=lambda: self._toggle_editor_tag("italic")
        ).pack(side="right")
        self.editor = tk.Text(corrected_frame, wrap="word", undo=True, font=("", 12))
        self.editor.pack(fill="both", expand=True)
        self.editor.tag_configure("bold", font=("", 12, "bold"))
        self.editor.tag_configure("italic", font=("", 12, "italic"))
        self.editor.bind("<Return>", self._insert_editor_newline)
        self.editor.bind("<Control-Return>", self._insert_editor_newline)
        self.raw_editor = tk.Text(raw_frame, wrap="word", font=("", 12), state="disabled")
        self.raw_editor.pack(fill="both", expand=True)
        self.details = tk.Text(
            details_frame, wrap="word", font=("TkFixedFont", 10), state="disabled"
        )
        self.details.pack(fill="both", expand=True)

        export_bar = ttk.Frame(editor_frame)
        export_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(export_bar, text="Зберегти правки", command=self._save_edits).pack(side="left")
        self.cleanup_button = ttk.Button(export_bar, text="AI cleanup…", command=self._ai_cleanup)
        self.cleanup_button.pack(side="left", padx=6)
        self.undo_cleanup_button = ttk.Button(
            export_bar, text="Undo AI cleanup", command=self._undo_ai_cleanup
        )
        self.undo_cleanup_button.pack(side="left")
        for fmt in ("TXT", "MD", "JSON", "SRT", "VTT"):
            ttk.Button(
                export_bar,
                text=fmt,
                command=partial(self._export, fmt.lower()),
            ).pack(side="right", padx=2)
        self._update_engine_label()

    def _update_engine_label(self) -> None:
        if self.settings.engine == "hermes-whisper":
            model = (
                Path(self.settings.hermes_bundle).name
                if self.settings.hermes_bundle
                else "bundle не вибрано"
            )
        elif self.settings.engine == "openai-cloud":
            model = self.settings.openai_transcription_model
        else:
            model = self.settings.model
        self.engine_label.set(f"Движок: {self.settings.engine} / {model}")

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
            self.status.set(f"Hotkey недоступний: {exc}. Кнопка запису працює")

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
                    if cleanup:
                        Path(cleanup).unlink(missing_ok=True)
                    self._set_busy(False)
                    self._show_result(transcript, copy=True)
                elif event == "error":
                    error, cleanup = value
                    if cleanup:
                        Path(cleanup).unlink(missing_ok=True)
                    self._set_busy(False)
                    self.status.set("Помилка обробки")
                    messagebox.showerror("Помилка", str(error))
                elif event == "model_progress":
                    downloaded, expected = value
                    percent = min(99, round(downloaded * 100 / max(expected, 1)))
                    self.status.set(f"Завантаження моделі… {percent}%")
                elif event == "model_done":
                    self._set_busy(False)
                    self.status.set(f"Модель встановлено: {value['id']}")
                    messagebox.showinfo("Моделі", f"Модель {value['id']} встановлено.")
                elif event == "model_error":
                    self._set_busy(False)
                    self.status.set("Помилка встановлення моделі")
                    messagebox.showerror("Моделі", str(value))
                elif event == "job_progress":
                    phase, elapsed = value
                    labels = {
                        "importing": "Імпорт керованої копії",
                        "loading": "Завантаження локальної моделі",
                        "transcribing": "Локальне транскрибування",
                        "saving": "Збереження результату",
                        "completed": "Завершено",
                    }
                    self.status.set(f"{labels.get(phase, phase)}… {elapsed:.1f} с")
                elif event == "job_cancelled":
                    cleanup = value
                    if cleanup:
                        Path(cleanup).unlink(missing_ok=True)
                    self._set_busy(False)
                    self.status.set("Задачу скасовано")
                elif event == "backup_done":
                    action, result = value
                    self._set_busy(False)
                    if action == "restore":
                        self.store = LocalStore(data_dir())
                        self.job_controller = TranscriptionJobController(
                            self.store,
                            cache_dir(),
                        )
                        self.settings = load_settings()
                        self._refresh_history()
                        self._update_engine_label()
                        self._start_hotkey()
                        recovery = result.get("recovery") or "не створено"
                        message = (
                            f"Відновлено записів: {result['records']}.\n"
                            f"Попереднє сховище: {recovery}"
                        )
                    elif action == "verify":
                        message = (
                            f"Backup PASS: {result['records']} записів, "
                            f"{result['members']} members."
                        )
                    else:
                        message = (
                            f"Backup створено: {result['path']}\n"
                            f"Записів: {result['records']}, аудіофайлів: "
                            f"{result['audio_files']}"
                        )
                    self.status.set("Операцію резервної копії завершено")
                    messagebox.showinfo("Резервна копія", message)
                elif event == "backup_error":
                    self._set_busy(False)
                    self.status.set("Помилка резервної копії")
                    messagebox.showerror("Резервна копія", str(value))
                elif event == "cleanup_proposal":
                    transcript, proposal = value
                    self._set_busy(False)
                    preview = (
                        "До:\n"
                        f"{transcript.corrected_text[:1000]}\n\nПісля:\n"
                        f"{proposal.corrected_text[:1000]}\n\n"
                        "Застосувати цей AI cleanup?"
                    )
                    if messagebox.askyesno("AI cleanup preview", preview, parent=self):
                        updated = self.store.apply_ai_cleanup(
                            transcript.id,
                            proposal.to_dict(),
                            provider="openai",
                            model=self.settings.openai_cleanup_model,
                        )
                        self._show_result(updated, refresh=True)
                        self.status.set("AI cleanup застосовано; raw text не змінено")
                    else:
                        self.status.set("AI cleanup proposal не застосовано")
                elif event == "cleanup_error":
                    self._set_busy(False)
                    self.status.set("Помилка AI cleanup")
                    messagebox.showerror("AI cleanup", str(value), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _record_start(self, *, continuous: bool = False) -> None:
        if self._busy or self.recorder.recording:
            return
        try:
            self.recorder.start()
            self._continuous_recording = continuous
            if continuous:
                self.continuous_record_button.configure(text="■ Зупинити запис")
                self.status.set("Постійний запис… натисніть «Зупинити запис», коли завершите")
            else:
                self.status.set("Запис… відпустіть кнопку або hotkey")
        except Exception as exc:
            messagebox.showerror("Мікрофон", str(exc))

    def _toggle_continuous_recording(self) -> None:
        if self.recorder.recording:
            if self._continuous_recording:
                self._record_stop(force=True)
            return
        self._record_start(continuous=True)

    def _record_stop(self, *, force: bool = False) -> None:
        if not self.recorder.recording:
            return
        if self._continuous_recording and not force:
            return
        handle = tempfile.NamedTemporaryFile(prefix="hermes-voice-", suffix=".wav", delete=False)
        handle.close()
        target = Path(handle.name)
        try:
            self.recorder.stop(target)
            self._continuous_recording = False
            self.continuous_record_button.configure(text="● Постійний запис")
            self._process(target, cleanup=True)
        except Exception as exc:
            self._continuous_recording = False
            self.continuous_record_button.configure(text="● Постійний запис")
            target.unlink(missing_ok=True)
            messagebox.showerror("Мікрофон", str(exc))

    def _choose_file(self) -> None:
        if self._busy:
            return
        name = filedialog.askopenfilename(filetypes=MEDIA_FILETYPES)
        if name:
            self._process(Path(name), cleanup=False)

    def _process(self, source: Path, *, cleanup: bool) -> None:
        if self.settings.engine == "openai-cloud":
            if self.settings.offline_only:
                messagebox.showerror("Cloud STT", "Offline-only режим блокує OpenAI.")
                return
            try:
                from .engines.openai_cloud import OpenAICloudEngine

                OpenAICloudEngine.validate_upload(source)
            except Exception as exc:
                messagebox.showerror("Cloud STT", str(exc), parent=self)
                return
            if not messagebox.askyesno(
                "Передати аудіо OpenAI?",
                f"Provider: OpenAI\nФайл: {source.name}\n"
                f"Розмір: {source.stat().st_size / 1_000_000:.1f} MB\n\n"
                "Аудіо буде передано третій стороні для транскрипції. Продовжити?",
                parent=self,
            ):
                return
        self._set_busy(True)
        self._cancel_event.clear()
        self.status.set("Підготовка локального транскрибування…")

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

    def _cancel_current(self) -> None:
        if self._busy:
            self._cancel_event.set()
            self.status.set("Скасування…")

    def _show_result(
        self, transcript: Transcript, *, copy: bool = False, refresh: bool = True
    ) -> None:
        self.current = transcript
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", transcript.corrected_text)
        self._apply_editor_formatting(transcript.metadata.get("editor_formatting", {}))
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
            f"Готово — {transcript.language}, {len(transcript.segments)} сегментів{rtf}"
        )
        if refresh:
            self._refresh_history(select_id=transcript.id)
        if copy and self.settings.auto_copy:
            self._copy_to_clipboard(transcript.corrected_text)

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
            self.status.set("Спочатку виділіть текст для оформлення")
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
            messagebox.showinfo("Буфер обміну", "Спочатку створіть або виберіть транскрипт.")
            return
        self._copy_to_clipboard(self.editor.get("1.0", "end-1c"))
        self.status.set("Текст скопійовано")

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
            self.history.selection_set(selected_index)
            self.history.see(selected_index)

    def _select_history(self, _event: Any = None) -> None:
        selection = self.history.curselection()
        if selection:
            self._show_result(self._history_items[selection[0]], copy=False, refresh=False)

    def _selected_history_item(self) -> Transcript | None:
        selection = self.history.curselection()
        return self._history_items[selection[0]] if selection else None

    def _rename_selected_history(self) -> None:
        transcript = self._selected_history_item()
        if transcript is None:
            messagebox.showinfo("Історія", "Спочатку виберіть запис в історії.")
            return
        name = simpledialog.askstring(
            "Перейменувати запис",
            "Нова назва для історії та експорту (без шляху):",
            initialvalue=transcript.source_name,
            parent=self,
        )
        if name is None:
            return
        try:
            renamed = self.store.rename_source_name(transcript.id, name)
        except Exception as exc:
            messagebox.showerror("Перейменування", str(exc), parent=self)
            return
        self._show_result(renamed, refresh=True)
        self.status.set("Назву запису в історії змінено; аудіофайл не перейменовувався")

    def _delete_selected_history(self) -> None:
        transcript = self._selected_history_item()
        if transcript is None:
            messagebox.showinfo("Історія", "Спочатку виберіть запис в історії.")
            return
        if not messagebox.askyesno(
            "Видалити запис",
            "Видалити цей транскрипт з історії? Оригінальний файл користувача не буде видалено.",
            parent=self,
        ):
            return
        delete_audio = False
        if transcript.audio_retained:
            choice = messagebox.askyesnocancel(
                "Керована копія аудіо",
                "Так — також видалити локальну керовану копію аудіо.\n"
                "Ні — залишити її локально.\n"
                "Скасувати — не видаляти запис.",
                parent=self,
            )
            if choice is None:
                return
            delete_audio = bool(choice)
        self.store.delete(transcript.id, delete_audio=delete_audio)
        if self.current and self.current.id == transcript.id:
            self.current = None
            self.editor.delete("1.0", "end")
            self._set_readonly_text(self.raw_editor, "")
            self._set_readonly_text(self.details, "")
        self._refresh_history()
        self.status.set("Запис видалено з історії; оригінальний файл не змінено")

    def _save_edits(self) -> None:
        if not self.current:
            return
        self.current = self.store.update_corrected_text(
            self.current.id,
            self.editor.get("1.0", "end-1c"),
        )
        self.current = self.store.update_editor_formatting(
            self.current.id,
            self._editor_formatting(),
        )
        self.status.set("Правки збережено")

    def _ai_cleanup(self) -> None:
        if not self.current:
            messagebox.showinfo("AI cleanup", "Спочатку виберіть транскрипт.", parent=self)
            return
        if self.settings.offline_only:
            messagebox.showerror("AI cleanup", "Offline-only режим блокує OpenAI.", parent=self)
            return
        if not messagebox.askyesno(
            "Передати текст OpenAI?",
            "До OpenAI буде передано лише corrected text і сегменти, не raw text. "
            "Перед застосуванням ви побачите proposal. Продовжити?",
            parent=self,
        ):
            return
        self._save_edits()
        transcript = self.current
        self._set_busy(True)

        def work() -> None:
            try:
                proposal = propose_cleanup(transcript, model=self.settings.openai_cleanup_model)
                self.events.put(("cleanup_proposal", (transcript, proposal)))
            except Exception as exc:
                self.events.put(("cleanup_error", exc))

        threading.Thread(target=work, daemon=True, name="ai-cleanup").start()

    def _undo_ai_cleanup(self) -> None:
        if not self.current:
            return
        try:
            self._show_result(self.store.undo_last_ai_cleanup(self.current.id), refresh=True)
            self.status.set("Останнє AI cleanup скасовано")
        except Exception as exc:
            messagebox.showerror("AI cleanup", str(exc), parent=self)

    def _export(self, fmt: str) -> None:
        if not self.current:
            messagebox.showinfo("Експорт", "Спочатку створіть або виберіть транскрипт.")
            return
        self._save_edits()
        destination = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            initialfile=f"{Path(self.current.source_name).stem}.{fmt}",
        )
        if destination:
            export_transcript(self.current, fmt, Path(destination))
            self.status.set(f"Експортовано: {Path(destination).name}")

    def _close_settings_dialog(self, dialog: tk.Toplevel) -> None:
        """Finish Tk teardown before starting the native keyboard listener."""

        dialog.grab_release()
        dialog.destroy()
        self.after_idle(self._start_hotkey)

    def _settings_dialog(self) -> None:
        # Do not let the currently configured global shortcut start a recording
        # while the user is choosing a new shortcut in this modal dialog.
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        dialog = tk.Toplevel(self)
        dialog.title("Налаштування Hermes Voice Studio")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        variables: dict[str, tk.Variable] = {
            "engine": tk.StringVar(value=self.settings.engine),
            "language": tk.StringVar(value=self.settings.language),
            "model": tk.StringVar(value=self.settings.model),
            "hermes_bundle": tk.StringVar(value=self.settings.hermes_bundle),
            "device": tk.StringVar(value=self.settings.device),
            "compute_type": tk.StringVar(value=self.settings.compute_type),
            "retention": tk.StringVar(value=self.settings.retention),
            "dictionary_path": tk.StringVar(value=self.settings.dictionary_path),
            "hotkey": tk.StringVar(value=self.settings.hotkey),
            "auto_copy": tk.BooleanVar(value=self.settings.auto_copy),
            "offline_only": tk.BooleanVar(value=self.settings.offline_only),
            "openai_transcription_model": tk.StringVar(
                value=self.settings.openai_transcription_model
            ),
            "openai_cleanup_model": tk.StringVar(value=self.settings.openai_cleanup_model),
        }

        def row_label(row: int, text: str) -> None:
            ttk.Label(dialog, text=text).grid(row=row, column=0, sticky="w", padx=10, pady=5)

        row_label(0, "Движок")
        ttk.Combobox(
            dialog,
            textvariable=variables["engine"],
            values=("faster-whisper", "hermes-whisper", "openai-cloud"),
            state="readonly",
            width=42,
        ).grid(row=0, column=1, columnspan=2, sticky="ew", padx=10, pady=5)

        row_label(1, "Мова")
        ttk.Combobox(
            dialog,
            textvariable=variables["language"],
            values=("auto", "uk", "cs", "en"),
            state="readonly",
            width=42,
        ).grid(row=1, column=1, columnspan=2, sticky="ew", padx=10, pady=5)

        row_label(2, "faster-whisper model")
        ttk.Entry(dialog, textvariable=variables["model"], width=45).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=10, pady=5
        )

        row_label(3, "Hermes .hws")
        ttk.Entry(dialog, textvariable=variables["hermes_bundle"], width=45).grid(
            row=3, column=1, sticky="ew", padx=(10, 4), pady=5
        )

        def choose_bundle() -> None:
            path = filedialog.askopenfilename(parent=dialog, filetypes=[("Hermes model", "*.hws")])
            if path:
                variables["hermes_bundle"].set(path)

        ttk.Button(dialog, text="Огляд…", command=choose_bundle).grid(
            row=3, column=2, sticky="ew", padx=(4, 10), pady=5
        )

        row_label(4, "Device")
        ttk.Entry(dialog, textvariable=variables["device"], width=45).grid(
            row=4, column=1, columnspan=2, sticky="ew", padx=10, pady=5
        )
        row_label(5, "Compute type")
        ttk.Entry(dialog, textvariable=variables["compute_type"], width=45).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=10, pady=5
        )
        row_label(6, "Зберігання аудіо")
        ttk.Combobox(
            dialog,
            textvariable=variables["retention"],
            values=("keep", "delete_after_transcription"),
            state="readonly",
            width=42,
        ).grid(row=6, column=1, columnspan=2, sticky="ew", padx=10, pady=5)

        row_label(7, "Словник JSON")
        ttk.Entry(dialog, textvariable=variables["dictionary_path"], width=45).grid(
            row=7, column=1, sticky="ew", padx=(10, 4), pady=5
        )

        def choose_dictionary() -> None:
            path = filedialog.askopenfilename(parent=dialog, filetypes=[("JSON", "*.json")])
            if path:
                variables["dictionary_path"].set(path)

        ttk.Button(dialog, text="Огляд…", command=choose_dictionary).grid(
            row=7, column=2, sticky="ew", padx=(4, 10), pady=5
        )
        row_label(8, "Hotkey")
        ttk.Entry(dialog, textvariable=variables["hotkey"], width=34).grid(
            row=8, column=1, sticky="ew", padx=(10, 4), pady=5
        )
        capture_active = False

        def capture_hotkey(event: Any) -> str | None:
            nonlocal capture_active
            if not capture_active:
                return None
            if event.keysym == "Escape":
                capture_active = False
                info.set("Запам'ятовування hotkey скасовано.")
                return "break"
            captured = hotkey_from_tk_event(event)
            if captured:
                variables["hotkey"].set(captured)
                capture_active = False
                info.set(f"Hotkey запам'ятовано: {captured}. Натисніть «Зберегти».")
                return "break"
            return None

        def begin_hotkey_capture() -> None:
            nonlocal capture_active
            capture_active = True
            info.set("Натисніть потрібне поєднання, наприклад Ctrl+Alt+Space. Esc — скасувати.")
            dialog.focus_set()

        dialog.bind("<KeyPress>", capture_hotkey)
        ttk.Button(dialog, text="Запам'ятати клавішу…", command=begin_hotkey_capture).grid(
            row=8, column=2, sticky="ew", padx=(4, 10), pady=5
        )
        ttk.Checkbutton(
            dialog,
            text=(
                "Автокопіювання нового результату (може потрапити в історію/"
                "синхронізацію буфера ОС)"
            ),
            variable=variables["auto_copy"],
        ).grid(row=9, column=1, columnspan=2, sticky="w", padx=10, pady=5)
        ttk.Checkbutton(
            dialog,
            text="Offline-only: заборонити завантаження моделей",
            variable=variables["offline_only"],
        ).grid(row=10, column=1, columnspan=2, sticky="w", padx=10, pady=5)

        row_label(11, "OpenAI STT model")
        ttk.Entry(dialog, textvariable=variables["openai_transcription_model"], width=45).grid(
            row=11, column=1, columnspan=2, sticky="ew", padx=10, pady=5
        )
        row_label(12, "OpenAI cleanup model")
        ttk.Entry(dialog, textvariable=variables["openai_cleanup_model"], width=45).grid(
            row=12, column=1, columnspan=2, sticky="ew", padx=10, pady=5
        )

        def set_cloud_key() -> None:
            value = simpledialog.askstring(
                "OpenAI API key",
                "Ключ збережеться лише в OS Keychain:",
                show="*",
                parent=dialog,
            )
            if value is None:
                return
            try:
                set_openai_api_key(value)
                info.set("OpenAI key збережено в OS Keychain.")
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=dialog)

        def delete_cloud_key() -> None:
            try:
                removed = delete_openai_api_key()
                info.set("OpenAI key видалено." if removed else "OpenAI key не знайдено.")
            except Exception as exc:
                messagebox.showerror("OpenAI", str(exc), parent=dialog)

        def test_cloud_key() -> None:
            try:
                from openai import OpenAI

                OpenAI(api_key=get_openai_api_key(), timeout=30.0, max_retries=0).models.list()
                info.set("OpenAI connection: PASS")
            except Exception as exc:
                info.set(f"OpenAI connection: FAIL ({type(exc).__name__})")

        def key_status() -> None:
            status = openai_key_status()
            info.set(f"OpenAI key: {status.get('source', 'unknown')}")

        key_row = ttk.Frame(dialog)
        key_row.grid(row=13, column=1, columnspan=2, sticky="w", padx=10, pady=5)
        ttk.Button(key_row, text="Set / Replace OpenAI key", command=set_cloud_key).pack(
            side="left"
        )
        ttk.Button(key_row, text="Delete key", command=delete_cloud_key).pack(side="left", padx=5)
        ttk.Button(key_row, text="Status", command=key_status).pack(side="left", padx=5)
        ttk.Button(key_row, text="Test connection", command=test_cloud_key).pack(
            side="left", padx=5
        )

        info = tk.StringVar(value="Hermes 0.1 потребує навченого та перевіреного .hws bundle.")
        ttk.Label(dialog, textvariable=info, wraplength=470).grid(
            row=14, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4)
        )

        def verify_bundle() -> None:
            from hermes_whisper.bundle import verify_model_bundle

            path = str(variables["hermes_bundle"].get()).strip()
            if not path:
                info.set("Bundle не вибрано.")
                return
            try:
                result = verify_model_bundle(path)
                info.set(
                    f"PASS: {result['model_name']}, step {result['checkpoint_step']}, "
                    f"SHA-256 {result['sha256'][:16]}…"
                )
            except Exception as exc:
                info.set(f"FAIL: {exc}")

        ttk.Button(dialog, text="Перевірити .hws", command=verify_bundle).grid(
            row=15, column=0, sticky="w", padx=10, pady=10
        )

        def close_without_saving() -> None:
            self._close_settings_dialog(dialog)

        def save() -> None:
            try:
                updated = replace(
                    self.settings,
                    engine=str(variables["engine"].get()).strip(),
                    language=str(variables["language"].get()).strip(),
                    model=str(variables["model"].get()).strip(),
                    hermes_bundle=str(variables["hermes_bundle"].get()).strip(),
                    device=str(variables["device"].get()).strip(),
                    compute_type=str(variables["compute_type"].get()).strip(),
                    retention=str(variables["retention"].get()).strip(),
                    dictionary_path=str(variables["dictionary_path"].get()).strip(),
                    hotkey=str(variables["hotkey"].get()).strip(),
                    auto_copy=bool(variables["auto_copy"].get()),
                    offline_only=bool(variables["offline_only"].get()),
                    openai_transcription_model=str(
                        variables["openai_transcription_model"].get()
                    ).strip(),
                    openai_cleanup_model=str(variables["openai_cleanup_model"].get()).strip(),
                )
                updated.validate()
                if updated.dictionary_path:
                    TerminologyDictionary.load(updated.dictionary_path)
                self.settings = updated
                save_settings(updated)
            except Exception as exc:
                messagebox.showerror("Налаштування", str(exc), parent=dialog)
                return
            self.job_controller.close()
            self._update_engine_label()
            self.status.set("Налаштування збережено")
            self._close_settings_dialog(dialog)

        ttk.Button(dialog, text="Скасувати", command=close_without_saving).grid(
            row=15, column=1, sticky="e", padx=5, pady=10
        )
        ttk.Button(dialog, text="Зберегти", command=save).grid(
            row=15, column=2, sticky="e", padx=10, pady=10
        )
        dialog.protocol("WM_DELETE_WINDOW", close_without_saving)

    def _models_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Локальні faster-whisper моделі")
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
            source = filedialog.askdirectory(parent=dialog, title="Каталог faster-whisper")
            if not source:
                return
            model_id = simpledialog.askstring(
                "Model ID",
                "Локальний ідентифікатор моделі:",
                parent=dialog,
            )
            if not model_id:
                return
            try:
                catalog.import_local(model_id, Path(source))
                refresh()
            except Exception as exc:
                messagebox.showerror("Моделі", str(exc), parent=dialog)

        def download() -> None:
            if self.settings.offline_only:
                messagebox.showerror(
                    "Моделі",
                    "Offline-only режим забороняє завантаження.",
                    parent=dialog,
                )
                return
            model_id = simpledialog.askstring(
                "Завантажити модель",
                "Model ID. tiny — starter profile за замовчуванням. small — багатомовна "
                "модель кращої якості (близько 520 MB).",
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
                messagebox.showinfo("Моделі", f"{model_id}: PASS", parent=dialog)
            except Exception as exc:
                messagebox.showerror("Моделі", str(exc), parent=dialog)

        def remove() -> None:
            model_id = selected_id()
            if not model_id:
                return
            if not messagebox.askyesno(
                "Видалити модель",
                f"Видалити керовану копію {model_id}?",
                parent=dialog,
            ):
                return
            try:
                catalog.remove(model_id, confirmed=True)
                refresh()
            except Exception as exc:
                messagebox.showerror("Моделі", str(exc), parent=dialog)

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Імпортувати локальну", command=import_local).pack(side="left")
        ttk.Button(buttons, text="Завантажити", command=download).pack(side="left", padx=5)
        ttk.Button(buttons, text="Перевірити", command=verify).pack(side="left")
        ttk.Button(buttons, text="Видалити", command=remove).pack(side="right")
        refresh()

    def _backup_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Резервна копія")
        dialog.transient(self)
        dialog.resizable(False, False)
        include_audio = tk.BooleanVar(value=True)
        ttk.Label(
            dialog,
            text=(
                "Backup містить історію, налаштування, словник і, за вибором, "
                "лише керовані копії аудіо. Зовнішні originals та моделі не включаються."
            ),
            wraplength=520,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 8))
        ttk.Checkbutton(
            dialog,
            text="Включити керовані копії аудіо",
            variable=include_audio,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=6)

        def start_operation(action: str, callback: Any) -> None:
            self._set_busy(True)
            self.status.set("Обробка резервної копії…")
            dialog.destroy()

            def work() -> None:
                try:
                    self.events.put(("backup_done", (action, callback())))
                except Exception as exc:
                    self.events.put(("backup_error", exc))

            threading.Thread(
                target=work,
                daemon=True,
                name=f"backup-{action}",
            ).start()

        def create() -> None:
            destination = filedialog.asksaveasfilename(
                parent=dialog,
                defaultextension=".hvs-backup",
                filetypes=[("Hermes Voice backup", "*.hvs-backup")],
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
                filetypes=[("Hermes Voice backup", "*.hvs-backup")],
            )
            if source:
                start_operation("verify", lambda: verify_backup(Path(source)))

        def restore() -> None:
            source = filedialog.askopenfilename(
                parent=dialog,
                filetypes=[("Hermes Voice backup", "*.hvs-backup")],
            )
            if not source:
                return
            if not messagebox.askyesno(
                "Відновити backup",
                (
                    "Архів буде повністю перевірено. Поточне сховище буде "
                    "переміщено до recovery directory, а не видалено. Продовжити?"
                ),
                parent=dialog,
            ):
                return
            self.job_controller.close()
            start_operation(
                "restore",
                lambda: restore_backup(
                    Path(source),
                    data_dir(),
                    settings_target=settings_path(),
                ),
            )

        ttk.Button(dialog, text="Створити…", command=create).grid(row=2, column=0, padx=12, pady=12)
        ttk.Button(dialog, text="Перевірити…", command=verify).grid(
            row=2, column=1, padx=6, pady=12
        )
        ttk.Button(dialog, text="Відновити…", command=restore).grid(
            row=2, column=2, padx=12, pady=12
        )

    def _close(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        if self.recorder.recording:
            self.recorder.cancel()
        self._cancel_event.set()
        self.job_controller.close()
        self.destroy()


def main() -> None:
    HermesVoiceApp().mainloop()


if __name__ == "__main__":
    main()
