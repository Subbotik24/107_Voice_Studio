from __future__ import annotations

import json
import re
import sys
import sysconfig
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

MANIFEST_NAME = "help-index.json"
_INLINE_MARKUP = re.compile(r"(\*\*|__|`|(?<!\*)\*(?!\*)|(?<!_)_(?!_))")
_LINK = re.compile(r"^\[([^]]+)]\(([^)]+)\)$")
_IMAGE = re.compile(r"^!\[([^]]*)]\(([^)]+)\)$")
_ALL_LOCAL_TARGETS = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


@dataclass(frozen=True)
class HelpTopic:
    slug: str
    title: str
    source_path: Path
    markdown: str


@dataclass(frozen=True)
class MarkdownBlock:
    kind: Literal["heading", "paragraph", "bullet", "numbered", "code", "image", "link", "table"]
    text: str
    level: int = 0
    target: str | None = None


def _is_help_root(path: Path) -> bool:
    return path.is_dir() and (path / MANIFEST_NAME).is_file()


def resolve_help_root(
    explicit_root: Path | None = None,
    *,
    module_path: Path | None = None,
    frozen_root: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    """Locate the canonical Help tree in source, frozen, or wheel installs."""

    if explicit_root is not None:
        resolved = Path(explicit_root).resolve()
        if not _is_help_root(resolved):
            raise FileNotFoundError(f"VOICE Studio Help is unavailable at {resolved}")
        return resolved

    if frozen_root is None and getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            frozen_root = Path(bundle_root)
    module = Path(module_path) if module_path is not None else Path(__file__)
    installed_data = Path(data_root) if data_root is not None else Path(sysconfig.get_path("data"))
    candidates: list[Path] = []
    if frozen_root is not None:
        candidates.append(Path(frozen_root) / "docs" / "help")
    if len(module.resolve().parents) >= 3:
        candidates.append(module.resolve().parents[2] / "docs" / "help")
    candidates.append(installed_data / "share" / "voice-studio" / "help")

    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_help_root(resolved):
            return resolved
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"VOICE Studio Help is unavailable; searched: {searched}")


