"""Naija School Manager v3.0 — FastAPI application (license-key access control)."""
import datetime as dt
import secrets
import string
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from . import models, schemas
from .auth import (
    Principal,
    check_school_license,
    create_access_token,
    get_current_principal,
    hash_secret,
    require_admin,
    require_staff,
    require_student,
    require_superadmin,
    verify_secret,
)
from .database import Base, engine, get_db
from .grading import DEFAULT_WAEC_BANDS, grade_for

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Naija School Manager API",
    version="3.0.0",
    description="Multi-school cloud backend with owner-issued license keys: "
                "students, scores, fees, attendance, timetable, announcements.",
)


# ---------- licensing helpers ----------

_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


def generate_license_key() -> str:
    parts = ["".join(secrets.choice(_KEY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "NSM-" + "-".join(parts)


def ensure_legacy_licenses(db: Session):
    """Schools created before licensing (or without a key) get a perpetual
    auto-issued key so nothing that already works breaks."""
    schools = db.query(models.School).all()
    for school in schools:
        has = db.query(models.LicenseKey).filter(
            models.LicenseKey.school_id == school.id).first()
        if not has:
            db.add(models.LicenseKey(
                key=generate_license_key(), school_id=school.id,
                label="legacy-auto", max_students=2000,
                expires_at=None, is_active=True,
                bound_at=dt.datetime.utcnow(),
            ))
    db.commit()


@app.on_event("startup")
def _startup_licensing():
    db = next(get_db())
    try:
        ensure_legacy_licenses(db)
    finally:
        db.close()


# ---------- helpers ----------


def scoped(model, db: Session, principal: Principal):
    """Query limited to the caller's school."""
    return db.query(model).filter(model.school_id == principal.school_id)


def get_or_404(model, obj_id: int, db: Session, principal: Principal):
    obj = db.get(model, obj_id)
    if not obj or obj.school_id != principal.school_id:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def default_term_session(school: models.School, term: Optional[int], session: Optional[str]):
    term = term if term is not None else school.current_term
    session = session if session is not None else school.session
    if term not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="term must be 1, 2 or 3")
    return term, session


def _arm_filter(model_class_name, model_arm, class_name: str, arm: Optional[str]):
    """class_name/arm equality with NULL-safe arm comparison."""
    conds = [model_class_name == class_name]
    conds.append(model_arm.is_(None) if arm is None else model_arm == arm)
    return and_(*conds)


def student_total(db: Session, school_id: int, student: models.Student, term: int, session: str) -> float:
    total = (
        db.query(func.coalesce(func.sum(models.Score.ca + models.Score.exam), 0.0))
        .filter(
            models.Score.school_id == school_id,
            models.Score.student_id == student.id,
            models.Score.term == term,
            models.Score.session == session,
        )
        .scalar()
    )
    return float(total)


def build_report_card(db: Session, student: models.Student, term: int, session: str):
    school = db.get(models.School, student.school_id)
    bands = school.grading_bands or DEFAULT_WAEC_BANDS

    scores = (
        db.query(models.Score)
        .options(joinedload(models.Score.subject))
        .filter(
            models.Score.school_id == student.school_id,
            models.Score.student_id == student.id,
            models.Score.term == term,
            models.Score.session == session,
        )
        .all()
    )

    rows = []
    for sc in scores:
        total = (sc.ca or 0) + (sc.exam or 0)
        grade, remark = grade_for(total, bands)
        avg = (
            db.query(func.coalesce(func.avg(models.Score.ca + models.Score.exam), 0.0))
            .join(models.Student, models.Student.id == models.Score.student_id)
            .filter(
                models.Score.school_id == student.school_id,
                models.Score.subject_id == sc.subject_id,
                models.Score.term == term,
                models.Score.session == session,
                _arm_filter(models.Student.class_name, models.Student.arm,
                            student.class_name, student.arm),
            )
            .scalar()
        )
        rows.append(
            schemas.ReportSubjectRow(
                subject=sc.subject.name,
                ca=sc.ca, exam=sc.exam, total=total,
                grade=grade, remark=remark, class_average=round(float(avg), 2),
            )
        )
    rows.sort(key=lambda r: r.subject)

    total = round(sum(r.total for r in rows), 2)
    average = round(total / len(rows), 2) if rows else 0.0

    # Position in class: standard competition ranking, ties share a position
    classmates = (
        db.query(models.Student)
        .filter(
            models.Student.school_id == student.school_id,
            _arm_filter(models.Student.class_name, models.Student.arm,
                        student.class_name, student.arm),
        )
        .all()
    )
    totals = {
        c.id: student_total(db, student.school_id, c, term, session) for c in classmates
    }
    ranked = [c for c in classmates if totals[c.id] > 0] or (
        [student] if student.id in totals else []
    )
    mine = totals[student.id]
    higher = sum(1 for t in totals.values() if t > mine)
    position = (higher + 1) if mine > 0 else len(ranked) or 1
    # If the student has no scores at all, they are unranked -> place last
    if mine == 0:
        position = len([t for t in totals.values() if t > 0]) + 1

    grade_counts = {b["grade"]: 0 for b in bands}
    for r in rows:
        grade_counts[r.grade] = grade_counts.get(r.grade, 0) + 1

    # Attendance: all marks recorded for this student
    marks = (
        db.query(models.AttendanceMark.status, func.count())
        .join(models.AttendanceDay, models.AttendanceDay.id == models.AttendanceMark.day_id)
        .filter(
            models.AttendanceDay.school_id == student.school_id,
            models.AttendanceMark.student_id == student.id,
        )
        .group_by(models.AttendanceMark.status)
        .all()
    )
    counts = {"present": 0, "absent": 0, "late": 0}
    for st, n in marks:
        counts[st] = n
    attendance = schemas.AttendanceSummary(
        present=counts["present"], absent=counts["absent"], late=counts["late"],
        total=sum(counts.values()),
    )

    return schemas.ReportCardOut(
        student=schemas.StudentOut.model_validate(student),
        term=term, session=session,
        subjects=rows, total=total, average=average,
        position_in_class=position, class_size=len(classmates),
        grade_counts=grade_counts, attendance=attendance,
    )


# ---------- health ----------


@app.get("/health")
def health():
    return {"status": "ok", "app": "naija-school-manager", "version": "3.0.0"}


# ---------- auth ----------


@app.post("/auth/register-school", response_model=schemas.TokenOut, status_code=201)
def register_school(payload: schemas.RegisterSchoolIn, db: Session = Depends(get_db)):
    """Registers a new school + its first admin. REQUIRES a valid owner-issued
    license key: the key must exist, be active, unexpired and not already bound
    to another school. On success the key is permanently bound to this school."""
    if db.query(models.School).filter(models.School.school_code == payload.school_code).first():
        raise HTTPException(status_code=409, detail="school_code already exists")

    # --- license gate ---
    key_str = payload.license_key.strip().upper()
    lic = db.query(models.LicenseKey).filter_by(key=key_str).first()
    if not lic:
        raise HTTPException(status_code=403, detail="LICENSE_INVALID: License key not recognized.")
    if lic.school_id is not None:
        raise HTTPException(status_code=403,
                            detail="LICENSE_INVALID: This license key is already used by another school.")
    if not lic.is_active or lic.revoked_at is not None:
        raise HTTPException(status_code=403,
                            detail="LICENSE_INVALID: This license key has been revoked.")
    if lic.expires_at is not None and lic.expires_at <= dt.datetime.utcnow():
        raise HTTPException(status_code=403,
                            detail="LICENSE_INVALID: This license key has expired.")
    # --- end license gate ---

    school = models.School(
        name=payload.school_name, motto=payload.motto, address=payload.address,
        phone=payload.phone, school_code=payload.school_code,
        current_term=payload.current_term, session=payload.session,
    )
    admin = models.AppUser(
        school=school, username=payload.admin_username,
        password_hash=hash_secret(payload.admin_password), role="admin",
    )
    db.add_all([school, admin])
    db.flush()
    # bind the key to this school
    lic.school_id = school.id
    lic.bound_at = dt.datetime.utcnow()
    if not lic.label:
        lic.label = payload.school_name
    db.commit()
    db.refresh(school)
    db.refresh(admin)
    token = create_access_token({
        "role": "admin", "school_id": school.id, "school_code": school.school_code,
        "user_id": admin.id, "display_name": admin.username, "school_name": school.name,
    })
    return schemas.TokenOut(
        token=token, role="admin", school_name=school.name,
        school_code=school.school_code, display_name=admin.username,
    )


# ---------- license status ----------


@app.get("/license/status", response_model=schemas.LicenseStatusOut)
def license_status(principal: Principal = Depends(get_current_principal),
                   db: Session = Depends(get_db)):
    """Any authenticated user can check whether their school's license is valid."""
    if principal.is_superadmin:
        raise HTTPException(status_code=400, detail="Superadmin is not a licensed school")
    valid, reason = check_school_license(db, principal.school_id)
    lic = (db.query(models.LicenseKey)
           .filter(models.LicenseKey.school_id == principal.school_id)
           .order_by(models.LicenseKey.bound_at.desc()).first())
    return schemas.LicenseStatusOut(
        valid=valid, reason=reason, school_name=principal.school_name,
        school_code=principal.school_code,
        key_label=lic.label if lic else None,
        expires_at=lic.expires_at if lic else None,
    )


# ---------- superadmin (owner) ----------


@app.post("/superadmin/login", response_model=schemas.TokenOut)
def superadmin_login(payload: schemas.SuperAdminLoginIn, db: Session = Depends(get_db)):
    sa = db.query(models.SuperAdmin).filter_by(username=payload.username).first()
    if not sa or not verify_secret(payload.password, sa.password_hash):
        raise HTTPException(status_code=401, detail="Invalid owner credentials")
    token = create_access_token({
        "role": "superadmin", "school_id": None, "school_code": "",
        "user_id": sa.id, "display_name": sa.username, "school_name": "",
    })
    return schemas.TokenOut(token=token, role="superadmin", school_name="",
                            school_code="", display_name=sa.username)


@app.post("/superadmin/licenses", response_model=list[schemas.LicenseKeyOut],
          status_code=status.HTTP_201_CREATED)
def superadmin_generate_licenses(payload: schemas.LicenseGenerateIn,
                                 db: Session = Depends(get_db),
                                 principal: Principal = Depends(require_superadmin)):
    """Generate one or more fresh, unbound license keys."""
    out = []
    expires = None
    if payload.expiry_days:
        expires = dt.datetime.utcnow() + dt.timedelta(days=payload.expiry_days)
    for _ in range(payload.count):
        for _attempt in range(10):
            key = generate_license_key()
            if not db.query(models.LicenseKey).filter_by(key=key).first():
                break
        else:
            raise HTTPException(status_code=500, detail="Could not generate a unique key")
        lic = models.LicenseKey(
            key=key, school_id=None, label=payload.label,
            max_students=payload.max_students, expires_at=expires, is_active=True,
        )
        db.add(lic)
        out.append(lic)
    db.commit()
    for lic in out:
        db.refresh(lic)
    return [_license_row(db, lic) for lic in out]


def _license_row(db: Session, lic: models.LicenseKey) -> schemas.LicenseKeyOut:
    school = db.get(models.School, lic.school_id) if lic.school_id else None
    return schemas.LicenseKeyOut(
        key=lic.key, label=lic.label,
        school_code=school.school_code if school else None,
        school_name=school.name if school else None,
        max_students=lic.max_students, expires_at=lic.expires_at,
        is_active=lic.is_active, revoked_at=lic.revoked_at,
        bound_at=lic.bound_at, created_at=lic.created_at,
    )


@app.get("/superadmin/licenses", response_model=list[schemas.LicenseKeyOut])
def superadmin_list_licenses(db: Session = Depends(get_db),
                             principal: Principal = Depends(require_superadmin)):
    lics = db.query(models.LicenseKey).order_by(models.LicenseKey.created_at.desc()).all()
    return [_license_row(db, lic) for lic in lics]


@app.post("/superadmin/licenses/{key}/revoke", response_model=schemas.LicenseKeyOut)
def superadmin_revoke_license(key: str, db: Session = Depends(get_db),
                              principal: Principal = Depends(require_superadmin)):
    lic = db.query(models.LicenseKey).filter_by(key=key.upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")
    lic.is_active = False
    lic.revoked_at = dt.datetime.utcnow()
    db.commit()
    return _license_row(db, lic)


@app.post("/superadmin/licenses/{key}/unrevoke", response_model=schemas.LicenseKeyOut)
def superadmin_unrevoke_license(key: str, db: Session = Depends(get_db),
                                principal: Principal = Depends(require_superadmin)):
    lic = db.query(models.LicenseKey).filter_by(key=key.upper()).first()
    if not lic:
        raise HTTPException(status_code=404, detail="Key not found")
    lic.is_active = True
    lic.revoked_at = None
    db.commit()
    return _license_row(db, lic)


@app.get("/superadmin/schools", response_model=list[schemas.SchoolLicenseRow])
def superadmin_list_schools(db: Session = Depends(get_db),
                            principal: Principal = Depends(require_superadmin)):
    rows = []
    for school in db.query(models.School).order_by(models.School.id).all():
        valid, reason = check_school_license(db, school.id)
        lic = (db.query(models.LicenseKey)
               .filter(models.LicenseKey.school_id == school.id)
               .order_by(models.LicenseKey.bound_at.desc()).first())
        student_count = db.query(models.Student).filter_by(school_id=school.id).count()
        rows.append(schemas.SchoolLicenseRow(
            id=school.id, name=school.name, school_code=school.school_code,
            is_disabled=school.is_disabled,
            license_key=lic.key if lic else None,
            license_valid=valid, license_reason=reason,
            student_count=student_count, created_at=school.created_at,
        ))
    return rows


@app.post("/superadmin/schools/{school_id}/disable")
def superadmin_disable_school(school_id: int, db: Session = Depends(get_db),
                              principal: Principal = Depends(require_superadmin)):
    school = db.get(models.School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    school.is_disabled = True
    db.commit()
    return {"school_id": school.id, "is_disabled": True}


@app.post("/superadmin/schools/{school_id}/enable")
def superadmin_enable_school(school_id: int, db: Session = Depends(get_db),
                             principal: Principal = Depends(require_superadmin)):
    school = db.get(models.School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    school.is_disabled = False
    db.commit()
    return {"school_id": school.id, "is_disabled": False}


@app.post("/auth/login", response_model=schemas.TokenOut)
def login(payload: schemas.LoginIn, db: Session = Depends(get_db)):
    school = db.query(models.School).filter(
        models.School.school_code == payload.school_code
    ).first()
    if not school:
        raise HTTPException(status_code=401, detail="Invalid school code or credentials")

    # Staff first: username + password
    user = db.query(models.AppUser).filter(
        models.AppUser.school_id == school.id,
        models.AppUser.username == payload.username,
    ).first()
    if user:
        if not verify_secret(payload.password_or_pin, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid school code or credentials")
        token = create_access_token({
            "role": user.role, "school_id": school.id, "school_code": school.school_code,
            "user_id": user.id, "display_name": user.username, "school_name": school.name,
        })
        return schemas.TokenOut(
            token=token, role=user.role, school_name=school.name,
            school_code=school.school_code, display_name=user.username,
        )

    # Students: admission_no + PIN
    student = db.query(models.Student).filter(
        models.Student.school_id == school.id,
        models.Student.admission_no == payload.username,
    ).first()
    if student and student.pin_hash and verify_secret(payload.password_or_pin, student.pin_hash):
        token = create_access_token({
            "role": "student", "school_id": school.id, "school_code": school.school_code,
            "student_id": student.id, "display_name": student.full_name,
            "school_name": school.name,
        })
        return schemas.TokenOut(
            token=token, role="student", school_name=school.name,
            school_code=school.school_code, display_name=student.full_name,
        )
    raise HTTPException(status_code=401, detail="Invalid school code or credentials")


# ---------- school settings ----------


class SchoolUpdateIn(BaseModel):
    name: Optional[str] = None
    motto: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    current_term: Optional[int] = Field(default=None, ge=1, le=3)
    session: Optional[str] = None
    ca_max: Optional[float] = Field(default=None, gt=0)
    exam_max: Optional[float] = Field(default=None, gt=0)
    grading_bands: Optional[list] = None


class SchoolOut(BaseModel):
    id: int
    name: str
    motto: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    school_code: str
    current_term: int
    session: str
    ca_max: float
    exam_max: float
    grading_bands: Optional[list] = None

    model_config = {"from_attributes": True}


@app.get("/schools/me", response_model=SchoolOut)
def get_school(db: Session = Depends(get_db),
               principal: Principal = Depends(require_staff)):
    return db.get(models.School, principal.school_id)


@app.patch("/schools/me", response_model=SchoolOut, dependencies=[Depends(require_admin)])
def update_school(payload: SchoolUpdateIn, db: Session = Depends(get_db),
                  principal: Principal = Depends(require_admin)):
    school = db.get(models.School, principal.school_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(school, k, v)
    db.commit()
    db.refresh(school)
    return school


# ---------- users (admin only) ----------


@app.post("/users", response_model=schemas.UserOut, status_code=201,
          dependencies=[Depends(require_admin)])
def create_user(payload: schemas.UserCreateIn, db: Session = Depends(get_db),
                principal: Principal = Depends(require_admin)):
    user = models.AppUser(
        school_id=principal.school_id, username=payload.username,
        password_hash=hash_secret(payload.password), role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists in this school")
    db.refresh(user)
    return user


@app.get("/users", response_model=list[schemas.UserOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db), principal: Principal = Depends(require_admin)):
    return scoped(models.AppUser, db, principal).all()


# ---------- students ----------


@app.post("/students", response_model=schemas.StudentOut, status_code=201)
def create_student(payload: schemas.StudentIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    student = models.Student(school_id=principal.school_id, **payload.model_dump())
    db.add(student)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="admission_no already exists in this school")
    db.refresh(student)
    return student


@app.get("/students", response_model=list[schemas.StudentOut])
def list_students(
    class_name: Optional[str] = None, arm: Optional[str] = None, q: Optional[str] = None,
    db: Session = Depends(get_db), principal: Principal = Depends(require_staff),
):
    query = scoped(models.Student, db, principal)
    if class_name:
        query = query.filter(models.Student.class_name == class_name)
    if arm is not None:
        query = query.filter(models.Student.arm == arm)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.Student.full_name.ilike(like) | models.Student.admission_no.ilike(like)
        )
    return query.order_by(models.Student.full_name).all()


@app.get("/students/{student_id}", response_model=schemas.StudentOut)
def get_student(student_id: int, db: Session = Depends(get_db),
                principal: Principal = Depends(require_staff)):
    return get_or_404(models.Student, student_id, db, principal)


@app.put("/students/{student_id}", response_model=schemas.StudentOut)
def update_student(student_id: int, payload: schemas.StudentIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    student = get_or_404(models.Student, student_id, db, principal)
    for k, v in payload.model_dump().items():
        setattr(student, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="admission_no already exists in this school")
    db.refresh(student)
    return student


@app.delete("/students/{student_id}", status_code=204)
def delete_student(student_id: int, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    student = get_or_404(models.Student, student_id, db, principal)
    db.delete(student)
    db.commit()
    return None


@app.post("/students/{student_id}/pin", dependencies=[Depends(require_admin)])
def set_student_pin(student_id: int, payload: schemas.PinIn, db: Session = Depends(get_db),
                    principal: Principal = Depends(require_admin)):
    """Admin sets/resets a student's login PIN."""
    student = get_or_404(models.Student, student_id, db, principal)
    student.pin_hash = hash_secret(payload.pin)
    db.commit()
    return {"detail": f"PIN set for {student.admission_no}"}


@app.get("/students/{student_id}/report-card", response_model=schemas.ReportCardOut)
def staff_report_card(
    student_id: int, term: Optional[int] = None, session: Optional[str] = None,
    db: Session = Depends(get_db), principal: Principal = Depends(require_staff),
):
    student = get_or_404(models.Student, student_id, db, principal)
    term, session = default_term_session(db.get(models.School, principal.school_id), term, session)
    return build_report_card(db, student, term, session)


# ---------- class arms ----------


@app.get("/class-arms", response_model=list[schemas.ClassArmOut])
def list_class_arms(db: Session = Depends(get_db), principal: Principal = Depends(require_staff)):
    return scoped(models.ClassArm, db, principal).order_by(
        models.ClassArm.class_name, models.ClassArm.arm).all()


@app.post("/class-arms", response_model=schemas.ClassArmOut, status_code=201)
def create_class_arm(payload: schemas.ClassArmIn, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_staff)):
    arm = models.ClassArm(school_id=principal.school_id, **payload.model_dump())
    db.add(arm)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Class arm already exists in this school")
    db.refresh(arm)
    return arm


@app.put("/class-arms/{arm_id}", response_model=schemas.ClassArmOut)
def update_class_arm(arm_id: int, payload: schemas.ClassArmIn, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_staff)):
    arm = get_or_404(models.ClassArm, arm_id, db, principal)
    for k, v in payload.model_dump().items():
        setattr(arm, k, v)
    db.commit()
    db.refresh(arm)
    return arm


@app.delete("/class-arms/{arm_id}", status_code=204)
def delete_class_arm(arm_id: int, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_staff)):
    db.delete(get_or_404(models.ClassArm, arm_id, db, principal))
    db.commit()
    return None


# ---------- subjects ----------


@app.get("/subjects", response_model=list[schemas.SubjectOut])
def list_subjects(db: Session = Depends(get_db), principal: Principal = Depends(require_staff)):
    return scoped(models.Subject, db, principal).order_by(models.Subject.name).all()


@app.post("/subjects", response_model=schemas.SubjectOut, status_code=201)
def create_subject(payload: schemas.SubjectIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    subject = models.Subject(school_id=principal.school_id, **payload.model_dump())
    db.add(subject)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Subject already exists in this school")
    db.refresh(subject)
    return subject


@app.put("/subjects/{subject_id}", response_model=schemas.SubjectOut)
def update_subject(subject_id: int, payload: schemas.SubjectIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    subject = get_or_404(models.Subject, subject_id, db, principal)
    subject.name, subject.code = payload.name, payload.code
    db.commit()
    db.refresh(subject)
    return subject


@app.delete("/subjects/{subject_id}", status_code=204)
def delete_subject(subject_id: int, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    db.delete(get_or_404(models.Subject, subject_id, db, principal))
    db.commit()
    return None


# ---------- scores ----------


@app.post("/scores", response_model=schemas.ScoreOut, status_code=201)
def upsert_score(payload: schemas.ScoreIn, db: Session = Depends(get_db),
                 principal: Principal = Depends(require_staff)):
    school = db.get(models.School, principal.school_id)
    term, session = default_term_session(school, payload.term, payload.session)
    if payload.ca > school.ca_max or payload.exam > school.exam_max:
        raise HTTPException(
            status_code=400,
            detail=f"ca must be <= {school.ca_max}, exam must be <= {school.exam_max}",
        )
    student = get_or_404(models.Student, payload.student_id, db, principal)
    subject = get_or_404(models.Subject, payload.subject_id, db, principal)

    score = db.query(models.Score).filter(
        models.Score.student_id == student.id,
        models.Score.subject_id == subject.id,
        models.Score.term == term,
        models.Score.session == session,
    ).first()
    if score:
        score.ca, score.exam = payload.ca, payload.exam
    else:
        score = models.Score(
            school_id=principal.school_id, student_id=student.id, subject_id=subject.id,
            term=term, session=session, ca=payload.ca, exam=payload.exam,
        )
        db.add(score)
    db.commit()
    db.refresh(score)
    return schemas.ScoreOut(
        id=score.id, student_id=score.student_id, subject_id=score.subject_id,
        term=score.term, session=score.session,
        ca=score.ca, exam=score.exam, total=score.ca + score.exam,
    )


@app.get("/scores", response_model=list[schemas.ScoreOut])
def list_scores(
    student_id: Optional[int] = None, subject_id: Optional[int] = None,
    term: Optional[int] = None, session: Optional[str] = None,
    db: Session = Depends(get_db), principal: Principal = Depends(require_staff),
):
    school = db.get(models.School, principal.school_id)
    term, session = default_term_session(school, term, session)
    query = scoped(models.Score, db, principal).filter(
        models.Score.term == term, models.Score.session == session)
    if student_id:
        query = query.filter(models.Score.student_id == student_id)
    if subject_id:
        query = query.filter(models.Score.subject_id == subject_id)
    return [
        schemas.ScoreOut(
            id=s.id, student_id=s.student_id, subject_id=s.subject_id,
            term=s.term, session=s.session,
            ca=s.ca, exam=s.exam, total=s.ca + s.exam,
        )
        for s in query.all()
    ]


# ---------- fees ----------


@app.post("/fees/upsert", response_model=schemas.FeeOut, status_code=201)
def upsert_fee(payload: schemas.FeeUpsertIn, db: Session = Depends(get_db),
               principal: Principal = Depends(require_staff)):
    school = db.get(models.School, principal.school_id)
    term, session = default_term_session(school, payload.term, payload.session)
    student = get_or_404(models.Student, payload.student_id, db, principal)
    fee = db.query(models.FeeRecord).filter(
        models.FeeRecord.student_id == student.id,
        models.FeeRecord.term == term, models.FeeRecord.session == session,
    ).first()
    if fee:
        fee.billed = payload.billed
    else:
        fee = models.FeeRecord(
            school_id=principal.school_id, student_id=student.id,
            term=term, session=session, billed=payload.billed, paid=0.0,
        )
        db.add(fee)
    db.commit()
    db.refresh(fee)
    return schemas.FeeOut(
        id=fee.id, student_id=fee.student_id, term=fee.term, session=fee.session,
        billed=fee.billed, paid=fee.paid, balance=fee.billed - fee.paid,
    )


@app.post("/fees/{student_id}/pay", response_model=schemas.FeeOut)
def record_payment(student_id: int, payload: schemas.FeePayIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require_staff)):
    school = db.get(models.School, principal.school_id)
    term, session = default_term_session(school, payload.term, payload.session)
    student = get_or_404(models.Student, student_id, db, principal)
    fee = db.query(models.FeeRecord).filter(
        models.FeeRecord.student_id == student.id,
        models.FeeRecord.term == term, models.FeeRecord.session == session,
    ).first()
    if not fee:
        fee = models.FeeRecord(
            school_id=principal.school_id, student_id=student.id,
            term=term, session=session, billed=0.0, paid=0.0,
        )
        db.add(fee)
    fee.paid = (fee.paid or 0) + payload.amount
    db.commit()
    db.refresh(fee)
    return schemas.FeeOut(
        id=fee.id, student_id=fee.student_id, term=fee.term, session=fee.session,
        billed=fee.billed, paid=fee.paid, balance=fee.billed - fee.paid,
    )


@app.get("/fees", response_model=list[schemas.FeeOut])
def list_fees(
    student_id: Optional[int] = None, term: Optional[int] = None,
    session: Optional[str] = None, db: Session = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    school = db.get(models.School, principal.school_id)
    term, session = default_term_session(school, term, session)
    query = scoped(models.FeeRecord, db, principal).filter(
        models.FeeRecord.term == term, models.FeeRecord.session == session)
    if student_id:
        query = query.filter(models.FeeRecord.student_id == student_id)
    return [
        schemas.FeeOut(id=f.id, student_id=f.student_id, term=f.term, session=f.session,
                       billed=f.billed, paid=f.paid, balance=f.billed - f.paid)
        for f in query.all()
    ]


# ---------- attendance ----------


@app.post("/attendance", status_code=201)
def post_attendance(payload: schemas.AttendanceDayIn, db: Session = Depends(get_db),
                    principal: Principal = Depends(require_staff)):
    day = db.query(models.AttendanceDay).filter(
        models.AttendanceDay.school_id == principal.school_id,
        _arm_filter(models.AttendanceDay.class_name, models.AttendanceDay.arm,
                    payload.class_name, payload.arm),
        models.AttendanceDay.date == payload.date,
    ).first()
    if not day:
        day = models.AttendanceDay(
            school_id=principal.school_id, class_name=payload.class_name,
            arm=payload.arm, date=payload.date,
        )
        db.add(day)
        db.flush()
    for mark_in in payload.marks:
        student = db.get(models.Student, mark_in.student_id)
        if not student or student.school_id != principal.school_id:
            raise HTTPException(
                status_code=400,
                detail=f"Student {mark_in.student_id} not found in this school",
            )
        mark = db.query(models.AttendanceMark).filter(
            models.AttendanceMark.day_id == day.id,
            models.AttendanceMark.student_id == mark_in.student_id,
        ).first()
        if mark:
            mark.status = mark_in.status
        else:
            db.add(models.AttendanceMark(
                day_id=day.id, student_id=mark_in.student_id, status=mark_in.status))
    db.commit()
    return {"detail": "Attendance saved", "day_id": day.id}


@app.get("/attendance")
def get_attendance(
    class_name: str = Query(...), arm: Optional[str] = None,
    date: Optional[dt.date] = None, db: Session = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    query = db.query(models.AttendanceDay).filter(
        models.AttendanceDay.school_id == principal.school_id,
        _arm_filter(models.AttendanceDay.class_name, models.AttendanceDay.arm,
                    class_name, arm),
    )
    if date:
        query = query.filter(models.AttendanceDay.date == date)
    days = query.options(joinedload(models.AttendanceDay.marks)).all()
    return [
        {
            "id": d.id, "class_name": d.class_name, "arm": d.arm,
            "date": d.date.isoformat(),
            "marks": [{"student_id": m.student_id, "status": m.status} for m in d.marks],
        }
        for d in days
    ]


# ---------- timetable ----------


@app.get("/timetable", response_model=list[schemas.TimetableOut])
def list_timetable(
    class_name: str = Query(...), arm: Optional[str] = None,
    day_of_week: Optional[int] = None, db: Session = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    query = scoped(models.TimetableEntry, db, principal).filter(
        _arm_filter(models.TimetableEntry.class_name, models.TimetableEntry.arm,
                    class_name, arm))
    if day_of_week is not None:
        query = query.filter(models.TimetableEntry.day_of_week == day_of_week)
    return query.order_by(models.TimetableEntry.day_of_week, models.TimetableEntry.period).all()


@app.post("/timetable", response_model=schemas.TimetableOut, status_code=201)
def upsert_timetable(payload: schemas.TimetableIn, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_staff)):
    entry = db.query(models.TimetableEntry).filter(
        models.TimetableEntry.school_id == principal.school_id,
        _arm_filter(models.TimetableEntry.class_name, models.TimetableEntry.arm,
                    payload.class_name, payload.arm),
        models.TimetableEntry.day_of_week == payload.day_of_week,
        models.TimetableEntry.period == payload.period,
    ).first()
    if entry:
        entry.subject, entry.time_label = payload.subject, payload.time_label
    else:
        entry = models.TimetableEntry(school_id=principal.school_id, **payload.model_dump())
        db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/timetable/{entry_id}", status_code=204)
def delete_timetable(entry_id: int, db: Session = Depends(get_db),
                     principal: Principal = Depends(require_staff)):
    db.delete(get_or_404(models.TimetableEntry, entry_id, db, principal))
    db.commit()
    return None


# ---------- announcements ----------


@app.get("/announcements", response_model=list[schemas.AnnouncementOut])
def list_announcements(audience: Optional[str] = None, db: Session = Depends(get_db),
                       principal: Principal = Depends(require_staff)):
    query = scoped(models.Announcement, db, principal)
    if audience:
        query = query.filter(models.Announcement.audience == audience)
    return query.order_by(models.Announcement.created_at.desc()).all()


@app.post("/announcements", response_model=schemas.AnnouncementOut, status_code=201)
def create_announcement(payload: schemas.AnnouncementIn, db: Session = Depends(get_db),
                        principal: Principal = Depends(require_staff)):
    ann = models.Announcement(school_id=principal.school_id, **payload.model_dump())
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return ann


@app.delete("/announcements/{announcement_id}", status_code=204)
def delete_announcement(announcement_id: int, db: Session = Depends(get_db),
                        principal: Principal = Depends(require_staff)):
    db.delete(get_or_404(models.Announcement, announcement_id, db, principal))
    db.commit()
    return None


# ---------- student self-service ----------


def _current_student(db: Session = Depends(get_db),
                   principal: Principal = Depends(require_student)):
    student = db.get(models.Student, principal.student_id)
    if not student or student.school_id != principal.school_id:
        raise HTTPException(status_code=404, detail="Student record not found")
    return student


@app.get("/me/report-card", response_model=schemas.ReportCardOut)
def my_report_card(
    term: Optional[int] = None, session: Optional[str] = None,
    db: Session = Depends(get_db),
    student: models.Student = Depends(_current_student),
):
    school = db.get(models.School, student.school_id)
    term, session = default_term_session(school, term, session)
    return build_report_card(db, student, term, session)


@app.get("/me/fees", response_model=list[schemas.FeeOut])
def my_fees(
    term: Optional[int] = None, session: Optional[str] = None,
    db: Session = Depends(get_db),
    student: models.Student = Depends(_current_student),
):
    query = db.query(models.FeeRecord).filter(models.FeeRecord.student_id == student.id)
    if term is not None:
        query = query.filter(models.FeeRecord.term == term)
    if session is not None:
        query = query.filter(models.FeeRecord.session == session)
    return [
        schemas.FeeOut(id=f.id, student_id=f.student_id, term=f.term, session=f.session,
                       billed=f.billed, paid=f.paid, balance=f.billed - f.paid)
        for f in query.order_by(models.FeeRecord.term).all()
    ]


@app.get("/me/attendance", response_model=schemas.AttendanceSummary)
def my_attendance(db: Session = Depends(get_db),
                 student: models.Student = Depends(_current_student)):
    marks = (
        db.query(models.AttendanceMark.status, func.count())
        .join(models.AttendanceDay, models.AttendanceDay.id == models.AttendanceMark.day_id)
        .filter(
            models.AttendanceDay.school_id == student.school_id,
            models.AttendanceMark.student_id == student.id,
        )
        .group_by(models.AttendanceMark.status)
        .all()
    )
    counts = {"present": 0, "absent": 0, "late": 0}
    for st, n in marks:
        counts[st] = n
    return schemas.AttendanceSummary(
        present=counts["present"], absent=counts["absent"], late=counts["late"],
        total=sum(counts.values()),
    )


@app.get("/me/timetable", response_model=list[schemas.TimetableOut])
def my_timetable(db: Session = Depends(get_db),
                 student: models.Student = Depends(_current_student)):
    return (
        db.query(models.TimetableEntry)
        .filter(
            models.TimetableEntry.school_id == student.school_id,
            _arm_filter(models.TimetableEntry.class_name, models.TimetableEntry.arm,
                        student.class_name, student.arm),
        )
        .order_by(models.TimetableEntry.day_of_week, models.TimetableEntry.period)
        .all()
    )


@app.get("/me/announcements", response_model=list[schemas.AnnouncementOut])
def my_announcements(db: Session = Depends(get_db),
                     student: models.Student = Depends(_current_student)):
    return (
        db.query(models.Announcement)
        .filter(
            models.Announcement.school_id == student.school_id,
            models.Announcement.audience.in_(["all", "students"]),
        )
        .order_by(models.Announcement.created_at.desc())
        .all()
    )
