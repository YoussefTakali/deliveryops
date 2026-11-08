# Feature — Livrables (COPIL, matrice de couverture, plan de charge, and your own)

Generate the firm's project‑management deliverables as **real Office files** from a step‑by‑step
questionnaire, following the house method. Extensible: adding a deliverable = one registry entry + one
generator; each company can tweak a template.

- Backend: `backend/app/deliverables/` (`templates.py`, `generate.py`, `reference/*.md`),
  `backend/app/routes/deliverables.py` (`/api/deliverables/*`), table `DeliverableTemplate`.
- Frontend: `frontend/src/app/deliverables.service.ts`, `pages/perimeter/deliverables.ts`
  (`DeliverablesTab`).
- Placement: it's a **tab under a périmètre** (`/p/:perimeterId/livrables`) — deliverables belong to a
  project. The tab pre‑fills project name + client from the périmètre and skips those questions.

## Company catalogue & project livrables
A company can't ship every livrable type up front, so livrables are **data**, scoped like this
(tables `CustomDeliverable`, `ProjectLivrable`; routes `app/routes/custom_deliverables.py`):

1. **Catalogue de l'entreprise** — the house method (COPIL, matrice de couverture, plan de charge) +
   livrables the company adds (`CustomDeliverable.project_id IS NULL`). Managed by holders of
   `deliverables.catalog` (page **Administration › Catalogue de livrables**, `/livrables/catalogue`).
2. **The project chooses** — each `DeliveryProject` ticks the catalogue livrables it uses
   (`ProjectLivrable` rows keyed `builtin key` or `custom:<id>`; no rows = all built‑ins) and can
   create **its own** livrables (`CustomDeliverable.project_id = <project>`), from the project's
   **Livrables** tab (`/pr/:id/livrables`). Deciders of the project edit them; a catalogue manager can
   **promote** a project livrable into the catalogue (copy).
3. **Périmètres produce** — the périmètre Livrables tab lists exactly its project's set
   (`GET /api/deliverables/templates?perimeter_id=…` → `available_for_perimeter`). A périmètre outside
   any project sees the whole catalogue. Generating a project livrable from another project's
   périmètre is refused.

**Level (project vs périmètre)** — table `LivrableScope` (`project_id`, `key`, `level`, `perimeter_ids`), set with
`PUT /api/projects/{id}/livrables/scope` from the project's Livrables tab. `project` = one document for the whole
project, generated from the project tab (`prepare`/`generate` with `project_id`), fed by the uploaded inputs **and**
the knowledge base of all its périmètres, stored as a `ProjectDeliverable` and indexed into every périmètre.
`perimeter` = generated from each chosen périmètre's tab (`perimeter_ids` null = all). Built-ins are locked:
plan de charge → project; COPIL, matrice → périmètre. Custom livrables default to project level. The périmètre
tab only lists périmètre-level livrables that include it; running one at the wrong level returns a clear 400.

**Inputs** — `knowledge.extract_text` reads PDF, Word, Excel and PowerPoint. A Word file is walked in document order and
rendered as markdown (heading levels kept, tables as `| a | b |` rows), so a `.docx` cahier des charges or spec parses
exactly like the shipped `.md` samples; old binary Office formats and other
binary files get a clear 400 (they used to be stored as raw bytes → PostgreSQL NUL-byte 500). `ingest_document`
also strips NUL bytes, which fixed the same crash when indexing PC files.

Designing one (`/livrables/nouveau[?project=<id>]`): describe it like to a colleague — inputs
(files / text / connectors), output (docx / pptx / xlsx / md), sections, style rules, one or two
examples. The AI turns it into a spec and **asks what's unclear, with suggestions (one recommended)**.
At generation time it asks again only for what the inputs don't answer.

## The method (seeded from client docs → `reference/*.md`)
- **Support de COPIL** → `.pptx`. Sections: Faits marquants (actions réalisées / alertes / en cours /
  à initier), Suivi de l'activité, Plan d'action‑décisions‑risques, Perspectives et divers. One slide
  per section, bullets from the answers.
