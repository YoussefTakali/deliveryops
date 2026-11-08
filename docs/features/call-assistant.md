# Feature — Call / meeting assistant

> The **context** typed in the overlay's settings (who is on the call, products, acronyms) does two things: it is
> sent to the copilot with every turn, and its first 300 characters are passed to the transcriber as `hint`
> (whisper `initial_prompt`), which is what makes names like NOEMIE, Alfresco or SharePoint come out spelled right.
> With a périmètre pinned on the call, every substantive turn that matches the project's documentation is
> answered from it (route `project`) before any web search.

A live meeting copilot: it transcribes in real time and answers questions with sources, without
switching languages mid‑conversation or answering before the speaker finishes.

- Backend: `backend/app/live_assistant.py`, `backend/app/stt.py`, `backend/app/transcripts.py`,
  `backend/app/routes/live_assistant.py`.
- Frontend: `frontend/src/app/pages/call-assistant.ts`, `frontend/src/app/voice-segmenter.ts`.
- Desktop: opens as its own window; can read Microsoft **Teams live captions** directly
  (`automation-interact.js::readTeamsCaptions`) for exact speaker names.
- Config: `STT_*` (faster‑whisper) and `SPEAKER_*` (diarization) in `config.py`.

## How it works
- **Transcription**: local `faster‑whisper` (`STT_MODEL`, CPU int8 by default) — transcribes
  immediately as audio comes in. Browser mic or typing also supported.
- **Voice segmentation** (`voice-segmenter.ts`): a minimum voiced duration gate so it waits for the
  speaker to actually finish a thought before considering an answer.
- **Speaker identity** ("who is speaking"): per‑utterance voice embeddings via ONNX WeSpeaker
  (`SPEAKER_EMBEDDING=onnx`, `speaker_onnx_repo`), assigned conservatively so a change of tone or two
  turns in a row don't get confused. When Teams captions are available, exact speaker names are used.
- **Answering**: answers only when a question is actually finished and worth answering — filler
  ("euh…") is ignored (a filler gate + backend NOOP), and the reply is pinned to the conversation
  language so it doesn't drift. Answers cite sources from the knowledge base and flag out‑of‑scope
  requests.
- **After the call**: minutes with actions, decisions and risks (meeting intelligence agent); Teams
  transcript import (`.vtt`/`.docx`) via `transcripts.py`.
- **High‑precision capture**: when a meeting ends (or is imported), the meeting intelligence agent
  indexes the **verbatim transcript** into the périmètre's knowledge base as a `meeting_transcript`
  document — **unconditionally**, deduped by filename (`meeting_intelligence._index_transcript`). The
  gated `minutes` recommendation still indexes the summary once approved, but the full text is
  searchable immediately, so the copilot / companion / call assistant can answer *exactly* what was
  said about that project.

## Tested
`backend/tests/test_live_assistant.py` (filler/NOOP, language pinning, transcript handling).
