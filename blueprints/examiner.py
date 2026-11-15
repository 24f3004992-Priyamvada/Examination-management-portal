from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user
from decorators import role_required
from extensions import db
from models import (Exam, ExamExaminer, Slot, Booking, StudentProfile, Evaluation, RubricScore, BookingHistory)

examiner_bp = Blueprint("examiner", __name__, url_prefix="/examiner")


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_time(value):
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except (ValueError, TypeError):
            pass
    return None


def is_assigned(exam, examiner):
    return ExamExaminer.query.filter_by(exam_id=exam.id, examiner_id=examiner.id).first() is not None


def slot_creation_open(exam):
    today = date.today()
    return (exam.status == "Slot Creation"
            and exam.slot_create_start <= today <= exam.slot_create_end)


def slot_changeable(slot):
    return slot.exam.status in ("Slot Creation", "Closed") and len(slot.bookings) == 0


def read_slot_form(exam, examiner, form, slot=None):
    d = parse_date(form.get("date", "").strip())
    start = parse_time(form.get("start_time", "").strip())
    end = parse_time(form.get("end_time", "").strip())
    capacity = form.get("capacity", "").strip()

    if d is None or start is None or end is None:
        return "Date, start time and end time are required.", None
    if not capacity.isdigit() or not (1 <= int(capacity) <= 100):
        return "Capacity must be a whole number between 1 and 100.", None
    if end <= start:
        return "End time must be after the start time.", None

    minutes = (datetime.combine(d, end) - datetime.combine(d, start)).total_seconds() / 60
    if minutes < exam.duration:
        return f"The slot must be at least {exam.duration} minutes long (the examination duration).", None
    if d < date.today():
        return "The slot date cannot be in the past.", None
    if d < exam.booking_end:
        return f"The slot date cannot be before the booking end date ({exam.booking_end}).", None

    clashes = Slot.query.filter(Slot.examiner_id == examiner.id, Slot.date == d, Slot.status != "Cancelled")
    if slot:
        clashes = clashes.filter(Slot.id != slot.id)
    for other in clashes.all():
        if start < other.end_time and other.start_time < end:
            return (f"This overlaps your other slot on {d} "
                    f"({other.start_time.strftime('%H:%M')} to {other.end_time.strftime('%H:%M')}).", None)

    return None, dict(date=d, start_time=start, end_time=end, capacity=int(capacity))


@examiner_bp.route("/dashboard")
@role_required("examiner")
def dashboard():
    me = current_user.examiner_profile
    assigned = (Exam.query.join(ExamExaminer, ExamExaminer.exam_id == Exam.id)
                .filter(ExamExaminer.examiner_id == me.id).order_by(Exam.id).all())
    my_bookings = (Booking.query.join(Slot, Booking.slot_id == Slot.id)
                   .filter(Slot.examiner_id == me.id, Booking.status != "Cancelled"))
    stats = [
        ("Assigned examinations", len(assigned)),
        ("Slots created", Slot.query.filter_by(examiner_id=me.id).count()),
        ("Students booked", my_bookings.count()),
        ("Pending evaluations", my_bookings.filter(Booking.status == "Booked").count()),
    ]
    rows = [(e,
             Slot.query.filter_by(exam_id=e.id, examiner_id=me.id).count(),
             slot_creation_open(e)) for e in assigned]
    return render_template("examiner/dashboard.html", stats=stats, rows=rows)


@examiner_bp.route("/slots")
@role_required("examiner")
def slots():
    me = current_user.examiner_profile
    exam_id = request.args.get("exam", "").strip()
    query = Slot.query.filter_by(examiner_id=me.id)
    if exam_id.isdigit():
        query = query.filter(Slot.exam_id == int(exam_id))
    rows = []
    for s in query.order_by(Slot.date, Slot.start_time).all():
        booked = len([b for b in s.bookings if b.status != "Cancelled"])
        rows.append((s, booked, slot_changeable(s)))
    assigned = (Exam.query.join(ExamExaminer, ExamExaminer.exam_id == Exam.id)
                .filter(ExamExaminer.examiner_id == me.id).order_by(Exam.name).all())
    return render_template("examiner/slots.html", rows=rows, exams=assigned, exam_id=exam_id)


@examiner_bp.route("/exams/<int:exam_id>/slots/new", methods=["GET", "POST"])
@examiner_bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
@role_required("examiner")
def slot_form(exam_id=None, slot_id=None):
    me = current_user.examiner_profile
    slot = db.get_or_404(Slot, slot_id) if slot_id else None

    if slot:
        if slot.examiner_id != me.id:
            abort(403)
        exam = slot.exam
        if not slot_changeable(slot):
            flash("This slot can no longer be changed (booking has started or it has bookings).", "warning")
            return redirect(url_for("examiner.slots"))
    else:
        exam = db.get_or_404(Exam, exam_id)
        if not is_assigned(exam, me):
            abort(403)
        if not slot_creation_open(exam):
            flash("Slot creation is not open for this examination right now.", "warning")
            return redirect(url_for("examiner.dashboard"))

    form = {"date": "", "start_time": "", "end_time": "", "capacity": ""}
    if slot:
        form = {"date": slot.date.isoformat(), "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"), "capacity": str(slot.capacity)}

    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        error, data = read_slot_form(exam, me, request.form, slot)
        if error:
            flash(error, "danger")
        else:
            if slot is None:
                slot = Slot(exam_id=exam.id, examiner_id=me.id, status="Available")
                db.session.add(slot)
            slot.date = data["date"]
            slot.start_time = data["start_time"]
            slot.end_time = data["end_time"]
            slot.capacity = data["capacity"]
            slot.available_seats = data["capacity"]
            db.session.commit()
            flash("Slot saved.", "success")
            return redirect(url_for("examiner.slots", exam=exam.id))

    return render_template("examiner/slot_form.html", exam=exam, slot=slot, form=form)


