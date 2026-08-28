from __future__ import annotations

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


def test_unknown_translation_key_fails_fast() -> None:
    with pytest.raises(KeyError):
        translate("uk", "missing-key")


def test_transcript_language_can_be_formatted_without_conflicting_with_locale() -> None:
    assert translate(
        "en", "transcription_done", language="uk", segments=3, rtf=""
    ) == "Done — uk, 3 segment(s)"


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
