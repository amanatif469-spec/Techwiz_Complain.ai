from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.models import Customer, CustomerType, User, UserRole
from database.session import get_db
from security.audit import write_audit
from security.auth import AdminUser, StaffUser, hash_password
from src.api.schemas import RegisterRequest, UserOut

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(user: AdminUser, db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@router.get("/staff")
def list_staff(user: StaffUser, db: Session = Depends(get_db)):
    """Assignable staff for the assignment picker; available to every staff role."""
    rows = db.query(User).filter(User.role != UserRole.customer, User.is_active.is_(True)).order_by(User.full_name).all()
    return [{"id": u.id, "full_name": u.full_name, "role": u.role.value} for u in rows]


@router.post("", response_model=UserOut)
def create_user(payload: RegisterRequest, user: AdminUser, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email exists")
    row = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(row)
    db.flush()
    if payload.role == UserRole.customer:
        # Without a profile a customer account could log in but never submit a complaint.
        db.add(
            Customer(
                user_id=row.id,
                customer_code=f"CUST-{10000 + db.query(Customer).count() + 1}",
                display_name=payload.full_name,
                customer_type=payload.customer_type,
                email=payload.email,
                is_vip=payload.customer_type == CustomerType.vip,
            )
        )
    write_audit(db, actor_id=user.id, entity_type="user", entity_id=str(row.id), action="create", details={"role": payload.role.value})
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{user_id}/active")
def set_active(user_id: int, is_active: bool, user: AdminUser, db: Session = Depends(get_db)):
    row = db.query(User).filter(User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row.id == user.id and not is_active:
        raise HTTPException(status_code=422, detail="You cannot deactivate your own account.")
    row.is_active = is_active
    write_audit(db, actor_id=user.id, entity_type="user", entity_id=str(row.id), action="set_active", details={"is_active": is_active})
    db.commit()
    return UserOut.model_validate(row)
