from datetime import date, datetime
from extensions import db
from models import Slot, Booking, BookingHistory, ExamExaminer

ACTIVE = ("Booked", "Completed")


def booking_open(exam):
    today = date.today()
    return exam.status == "Booking Open" and exam.booking_start <= today <= exam.booking_end


def active_booking_for_exam(student, exam_id):
    return Booking.query.filter(Booking.student_id == student.id, Booking.exam_id == exam_id, Booking.status.in_(ACTIVE)).first()


def can_book(student, slot):
    exam = slot.exam
    if not booking_open(exam):
        return "Booking is not open for this examination right now."
    if slot.status != "Available" or slot.available_seats <= 0:
        return "This slot is full or no longer available."
    if datetime.combine(slot.date, slot.start_time) <= datetime.now():
        return "This slot has already started or passed."
    if active_booking_for_exam(student, exam.id):
        return "You already have a booking for this examination. Cancel it first to pick another slot."

    mine = (Booking.query.join(Slot, Booking.slot_id == Slot.id)
            .filter(Booking.student_id == student.id, Booking.status == "Booked",
                    Slot.date == slot.date).all())
    for b in mine:
        if slot.start_time < b.slot.end_time and b.slot.start_time < slot.end_time:
            return f"This overlaps your booking for {b.exam.name} on the same day."
    return None


def book_slot(student, slot):
    error = can_book(student, slot)
    if error:
        return None, error

    claimed = (db.session.query(Slot)
               .filter(Slot.id == slot.id, Slot.status == "Available", Slot.available_seats > 0)
               .update({Slot.available_seats: Slot.available_seats - 1}, synchronize_session=False))
    if claimed == 0:
        db.session.rollback()
        return None, "Sorry, this slot has just been filled."

    db.session.refresh(slot)
    if slot.available_seats == 0:
        slot.status = "Full"

    booking = Booking(student_id=student.id, slot_id=slot.id, exam_id=slot.exam_id, status="Booked")
    db.session.add(booking)
    db.session.flush()
    db.session.add(BookingHistory(booking_id=booking.id, action="Booked", details=f"Slot {slot.id} on {slot.date}"))
    db.session.commit()
    return booking, None


def can_cancel(booking):
    if booking.status != "Booked":
        return "Only active bookings can be cancelled."
    if not booking_open(booking.exam):
        return "The booking deadline has passed, so this booking can no longer be cancelled."
    return None


def cancel_booking(booking):
    error = can_cancel(booking)
    if error:
        return error
    slot = booking.slot
    booking.status = "Cancelled"
    slot.available_seats = min(slot.capacity, slot.available_seats + 1)
    if slot.status == "Full":
        slot.status = "Available"
    db.session.add(BookingHistory(booking_id=booking.id, action="Cancelled", details=f"Slot {slot.id} on {slot.date}"))
    db.session.commit()
    return None

def reschedule_booking(booking, new_slot):
    exam = booking.exam
    if booking.status != "Booked":
        return "Only active bookings can be rescheduled."
    if booking.evaluation is not None:
        return "This booking has already been evaluated and cannot be moved."
    if exam.status == "Completed" or exam.results_published:
        return "This examination is completed, so its bookings can no longer be moved."
    if new_slot.exam_id != booking.exam_id:
        return "The new slot must belong to the same examination."
    if new_slot.id == booking.slot_id:
        return "The booking is already in this slot."
    if new_slot.status != "Available" or new_slot.available_seats <= 0:
        return "The new slot is full or not available."
    if datetime.combine(new_slot.date, new_slot.start_time) <= datetime.now():
        return "The new slot has already started or passed."

    others = (Booking.query.join(Slot, Booking.slot_id == Slot.id)
              .filter(Booking.student_id == booking.student_id, Booking.status == "Booked",
                      Booking.id != booking.id, Slot.date == new_slot.date).all())
    for b in others:
        if new_slot.start_time < b.slot.end_time and b.slot.start_time < new_slot.end_time:
            return f"The student already has an overlapping booking for {b.exam.name} at that time."

    claimed = (db.session.query(Slot)
               .filter(Slot.id == new_slot.id, Slot.status == "Available", Slot.available_seats > 0)
               .update({Slot.available_seats: Slot.available_seats - 1}, synchronize_session=False))
    if claimed == 0:
        db.session.rollback()
        return "The new slot has just been filled."

    old_slot = booking.slot
    db.session.refresh(new_slot)
    if new_slot.available_seats == 0:
        new_slot.status = "Full"
    old_slot.available_seats = min(old_slot.capacity, old_slot.available_seats + 1)
    if old_slot.status == "Full":
        old_slot.status = "Available"

    booking.slot = new_slot
    db.session.add(BookingHistory(
        booking_id=booking.id, action="Rescheduled",
        details=f"Slot {old_slot.id} ({old_slot.date}) to slot {new_slot.id} ({new_slot.date})"))
    db.session.commit()
    return None


def change_slot_examiner(slot, new_examiner):
    if slot.status in ("Completed", "Cancelled"):
        return "This slot is completed or cancelled."
    if new_examiner.id == slot.examiner_id:
        return "This examiner is already assigned to the slot."
    if new_examiner.status != "Approved":
        return "The new examiner must be approved."
    if not ExamExaminer.query.filter_by(exam_id=slot.exam_id, examiner_id=new_examiner.id).first():
        return "Assign this examiner to the examination first."
    if any(b.evaluation is not None for b in slot.bookings):
        return "Some students in this slot are already evaluated, so the examiner cannot be changed."

    clashes = Slot.query.filter(Slot.examiner_id == new_examiner.id, Slot.date == slot.date, Slot.status != "Cancelled", Slot.id != slot.id).all()
    for other in clashes:
        if slot.start_time < other.end_time and other.start_time < slot.end_time:
            return "The new examiner already has another slot at that time."

    old = slot.examiner
    slot.examiner = new_examiner
    for b in slot.bookings:
        if b.status == "Booked":
            db.session.add(BookingHistory(booking_id=b.id, action="Examiner changed", details=f"{old.user.name} to {new_examiner.user.name}"))
    db.session.commit()
    return None