- **Matrice de couverture** → `.xlsx`. Crosses **exigences métier** (columns) × **cas d'usage / user
  stories** (rows), with Impact / Probabilité / **Criticité** per requirement and coverage marks; a
  second sheet holds the coloured criticité grid.
  - **Criticité grid** (Impact × Probabilité → 1..3): Catastrophique/Grave/Modéré/Ennuyeux ×
    Inévitable/Fréquent/Occasionnel/Rare. Lookup is accent/case‑insensitive.
  - Method has 4 steps (create matrix → identify dependencies → square regression matrix
    sensibilité/influence → test load). v1 generates the matrix + criticité; dependencies →
    regression + charge de test is a planned follow‑up.

## Where it sits in the périmètre lifecycle
A new périmètre (a manager may run several, one per client) follows: **kick‑off meeting → access to
Jira / cahier des charges / specs → matrice de couverture → technologies + team (competence matrix) →
plan de charge → billing monitoring**. The Pilotage tab shows this as a **lifecycle tracker**
(`GET /api/perimeters/{id}/lifecycle`, computed from what actually exists), each stage linking to the
tab that does it. Everything produced is stored per périmètre (`PerimeterDeliverable`: structured draft
+ file) and **indexed into the knowledge base** (inputs as `cahier_des_charges`/`spec`, outputs as
`livrable`) so the copilot, the companion and the call assistant can answer from it.

## Two template modes (generic for other companies' needs)
- **`mode: "questions"`** (COPIL) — answered one question at a time, then generated.
- **`mode: "extract"`** (matrice de couverture) — the template declares **typed `inputs`**
  (`file` / `files` / `text` / `connector`); the app **extracts** the content from them and asks
  clarifying questions **only when something changes the result** (like the companion). Other
  companies declare other inputs/outputs by editing the registry or their per‑company override.

### The matrice's inputs
| Input | Kind | Content |
|---|---|---|
| `cahier_des_charges` (required) | file | the requirements — tables `Réf | Exigence | Impact | Probabilité` or bullets `EX‑01 : …` |
| `specs` | files | user stories (`## US‑01 — Titre` + `Exigences couvertes : EXF‑01, …`) |
| `jira` | connector | tickets (demo data now; the Jira connector later) — summary/labels/description refs |

## How extraction works (`extract.py`)
1. **Deterministic pass** (always): parse requirement tables/bullets, user‑story sections and their
   "Exigences couvertes", and tickets (labels + refs in text) → `MatriceDraft {exigences, cas_usage}`;
   use cases de‑duplicated by name (a spec story and its Jira ticket merge, coverage unioned).
2. **LLM refinement** (optional, `llm.structured` with the deterministic draft as fallback): better
   names / inferred coverage — it can never drop a requirement or a coverage the documents state.
3. **Clarifying questions** (≤5, clickable options + free text): default Impact / Probabilité when
   requirements lack criticité; whether **Bug** tickets count as use cases. Answers re‑run the
   extraction (`apply_answers`).
4. The user **reviews the draft** (counts + previews) and generates.

## Plan de charge (`plan.py`) — a PROJECT-level document
**The plan de charge is one document for the whole project, not per-périmètre.** It is generated from
the **project page** (`GET/POST /api/projects/{id}/plan-de-charge/prepare|generate`, stored as
`ProjectDeliverable`, listed at `/api/projects/{id}/deliverables`) — the périmètre Livrables tab only
offers COPIL + matrice. It **aggregates the latest matrice de couverture of every périmètre** under the
project (refs namespaced per-périmètre so coverage never matches across them), staffs from the
**project team** (`ProjectMember`; reusing staffing profile/capacity/skills where a member is staffed,
else defaults + their competence matrix), and uses the project **technologies** and a rate card taken
from the project's périmètre contracts. The produced plan is indexed into every périmètre's knowledge
base so all agents can read it. It asks the manager only
what it can't know: base days per use case, start date, horizon (weeks); who works on it if nobody is
staffed; the TJM if the contract has no rate card.
- **Charge model**: `days = base × nb_use_cases_covering × factor(criticité)` (3 → 1.5, 2 → 1.0, 1 → 0.7).
- **Assignment**: anyone whose skills match a project technology is eligible; each exigence goes to the
  eligible person with the lowest load relative to their capacity (skill score breaks ties), so the charge
  is spread across qualified people instead of piling on the single best match; **weekly scheduling**
  within each person's availability (capacity − absences); unscheduled days are flagged.
