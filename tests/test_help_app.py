from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_studio.app import VoiceStudioApp
from voice_studio.help_content import (
    help_anchor,
    load_help_topics,
    parse_markdown,
    resolve_help_asset,
    resolve_help_root,
    search_help_topics,
    split_help_target,
    validate_help_tree,
)


def _write_help_tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "help-index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "language": "uk",
                "topics": [
                    {"slug": "quick-start", "title": "Швидкий старт", "file": "quick.md"},
                    {"slug": "workflows", "title": "Основні сценарії", "file": "work.md"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "quick.md").write_text(
        "# Швидкий старт\n\nОберіть **Транскрибувати файл…**.\n",
        encoding="utf-8",
    )
    (root / "work.md").write_text(
        "# Сценарії\n\nЛокальна Ollama виправляє редагований текст.\n",
        encoding="utf-8",
    )
    return root


def test_help_catalog_loads_manifest_order_and_canonical_markdown(tmp_path: Path) -> None:
    root = _write_help_tree(tmp_path / "help")

    topics = load_help_topics(root)

    assert [(topic.slug, topic.title) for topic in topics] == [
        ("quick-start", "Швидкий старт"),
        ("workflows", "Основні сценарії"),
    ]
    assert topics[0].source_path == root / "quick.md"
    assert "Транскрибувати файл" in topics[0].markdown


def test_help_catalog_rejects_topic_outside_canonical_root(tmp_path: Path) -> None:
    root = _write_help_tree(tmp_path / "help")
    manifest = json.loads((root / "help-index.json").read_text(encoding="utf-8"))
    manifest["topics"][0]["file"] = "../private.md"
    (root / "help-index.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "private.md").write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the help directory"):
        load_help_topics(root)


def test_help_search_matches_titles_and_body_without_case_sensitivity(tmp_path: Path) -> None:
    topics = load_help_topics(_write_help_tree(tmp_path / "help"))

    assert [topic.slug for topic in search_help_topics(topics, "ollama")] == ["workflows"]
    assert [topic.slug for topic in search_help_topics(topics, "ШВИДКИЙ")] == ["quick-start"]
    assert search_help_topics(topics, "діаризація") == ()
    assert search_help_topics(topics, "  ") == topics


def test_markdown_parser_emits_readable_blocks_for_supported_help_content() -> None:
    markdown = """# Заголовок

Абзац із **важливою** дією та `кодом`.

- Перший пункт
1. Перший крок

```text
voice-studio --help
```

![Головне вікно](assets/main-window.png)

[Наступний розділ](workflows.md#export)

| Поле | Значення |
|---|---|
| Мова | uk |
"""

    blocks = parse_markdown(markdown)

    assert [block.kind for block in blocks] == [
        "heading",
        "paragraph",
        "bullet",
        "numbered",
        "code",
        "image",
        "link",
        "table",
    ]
    assert blocks[0].level == 1
    assert blocks[1].text == "Абзац із важливою дією та кодом."
    assert blocks[3].text == "1. Перший крок"
    assert blocks[4].text == "voice-studio --help"
    assert blocks[5].text == "Головне вікно"
    assert blocks[5].target == "assets/main-window.png"
    assert "| Мова | uk |" in blocks[7].text


def test_help_asset_resolution_stays_inside_help_root(tmp_path: Path) -> None:
    root = _write_help_tree(tmp_path / "help")
    assets = root / "assets"
    assets.mkdir()
    image = assets / "main-window.png"
    image.write_bytes(b"png")

    assert resolve_help_asset(root, root / "quick.md", "assets/main-window.png") == image
    with pytest.raises(ValueError, match="inside the help directory"):
        resolve_help_asset(root, root / "quick.md", "../../private.png")


def test_help_root_resolution_supports_source_frozen_and_installed_layouts(
    tmp_path: Path,
) -> None:
    source_root = _write_help_tree(tmp_path / "repo" / "docs" / "help")
    module_path = tmp_path / "repo" / "src" / "voice_studio" / "help_content.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    frozen_root = tmp_path / "frozen"
    frozen_help = _write_help_tree(frozen_root / "docs" / "help")
    data_root = tmp_path / "installed"
    installed_help = _write_help_tree(data_root / "share" / "voice-studio" / "help")

    assert resolve_help_root(module_path=module_path, data_root=data_root) == source_root
    assert (
        resolve_help_root(
            module_path=tmp_path / "missing.py",
            frozen_root=frozen_root,
            data_root=data_root,
        )
        == frozen_help
    )
    assert (
        resolve_help_root(
            module_path=tmp_path / "missing.py",
            frozen_root=tmp_path / "missing-frozen",
            data_root=data_root,
        )
        == installed_help
    )


def test_help_validator_reports_broken_local_links_and_accepts_valid_tree(
    tmp_path: Path,
) -> None:
    root = _write_help_tree(tmp_path / "help")
    (root / "quick.md").write_text(
        "# Швидкий старт\n\n[Сценарії](work.md)\n\n![UI](assets/main.png)\n",
        encoding="utf-8",
    )

    issues = validate_help_tree(root)

    assert issues == ("quick.md: missing local target assets/main.png",)
    (root / "assets").mkdir()
    (root / "assets" / "main.png").write_bytes(b"png")
    assert validate_help_tree(root) == ()


def test_help_fragments_match_ukrainian_headings_and_are_validated(tmp_path: Path) -> None:
    root = _write_help_tree(tmp_path / "help")
    (root / "work.md").write_text(
        "# Основні сценарії\n\n## Локальне AI-редагування через Ollama\n",
        encoding="utf-8",
    )
    (root / "quick.md").write_text(
        "# Швидкий старт\n\n"
        "[Ollama](work.md#локальне-ai-редагування-через-ollama)\n",
        encoding="utf-8",
    )

    assert help_anchor("Локальне AI-редагування через Ollama") == (
        "локальне-ai-редагування-через-ollama"
    )
    assert split_help_target(
        "work.md#локальне-ai-редагування-через-ollama"
    ) == ("work.md", "локальне-ai-редагування-через-ollama")
    assert validate_help_tree(root) == ()

    (root / "quick.md").write_text(
        "# Швидкий старт\n\n[Немає](work.md#відсутній-розділ)\n",
        encoding="utf-8",
    )
    assert validate_help_tree(root) == (
        "quick.md: missing anchor #відсутній-розділ in work.md",
    )


def test_open_help_reuses_and_focuses_the_existing_window() -> None:
    app = object.__new__(VoiceStudioApp)
    actions: list[str] = []

    class ExistingWindow:
        def winfo_exists(self) -> bool:
            return True

        def deiconify(self) -> None:
            actions.append("deiconify")

        def lift(self) -> None:
            actions.append("lift")

        def focus_force(self) -> None:
            actions.append("focus")

    app._help_window = ExistingWindow()

    assert VoiceStudioApp._raise_existing_help(app) is True
    assert actions == ["deiconify", "lift", "focus"]
