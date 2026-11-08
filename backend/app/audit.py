from sqlalchemy.orm import Session

from app.models import AuditLog


def log(db: Session, actor: str, action: str, perimeter_id: str | None = None, entity: str = "", entity_id: str = "", **details) -> None:
    db.add(AuditLog(actor=actor, action=action, perimeter_id=perimeter_id, entity=entity, entity_id=entity_id, details=details))
