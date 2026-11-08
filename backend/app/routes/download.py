"""Desktop app distribution. The web app's Download page reads /info and links to /desktop, which
streams an installer placed in the downloads directory (built via `npm run dist` / `npm run
dist:linux` in desktop/). Public on purpose so a plain <a href> download works without carrying an
auth token."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/api/download")
settings = get_settings()

# (glob, platform, label), in the order we prefer to surface them. Only the newest file per pattern
# is offered.
_INSTALLERS = [
    ("*.exe", "windows", "Installeur Windows (.exe)"),
    ("*.msi", "windows", "Installeur Windows (.msi)"),
    ("*.dmg", "macos", "macOS (.dmg)"),
    ("*.AppImage", "linux", "AppImage (toutes distributions)"),
    ("*.deb", "linux", "Debian / Ubuntu (.deb)"),
    ("*.rpm", "linux", "Fedora / RHEL (.rpm)"),
]


def _find_all() -> list[dict]:
    base = Path(settings.downloads_dir)
    if not base.exists():
        return []
    found = []
    for pattern, platform, label in _INSTALLERS:
        matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            path = matches[0]
            found.append({"path": path, "filename": path.name, "size": path.stat().st_size, "platform": platform, "label": label})
    return found


@router.get("/info")
def info():
    found = _find_all()
    if not found:
        return {"available": False, "installers": []}
    first = found[0]
    return {
        "available": True,
        # First installer, kept for older clients.
        "filename": first["filename"], "size": first["size"], "platform": first["platform"],
        "installers": [{k: v for k, v in i.items() if k != "path"} for i in found],
    }


@router.get("/desktop")
def desktop(file: str | None = None):
    found = _find_all()
    if not found:
        raise HTTPException(404, "Aucun installeur disponible. Construisez-le (desktop : npm run dist) puis placez le fichier dans le dossier downloads du backend.")
    if file is None:
        chosen = found[0]
    else:
        # Only names from the listing are served, so `file` can't reach outside the folder.
        chosen = next((i for i in found if i["filename"] == file), None)
        if chosen is None:
            raise HTTPException(404, "Installeur introuvable.")
    return FileResponse(str(chosen["path"]), filename=chosen["filename"], media_type="application/octet-stream")