- **Costs**: days × TJM of the person's profile (contract `rate_card`) → feeds billing.
- **Workbook** (mirrors the firm's plan de charge): `Plan de charge` (months / S‑weeks / dates /
  working days; calendrier de disponibilité; charge par exigence; récap par ressource with totals and
  remaining capacity; taux d'occupation with >100 % highlighted), `Congés`, `Charge.appli` (per
  exigence with cost), `Param`, `Périmètre`.
- **Billing monitoring**: `GET /api/perimeters/{id}/plan-vs-reel` compares planned days per person
  (latest plan) with timesheets — shown on the Facturation tab.

## Generation (`generate.py`)
- `generate_copil` via **python‑pptx**.
- `generate_matrice_from_draft` via **openpyxl** (shared by the extracted draft and the legacy
  free‑text answers): columns `REF — exigence`, rows `REF — cas d'usage`, Impact/Probabilité/Criticité
  rows (defaults shown in grey italics), `X` where a use case covers a requirement (by ref, or fuzzy
  name), plus the coloured criticité grid sheet.

## Sample data (shipped, for testing & tuning)
`app/deliverables/samples/` — **`portail_client`** (rich: criticité given, 14 requirements, 2 specs,
14 Jira tickets incl. a Bug) and **`gestion_conges`** (lean: no criticité → triggers the default
questions). Listed/downloadable via the API and loadable in one click from the tab ("tester avec un
jeu d'exemple") — no upload needed.

## API
| Method | Path | Perm | Purpose |
|---|---|---|---|
| GET | `/api/deliverables/templates` | `automation.use` | list effective templates (mode, inputs, questions, prefill) |
| GET | `/api/deliverables/{key}/samples` · `/{set}/{name}` | `automation.use` | list / download sample files |
| GET | `/api/perimeters/{id}/deliverables` · `/{did}` · `/{did}/file` | périmètre access | history of generated deliverables (+ draft, périmètre‑scoped download) |
| POST | `/api/deliverables/{key}/prepare` | `automation.use` | inputs (uploaded file ids via `/api/automations/files`, connector `{demo:true}`) or `samples` + `answers` → `{status: questions\|ready, draft, questions, sources, used_llm}` |
| POST | `/api/deliverables/{key}/generate` | `automation.use` | `draft` (+`projet`) or legacy `answers` → the file bytes; accepts `perimeter_id` |
| GET/PUT | `/api/deliverables/{key}/template` | `companion.admin` | read / save per‑company overrides (incl. `inputs`) |

## Per-company customization
Overrides (title, description, questions, impacts/probas scales, grid, sections) are stored in
`DeliverableTemplate` and merged over the built‑in defaults — so each company adapts the method
without code changes.

## Recommended options & debugging
Every clarifying question now carries a `recommended` option (matrice: default impact/probabilité,
Bugs excluded from coverage; plan de charge: 1 j/cas, next Monday, 12 weeks, 550 € TJM; companion
tasks and custom livrables: chosen by the AI). The UI shows a « Recommandé » badge and pre-selects it.
Errors are no longer swallowed: a failed file generation returns its reason inside the downloaded
Blob, which the UI now reads (`http-error.ts`), and every error offers « Déboguer avec l'IA ».

## Tested
`backend/tests/test_custom_deliverables.py` (4): design asks with a recommended option then completes;
sections learned from an uploaded example, prepare → question → draft grounded in the source → docx
stored on the périmètre, then pptx/xlsx/md renders; scoped to the catalogue or a project (project deciders / catalogue managers edit);
built-in questions carry a recommended option.
`backend/tests/test_lifecycle.py` (4) covers the end‑to‑end lifecycle (competence matrix → skill
match → staffing → matrice stored+indexed → plan de charge questions/allocation/costs → workbook →
lifecycle tracker → plan vs réel). `backend/tests/test_deliverables.py` (9): templates, COPIL `.pptx`, matrice `.xlsx` read back
(criticité, coverage, grid), samples listed/downloadable (path traversal refused), **extraction from
both sample sets** (rich → only the Bug question; lean → default‑criticité questions with clickable
scale options), answers re‑run extraction, uploaded files go through the same extractor, required
input missing → 400, draft → workbook with `REF —` headers and `X` marks, per‑company override.
Frontend builds clean.