@examiner_bp.route("/slots/<int:slot_id>/delete", methods=["POST"])
@role_required("examiner")
def slot_delete(slot_id):
    me = current_user.examiner_profile
    slot = db.get_or_404(Slot, slot_id)
    if slot.examiner_id != me.id:
        abort(403)
    if not slot_changeable(slot):
        flash("This slot can no longer be deleted (booking has started or it has bookings).", "warning")
    else:
        db.session.delete(slot)
        db.session.commit()
        flash("Slot deleted.", "success")
    return redirect(url_for("examiner.slots"))


@examiner_bp.route("/slots/<int:slot_id>/students")
@role_required("examiner")
def slot_students(slot_id):
    me = current_user.examiner_profile
    slot = db.get_or_404(Slot, slot_id)
    if slot.examiner_id != me.id:
        abort(403)
    bookings = [b for b in slot.bookings if b.status != "Cancelled"]
    return render_template("examiner/slot_students.html", slot=slot, bookings=bookings)


@examiner_bp.route("/evaluations")
@role_required("examiner")
def evaluations():
    me = current_user.examiner_profile
    status = request.args.get("status", "").strip()
    query = (Booking.query
             .join(Slot, Booking.slot_id == Slot.id)
             .join(StudentProfile, Booking.student_id == StudentProfile.id)
             .filter(Slot.examiner_id == me.id, Booking.status != "Cancelled"))
    if status in ("Booked", "Completed"):
        query = query.filter(Booking.status == status)
    bookings = query.order_by(Slot.date, Slot.start_time, Booking.id).all()
    return render_template("examiner/evaluations.html", bookings=bookings, status=status)


@examiner_bp.route("/evaluate/<int:booking_id>", methods=["GET", "POST"])
@role_required("examiner")
def evaluate(booking_id):
    me = current_user.examiner_profile
    booking = db.get_or_404(Booking, booking_id)
    if booking.slot.examiner_id != me.id:
        abort(403)
    if booking.status == "Cancelled":
        flash("This booking was cancelled, so it cannot be evaluated.", "warning")
        return redirect(url_for("examiner.evaluations"))

    exam = booking.exam
    if exam.status not in ("Closed", "Completed"):
        flash("Evaluation opens after the admin closes booking for this examination.", "warning")
        return redirect(url_for("examiner.slot_students", slot_id=booking.slot_id))

    rubrics = exam.rubrics
    evaluation = booking.evaluation
    locked = exam.results_published
    existing = {s.rubric_id: s.marks for s in evaluation.scores} if evaluation else {}

    if request.method == "POST":
        if locked:
            flash("Results are published, so this evaluation is locked.", "warning")
            return redirect(url_for("examiner.evaluate", booking_id=booking.id))

        scores, error = {}, None
        for r in rubrics:
            raw = request.form.get(f"mark_{r.id}", "").strip()
            try:
                value = float(raw)
            except ValueError:
                error = f"Enter marks for '{r.criterion}'."
                break
            if value < 0 or value > r.max_marks:
                error = f"Marks for '{r.criterion}' must be between 0 and {r.max_marks}."
                break
            scores[r.id] = round(value, 2)
        remarks = request.form.get("remarks", "").strip()
        if not error and not remarks:
            error = "Remarks are required."

        if error:
            flash(error, "danger")
        else:
            if evaluation is None:
                evaluation = Evaluation(booking_id=booking.id)
                db.session.add(evaluation)
                db.session.flush()
            saved = {s.rubric_id: s for s in evaluation.scores}
            for r in rubrics:
                if r.id in saved:
                    saved[r.id].marks = scores[r.id]
                else:
                    db.session.add(RubricScore(evaluation_id=evaluation.id, rubric_id=r.id, marks=scores[r.id]))
            evaluation.total_marks = round(sum(scores.values()), 2)
            evaluation.remarks = remarks
            evaluation.evaluated_on = datetime.now()
            booking.status = "Completed"

            slot = booking.slot
            active = [b for b in slot.bookings if b.status != "Cancelled"]
            if active and all(b.status == "Completed" for b in active):
                slot.status = "Completed"

            db.session.add(BookingHistory(booking_id=booking.id, action="Evaluated", details=f"Total marks {evaluation.total_marks}"))
            db.session.commit()
            flash("Evaluation saved.", "success")
            return redirect(url_for("examiner.slot_students", slot_id=booking.slot_id))

    return render_template("examiner/evaluate.html", booking=booking, exam=exam, rubrics=rubrics, evaluation=evaluation, existing=existing, locked=locked)