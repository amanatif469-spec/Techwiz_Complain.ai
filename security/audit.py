from database.models import AuditLog
from sqlalchemy.orm import Session


def write_audit(
    db: Session,
    *,
    actor_id: int | None,
    entity_type: str,
    entity_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            details=details or {},
        )
    )
