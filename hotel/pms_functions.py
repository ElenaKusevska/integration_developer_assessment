import time
import json
import logging
import datetime
import os

from json.decoder import JSONDecodeError

from django.db.utils import IntegrityError

from hotel.models import Stay, Guest, Hotel
from hotel.constants import RESERVATION_STATUS_MAPPING
from hotel.external_api import APIError, get_guest_details

logging.basicConfig(level=os.environ.get("LOGLEVEL", "DEBUG"))
logger = logging.getLogger(__name__)


def api_call_with_retries(apiname, *args):
    i = 0
    while i < 20:
        try:
            payload = apiname(*args)
            try:
                payload_dict = json.loads(payload)
                return payload_dict
            except JSONDecodeError as j:
                logger.error(f"A JSONDecodeError occured on retry {i}: {j}. Retrying again...")
            except Exception as e:
                logger.error(f"An Exception occured on retry {i}: {e}. Retrying again...")

        except APIError as a:
            logger.error(f"APIError occured on retry {i}: {a}. Retrying again...")
        except Exception as e:
            logger.error(f"An API Exception occured on retry {i}: {e}. Retrying again...")

        time.sleep(1)
        i = i + 1
    
    raise Exception(f"Api Call for {apiname.__name__}. failed after 20 retries. "
        "Raising Exception and preparing report")
    # Store data or send a notification in the system about failed API call


def date_is_valid(datestr, dateformat, propertyallowsnull):
    # dateformat should be stored somewhere for the API
    # propertyallowsnull should be stored somewhere for the specific hotel
    # In this case I am using it as hardcoded values as an example
    if datestr == None or datestr == '':
        if propertyallowsnull:
            return True
        else:
            return False
    else:
        try:
            date = datetime.datetime.strptime(datestr, dateformat)
            return True
        except ValueError:
            return False


def phone_is_valid(phone, e164formatted):
    try:
        x = phonenumbers.parse(phone, None)
    except:
        return False
    # Not supplying a country because maybe the guest doesn't have a
    # phone from his country because he is travelling a lot
    return(phonenumbers.is_valid_number(x))


def checkin_and_checkout_are_valid(checkin, checkout):
    checkin_isvalid = date_is_valid(checkin, "%Y-%m-%d", True)
    checkout_isvalid = date_is_valid(checkout, "%Y-%m-%d", True)

    # In the following cases return False:
    # 1) The reservation status is After, but the stay checkin
    # is in the future process as an error or warning
    # 2) The reservation status is being changed from After to Before
    # 3) The reservation status is Before, but the dates are already passed
    # (It's not possible to get this scenario with the external_api.py)

    if not (checkin_isvalid and checkout_isvalid):
        # I think that if the checkin or checkout is not valid
        # we should raise an error and stop the processing,
        # but in the Stay model it is allowed to have a null for
        # checkin and checkout, so I'm not sure what is the correct
        # action
        pass

    return True


def get_guest_from_reservation_guest_id(reservation_guest_id):
    try:
        reservation_guest_details = api_call_with_retries(
            get_guest_details,
            reservation_guest_id
        )
    except Exception as e:
        raise Exception(f"API call for get_guest_details failed with Exception '{e}'. "
            "Stopping Webhook processing.")

    reservation_guest_name = reservation_guest_details["Name"]
    if reservation_guest_name == "":
        reservation_guest_name = None
    reservation_guest_phone = reservation_guest_details["Phone"]

    if not phone_is_valid(reservation_guest_phone, True):
        # In case the phone is important for reasons other than as a
        # unique id on the user, an error should be raised here
        pass

    # For the case where the guest name on the reservation
    # doesn't match the guest name for that guestId in the db:
    # we store or update the reservation/stay
    # even though the name doesn't match that in the database.
    # Because the phone is like a unique identifier for the guest, not the name.
    # And also, the reservation was already created, so there is priority for
    # the stays in the db to reflect that
    # We could also raise and error, or just a warning and notify an admin

    # I also don't know if there is any logic for the name, for example
    # if only the first name is provided. But because we use the phone as
    # the id, I am allowing that to be accepted.

    try:
        guest, created = Guest.objects.get_or_create(
            phone=reservation_guest_phone, 
            defaults = {"name": reservation_guest_name})
    except IntegrityError as i:
        raise Exception(f"Encountered integrity error when get_or_create on guest: {i}"
            "Stopping webhook processing")
    except Exception as e:
        raise Exception(f"Encountered error when get_or_create on guest: {e}"
            "Stopping webhook processing")

    # But we can still catch the name mismatch:
    if guest.name != reservation_guest_name:
        # Maybe update the name in the db to be the name in the latest reservation
        # Or notify an admin or raise an exception
        pass

    return guest

def create_or_update_stay(
    pms_reservation_id,
    reservation_status, 
    reservation_checkin, 
    reservation_checkout, 
    pms_guest_id,
    guest, 
    hotel,
    ):

    if reservation_status in RESERVATION_STATUS_MAPPING:
        status = RESERVATION_STATUS_MAPPING[reservation_status]
    else:
        # log error and return false
        return False

    
    

    # Get Stay if it exists:
    stay_exists = Stay.objects.filter(
        pms_reservation_id=pms_reservation_id, 
        hotel=hotel,
    ).exists()

    if not stay_exists:
        # I'm not sure if CANCEL, INSTAY and AFTER for a stay that
        # doesn't exist should be allowed or we should
        # raise an error and return false

        try:
            new_stay = Stay(
                pms_reservation_id=pms_reservation_id,
                hotel=hotel,
                guest=guest,
                pms_guest_id=pms_guest_id,
                status=status,
                checkin=reservation_checkin,
                checkout=reservation_checkout,
            )
            new_stay.save()
            return new_stay
        except Exception as e:
            raise Exception(f"Exception creating new stay: {e}")
            

    else:
        try:
            old_stay = Stay.objects.get(
                pms_reservation_id=pms_reservation_id,
                hotel=hotel,
            )

            if old_stay.status != status:
                old_stay.status = status

            if old_stay.guest != guest:
                pass
                # Is a guest change allowed?

            # I'm not checking for pms_guest_id because
            # it gives us the phone and phone gives us the guest

            if old_stay.checkin != reservation_checkin:
                old_stay.checkin = reservation_checkin

            if old_stay.checkout != reservation_checkout:
                old_stay.checkout = reservation_checkout
            
            old_stay.save()

            # We want to do one db operation for

            return old_stay

        except Exception as e:
            raise Exception(f"Exception updating existing stay {e}")