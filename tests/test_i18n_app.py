from __future__ import annotations

from string import Formatter

import pytest

from voice_studio.i18n import _CATALOGS, UI_LANGUAGE_CHOICES, translate
from voice_studio.models import Settings


def test_interface_language_is_independent_from_transcription_language() -> None:
    settings = Settings(language="cs", ui_language="en")

    settings.validate()

    assert settings.language == "cs"
    assert settings.ui_language == "en"


def test_interface_language_defaults_to_ukrainian_and_round_trips() -> None:
    assert Settings().ui_language == "uk"
    assert Settings.from_dict(Settings(ui_language="cs").to_dict()).ui_language == "cs"


def test_interface_language_rejects_non_ui_language_values() -> None:
    with pytest.raises(ValueError, match="interface language"):
        Settings(ui_language="auto").validate()


def test_all_supported_interface_languages_have_the_same_catalog() -> None:
    assert UI_LANGUAGE_CHOICES == (
        ("uk", "Українська"),
        ("cs", "Čeština"),
        ("en", "English"),
    )
    assert translate("uk", "settings") == "Налаштування"
    assert translate("cs", "settings") == "Nastavení"
    assert translate("en", "settings") == "Settings"


def test_help_navigation_is_localized_for_every_interface_language() -> None:
    assert translate("uk", "help") == "Довідка"
    assert translate("cs", "help") == "Nápověda"
    assert translate("en", "help") == "Help"
    assert translate("uk", "help_search") == "Пошук у довідці"
    assert translate("uk", "help_no_results") == "Нічого не знайдено."


def test_help_intro_describes_the_current_localized_manual() -> None:
    assert "canonical docs/help" in translate("uk", "help_intro")
    assert "kanonických docs/help" in translate("cs", "help_intro")
    assert "canonical docs/help" in translate("en", "help_intro")


@pytest.mark.parametrize(
    ("language", "expected_detail"),
    [
        ("uk", "не зупинився"),
        ("cs", "nezastavil"),
        ("en", "did not stop"),
    ],
)
def test_hotkey_stop_retry_detail_is_localized(language: str, expected_detail: str) -> None:
    detail = translate(language, "hotkey_stop_retry")

    assert expected_detail in detail
    assert detail in translate(language, "hotkey_unavailable", error=detail)


@pytest.mark.parametrize("language", ["uk", "cs", "en"])
def test_shutdown_residue_is_localized_and_accepts_worker_names(language: str) -> None:
    message = translate(language, "shutdown_residue", workers="transcription")

    assert "transcription" in message
    assert message != "shutdown_residue"


@pytest.mark.parametrize("language", ["uk", "cs", "en"])
def test_profile_cards_are_localized_for_every_interface_language(language: str) -> None:
    for key in (
        "profiles_settings",
        "profile_ollama_title",
        "profile_ollama_description",
        "profile_whisper_title",
        "profile_whisper_description",
        "profile_openai_title",
        "profile_openai_description",
        "use_profile",
        "active_profile",
        "ollama_checking",
    ):
        assert translate(language, key).strip()


def test_central_page_labels_and_placeholders_are_localized_everywhere() -> None:
    for language, _label in UI_LANGUAGE_CHOICES:
        for key in (
            "dashboard",
            "dictionary",
            "dashboard_title",
            "dashboard_recent",
            "dashboard_recent_empty",
            "dictionary_placeholder_title",
            "dictionary_placeholder_detail",
        ):
            assert translate(language, key).strip()


@pytest.mark.parametrize("language", ["uk", "cs", "en"])
def test_dictionary_copy_explains_the_local_whisper_hint_limitation(language: str) -> None:
    detail = translate(language, "dictionary_detail")

    assert "Whisper" in detail
    assert "accuracy" in detail.lower() or "точності" in detail or "přesnost" in detail


def test_unknown_translation_key_fails_fast() -> None:
    with pytest.raises(KeyError):
        translate("uk", "missing-key")


def test_transcript_language_can_be_formatted_without_conflicting_with_locale() -> None:
    assert (
        translate("en", "transcription_done", language="uk", segments=3, rtf="")
        == "Done — uk, 3 segment(s)"
    )


def test_every_catalog_carries_exactly_the_same_keys() -> None:
    """The import-time contract raises; this pins the invariant it protects."""

    uk, cs, en = (set(_CATALOGS[code]) for code, _label in UI_LANGUAGE_CHOICES)

    assert uk == cs == en


def test_interrupted_restore_outcomes_are_localized_everywhere() -> None:
    for code, _label in UI_LANGUAGE_CHOICES:
        assert translate(code, "restore_recovered", records=2)
        assert translate(code, "restore_rolled_back")
        assert translate(code, "restore_staging_discarded")
        assert translate(code, "restore_recovery_failed", error="boom")


def test_model_catalog_messages_exist_in_every_locale() -> None:
    required = {
        "model_catalog_repaired",
        "model_catalog_attention",
        "model_catalog_rebuilt",
        "model_catalog_repair_failed",
    }
    for catalog in _CATALOGS.values():
        assert required <= set(catalog)

    for language, _label in UI_LANGUAGE_CHOICES:
        assert translate(language, "model_catalog_repaired", adopted="tiny", dropped="missing")
        assert translate(language, "model_catalog_attention", details="broken")
        assert translate(language, "model_catalog_rebuilt", path="catalog.json.corrupt")
        assert translate(language, "model_catalog_repair_failed", error="disk")


def test_model_catalog_messages_use_exact_format_variables_in_every_locale() -> None:
    expected = {
        "model_catalog_repaired": {"adopted", "dropped"},
        "model_catalog_attention": {"details"},
        "model_catalog_rebuilt": {"path"},
        "model_catalog_repair_failed": {"error"},
    }
    formatter = Formatter()
    for catalog in _CATALOGS.values():
        for key, fields in expected.items():
            actual = {
                field
                for _literal, field, _format_spec, _conversion in formatter.parse(catalog[key])
                if field is not None
            }
            assert actual == fields