def _inside(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("Help targets must stay inside the help directory")
    return resolved_candidate


def load_help_topics(help_root: Path | None = None) -> tuple[HelpTopic, ...]:
    root = resolve_help_root(help_root) if help_root is not None else resolve_help_root()
    try:
        payload = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Help manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Help manifest has an unsupported format")
    if payload.get("language") != "uk" or not isinstance(payload.get("topics"), list):
        raise ValueError("Help manifest must define Ukrainian topics")

    topics: list[HelpTopic] = []
    slugs: set[str] = set()
    for item in payload["topics"]:
        if not isinstance(item, dict):
            raise ValueError("Help manifest topic must be an object")
        slug, title, filename = item.get("slug"), item.get("title"), item.get("file")
        if not all(isinstance(value, str) and value.strip() for value in (slug, title, filename)):
            raise ValueError("Help manifest topic fields cannot be empty")
        if slug in slugs or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError(f"invalid or duplicate Help topic slug: {slug}")
        source_path = _inside(root, root / filename)
        if not source_path.is_file():
            raise FileNotFoundError(f"Help topic is missing: {filename}")
        slugs.add(slug)
        topics.append(
            HelpTopic(
                slug=slug,
                title=title.strip(),
                source_path=source_path,
                markdown=source_path.read_text(encoding="utf-8"),
            )
        )
    if not topics:
        raise ValueError("Help manifest must contain at least one topic")
    return tuple(topics)


def search_help_topics(topics: Iterable[HelpTopic], query: str) -> tuple[HelpTopic, ...]:
    available = tuple(topics)
    needle = query.strip().casefold()
    if not needle:
        return available
    return tuple(
        topic
        for topic in available
        if needle in topic.title.casefold() or needle in topic.markdown.casefold()
    )


def _plain_inline(text: str) -> str:
    return _INLINE_MARKUP.sub("", text).strip()


def help_anchor(text: str) -> str:
    """Return the stable Markdown fragment used by canonical Help headings."""

    plain = _plain_inline(text).casefold()
    plain = re.sub(r"[^\w\s-]", "", plain, flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", plain).strip("-")


def split_help_target(target: str) -> tuple[str, str]:
    """Split a local Help target into its topic filename and heading fragment."""

    parsed = urlsplit(target)
    return Path(unquote(parsed.path)).name, unquote(parsed.fragment)


def parse_markdown(markdown: str) -> tuple[MarkdownBlock, ...]:
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    code: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(MarkdownBlock("paragraph", _plain_inline(" ".join(paragraph))))
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            if code is None:
                flush_paragraph()
                code = []
            else:
                blocks.append(MarkdownBlock("code", "\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            if blocks and blocks[-1].kind == "table":
                previous = blocks[-1]
                blocks[-1] = MarkdownBlock("table", f"{previous.text}\n{stripped}")
            else:
                blocks.append(MarkdownBlock("table", stripped))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        image = _IMAGE.match(stripped)
        link = _LINK.match(stripped)
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            blocks.append(
                MarkdownBlock("heading", _plain_inline(heading.group(2)), len(heading.group(1)))
            )
        elif image:
            flush_paragraph()
            blocks.append(MarkdownBlock("image", image.group(1).strip(), target=image.group(2)))
        elif link:
            flush_paragraph()
            blocks.append(MarkdownBlock("link", _plain_inline(link.group(1)), target=link.group(2)))
        elif bullet:
            flush_paragraph()
            blocks.append(MarkdownBlock("bullet", _plain_inline(bullet.group(1))))
        elif numbered:
            flush_paragraph()
            blocks.append(
                MarkdownBlock(
                    "numbered",
                    f"{numbered.group(1)}. {_plain_inline(numbered.group(2))}",
                )
            )
        else:
            paragraph.append(stripped)
    if code is not None:
        blocks.append(MarkdownBlock("code", "\n".join(code)))
    flush_paragraph()
    return tuple(blocks)


def _local_target_path(help_root: Path, source_path: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or (not parsed.path and parsed.fragment):
        return None
    relative = Path(unquote(parsed.path))
    if relative.is_absolute():
        raise ValueError("Help targets must stay inside the help directory")
    return _inside(help_root, source_path.parent / relative)


def resolve_help_asset(help_root: Path, source_path: Path, target: str) -> Path:
    resolved = _local_target_path(help_root, source_path, target)
    if resolved is None:
        raise ValueError("Help image target must be a local file")
    return resolved


def validate_help_tree(help_root: Path | None = None) -> tuple[str, ...]:
    root = resolve_help_root(help_root) if help_root is not None else resolve_help_root()
    issues: list[str] = []
    try:
        load_help_topics(root)
    except (OSError, ValueError) as exc:
        return (str(exc),)
    for source in sorted(root.rglob("*.md")):
        markdown = source.read_text(encoding="utf-8")
        for match in _ALL_LOCAL_TARGETS.finditer(markdown):
            target = match.group(1).strip()
            parsed = urlsplit(target)
            try:
                resolved = _local_target_path(root, source, target)
            except ValueError as exc:
                issues.append(f"{source.relative_to(root).as_posix()}: {exc}")
                continue
            if resolved is not None and not resolved.exists():
                local_target = target.split("#", 1)[0]
                issues.append(
                    f"{source.relative_to(root).as_posix()}: "
                    f"missing local target {local_target}"
                )
                continue
            if parsed.scheme or parsed.netloc or not parsed.fragment:
                continue
            target_source = resolved if resolved is not None else source
            if target_source.suffix.casefold() != ".md" or not target_source.is_file():
                continue
            anchors = {
                help_anchor(block.text)
                for block in parse_markdown(target_source.read_text(encoding="utf-8"))
                if block.kind == "heading"
            }
            fragment = unquote(parsed.fragment)
            if fragment not in anchors:
                issues.append(
                    f"{source.relative_to(root).as_posix()}: missing anchor "
                    f"#{fragment} in {target_source.relative_to(root).as_posix()}"
                )
    return tuple(issues)
