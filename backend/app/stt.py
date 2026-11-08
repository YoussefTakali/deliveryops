"""Local speech-to-text for the call-assistant's "listen to the whole call" mode (see
frontend/src/app/pages/call-assistant.ts and POST /api/meetings/{id}/transcribe-chunk in app/api.py).

Runs faster-whisper (a CTranslate2 reimplementation of OpenAI Whisper) fully locally/offline — no
cloud STT credential needed, unlike Azure Speech or Whisper-via-API. The model is lazily loaded once
(a few hundred MB) and reused across requests; CPU inference on a short (4-6s) chunk is fast enough
for a "near real time" assistant on typical laptop hardware.
"""

import io
import logging
import threading

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_model = None
_model_lock = threading.Lock()


class STTUnavailable(RuntimeError):
    """Raised when faster-whisper isn't installed, the model can't be loaded, or a chunk fails to
    decode — callers should turn this into a clean 503/400 rather than a 500."""


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:  # dependency not installed in this environment
                    raise STTUnavailable("faster-whisper is not installed") from exc
                log.info("Loading faster-whisper model=%s device=%s compute_type=%s ...", settings.stt_model, settings.stt_device, settings.stt_compute_type)
                try:
                    _model = WhisperModel(settings.stt_model, device=settings.stt_device, compute_type=settings.stt_compute_type)
                except Exception as exc:  # e.g. no internet on first run to fetch model weights
                    raise STTUnavailable(f"failed to load whisper model: {exc}") from exc
    return _model


def warm_up() -> None:
    """Load the model in a background thread at API startup, so the first chunk of a call isn't
    stuck behind a multi-second model load (or a first-run download). Failures are only logged —
    transcribe() retries the load on demand."""
    def _load():
        try:
            _get_model()
            log.info("faster-whisper model ready")
        except STTUnavailable as exc:
            log.warning("STT warm-up failed: %s", exc)
        if settings.speaker_embedding == "onnx":
            try:
                _get_speaker_session()  # download + open the speaker model ahead of the first call
            except Exception as exc:  # noqa: BLE001
                log.info("speaker model warm-up failed (%s) — will use fbank fallback", type(exc).__name__)
    threading.Thread(target=_load, name="stt-warmup", daemon=True).start()


def transcribe(audio_bytes: bytes, *, language: str | None = None, hint: str | None = None) -> str:
    """Transcribe a short audio chunk into text. Accepts whatever container/codec the browser's
    MediaRecorder produced (webm/opus by default) or a plain wav/mp3 test file — faster-whisper
    decodes via the bundled PyAV, so no system ffmpeg binary is required.

    Raises STTUnavailable if the dependency/model isn't usable or the chunk can't be decoded.
    """
    if not audio_bytes:
        return ""
    model = _get_model()
    try:
        # `hint` (the call's context: names, products, acronyms) primes the decoder — whisper spells the
        # words it has just "read" instead of guessing a phonetic neighbour (NOEMIE, Alfresco, SharePoint…)
        segments, _info = model.transcribe(io.BytesIO(audio_bytes), language=language, vad_filter=True, beam_size=1, initial_prompt=(hint or None))
        return " ".join(s.text.strip() for s in segments).strip()
    except STTUnavailable:
        raise
    except Exception as exc:  # corrupt/empty chunk, unsupported codec, etc.
        log.warning("STT transcription failed: %s", exc)
        raise STTUnavailable(str(exc)) from exc


# ---------------------------------------------------------------- speaker diarization

_SR = 16000


