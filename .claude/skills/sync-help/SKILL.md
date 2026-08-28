---
name: sync-help
description: Use after user-visible functionality, UI, workflows, settings, validation, import/export, or other documented VOICE Studio behavior changes.
---

# Sync Help

Keep the canonical `docs/help/` manual and in-app Help synchronized incrementally.

## Workflow

1. Find the latest commit that changed Help:

   ```bash
   git log -1 --format=%H -- docs/help src/voice_studio/help_content.py src/voice_studio/app.py
   ```

   If it returns nothing, use `git rev-list --max-parents=0 HEAD` as the base.

2. Inspect `git diff --name-status <help-commit>..HEAD`, `git diff`,
   `git diff --cached`, and `git status --short`. Isolate only changes that alter
   what a user sees, enters, selects, or receives. If there are none, stop: do
   not change Help or create a Help commit.
3. Map each user-visible change to affected topics with targeted `rg` searches.
   Verify the new behavior in production code and the closest relevant tests.
   When visible UI or workflow changed and the Windows app can run, verify it in
   the running app too.
4. Update only those `docs/help/` topics. Update a screenshot only when its
   visible text, controls, layout, or documented state actually changed.
5. Run:

   ```bash
   .venv/Scripts/python.exe scripts/check_help.py
   ```

   Also run targeted tests. Run the project-documented full quality gate and
   Windows build when the Help renderer, navigation, assets, or packaging path
   changed.
6. Inspect the final diff. Stage only changed documentation, screenshots,
   in-app Help code, tests, and packaging files directly required by the sync.
   If changes were made, commit them with a descriptive `docs(help): ...`
   message.

## Rules

- `docs/help/` is the single source of truth; do not hardcode a second manual.
- Do not reread or rewrite the whole manual when one topic is affected.
- Do not invent behavior, shortcuts, restrictions, screenshots, or FAQ entries.
- Do not stage unrelated user or agent changes.
- Keep English and Ukrainian public README links aligned when discoverability
  changes.
