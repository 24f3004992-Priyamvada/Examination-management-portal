from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from decorators import role_required
from extensions import db
from models import (User, Course, Exam, ExaminerProfile, StudentProfile, Slot, Booking, Rubric, RubricScore, ExamExaminer)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]
DATE_FIELDS = ["slot_create_start", "slot_create_end", "booking_start", "booking_end"]

# action -> (statuses it may start from, status it moves to)
EXAM_STATUS_ACTIONS = {
    "open_slots":    ({"Draft", "Closed"}, "Slot Creation"),
    "close_slots":   ({"Slot Creation"}, "Closed"),
    "open_booking":  ({"Closed"}, "Booking Open"),
    "close_booking": ({"Booking Open"}, "Closed"),
    "complete":      ({"Closed"}, "Completed"),
}

EXAMINER_ACTIONS = {
    "approve":    ({"Pending", "Rejected", "Deactivated"}, "Approved"),
    "reject":     ({"Pending"}, "Rejected"),
    "deactivate": ({"Approved"}, "Deactivated"),
}


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def match(q, *columns, id_column=None):
    """Build a 'contains q' search over text columns, plus an exact match on an ID."""
    conditions = [c.ilike(f"%{q}%") for c in columns]
    if id_column is not None and q.isdigit():
        conditions.append(id_column == int(q))
    return db.or_(*conditions)


# --------------------------------------------------------------- dashboard
@admin_bp.route("/dashboard")
@role_required("admin")
def dashboard():
    stats = [
        ("Courses", Course.query.count()),
        ("Examinations", Exam.query.count()),
        ("Examiners", ExaminerProfile.query.count()),
        ("Students", StudentProfile.query.count()),
        ("Examination slots", Slot.query.count()),
        ("Bookings", Booking.query.count()),
    ]
    pending = ExaminerProfile.query.filter_by(status="Pending").count()
    return render_template("admin/dashboard.html", stats=stats, pending=pending)


# ------------------------------------------------------------------ courses
@admin_bp.route("/courses")
@role_required("admin")
def courses():
    q = request.args.get("q", "").strip()
    query = Course.query
    if q:
        query = query.filter(match(q, Course.name, Course.code, id_column=Course.id))
    return render_template("admin/courses.html", courses=query.order_by(Course.id).all(), q=q)


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def course_form(course_id=None):
    course = db.get_or_404(Course, course_id) if course_id else None

    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        status = request.form.get("status", "Active")

        error = None
        if not code or not name:
            error = "Course code and name are required."
        else:
            clash = Course.query.filter_by(code=code).first()
            if clash and (course is None or clash.id != course.id):
                error = "A course with this code already exists."

        if error:
            flash(error, "danger")
        else:
            if course is None:
                course = Course()
                db.session.add(course)
            course.code = code
            course.name = name
            course.description = description
            course.status = status
            db.session.commit()
            flash("Course saved.", "success")
            return redirect(url_for("admin.courses"))

    return render_template("admin/course_form.html", course=course)


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@role_required("admin")
def course_delete(course_id):
    course = db.get_or_404(Course, course_id)
    if course.exams:
        flash("This course has examinations, so it cannot be deleted. Set its status to Inactive instead.", "warning")
    else:
        db.session.delete(course)
        db.session.commit()
        flash("Course deleted.", "success")
    return redirect(url_for("admin.courses"))


# -------------------------------------------------------------- examinations
@admin_bp.route("/exams")
@role_required("admin")
def exams():
    q = request.args.get("q", "").strip()
    query = Exam.query.join(Course)
    if q:
        query = query.filter(match(q, Exam.name, Course.code, Course.name, id_column=Exam.id))
    return render_template("admin/exams.html", exams=query.order_by(Exam.id).all(), q=q)


