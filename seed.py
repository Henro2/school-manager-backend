"""Seed a demo school for Naija School Manager v2.0.

Usage: python seed.py   (run from the backend/ directory)
"""
import datetime as dt

from app.auth import hash_secret
from app.database import Base, SessionLocal, engine
from app.models import (
    Announcement,
    AppUser,
    AttendanceDay,
    AttendanceMark,
    ClassArm,
    FeeRecord,
    School,
    Score,
    Student,
    Subject,
    SuperAdmin,
    TimetableEntry,
)

SCHOOL_CODE = "DEMO-2026"
TERM, SESSION = 1, "2026/2027"

# subject -> total score per student (CA + Exam). Totals crafted to hit
# every WAEC band: A1 75-100, B2 70-74, B3 65-69, C4 60-64, C5 55-59,
# C6 50-54, D7 45-49, E8 40-44, F9 0-39.
STUDENT_SCORES = {
    "DEMO001": {"English Language": 78, "Mathematics": 72, "Physics": 68,
                "Chemistry": 63, "Biology": 57, "Economics": 53,
                "Government": 47, "Literature": 42},
    "DEMO002": {"English Language": 38, "Mathematics": 82, "Physics": 74,
                "Chemistry": 69, "Biology": 61, "Economics": 56,
                "Government": 51, "Literature": 46},
    "DEMO003": {"English Language": 66, "Mathematics": 59, "Physics": 44,
                "Chemistry": 77, "Biology": 71, "Economics": 60,
                "Government": 55, "Literature": 50},
    "DEMO004": {"English Language": 49, "Mathematics": 43, "Physics": 39,
                "Chemistry": 65, "Biology": 58, "Economics": 52,
                "Government": 70, "Literature": 75},
    # DEMO005 and DEMO006 share identical totals -> tests position ties
    "DEMO005": {"English Language": 62, "Mathematics": 62, "Physics": 62,
                "Chemistry": 62, "Biology": 62, "Economics": 62,
                "Government": 62, "Literature": 62},
    "DEMO006": {"English Language": 62, "Mathematics": 62, "Physics": 62,
                "Chemistry": 62, "Biology": 62, "Economics": 62,
                "Government": 62, "Literature": 62},
}

STUDENTS = [
    ("DEMO001", "Adaeze Okafor", "F", "SS2", "A"),
    ("DEMO002", "Tunde Bakare", "M", "SS2", "A"),
    ("DEMO003", "Chiamaka Eze", "F", "SS2", "A"),
    ("DEMO004", "Ibrahim Musa", "M", "SS2", "B"),
    ("DEMO005", "Ngozi Adeyemi", "F", "SS2", "B"),
    ("DEMO006", "Emeka Obi", "M", "SS2", "B"),
]

SUBJECTS = [
    ("English Language", "ENG"),
    ("Mathematics", "MTH"),
    ("Physics", "PHY"),
    ("Chemistry", "CHM"),
    ("Biology", "BIO"),
    ("Economics", "ECO"),
    ("Government", "GOV"),
    ("Literature", "LIT"),
]


