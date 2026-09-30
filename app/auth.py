"""JWT auth + password hashing helpers."""
import datetime as dt
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import get_db
from .models import AppUser, LicenseKey, School, Student, SuperAdmin

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_DAYS = 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def hash_secret(value: str) -> str:
    return pwd_context.hash(value)


def verify_secret(value: str, hashed: str) -> bool:
    return pwd_context.verify(value, hashed)


def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=TOKEN_DAYS)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


class Principal:
    """Authenticated caller: superadmin (owner), staff (admin/teacher) or student."""

    def __init__(self, role, school_id, school_code, user_id=None, student_id=None,
                 display_name="", school_name=""):
        self.role = role  # superadmin | admin | teacher | student
        self.school_id = school_id  # None for superadmin
        self.school_code = school_code
        self.user_id = user_id
        self.student_id = student_id
        self.display_name = display_name
        self.school_name = school_name

    @property
    def is_staff(self):
        return self.role in ("admin", "teacher")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_superadmin(self):
        return self.role == "superadmin"


def check_school_license(db: Session, school_id: int) -> tuple[bool, str]:
    """Returns (valid, reason). A school is licensed when it has a bound key
    that is active, not revoked, not expired — and the school is not disabled."""
    school = db.get(School, school_id)
    if not school:
        return False, "School not found"
    if school.is_disabled:
        return False, "This school's access has been disabled by the app owner."
    lic = (
        db.query(LicenseKey)
        .filter(LicenseKey.school_id == school_id)
        .order_by(LicenseKey.bound_at.desc())
        .first()
    )
    if not lic:
        return False, "No license key is bound to this school."
    if not lic.is_active or lic.revoked_at is not None:
        return False, "This school's license key has been revoked by the app owner."
    if lic.expires_at is not None and lic.expires_at <= dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
        return False, "This school's license key has expired."
    return True, "License valid"


def _license_error(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"LICENSE_INVALID: {reason}",
    )


def get_current_principal(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    payload = decode_token(creds.credentials)
    role = payload.get("role")
    school_id = payload.get("school_id")

    # Superadmin (owner): no school scope; validated against super_admins table.
    if role == "superadmin":
        sa = db.get(SuperAdmin, payload.get("user_id"))
        if not sa:
            raise _auth_error()
        return Principal(
            role="superadmin", school_id=None, school_code="",
            user_id=sa.id, display_name=sa.username, school_name="",
        )

    if role not in ("admin", "teacher", "student") or school_id is None:
        raise _auth_error()
    principal = Principal(
        role=role,
        school_id=school_id,
        school_code=payload.get("school_code", ""),
        user_id=payload.get("user_id"),
        student_id=payload.get("student_id"),
        display_name=payload.get("display_name", ""),
        school_name=payload.get("school_name", ""),
    )
    # Re-validate the account still exists (tokens can't outlive deletions)
    if role == "student":
        if not db.get(Student, principal.student_id) or \
                db.get(Student, principal.student_id).school_id != school_id:
            raise _auth_error()
    else:
        if not db.get(AppUser, principal.user_id) or \
                db.get(AppUser, principal.user_id).school_id != school_id:
            raise _auth_error()
    return principal


def _enforce_license(principal: Principal, db: Session) -> Principal:
    """Blocks every school-scoped caller whose license is not currently valid."""
    if principal.is_superadmin:
        return principal
    valid, reason = check_school_license(db, principal.school_id)
    if not valid:
        raise _license_error(reason)
    return principal


def require_staff(principal: Principal = Depends(get_current_principal),
                  db: Session = Depends(get_db)) -> Principal:
    if not principal.is_staff:
        raise HTTPException(status_code=403, detail="Staff only")
    return _enforce_license(principal, db)


def require_admin(principal: Principal = Depends(get_current_principal),
                  db: Session = Depends(get_db)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return _enforce_license(principal, db)


def require_student(principal: Principal = Depends(get_current_principal),
                    db: Session = Depends(get_db)) -> Principal:
    if principal.role != "student":
        raise HTTPException(status_code=403, detail="Students only")
    return _enforce_license(principal, db)


def require_superadmin(principal: Principal = Depends(get_current_principal)) -> Principal:
    if not principal.is_superadmin:
        raise HTTPException(status_code=403, detail="Owner only")
    return principal
