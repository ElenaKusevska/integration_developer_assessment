from hotel.models import Stay


RESERVATION_STATUS_MAPPING = {
    "in_house": Stay.Status.INSTAY,
    "checked_out": Stay.Status.AFTER,
    "cancelled": Stay.Status.CANCEL,
    "no_show": Stay.Status.UNKNOWN, #I 'm not sure if a no-show is a cancel
    "not_confirmed": Stay.Status.BEFORE,
    "booked": Stay.Status.BEFORE,
}