def transcribe_and_fingerprint(audio_bytes: bytes, *, language: str | None = None, hint: str | None = None) -> tuple[str, list[float] | None, str | None]:
    """Transcribe AND return a voice embedding for the chunk, so a call with many participants can be
    split by speaker. Returns (text, embedding, backend) where backend is "wespeaker" (trained ONNX
    speaker model) or "fbank" (numpy fallback) or None. The caller clusters embeddings into speakers.
    Embedding is None if the audio can't be decoded — the transcript still comes back regardless."""
    if not audio_bytes:
        return "", None, None, None
    model = _get_model()
    pcm = _decode_pcm(audio_bytes)
    try:
        source = pcm if pcm is not None else io.BytesIO(audio_bytes)
        segments, _info = model.transcribe(source, language=language, vad_filter=True, beam_size=1, initial_prompt=(hint or None))
        text = " ".join(s.text.strip() for s in segments).strip()
    except STTUnavailable:
        raise
    except Exception as exc:
        log.warning("STT transcription failed: %s", exc)
        raise STTUnavailable(str(exc)) from exc
    if pcm is None or settings.speaker_embedding == "off":
        return text, None, None, None
    # Very short utterances ("oui", "ok") give unreliable embeddings; the client uses the duration
    # to lean on speaker continuity instead of trusting the vector.
    emb, backend = voice_signature(pcm)
    return text, emb, backend, round(float(len(pcm)) / _SR, 2)


def _decode_pcm(audio_bytes: bytes):
    """Decode any container to mono 16 kHz float32 PCM via faster-whisper's bundled PyAV. Returns a
    numpy array, or None on failure (the caller then skips diarization for this chunk)."""
    try:
        from faster_whisper.audio import decode_audio

        return decode_audio(io.BytesIO(audio_bytes), sampling_rate=_SR)
    except Exception as exc:  # noqa: BLE001 — decoding is best-effort for diarization
        log.info("PCM decode for diarization failed: %s", exc)
        return None


# ---- trained ONNX speaker embedding (WeSpeaker ResNet34) --------------------------------------

_speaker_session = None
_speaker_lock = threading.Lock()
_FL, _FS, _NFFT, _NMEL = 400, 160, 512, 80  # 25ms/10ms frames, FFT size, mel bins (WeSpeaker/kaldi)
_kaldi_cache: dict = {}