def split_total(total):
    """Split a total into (ca, exam) with ca<=30, exam<=70."""
    ca = min(30, total // 2 + 3)
    exam = total - ca
    return float(ca), float(exam)


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(School).filter(School.school_code == SCHOOL_CODE).first():
            print(f"School {SCHOOL_CODE} already seeded. Skipping.")
            return

        school = School(
            name="Demo College", motto="Knowledge is Power",
            address="12 Education Close, Ikeja, Lagos", phone="+2348000000001",
            school_code=SCHOOL_CODE, current_term=TERM, session=SESSION,
        )
        db.add(school)
        db.flush()

        db.add(AppUser(school_id=school.id, username="admin",
                       password_hash=hash_secret("admin123"), role="admin"))
        db.add(AppUser(school_id=school.id, username="tch123",
                       password_hash=hash_secret("tch123"), role="teacher"))

        for cls, arm in [("SS2", "A"), ("SS2", "B")]:
            db.add(ClassArm(school_id=school.id, class_name=cls, arm=arm,
                            class_teacher=f"Mr/Mrs Class Teacher {cls}{arm}"))

        subjects = {}
        for name, code in SUBJECTS:
            s = Subject(school_id=school.id, name=name, code=code)
            db.add(s)
            db.flush()
            subjects[name] = s

        students = {}
        for adm, name, gender, cls, arm in STUDENTS:
            st = Student(
                school_id=school.id, admission_no=adm, full_name=name, gender=gender,
                dob=dt.date(2010, 5, 12), class_name=cls, arm=arm,
                guardian_name=f"Guardian of {name}", guardian_phone="+2348000000000",
                pin_hash=hash_secret("1234"),
            )
            db.add(st)
            db.flush()
            students[adm] = st

        for adm, subj_scores in STUDENT_SCORES.items():
            for subj_name, total in subj_scores.items():
                ca, exam = split_total(total)
                db.add(Score(
                    school_id=school.id, student_id=students[adm].id,
                    subject_id=subjects[subj_name].id,
                    term=TERM, session=SESSION, ca=ca, exam=exam,
                ))

        fees = [("DEMO001", 150000, 150000), ("DEMO002", 150000, 100000),
                ("DEMO003", 150000, 0), ("DEMO004", 150000, 75000),
                ("DEMO005", 150000, 150000), ("DEMO006", 150000, 50000)]
        for adm, billed, paid in fees:
            db.add(FeeRecord(school_id=school.id, student_id=students[adm].id,
                             term=TERM, session=SESSION, billed=billed, paid=paid))

        day_a = AttendanceDay(school_id=school.id, class_name="SS2", arm="A",
                              date=dt.date(2026, 9, 29))
        day_b = AttendanceDay(school_id=school.id, class_name="SS2", arm="B",
                              date=dt.date(2026, 9, 29))
        db.add_all([day_a, day_b])
        db.flush()
        for adm, status in [("DEMO001", "present"), ("DEMO002", "present"),
                            ("DEMO003", "late"), ("DEMO004", "present"),
                            ("DEMO005", "present"), ("DEMO006", "absent")]:
            day = day_a if students[adm].arm == "A" else day_b
            db.add(AttendanceMark(day_id=day.id, student_id=students[adm].id,
                                 status=status))

        db.add(Announcement(
            school_id=school.id, title="Welcome to Demo College",
            body="First term classes begin with orientation on Monday. "
                 "All SS2 students should report by 7:30am.",
            audience="all",
        ))

        days_labels = [(0, "Mon"), (1, "Tue"), (2, "Wed")]
        period_subjects = [
            ("8:00-8:40", "English Language"), ("8:40-9:20", "Mathematics"),
            ("9:20-10:00", "Physics"), ("10:30-11:10", "Chemistry"),
        ]
        for dow, _label in days_labels:
            for i, (time_label, subj) in enumerate(period_subjects, start=1):
                db.add(TimetableEntry(
                    school_id=school.id, class_name="SS2", arm="A",
                    day_of_week=dow, period=i, subject=subj, time_label=time_label,
                ))

        db.commit()
        print("Seeded Demo College (DEMO-2026):")
        print("  admin: admin / admin123")
        print("  teacher: tch123 / tch123")
        print("  students: DEMO001..DEMO006 / PIN 1234")
        seed_superadmin(db)
        # Any school created without going through /auth/register-school
        # (e.g. this demo seed) gets a perpetual auto-issued license key.
        from app.main import ensure_legacy_licenses
        ensure_legacy_licenses(db)
    finally:
        db.close()


def seed_superadmin(db):
    """Seed the owner superadmin from env vars (SUPERADMIN_USER / SUPERADMIN_PASSWORD)."""
    import os
    username = os.environ.get("SUPERADMIN_USER", "owner")
    password = os.environ.get("SUPERADMIN_PASSWORD", "owner123")
    if not db.query(SuperAdmin).filter_by(username=username).first():
        db.add(SuperAdmin(username=username, password_hash=hash_secret(password)))
        db.commit()
        if os.environ.get("SUPERADMIN_PASSWORD"):
            print(f"Seeded superadmin: {username}")
        else:
            print("WARNING: seeded default superadmin 'owner' / 'owner123' — "
                  "set SUPERADMIN_USER/SUPERADMIN_PASSWORD in production!")


if __name__ == "__main__":
    main()
