import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import audit, exports
from app.api import _guard_entity
from app.auth import User, current_user, perimeter_guard
from app.db import get_db
from app.models import Meeting, Perimeter

router = APIRouter(prefix="/api")

MIME = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _file(data: bytes, name: str, ext: str) -> Response:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name).strip("_")
    disposition = f"attachment; filename=\"{safe}.{ext}\"; filename*=UTF-8''{quote(name.replace(' ', '_'))}.{ext}"
    return Response(data, media_type=MIME[ext], headers={"Content-Disposition": disposition})


@router.get("/perimeters/{perimeter_id}/exports/copil.pptx")
def copil(p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = exports.copil_pptx(db, p)
    audit.log(db, user.username, "export.copil", p.id, "perimeter", p.id)
    db.commit()
    return _file(data, f"COPIL_{p.name}", "pptx")


@router.get("/perimeters/{perimeter_id}/exports/prebilling.xlsx")
def prebilling(year: int, month: int, p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not 1 <= month <= 12:
        raise HTTPException(400, "Invalid month")
    data = exports.prebilling_xlsx(db, p, year, month)
    audit.log(db, user.username, "export.prebilling", p.id, "perimeter", p.id, period=f"{year}-{month:02d}")
    db.commit()
    return _file(data, f"Prefacture_{p.name}_{year}-{month:02d}", "xlsx")


@router.get("/perimeters/{perimeter_id}/exports/onboarding.docx")
def onboarding(p: Perimeter = Depends(perimeter_guard), user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = exports.onboarding_docx(db, p)
    audit.log(db, user.username, "export.onboarding", p.id, "perimeter", p.id)
    db.commit()
    return _file(data, f"Onboarding_{p.name}", "docx")


@router.get("/meetings/{meeting_id}/exports/minutes.docx")
def minutes(meeting_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(404)
    _guard_entity(db, user, meeting.perimeter_id)
    data = exports.minutes_docx(db, meeting)
    audit.log(db, user.username, "export.minutes", meeting.perimeter_id, "meeting", meeting.id)
    db.commit()
    return _file(data, f"CR_{meeting.title}", "docx")