def _kaldi_bits():
    """Hamming window and kaldi mel filterbank, computed once. Matches the WeSpeaker feature
    extractor: 80 mel bins, low-freq 20 Hz, HTK mel scale, on an FFT of 512."""
    if not _kaldi_cache:
        import numpy as np

        def mel(f):
            return 1127.0 * np.log(1.0 + f / 700.0)

        lo, hi = mel(20.0), mel(_SR / 2)
        delta = (hi - lo) / (_NMEL + 1)
        mf = mel(np.arange(_NFFT // 2 + 1) * _SR / _NFFT)
        fb = np.zeros((_NMEL, _NFFT // 2 + 1), dtype=np.float32)
        for m in range(_NMEL):
            left = lo + m * delta
            fb[m] = np.maximum(0.0, np.minimum((mf - left) / delta, (left + 2 * delta - mf) / delta))
        _kaldi_cache["fb"] = fb
        _kaldi_cache["win"] = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(_FL) / (_FL - 1))).astype(np.float32)
    return _kaldi_cache["win"], _kaldi_cache["fb"]


def _kaldi_fbank(pcm):
    """kaldi-compatible log-mel fbank with per-utterance mean normalisation (CMN), the input the
    WeSpeaker ONNX model expects: shape (num_frames, 80)."""
    import numpy as np

    x = np.asarray(pcm, dtype=np.float32).flatten()
    if x.size < _FL + _FS:
        return None
    win, fb = _kaldi_bits()
    n_frames = 1 + (x.size - _FL) // _FS
    if n_frames < 9:  # WeSpeaker min_num_frames
        return None
    idx = np.arange(_FL)[None, :] + _FS * np.arange(n_frames)[:, None]
    frames = x[idx].astype(np.float32)
    frames = frames - frames.mean(axis=1, keepdims=True)          # remove DC offset
    pre = frames.copy()
    pre[:, 1:] -= 0.97 * frames[:, :-1]
    pre[:, 0] -= 0.97 * frames[:, 0]                              # pre-emphasis 0.97
    frames = pre * win                                            # hamming window
    power = np.abs(np.fft.rfft(frames, n=_NFFT, axis=1)) ** 2
    feat = np.log(np.maximum(power @ fb.T, 1.19e-7))
    return (feat - feat.mean(axis=0, keepdims=True)).astype(np.float32)


def _get_speaker_session():
    """Lazily download + open the WeSpeaker ONNX model. Returns None if onnxruntime or the model is
    unavailable, so diarization silently falls back to the numpy fingerprint."""
    global _speaker_session
    if _speaker_session is None:
        with _speaker_lock:
            if _speaker_session is None:
                import os

                import onnxruntime as ort
                from huggingface_hub import hf_hub_download

                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                path = hf_hub_download(settings.speaker_onnx_repo, settings.speaker_onnx_file)
                _speaker_session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
                log.info("speaker embedding model ready (%s)", settings.speaker_onnx_repo)
    return _speaker_session


def _onnx_signature(pcm) -> list[float] | None:
    import numpy as np

    feat = _kaldi_fbank(pcm)
    if feat is None:
        return None
    sess = _get_speaker_session()
    out = sess.run(None, {"input_features": feat[None, :, :]})[0][0]
    norm = float(np.linalg.norm(out))
    return (out / norm).astype(float).tolist() if norm else None


def voice_signature(pcm) -> tuple[list[float] | None, str | None]:
    """Voice embedding for a chunk of PCM. Prefers the trained ONNX speaker model (returns backend
    "wespeaker"); on any failure falls back to a numpy log-mel fingerprint ("fbank"). Both are
    L2-normalised so the client can cluster by cosine similarity."""
    if settings.speaker_embedding == "onnx":
        try:
            emb = _onnx_signature(pcm)
            if emb is not None:
                return emb, "wespeaker"
        except Exception as exc:  # noqa: BLE001 — model missing/offline: fall back, don't fail the call
            log.info("ONNX speaker embedding unavailable (%s), using fbank fallback", type(exc).__name__)
    return _fbank_signature(pcm), "fbank"


def _fbank_signature(pcm, n_fft: int = 400, hop: int = 160, n_mels: int = 40) -> list[float] | None:
    """Dependency-free fallback fingerprint: mean+std of voiced log-mel frames (2*n_mels dims),
    L2-normalised. Weaker than the trained model but needs numpy only."""
    try:
        import numpy as np

        x = np.asarray(pcm, dtype=np.float32).flatten()
        if x.size < n_fft * 3:
            return None
        window = np.hanning(n_fft).astype(np.float32)
        n_frames = 1 + (x.size - n_fft) // hop
        if n_frames < 3:
            return None
        idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
        frames = x[idx] * window
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2

        def hz_to_mel(f):
            return 2595.0 * np.log10(1.0 + f / 700.0)

        def mel_to_hz(m):
            return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

        n_freqs = n_fft // 2 + 1
        mel_pts = np.linspace(hz_to_mel(0), hz_to_mel(_SR / 2), n_mels + 2)
        bins = np.floor((n_fft + 1) * mel_to_hz(mel_pts) / _SR).astype(int)
        fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
        for m in range(1, n_mels + 1):
            left, center, right = bins[m - 1], bins[m], bins[m + 1]
            for k in range(left, center):
                if center > left:
                    fb[m - 1, k] = (k - left) / (center - left)
            for k in range(center, right):
                if right > center:
                    fb[m - 1, k] = (right - k) / (right - center)
        logmel = np.log(power @ fb.T + 1e-6)
        energy = logmel.mean(axis=1)
        voiced = logmel[energy > np.percentile(energy, 40)]
        if voiced.shape[0] < 3:
            voiced = logmel
        emb = np.concatenate([voiced.mean(axis=0), voiced.std(axis=0)])
        norm = float(np.linalg.norm(emb))
        return (emb / norm).astype(float).tolist() if norm else None
    except Exception as exc:  # noqa: BLE001
        log.info("fbank signature failed: %s", exc)
        return None
