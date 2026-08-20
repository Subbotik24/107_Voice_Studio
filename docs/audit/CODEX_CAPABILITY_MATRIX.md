# Codex capability matrix — 107 Voice Studio

## Decision

No skill/plugin/MCP was installed. The existing built-in and official capabilities cover this read-only audit; adding third-party executable capability would increase supply-chain and context overhead without closing an evidence gap. Native Git is sufficient for local baseline/diff work, and official GitHub web/API access was sufficient for read-only remote run evidence despite unavailable `gh`.

## Environment observed

- root `AGENTS.md`; no nested override or repo-local `.agents/skills`;
- built-in planning, debugging, TDD, security review, threat modeling, verification and Git workflow skills;
- official document/spreadsheet/presentation/PDF/browser/visualization/sites tooling;
- local filesystem/shell, web research and bounded read-only subagents;
- bundled Git and Python 3.12.13 runtime; system `python` was a Microsoft Store alias; `py`/`gh` unavailable;
- project Python dependencies were not installed into the bundled runtime;
- Codex Security plugin was available for recommendation but not installed;
- no production credentials or external service mutation was needed.

## Capability mapping

| Project need | Capability used/considered | Existing coverage | Source/trust | Frequency | Context/permission overhead | Decision |
|---|---|---|---|---:|---|---|
| Repository inventory and flow tracing | shell, `rg`, Git, main-agent code reading | complete | built-in/local, high trust | high | low | use |
| Architecture/correctness review | main reasoning + independent desktop reviewer | complete | built-in, high trust | high | medium | use read-only |
| Security best practices | `security-best-practices` | complete for Python static review; generic Python/Tk has no specialized reference | official/built-in, high | medium | low | use |
| Threat modeling | `security-threat-model` | complete | official/built-in, high | periodic | medium | use; canonical threat model created |
| Dependency/supply chain | manifests/workflows review, official PyPI/GitHub sources | adequate for inventory; resolved vulnerability state blocked without environment | official web + local, high | periodic | medium | use; no scanner install |
| Test/build evidence | bundled Python, remote exact-SHA CI/CodeQL evidence | partial; local pytest/Ruff/build/pip-audit modules absent | local/official GitHub | high | low | run available checks; record blockers |
| Performance/resource review | static complexity, independent calculations | partial until target-hardware benchmark | built-in/local | medium | medium | use; do not claim measured performance |
| DSP/ML calculation verification | independent formula probes + Hermes reviewer | strong for algebra/semantics; authoritative feature/model source gaps remain | local/built-in | high in Hermes track | medium/high | use; calculation register |
| Database/file-format review | source/tests, threat model | complete static coverage | built-in/local | medium | low | use |
| Desktop/UI review | source and independent reviewer | static only; no live device/UI environment | built-in/local | medium | medium | use; physical tests deferred |
| Cloud API contract | official OpenAI docs + static source | adequate for current model-name check; live call not authorized | official vendor, high | low | medium/network | use read-only |
| Commercial architecture | main synthesis + independent commercial reviewer | complete design review | built-in, high | periodic | medium | use; no implementation |
| Documentation generation | Markdown/apply_patch | complete | built-in/local | high | low | use |
| GitHub integration plugin | GitHub plugin considered | native Git + read-only official API already sufficient | recommended official plugin, high | low now | account/permission/context | reject for this phase |
| Codex Security plugin | plugin considered | built-in security/threat passes already sufficient; deep scan would add installation/permissions | recommended official plugin, high | low | medium/high | reject for read-only phase |
| Repo-local audit skill | `.agents/skills` considered | `AGENTS.md` + canonical deep-audit prompt cover reusable workflow | would be local/high trust | future periodic | ongoing maintenance/context | do not create yet |
| General productivity plugins | Airtable/Asana/Canva/etc. | no project evidence gap | third-party/varied | none | account, permission, context | reject |

## Skill use and sequencing

- `using-superpowers`: established skill-first workflow.
- `brainstorming`: treated the user’s attached master prompt as the approved audit design; no product feature design occurred.
- `writing-plans`: converted the master prompt into a tracked, evidence-gated audit plan.
- `dispatching-parallel-agents`: independent read-only desktop, security and Hermes/commercial lanes.
- `security-best-practices` and `security-threat-model`: repository-grounded review and threat model.
- `systematic-debugging`: diagnosed a one-off PowerShell pipeline syntax failure; no product defect was modified.
- `verification-before-completion`: required before final protected-scope claims.

## Repository instruction decision

The existing root `AGENTS.md` is concise and preserves critical product invariants. It should be extended only with a canonical documentation map and the audit/design/approval gate. Nested instructions and a custom skill are not justified until subprojects develop distinct recurring commands or policies.

## Reconsideration triggers

Revisit tooling only if a later approved phase needs:

- authorized deep code/security scanning that existing static passes cannot provide;
- private GitHub issue/PR/release mutation;
- deterministic dependency/license/SBOM generation in an isolated build environment;
- recurring Hermes dataset/model validation that warrants a repo-local skill/script;
- live artifact/document formats beyond Markdown.

Any new skill/plugin must be source-inspected, minimally permissioned, verified after installation and recorded as an executable supply-chain dependency.
