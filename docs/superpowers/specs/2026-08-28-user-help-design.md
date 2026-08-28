# VOICE Studio User Help Design

## Goal

Give a first-time Ukrainian-speaking user an accurate task-oriented manual and
the same help inside the Windows desktop application, without maintaining a
second hardcoded copy.

## Source of truth

`docs/help/` is canonical. It contains a small ordered manifest, Markdown
topics, and sanitized PNG screenshots. The source application reads this tree
directly. Setuptools installs the same files under `share/voice-studio/help`,
and PyInstaller bundles the same directory as `docs/help`.

## Documentation structure

- `README.md`: contents, product scope, audience, version, and navigation.
- `quick-start.md`: first local transcription result.
- `workflows.md`: critical/common tasks—recording, file transcription, editing,
  history, export, local Ollama cleanup, models, and backup.
- `reference.md`: screens, settings, formats, shortcuts, privacy boundaries,
  and current limitations.
- `troubleshooting.md`: confirmed messages and recovery actions only.
- `help-index.json`: in-app topic order and titles.

## In-app UX

Add `Довідка` to the persistent sidebar and bind `F1`. A non-modal themed
window opens on Quick Start and provides topic navigation, a local text search,
scrolling, keyboard focus, and Close/Escape behavior. It renders a conservative
Markdown subset: headings, paragraphs, ordered/unordered lists, code blocks,
links, and local PNG images. Unsupported syntax remains readable as text.

## Safety and scope

- No network request is needed to open Help.
- Help paths must remain inside the resolved canonical help root.
- User originals, transcripts, settings, and cloud consent behavior are
  unchanged.
- Only confirmed GUI/CLI behavior is documented. Native microphone/hotkey and
  clean-machine gaps remain explicit limitations.

## Verification

- TDD covers catalog validation, traversal rejection, Markdown parsing,
  search, and source/frozen/installed root resolution.
- Link validation checks every local Markdown/image link and rejects stale
  placeholders.
- Source UI and final packaged `.exe` are exercised with Computer Use: sidebar,
  F1, topic navigation, search, image loading, close, and second launch.
- The full quality gate, wheel build, and Windows packaging run after changes.
