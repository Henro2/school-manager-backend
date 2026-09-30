"""Pydantic v2 schemas for requests/responses."""
import datetime as dt

from pydantic import BaseModel, Field

# ---------- Auth ----------


class RegisterSchoolIn(BaseModel):
    school_name: str = Field(min_length=2, max_length=200)
    school_code: str = Field(min_length=2, max_length=50)
    admin_username: str = Field(min_length=3, max_length=100)
    admin_password: str = Field(min_length=6, max_length=100)
    license_key: str = Field(min_length=8, max_length=32,
                             description="License key issued by the app owner (required)")
    motto: str | None = None
    address: str | None = None
    phone: str | None = None
    current_term: int = Field(default=1, ge=1, le=3)
    session: str = "2026/2027"


class LoginIn(BaseModel):
    school_code: str
    username: str  # staff username OR student admission_no
    password_or_pin: str


class TokenOut(BaseModel):
    token: str
    role: str
    school_name: str
    school_code: str
    display_name: str


# ---------- Students ----------


class StudentIn(BaseModel):
    admission_no: str
    full_name: str
    gender: str | None = None
    dob: dt.date | None = None
    class_name: str
    arm: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None


class StudentOut(BaseModel):
    id: int
    admission_no: str
    full_name: str
    gender: str | None
    dob: dt.date | None
    class_name: str
    arm: str | None
    guardian_name: str | None
    guardian_phone: str | None

    model_config = {"from_attributes": True}


class PinIn(BaseModel):
    pin: str = Field(min_length=4, max_length=20)


# ---------- Class arms ----------


class ClassArmIn(BaseModel):
    class_name: str
    arm: str | None = None
    class_teacher: str | None = None


class ClassArmOut(ClassArmIn):
    id: int

    model_config = {"from_attributes": True}


# ---------- Subjects ----------


class SubjectIn(BaseModel):
    name: str
    code: str | None = None


class SubjectOut(SubjectIn):
    id: int

    model_config = {"from_attributes": True}


# ---------- Scores ----------


class ScoreIn(BaseModel):
    student_id: int
    subject_id: int
    term: int | None = None
    session: str | None = None
    ca: float = Field(default=0, ge=0)
    exam: float = Field(default=0, ge=0)


class ScoreOut(BaseModel):
    id: int
    student_id: int
    subject_id: int
    term: int
    session: str
    ca: float
    exam: float
    total: float

    model_config = {"from_attributes": True}


# ---------- Fees ----------


class FeeUpsertIn(BaseModel):
    student_id: int
    term: int | None = None
    session: str | None = None
    billed: float = Field(ge=0)


class FeePayIn(BaseModel):
    term: int | None = None
    session: str | None = None
    amount: float = Field(gt=0)


class FeeOut(BaseModel):
    id: int
    student_id: int
    term: int
    session: str
    billed: float
    paid: float
    balance: float

    model_config = {"from_attributes": True}


# ---------- Attendance ----------


class AttendanceMarkIn(BaseModel):
    student_id: int
    status: str = Field(pattern="^(present|absent|late)$")


class AttendanceDayIn(BaseModel):
    class_name: str
    arm: str | None = None
    date: dt.date
    marks: list[AttendanceMarkIn]


# ---------- Timetable ----------


class TimetableIn(BaseModel):
    class_name: str
    arm: str | None = None
    day_of_week: int = Field(ge=0, le=4)
    period: int = Field(ge=1)
    subject: str
    time_label: str | None = None


class TimetableOut(TimetableIn):
    id: int

    model_config = {"from_attributes": True}


# ---------- Announcements ----------


class AnnouncementIn(BaseModel):
    title: str
    body: str | None = None
    audience: str = Field(default="all", pattern="^(all|students|teachers)$")


class AnnouncementOut(AnnouncementIn):
    id: int
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ---------- Users ----------


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=100)
    role: str = Field(pattern="^(admin|teacher)$")


class UserOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


# ---------- Licensing (v3.0) ----------


class SuperAdminLoginIn(BaseModel):
    username: str
    password: str


class LicenseGenerateIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    label: str | None = Field(default=None, max_length=200)
    max_students: int = Field(default=2000, ge=1)
    expiry_days: int | None = Field(default=None, ge=1,
                                    description="Days until expiry; null = perpetual")


class LicenseKeyOut(BaseModel):
    key: str
    label: str | None
    school_code: str | None
    school_name: str | None
    max_students: int
    expires_at: dt.datetime | None
    is_active: bool
    revoked_at: dt.datetime | None
    bound_at: dt.datetime | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class LicenseStatusOut(BaseModel):
    valid: bool
    reason: str
    school_name: str
    school_code: str
    key_label: str | None = None
    expires_at: dt.datetime | None = None


class SchoolLicenseRow(BaseModel):
    id: int
    name: str
    school_code: str
    is_disabled: bool
    license_key: str | None
    license_valid: bool
    license_reason: str
    student_count: int
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ---------- Report card ----------


class ReportSubjectRow(BaseModel):
    subject: str
    ca: float
    exam: float
    total: float
    grade: str
    remark: str
    class_average: float


class AttendanceSummary(BaseModel):
    present: int
    absent: int
    late: int
    total: int


class ReportCardOut(BaseModel):
    student: StudentOut
    term: int
    session: str
    subjects: list[ReportSubjectRow]
    total: float
    average: float
    position_in_class: int
    class_size: int
    grade_counts: dict[str, int]
    attendance: AttendanceSummary