@admin_bp.route("/exams/new", methods=["GET", "POST"])
@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def exam_form(exam_id=None):
    exam = db.get_or_404(Exam, exam_id) if exam_id else None

    allowed = [Course.status == "Active"]
    if exam:
        allowed.append(Course.id == exam.course_id)
    courses = Course.query.filter(db.or_(*allowed)).order_by(Course.code).all()

    fields = ["course_id", "name", "exam_type", "duration", "max_marks"] + DATE_FIELDS
    form = {f: "" for f in fields}
    if exam:
        form.update(course_id=str(exam.course_id), name=exam.name, exam_type=exam.exam_type,
                    duration=str(exam.duration), max_marks=str(exam.max_marks))
        for k in DATE_FIELDS:
            value = getattr(exam, k)
            form[k] = value.isoformat() if value else ""

    if request.method == "POST":
        form = {f: request.form.get(f, "").strip() for f in fields}
        course = db.session.get(Course, int(form["course_id"])) if form["course_id"].isdigit() else None
        d = {k: parse_date(form[k]) for k in DATE_FIELDS}

        error = None
        if course is None or not form["name"] or form["exam_type"] not in EXAM_TYPES:
            error = "Course, name and a valid examination type are required."
        elif not (form["duration"].isdigit() and int(form["duration"]) > 0) \
                or not (form["max_marks"].isdigit() and int(form["max_marks"]) > 0):
            error = "Duration and maximum marks must be positive whole numbers."
        elif None in d.values():
            error = "All four dates are required."
        elif d["slot_create_start"] > d["slot_create_end"]:
            error = "Slot creation end date cannot be before its start date."
        elif d["booking_start"] > d["booking_end"]:
            error = "Booking end date cannot be before its start date."
        elif d["booking_start"] < d["slot_create_end"]:
            error = "Booking must start on or after the day slot creation ends."
        elif exam and int(form["max_marks"]) < sum(r.max_marks for r in exam.rubrics):
            error = "Maximum marks cannot be lower than the total marks of its rubrics."

        if error:
            flash(error, "danger")
        else:
            if exam is None:
                exam = Exam()
                db.session.add(exam)
            exam.course_id = course.id
            exam.name = form["name"]
            exam.exam_type = form["exam_type"]
            exam.duration = int(form["duration"])
            exam.max_marks = int(form["max_marks"])
            for k, v in d.items():
                setattr(exam, k, v)
            db.session.commit()
            flash("Examination saved.", "success")
            return redirect(url_for("admin.exams"))

    return render_template("admin/exam_form.html", exam=exam, form=form, courses=courses, exam_types=EXAM_TYPES)


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@role_required("admin")
def exam_delete(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    if exam.slots or exam.bookings:
        flash("This examination already has slots or bookings, so it cannot be deleted.", "warning")
    else:
        for r in list(exam.rubrics):
            db.session.delete(r)
        for a in list(exam.assignments):
            db.session.delete(a)
        db.session.delete(exam)
        db.session.commit()
        flash("Examination deleted.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/status/<action>", methods=["POST"])
@role_required("admin")
def exam_status(exam_id, action):
    exam = db.get_or_404(Exam, exam_id)
    rule = EXAM_STATUS_ACTIONS.get(action)
    if rule is None:
        abort(404)
    allowed_from, new_status = rule

    error = None
    if exam.status not in allowed_from:
        error = f"This action is not available while the examination is '{exam.status}'."
    elif action == "open_slots":
        approved = [a for a in exam.assignments if a.examiner.status == "Approved"]
        if not exam.rubrics:
            error = "Add at least one rubric criterion before opening slot creation."
        elif not approved:
            error = "Assign at least one approved examiner before opening slot creation."
    elif action == "open_booking":
        if not any(s.status != "Cancelled" for s in exam.slots):
            error = "There are no slots yet, so booking cannot be opened."

    if error:
        flash(error, "danger")
        return redirect(url_for("admin.exams"))

    exam.status = new_status
    db.session.commit()
    flash(f"'{exam.name}' is now: {new_status}.", "success")

    today = date.today()
    if action == "open_slots" and not (exam.slot_create_start <= today <= exam.slot_create_end):
        flash("Note: today is outside the slot creation dates, so examiners can only create "
              "slots between those dates. Edit the examination to change them.", "warning")
    if action == "open_booking" and not (exam.booking_start <= today <= exam.booking_end):
        flash("Note: today is outside the booking dates, so students can only book "
              "between those dates. Edit the examination to change them.", "warning")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/examiners", methods=["GET", "POST"])
@role_required("admin")
def exam_examiners(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    approved = (ExaminerProfile.query.filter_by(status="Approved").join(User, ExaminerProfile.user_id == User.id).order_by(User.name).all())
    valid_ids = {e.id for e in approved}
    current = {a.examiner_id: a for a in exam.assignments}

    if request.method == "POST":
        chosen = {int(x) for x in request.form.getlist("examiner_ids") if x.isdigit()} & valid_ids
        blocked = []
        for eid in valid_ids:
            if eid in chosen and eid not in current:
                db.session.add(ExamExaminer(exam_id=exam.id, examiner_id=eid))
            elif eid not in chosen and eid in current:
                if Slot.query.filter_by(exam_id=exam.id, examiner_id=eid).first():
                    blocked.append(current[eid].examiner.user.name)
                else:
                    db.session.delete(current[eid])
        db.session.commit()
        flash("Examiner assignments saved.", "success")
        if blocked:
            flash("Not removed because they already have slots for this examination: "
                  + ", ".join(blocked) + ".", "warning")
        return redirect(url_for("admin.exam_examiners", exam_id=exam.id))

    return render_template("admin/exam_examiners.html", exam=exam, approved=approved, assigned_ids=set(current.keys()))


# ------------------------------------------------------------------ rubrics
def read_rubric_form(exam, form, rubric=None):
    criterion = form.get("criterion", "").strip()
    description = form.get("description", "").strip()
    max_marks = form.get("max_marks", "").strip()
    weightage = form.get("weightage", "").strip()

    if not criterion:
        return "Criterion name is required.", None
    if not max_marks.isdigit() or int(max_marks) <= 0:
        return "Maximum marks must be a positive whole number.", None
    try:
        weight = float(weightage) if weightage else 0.0
    except ValueError:
        return "Weightage must be a number.", None
    if weight < 0 or weight > 100:
        return "Weightage must be between 0 and 100.", None

    others = [r for r in exam.rubrics if rubric is None or r.id != rubric.id]
    if sum(r.max_marks for r in others) + int(max_marks) > exam.max_marks:
        return f"Total rubric marks cannot exceed the examination's maximum of {exam.max_marks}.", None
    if round(sum(r.weightage for r in others) + weight, 2) > 100:
        return "Total weightage cannot exceed 100%.", None

    return None, dict(criterion=criterion, description=description, max_marks=int(max_marks), weightage=weight)


@admin_bp.route("/exams/<int:exam_id>/rubrics", methods=["GET", "POST"])
@role_required("admin")
def rubrics(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    if request.method == "POST":
        error, data = read_rubric_form(exam, request.form)
        if error:
            flash(error, "danger")
        else:
            db.session.add(Rubric(exam_id=exam.id, **data))
            db.session.commit()
            flash("Rubric criterion added.", "success")
            return redirect(url_for("admin.rubrics", exam_id=exam.id))

    marks_total = sum(r.max_marks for r in exam.rubrics)
    weight_total = round(sum(r.weightage for r in exam.rubrics), 2)
    values = request.form if request.method == "POST" else {}
    return render_template("admin/rubrics.html", exam=exam, marks_total=marks_total,  weight_total=weight_total, values=values)


@admin_bp.route("/rubrics/<int:rubric_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def rubric_edit(rubric_id):
    rubric = db.get_or_404(Rubric, rubric_id)
    exam = rubric.exam
    if request.method == "POST":
        error, data = read_rubric_form(exam, request.form, rubric)
        if error:
            flash(error, "danger")
        else:
            rubric.criterion = data["criterion"]
            rubric.description = data["description"]
            rubric.max_marks = data["max_marks"]
            rubric.weightage = data["weightage"]
            db.session.commit()
            flash("Rubric criterion updated.", "success")
            return redirect(url_for("admin.rubrics", exam_id=exam.id))
    return render_template("admin/rubric_form.html", rubric=rubric, exam=exam)


@admin_bp.route("/rubrics/<int:rubric_id>/delete", methods=["POST"])
@role_required("admin")
def rubric_delete(rubric_id):
    rubric = db.get_or_404(Rubric, rubric_id)
    exam_id = rubric.exam_id
    if RubricScore.query.filter_by(rubric_id=rubric.id).first():
        flash("This criterion has already been used to award marks, so it cannot be deleted.", "warning")
    else:
        db.session.delete(rubric)
        db.session.commit()
        flash("Rubric criterion deleted.", "success")
    return redirect(url_for("admin.rubrics", exam_id=exam_id))


# --------------------------------------------------------------- examiners
@admin_bp.route("/examiners")
@role_required("admin")
def examiners():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    query = ExaminerProfile.query.join(User, ExaminerProfile.user_id == User.id)
    if q:
        query = query.filter(match(q, User.name, User.email, ExaminerProfile.department, id_column=ExaminerProfile.id))
    if status:
        query = query.filter(ExaminerProfile.status == status)
    return render_template("admin/examiners.html", examiners=query.order_by(ExaminerProfile.id).all(), q=q, status=status)


@admin_bp.route("/examiners/<int:examiner_id>")
@role_required("admin")
def examiner_view(examiner_id):
    examiner = db.get_or_404(ExaminerProfile, examiner_id)
    return render_template("admin/examiner_view.html", examiner=examiner)


@admin_bp.route("/examiners/<int:examiner_id>/<action>", methods=["POST"])
@role_required("admin")
def examiner_action(examiner_id, action):
    examiner = db.get_or_404(ExaminerProfile, examiner_id)
    rule = EXAMINER_ACTIONS.get(action)
    if rule is None:
        abort(404)
    allowed_from, new_status = rule
    if examiner.status not in allowed_from:
        flash(f"This action is not available for an examiner who is '{examiner.status}'.", "warning")
    else:
        examiner.status = new_status
        db.session.commit()
        flash(f"{examiner.user.name} is now: {new_status}.", "success")
    return redirect(request.form.get("next") or url_for("admin.examiners"))


# ---------------------------------------------------------------- students
@admin_bp.route("/students")
@role_required("admin")
def students():
    q = request.args.get("q", "").strip()
    exam_id = request.args.get("exam", "").strip()
    query = StudentProfile.query.join(User, StudentProfile.user_id == User.id)
    if q:
        query = query.filter(match(q, User.name, User.email, StudentProfile.roll_no, id_column=StudentProfile.id))
    if exam_id.isdigit():
        booked_ids = [row[0] for row in db.session.query(Booking.student_id).filter(Booking.exam_id == int(exam_id), Booking.status != "Cancelled").all()]
        query = query.filter(StudentProfile.id.in_(booked_ids))
    return render_template("admin/students.html", students=query.order_by(StudentProfile.id).all(), exams=Exam.query.order_by(Exam.name).all(), q=q, exam_id=exam_id)


# ------------------------------------------------------------------- slots
@admin_bp.route("/slots")
@role_required("admin")
def slots():
    exam_id = request.args.get("exam", "").strip()
    examiner_id = request.args.get("examiner", "").strip()
    status = request.args.get("status", "").strip()
    query = Slot.query
    if exam_id.isdigit():
        query = query.filter(Slot.exam_id == int(exam_id))
    if examiner_id.isdigit():
        query = query.filter(Slot.examiner_id == int(examiner_id))
    if status:
        query = query.filter(Slot.status == status)
    return render_template(
        "admin/slots.html",
        slots=query.order_by(Slot.date, Slot.start_time).all(),
        exams=Exam.query.order_by(Exam.name).all(),
        examiners=ExaminerProfile.query.join(User, ExaminerProfile.user_id == User.id).order_by(User.name).all(),
        exam_id=exam_id, examiner_id=examiner_id, status=status)


# ---------------------------------------------------------------- bookings
@admin_bp.route("/bookings")
@role_required("admin")
def bookings():
    q = request.args.get("q", "").strip()
    exam_id = request.args.get("exam", "").strip()
    status = request.args.get("status", "").strip()
    student_id = request.args.get("student", "").strip()
    query = (Booking.query
             .join(StudentProfile, Booking.student_id == StudentProfile.id)
             .join(User, StudentProfile.user_id == User.id))
    if q:
        query = query.filter(match(q, User.name, StudentProfile.roll_no, id_column=Booking.id))
    if exam_id.isdigit():
        query = query.filter(Booking.exam_id == int(exam_id))
    if status:
        query = query.filter(Booking.status == status)
    if student_id.isdigit():
        query = query.filter(Booking.student_id == int(student_id))
    return render_template("admin/bookings.html", bookings=query.order_by(Booking.id.desc()).all(), exams=Exam.query.order_by(Exam.name).all(), q=q, exam_id=exam_id, status=status, student_id=student_id)


# ------------------------------------------------------------ global search
@admin_bp.route("/search")
@role_required("admin")
def search():
    q = request.args.get("q", "").strip()
    results = None
    if q:
        results = {
            "students": (StudentProfile.query.join(User, StudentProfile.user_id == User.id)
                         .filter(match(q, User.name, User.email, StudentProfile.roll_no, id_column=StudentProfile.id)).limit(20).all()),
            "examiners": (ExaminerProfile.query.join(User, ExaminerProfile.user_id == User.id)
                          .filter(match(q, User.name, User.email, ExaminerProfile.department, id_column=ExaminerProfile.id)).limit(20).all()),
            "exams": (Exam.query.join(Course)
                      .filter(match(q, Exam.name, Course.code, Course.name, id_column=Exam.id))
                      .limit(20).all()),
            "bookings": (Booking.query
                         .join(StudentProfile, Booking.student_id == StudentProfile.id)
                         .join(User, StudentProfile.user_id == User.id)
                         .filter(match(q, User.name, StudentProfile.roll_no, id_column=Booking.id))
                         .limit(20).all()),
        }
    return render_template("admin/search.html", q=q, results=results)

@admin_bp.route("/exams/<int:exam_id>/publish", methods=["POST"])
@role_required("admin")
def exam_publish(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    active = [b for b in exam.bookings if b.status != "Cancelled"]
    if exam.results_published:
        exam.results_published = False
        db.session.commit()
        flash("Results unpublished. Examiners can edit evaluations again.", "success")
    elif exam.status not in ("Closed", "Completed"):
        flash("Close booking before publishing results.", "danger")
    elif not active:
        flash("There are no bookings to publish results for.", "danger")
    elif any(b.status == "Booked" for b in active):
        flash("Some students have not been evaluated yet.", "danger")
    else:
        exam.results_published = True
        db.session.commit()
        flash("Results published. Students can now see their marks and remarks.", "success")
    return redirect(url_for("admin.exams"))