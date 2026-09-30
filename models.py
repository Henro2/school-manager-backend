"""SQLAlchemy models for Naija School Manager v3.0 (license-key access control)."""
import datetime as dt
import datetime as dt

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    motto: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(50))
    school_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    current_term: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    session: Mapped[str] = mapped_column(String(20), default="2026/2027", nullable=False)
    ca_max: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    exam_max: Mapped[float] = mapped_column(Float, default=70.0, nullable=False)
    grading_bands: Mapped[list | None] = mapped_column(JSON, nullable=True)  # overrides WAEC
    # Owner kill-switch: a disabled school's users cannot log in or use the API.
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    users = relationship("AppUser", back_populates="school", cascade="all, delete-orphan")
    students = relationship("Student", back_populates="school", cascade="all, delete-orphan")
    license_key = relationship("LicenseKey", back_populates="school", uselist=False)


class SuperAdmin(Base):
    """The owner account. Exactly one (or a few) rows, seeded from env vars.
    Not tied to any school — can manage licenses and schools globally."""

    __tablename__ = "super_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class LicenseKey(Base):
    """A license key issued by the owner. One key binds to exactly one school
    on first use (during /auth/register-school)."""

    __tablename__ = "license_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    school_id: Mapped[int | None] = mapped_column(
        ForeignKey("schools.id"), nullable=True, index=True
    )
    label: Mapped[str | None] = mapped_column(String(200))  # e.g. school name / note
    max_students: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    bound_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    school = relationship("School", back_populates="license_key")


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = (UniqueConstraint("school_id", "username", name="uq_user_school_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin | teacher

    school = relationship("School", back_populates="users")


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("school_id", "admission_no", name="uq_student_school_adm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    admission_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    dob: Mapped[dt.date | None] = mapped_column(Date)
    class_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    arm: Mapped[str | None] = mapped_column(String(10))
    guardian_name: Mapped[str | None] = mapped_column(String(200))
    guardian_phone: Mapped[str | None] = mapped_column(String(50))
    pin_hash: Mapped[str | None] = mapped_column(String(255))

    school = relationship("School", back_populates="students")


class ClassArm(Base):
    __tablename__ = "class_arms"
    __table_args__ = (
        UniqueConstraint("school_id", "class_name", "arm", name="uq_classarm_school"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    class_name: Mapped[str] = mapped_column(String(50), nullable=False)
    arm: Mapped[str | None] = mapped_column(String(10))
    class_teacher: Mapped[str | None] = mapped_column(String(200))


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("school_id", "name", name="uq_subject_school_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str | None] = mapped_column(String(30))


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "term", "session", name="uq_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), nullable=False, index=True)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    session: Mapped[str] = mapped_column(String(20), nullable=False)
    ca: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    exam: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    student = relationship("Student")
    subject = relationship("Subject")


class FeeRecord(Base):
    __tablename__ = "fee_records"
    __table_args__ = (
        UniqueConstraint("student_id", "term", "session", name="uq_fee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    session: Mapped[str] = mapped_column(String(20), nullable=False)
    billed: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    paid: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AttendanceDay(Base):
    __tablename__ = "attendance_days"
    __table_args__ = (
        UniqueConstraint("school_id", "class_name", "arm", "date", name="uq_att_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    class_name: Mapped[str] = mapped_column(String(50), nullable=False)
    arm: Mapped[str | None] = mapped_column(String(10))
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    marks = relationship("AttendanceMark", back_populates="day", cascade="all, delete-orphan")


class AttendanceMark(Base):
    __tablename__ = "attendance_marks"
    __table_args__ = (UniqueConstraint("day_id", "student_id", name="uq_att_mark"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("attendance_days.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # present | absent | late

    day = relationship("AttendanceDay", back_populates="marks")


class TimetableEntry(Base):
    __tablename__ = "timetable_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    class_name: Mapped[str] = mapped_column(String(50), nullable=False)
    arm: Mapped[str | None] = mapped_column(String(10))
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon .. 4=Fri
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(150), nullable=False)
    time_label: Mapped[str | None] = mapped_column(String(30))


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(20), default="all", nullable=False)
    # all | students | teachers
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
