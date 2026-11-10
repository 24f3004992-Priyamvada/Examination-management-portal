from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)      # admin / examiner / student
    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ExaminerProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    department = db.Column(db.String(100))
    contact = db.Column(db.String(30))
    status = db.Column(db.String(20), default="Pending")  # Pending / Approved / Deactivated
    user = db.relationship("User", backref=db.backref("examiner_profile", uselist=False))


class StudentProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    roll_no = db.Column(db.String(30), unique=True, nullable=False)
    contact = db.Column(db.String(30))
    user = db.relationship("User", backref=db.backref("student_profile", uselist=False))


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="Active")   # Active / Inactive


class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    exam_type = db.Column(db.String(30), nullable=False)  # Viva / Practical / Project Demo / Assessment
    duration = db.Column(db.Integer, nullable=False)      # minutes
    max_marks = db.Column(db.Integer, nullable=False)
    slot_create_start = db.Column(db.Date)
    slot_create_end = db.Column(db.Date)
    booking_start = db.Column(db.Date)
    booking_end = db.Column(db.Date)
    status = db.Column(db.String(20), default="Draft")    # Draft / Slot Creation / Booking Open / Closed / Completed
    results_published = db.Column(db.Boolean, default=False)
    course = db.relationship("Course", backref="exams")


class ExamExaminer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("examiner_profile.id"), nullable=False)
    exam = db.relationship("Exam", backref="assignments")
    examiner = db.relationship("ExaminerProfile", backref="assignments")
    __table_args__ = (db.UniqueConstraint("exam_id", "examiner_id"),)


class Rubric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    criterion = db.Column(db.String(150), nullable=False)
    max_marks = db.Column(db.Integer, nullable=False)
    weightage = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    exam = db.relationship("Exam", backref="rubrics")


class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("examiner_profile.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    available_seats = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="Available")  # Available / Full / Cancelled / Completed
    exam = db.relationship("Exam", backref="slots")
    examiner = db.relationship("ExaminerProfile", backref="slots")


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profile.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    booked_on = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default="Booked")     # Booked / Cancelled / Completed
    student = db.relationship("StudentProfile", backref="bookings")
    slot = db.relationship("Slot", backref="bookings")
    exam = db.relationship("Exam", backref="bookings")


class Evaluation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), unique=True, nullable=False)
    total_marks = db.Column(db.Float, default=0)
    remarks = db.Column(db.Text)
    evaluated_on = db.Column(db.DateTime, default=datetime.now)
    booking = db.relationship("Booking", backref=db.backref("evaluation", uselist=False))


class RubricScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluation.id"), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey("rubric.id"), nullable=False)
    marks = db.Column(db.Float, nullable=False)
    evaluation = db.relationship("Evaluation", backref="scores")
    rubric = db.relationship("Rubric")


class BookingHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    action = db.Column(db.String(30), nullable=False)       # Booked / Cancelled / Rescheduled / Examiner changed
    details = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.now)
    booking = db.relationship("Booking", backref="history")