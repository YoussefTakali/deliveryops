# Feature — Desktop automation (record → replay + vision)

Record what you do on Windows once, then replay it reliably — re‑finding each control by accessibility
identity, image, or AI, and verifying before acting, rather than blindly replaying coordinates. Runs
slow if needed but aims to "do the job or refuse", never the wrong thing.

## Where the code lives
- **Recorder** (desktop): `desktop/src/recorder.js` — captures clicks (with the clicked element's
  UI‑Automation identity + a 72px image patch + coordinates), keystrokes (never the characters),
  foreground‑window changes, keyframes, and optional voice narration. Emits a bundle.
- **Vision** (desktop): `desktop/src/vision.js` — the "eyes": DPI‑aware screenshot + WinRT OCR +
  UI‑Automation elements + pure‑JS template matching (NCC). `locate()` returns where a target is;
  `clickAt()` acts.
- **Interact** (desktop): `desktop/src/automation-interact.js` — drive apps by UIA (click/type/read,
  window ops), `web.browse`, Teams captions; holds the shared `PS_PRELUDE`.
- **Local** (desktop): `desktop/src/automation-local.js` — print, files, **hash‑approved PowerShell**.
- **Brain** (backend): `backend/app/automations/` — `recording.build_faithful` (events → steps),
  `vision.py` (`decide`: the text LLM ranks perceived candidates), `compiler.py` (NL → semantic
  workflow), `catalog.py` (step catalog + template syntax), `validate.py`, `steps.py`, `documents.py`.
- **Runner** (frontend): `frontend/src/app/automations/runner.ts` — the client‑side execution engine
  (steps, `foreach`, `when`, `on_error`, per‑step confirm, hash approval, live log). Plus
  `template.ts` (templating), `dataset.ts` (table parsing), `models.ts`.
- Routes: `backend/app/routes/automations.py` (`/api/automations/*`).

## The two pipelines
1. **Faithful replay** (deterministic, no LLM re‑invention): the recording's real events become
   concrete `vision.*`/`desktop.*` steps in order. Each click keeps its **ground truth** (element
   identity + image patch + point) so the executor can re‑find the same target and verify it.
2. **Semantic compile** (LLM): the same recording (or a natural‑language description) is turned into a
   smarter, data‑driven workflow of semantic steps (`mail.*`, `file.*`, `doc.*`, `desktop.*`, …).

## Reliability: how it "clicks the right place"
- **DPI**: the shared PowerShell prelude declares **Per‑Monitor‑V2** awareness, so the screenshot,
  UI‑Automation rectangles and `SetCursorPos` share one physical‑pixel space (fixes "off by the scale
  factor" on 125/150% or multi‑monitor).
- **Invoke‑first**: for a recorded click with an accessibility identity, the runner **invokes the
  control through UI Automation** (Invoke/Toggle/Select) — coordinate‑free, immune to DPI, occlusion,
  scrolling and focus races — and only falls back to on‑screen vision for pattern‑less UIs
  (canvas/Citrix/custom‑drawn).
- **Locator cascade** (`vision.locate`): strong UIA identity (automation_id → unique name+type) →
  record‑time image patch → learned patch → unique visible label → **AI arbitration** over the
  perceived candidates. Low‑confidence picks are **refused** (a wrong click is worse than no click).
- Successful clicks are learned as image patches for a faster next run.

## Parameters, variables & datasets
- Templating: `{{inputs.key}}`, `{{steps.<id>.field}}`, per‑step `foreach` (max 200), `when`,
  `trigger.message.*`, `now/today/run.id`.
- **Datasets** (run once per user/row): a `dataset`‑typed input holds a pasted table; the whole
  automation replays once per row with `{{row.Column}}` (accent/case‑insensitive matching),
  `row_index`/`row_total`. Dry‑run previews the first row only. No‑dataset path is unchanged.

## Safety
- PowerShell runs only the exact **hash‑approved** script; side‑effecting steps can be gated by
  `flow.confirm`; automations validate before activation; runs can be dry‑run.
- Vision keeps screenshots on the PC — only text labels, positions and tiny icon crops are sent to the
  AI decision endpoint.

## Tested
`test_automations.py`, `test_faithful.py`, `test_vision.py` (backend); `template.spec.ts`,
`dataset.spec.ts` (frontend, pure node). Note: the on‑screen replay itself needs a real Windows GUI —
CI covers the deterministic brain, locators and templating, not the pixels.
