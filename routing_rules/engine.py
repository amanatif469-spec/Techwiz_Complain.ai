from sqlalchemy import or_
from sqlalchemy.orm import Session

from complaint_rules.engine import classify_from_rules
from database.models import Department


def _resolve(db: Session, value: str | None) -> Department | None:
    if not value:
        return None
    return db.query(Department).filter(or_(Department.code == value, Department.name == value)).first()


def recommend_departments(db: Session, text: str, python_result: dict | None = None) -> dict:
    """Map rule-matrix department codes to display names for primary and supporting desks."""
    result = python_result or classify_from_rules(db, text)
    primary_code = result.get("department")
    department = _resolve(db, primary_code)
    primary_name = department.name if department else primary_code
    supporting: list[str] = []
    for code in result.get("supporting_departments") or []:
        row = _resolve(db, code)
        name = row.name if row else code
        if name and name != primary_name and name not in supporting:
            supporting.append(name)
    return {
        "primary_department": primary_name,
        "primary_code": department.code if department else primary_code,
        "supporting_departments": supporting,
    }
