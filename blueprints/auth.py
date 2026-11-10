from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user, login_required
from extensions import db
from models import User, StudentProfile, ExaminerProfile

auth_bp = Blueprint("auth", __name__)


def go_to_dashboard(user):
    return redirect(url_for(f"{user.role}.dashboard"))


@auth_bp.route("/")
def index():
    if current_user.is_authenticated:
        return go_to_dashboard(current_user)
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return go_to_dashboard(current_user)
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
        elif not user.is_active:
            flash("Your account has been deactivated. Contact the admin.", "danger")
        else:
            login_user(user)
            return go_to_dashboard(user)
    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register/student", methods=["GET", "POST"])
def register_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        roll_no = request.form.get("roll_no", "").strip()
        contact = request.form.get("contact", "").strip()

        error = None
        if not name or not email or not roll_no:
            error = "Name, email and roll number are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "This email is already registered."
        elif StudentProfile.query.filter_by(roll_no=roll_no).first():
            error = "This roll number is already registered."

        if error:
            flash(error, "danger")
        else:
            user = User(name=name, email=email, role="student")
            user.set_password(password)
            db.session.add(user)
            db.session.flush()   # gives user.id without finishing the save
            db.session.add(StudentProfile(user_id=user.id, roll_no=roll_no, contact=contact))
            db.session.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register_student.html")


@auth_bp.route("/register/examiner", methods=["GET", "POST"])
def register_examiner():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        department = request.form.get("department", "").strip()
        contact = request.form.get("contact", "").strip()

        error = None
        if not name or not email or not department:
            error = "Name, email and department are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "This email is already registered."

        if error:
            flash(error, "danger")
        else:
            user = User(name=name, email=email, role="examiner")
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            db.session.add(ExaminerProfile(user_id=user.id, department=department,
                                           contact=contact, status="Pending"))
            db.session.commit()
            flash("Registered. You can log in once the admin approves you.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register_examiner.html")


@auth_bp.route("/pending")
@login_required
def pending():
    if current_user.role == "examiner" and current_user.examiner_profile.status == "Approved":
        return redirect(url_for("examiner.dashboard"))
    return render_template("auth/pending.html")