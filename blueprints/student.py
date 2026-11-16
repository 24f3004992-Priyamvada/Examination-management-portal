from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user
from decorators import role_required
from extensions import db
from models import Exam, Course, Slot, Booking
import services

student_bp = Blueprint("student", __name__, url_prefix="/student")

EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]


def open_exams():
    return [e for e in Exam.query.filter_by(status="Booking Open").all() if services.booking_open(e)]


def available_slots(exam_ids):
    if not exam_ids:
        return []
    now = datetime.now()
    slots = (Slot.query.filter(Slot.exam_id.in_(exam_ids), Slot.status == "Available",
                               Slot.available_seats > 0, Slot.date >= date.today())
             .order_by(Slot.date, Slot.start_time).all())
    return [s for s in slots if datetime.combine(s.date, s.start_time) > now]


def my_active_bookings(me):
    return (Booking.query.join(Slot, Booking.slot_id == Slot.id)
            .filter(Booking.student_id == me.id, Booking.status == "Booked")
            .order_by(Slot.date, Slot.start_time).all())


def published_results(me):
    rows = [b for b in me.bookings if b.evaluation and b.exam.results_published]
    return sorted(rows, key=lambda b: b.evaluation.evaluated_on, reverse=True)


@student_bp.route("/dashboard")
@role_required("student")
def dashboard():
    me = current_user.student_profile
    now = datetime.now()
    booked_exam_ids = {b.exam_id for b in me.bookings if b.status in services.ACTIVE}
    avail_exams = [e for e in open_exams() if e.id not in booked_exam_ids]
    avail_slots = available_slots([e.id for e in avail_exams])
    active = my_active_bookings(me)
    booked_rows = [(b, datetime.combine(b.slot.date, b.slot.start_time) >= now,
                    services.can_cancel(b) is None) for b in active]
    upcoming = [row for row in booked_rows if row[1]]
    results = published_results(me)
    stats = [
        ("Available examinations", len(avail_exams)),
        ("Available slots", len(avail_slots)),
        ("Upcoming examinations", len(upcoming)),
        ("Published results", len(results)),
    ]
    return render_template("student/dashboard.html", stats=stats, slots=avail_slots[:8],
                           total_slots=len(avail_slots), booked_rows=booked_rows, results=results[:5])


@student_bp.route("/exams")
@role_required("student")
def exams():
    me = current_user.student_profile
    q = request.args.get("q", "").strip()
    etype = request.args.get("type", "").strip()
    course_id = request.args.get("course", "").strip()
    only_open = request.args.get("open") == "1"

    query = Exam.query.join(Course).filter(Exam.status != "Draft")
    if q:
        conditions = [Exam.name.ilike(f"%{q}%"), Course.code.ilike(f"%{q}%"), Course.name.ilike(f"%{q}%")]
        if q.isdigit():
            conditions.append(Exam.id == int(q))
        query = query.filter(db.or_(*conditions))
    if etype in EXAM_TYPES:
        query = query.filter(Exam.exam_type == etype)
    if course_id.isdigit():
        query = query.filter(Exam.course_id == int(course_id))
    exams_list = query.order_by(Exam.id).all()

    open_ids = {e.id for e in exams_list if services.booking_open(e)}
    if only_open:
        exams_list = [e for e in exams_list if e.id in open_ids]
    mine = {b.exam_id: b for b in me.bookings if b.status in services.ACTIVE}
    return render_template("student/exams.html", exams=exams_list, open_ids=open_ids, mine=mine,
                           courses=Course.query.order_by(Course.code).all(), exam_types=EXAM_TYPES,
                           q=q, etype=etype, course_id=course_id, only_open=only_open)


@student_bp.route("/exams/<int:exam_id>")
@role_required("student")
def exam_detail(exam_id):
    me = current_user.student_profile
    exam = db.get_or_404(Exam, exam_id)
    if exam.status == "Draft":
        abort(404)
    my_booking = services.active_booking_for_exam(me, exam.id)
    slots = (Slot.query.filter(Slot.exam_id == exam.id, Slot.status != "Cancelled")
             .order_by(Slot.date, Slot.start_time).all())
    rows = [(s, services.can_book(me, s) is None) for s in slots]
    return render_template("student/exam_detail.html", exam=exam, rows=rows, my_booking=my_booking,
                           is_open=services.booking_open(exam))


@student_bp.route("/slots/<int:slot_id>/book", methods=["GET", "POST"])
@role_required("student")
def book(slot_id):
    me = current_user.student_profile
    slot = db.get_or_404(Slot, slot_id)
    exam_id = slot.exam_id

    if request.method == "POST":
        booking, error = services.book_slot(me, slot)
        if error:
            flash(error, "danger")
            return redirect(url_for("student.exam_detail", exam_id=exam_id))
        flash("Booking confirmed.", "success")
        return redirect(url_for("student.bookings"))

    error = services.can_book(me, slot)
    if error:
        flash(error, "warning")
        return redirect(url_for("student.exam_detail", exam_id=exam_id))
    return render_template("student/book_confirm.html", slot=slot)


@student_bp.route("/bookings")
@role_required("student")
def bookings():
    me = current_user.student_profile
    now = datetime.now()
    rows = [(b, datetime.combine(b.slot.date, b.slot.start_time) >= now,
             services.can_cancel(b) is None) for b in my_active_bookings(me)]
    return render_template("student/bookings.html", rows=rows)


@student_bp.route("/bookings/<int:booking_id>/cancel", methods=["GET", "POST"])
@role_required("student")
def cancel(booking_id):
    me = current_user.student_profile
    booking = db.get_or_404(Booking, booking_id)
    if booking.student_id != me.id:
        abort(403)

    if request.method == "POST":
        error = services.cancel_booking(booking)
        flash(error or "Booking cancelled.", "danger" if error else "success")
        return redirect(url_for("student.bookings"))

    error = services.can_cancel(booking)
    if error:
        flash(error, "warning")
        return redirect(url_for("student.bookings"))
    return render_template("student/cancel_confirm.html", booking=booking)


@student_bp.route("/history")
@role_required("student")
def history():
    me = current_user.student_profile
    rows = Booking.query.filter_by(student_id=me.id).order_by(Booking.id.desc()).all()
    return render_template("student/history.html", bookings=rows)


@student_bp.route("/results")
@role_required("student")
def results():
    me = current_user.student_profile
    return render_template("student/results.html", results=published_results(me))


@student_bp.route("/profile", methods=["GET", "POST"])
@role_required("student")
def profile():
    me = current_user.student_profile
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")

        error = None
        if not name:
            error = "Name is required."
        elif new_pw:
            if not current_user.check_password(current_pw):
                error = "Current password is incorrect."
            elif len(new_pw) < 6:
                error = "New password must be at least 6 characters."

        if error:
            flash(error, "danger")
        else:
            current_user.name = name
            me.contact = contact
            if new_pw:
                current_user.set_password(new_pw)
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("student.profile"))
    return render_template("student/profile.html", me=me)