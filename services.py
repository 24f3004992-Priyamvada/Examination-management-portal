from datetime import date, datetime
from extensions import db
from models import Slot, Booking, BookingHistory

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