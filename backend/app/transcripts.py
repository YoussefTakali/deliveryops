"""Parse meeting transcripts exported from Teams (.vtt, .docx) or plain text into
"Speaker: text" lines, merging consecutive cues from the same speaker."""

import io
import re

_VOICE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.DOTALL)
_TIMING = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}\s+-->")
_DOCX_SPEAKER = re.compile(r"^(?P<speaker>[^\d\n][^\n]{1,60}?)\s+\d{1,2}:\d{2}(:\d{2})?\s*$")


def _merge(lines: list[tuple[str, str]]) -> str:
    out: list[list[str]] = []
    for speaker, text in lines:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if out and out[-1][0] == speaker:
            out[-1][1] += " " + text
        else:
            out.append([speaker, text])
    return "\n".join(f"{s}: {t}" for s, t in out)


def parse_vtt(content: str) -> str:
    lines, pending = [], None
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or _TIMING.match(line) or line.isdigit() or re.fullmatch(r"[0-9a-f-]{20,}(/\d+-\d+)?", line):
            continue
        voice = _VOICE.search(line)
        if voice:
            lines.append((voice.group(1).strip(), re.sub(r"<[^>]+>", "", voice.group(2))))
        elif ":" in line[:60]:
            speaker, text = line.split(":", 1)
            lines.append((speaker.strip(), text))
        elif lines:
            lines[-1] = (lines[-1][0], lines[-1][1] + " " + line)
        else:
            pending = (pending or "") + " " + line
    if pending and not lines:
        lines.append(("Participant", pending))
    return _merge(lines)


def parse_docx(data: bytes) -> str:
    import docx

    paragraphs = [p.text.strip() for p in docx.Document(io.BytesIO(data)).paragraphs if p.text.strip()]
    lines, speaker = [], "Participant"
    for p in paragraphs:
        m = _DOCX_SPEAKER.match(p)
        if m:  # Teams .docx: "Firstname Lastname   0:03" on its own line, then the utterance
            speaker = m.group("speaker").strip()
        elif ":" in p[:60] and not p[:60].split(":")[0].strip().isdigit():
            s, t = p.split(":", 1)
            lines.append((s.strip(), t))
        else:
            lines.append((speaker, p))
    return _merge(lines)


def parse(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".docx"):
        return parse_docx(data)
    text = data.decode("utf-8-sig", errors="replace")
    if name.endswith(".vtt") or text.lstrip().startswith("WEBVTT"):
        return parse_vtt(text)
    return _merge([(l.split(":", 1)[0].strip(), l.split(":", 1)[1]) if ":" in l[:60] else ("Participant", l) for l in text.splitlines()])
