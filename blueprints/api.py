from functools import wraps
from flask import Blueprint, jsonify, request
from flask_login import current_user
from extensions import db
from models import Exam, Course, ExaminerProfile, StudentProfile, Booking, Slot, User

api_bp = Blueprint("api", __name__, url_prefix="/api")


def api_roles(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify(error="Login required"), 401
            if current_user.role not in roles:
                return jsonify(error="Forbidden"), 403
            if current_user.role == "examiner" and current_user.examiner_profile.status != "Approved":
                return jsonify(error="Examiner not approved"), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def iso(value):
    return value.isoformat() if value else None


def exam_json(e):
    return {"id": e.id, "course_code": e.course.code, "course_name": e.course.name,
            "name": e.name, "type": e.exam_type, "duration_minutes": e.duration,
            "max_marks": e.max_marks, "status": e.status,
            "slot_creation_start": iso(e.slot_create_start), "slot_creation_end": iso(e.slot_create_end),
            "booking_start": iso(e.booking_start), "booking_end": iso(e.booking_end)}


def booking_json(b):
    return {"id": b.id, "student_id": b.student_id, "student_name": b.student.user.name,
            "roll_no": b.student.roll_no, "exam_id": b.exam_id, "exam_name": b.exam.name,
            "slot_id": b.slot_id, "date": iso(b.slot.date),
            "start_time": b.slot.start_time.strftime("%H:%M"), "end_time": b.slot.end_time.strftime("%H:%M"),
            "status": b.status, "booked_on": b.booked_on.isoformat(timespec="minutes")}


@api_bp.route("/exams")
@api_roles("admin", "examiner", "student")
def exams():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    query = Exam.query.join(Course)
    if current_user.role != "admin":
        query = query.filter(Exam.status != "Draft")
    if status:
        query = query.filter(Exam.status == status)
    if q:
        query = query.filter(db.or_(Exam.name.ilike(f"%{q}%"), Course.code.ilike(f"%{q}%"), Course.name.ilike(f"%{q}%")))
    return jsonify([exam_json(e) for e in query.order_by(Exam.id).all()])


@api_bp.route("/exams/<int:exam_id>")
@api_roles("admin", "examiner", "student")
def exam_one(exam_id):
    exam = db.session.get(Exam, exam_id)
    if exam is None or (exam.status == "Draft" and current_user.role != "admin"):
        return jsonify(error="Examination not found"), 404
    data = exam_json(exam)
    data["rubrics"] = [{"id": r.id, "criterion": r.criterion, "max_marks": r.max_marks, "weightage": r.weightage} for r in exam.rubrics]
    data["slots"] = [{"id": s.id, "date": iso(s.date), "start_time": s.start_time.strftime("%H:%M"), "end_time": s.end_time.strftime("%H:%M"), "capacity": s.capacity, "available_seats": s.available_seats, "status": s.status, "examiner": s.examiner.user.name} for s in exam.slots]
    return jsonify(data)


@api_bp.route("/examiners")
@api_roles("admin")
def examiners():
    rows = ExaminerProfile.query.order_by(ExaminerProfile.id).all()
    return jsonify([{"id": p.id, "name": p.user.name, "email": p.user.email, "department": p.department, "contact": p.contact, "status": p.status} for p in rows])


@api_bp.route("/students")
@api_roles("admin")
def students():
    rows = StudentProfile.query.order_by(StudentProfile.id).all()
    return jsonify([{"id": s.id, "name": s.user.name, "email": s.user.email, "roll_no": s.roll_no, "contact": s.contact} for s in rows])


@api_bp.route("/bookings")
@api_roles("admin", "examiner", "student")
def bookings():
    status = request.args.get("status", "").strip()
    query = Booking.query
    if current_user.role == "student":
        query = query.filter(Booking.student_id == current_user.student_profile.id)
    elif current_user.role == "examiner":
        query = (query.join(Slot, Booking.slot_id == Slot.id)
                 .filter(Slot.examiner_id == current_user.examiner_profile.id))
    if status:
        query = query.filter(Booking.status == status)
    return jsonify([booking_json(b) for b in query.order_by(Booking.id).all()])