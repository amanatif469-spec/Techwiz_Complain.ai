from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from database.models import Customer, CustomerType, User, UserRole
from database.session import get_db
from security.auth import CurrentUser, create_access_token, hash_password, verify_password
from security.audit import write_audit
from src.api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.email, user.role.value)
    return TokenResponse(access_token=token, role=user.role.value)


@router.post("/login-json", response_model=TokenResponse)
def login_json(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.email, user.role.value), role=user.role.value)


@router.post("/register", response_model=UserOut)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    # Public registration is limited to customers. Staff accounts are created by administrators.
    role = UserRole.customer if payload.role != UserRole.administrator else UserRole.customer
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    db.flush()
    count = db.query(Customer).count() + 1
    db.add(
        Customer(
            user_id=user.id,
            customer_code=f"CUST-{10000 + count}",
            display_name=payload.full_name,
            customer_type=payload.customer_type,
            email=payload.email,
            is_vip=payload.customer_type == CustomerType.vip,
        )
    )
    write_audit(db, actor_id=user.id, entity_type="user", entity_id=str(user.id), action="register")
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
