from abc import ABC, abstractmethod
import inspect
import logging
import datetime
import phonenumbers
import time
import json
import sys

from json.decoder import JSONDecodeError

from typing import Optional, Dict, List
from pydantic import BaseModel, ValidationError

from django.db.utils import IntegrityError

from hotel.external_api import (
    get_reservations_for_given_checkin_date,
    get_reservation_details,
    get_guest_details,
    APIError,
)

from hotel.models import Stay, Guest, Hotel
from hotel.constants import RESERVATION_STATUS_MAPPING
from hotel.pms_functions import api_call_with_retries, phoneisvalid, dateisvalid


class Event(BaseModel):
    Name: str
    Value: Dict[str, str]


class Payload(BaseModel):
    HotelId: str
    IntegrationId: str 
    Events: List[Event]


class PMS(ABC):
    """
    Abstract class for Property Management Systems.
    """

    def __init__(self):
        pass

    @property
    def name(self):
        longname = self.__class__.__name__
        return longname[4:]

    @abstractmethod
    def clean_webhook_payload(self, payload: str) -> dict:
        """
        Clean the json payload and return a usable object.
        Make sure the payload contains all the needed information to handle it properly
        """
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(self, webhook_data: dict) -> bool:
        """
        This method is called when we receive a webhook from the PMS.
        Handle webhook handles the events and updates relevant models in the database.
        Requirements:
            - Now that the PMS has notified you about an update of a reservation, you need to
                get more details of this reservation. For this, you can use the mock API
                call get_reservation_details(reservation_id).
            - Handle the payload for the correct hotel.
            - Update or create a Stay.
            - Update or create Guest details.
        """
        raise NotImplementedError

    @abstractmethod
    def update_tomorrows_stays(self) -> bool:
        """
        This method is called every day at 00:00 to update the stays with a checkin date tomorrow.
        Requirements:
            - Get all stays checking in tomorrow by calling the mock API endpoint get_reservations_for_given_checkin_date.
            - Update or create the Stays.
            - Update or create Guest details. Deal with missing and incomplete data yourself
                as you see fit. Deal with the Language yourself. country != language.
        """
        raise NotImplementedError

    @abstractmethod
    def stay_has_breakfast(self, stay: Stay) -> Optional[bool]:
        """
        This method is called when we want to know if the stay includes breakfast.
        Notice that the breakfast data is not stored in any of the models, we always want real time data.
        - Return True if the stay includes breakfast, otherwise False. Return None if you don't know.
        """
        raise NotImplementedError


def handle_exception(message):
    logging.error(message)
    # Store details about failed webhook for future processing


class PMS_Mews(PMS):
    def clean_webhook_payload(self, payload: str) -> dict:

        # Test that the payload is a valid JSON object
        try:
            payload_dict = json.loads(payload)

        except JSONDecodeError as j:
            handle_exception(f"A jsonDecodeError has occured: {j}. Stopping webhook processing")
            return {"payload_valid": False}

        except Exception as e:
            handle_exception(f"An exception has occured: {e}. Stopping webhook processing")
            return {"payload_valid": False}

        # Test that the payload follows the appropriate schema.
        # I decided to use pydantic for validation because we are already using typing
        # But it can also be done by hand
        try:
            p = Payload(**payload_dict)

        except ValidationError as v:
            handle_exception(f"A payload validation error has occured: {v}. Stopping webhook processing")
            return {"payload_valid": False}

        except Exception as e:
            handle_exception(f"An exception has occured: {e}. Stopping webhook processing")
            return {"payload_valid": False}

        # I could also add some code here to test the format of the Id strings
        # if that is a requirement for validation of the API payload. 
        # The string formatting for the corresponding db model fields don't
        # have that same constraint on the string format

        payload_dict["payload_valid"] = True
        return payload_dict


    # make atomic
    def handle_webhook(self, webhook_data: dict) -> bool:
        if not webhook_data["payload_valid"]:
            logging.error("Webhook payload invalid. Stoping webhook processing")
            # store information about failed webhook
            return False

        webhook_hotel_id = webhook_data["HotelId"]

        for event in webhook_data["Events"]:
            event_name = event["Name"]

            if event_name == "ReservationUpdated":
                # Get reservation data:
                reservation_id = event["Value"]["ReservationId"]
                logging.info("Processing reservation update event for reservation Id {reservation_id}")

                # To do: move the next 20 lines out to a separate function for readability
                try:
                    reservation_details = api_call_with_retries(
                        get_reservation_details,
                        reservation_id
                    )
                except Exception as e:
                    logging.error(f"Api call for get_reservation_details failed with Exception '{e}'. "
                        "Stopping webhook processing")
                    return False

                reservation_hotel_id = reservation_details["HotelId"]
                if reservation_hotel_id != webhook_hotel_id:
                    logging.error(f"reservation id in webhook payload {webhook_hotel_id} "
                        f"and in api response payload {reservation_hotel_id} doesn't "
                        "match. Preparing a bug report and stopping webhook processing")
                    # Call function/method to prepare and post bug report or store
                    # information somewhere for later processing
                    return False


                # Get Guest data:
                reservation_guest_id = reservation_details["GuestId"]
                
                try:
                    hotel = Hotel.objects.get(pms_hotel_id=reservation_hotel_id)
                except Hotel.DoesNotExist:
                    # I wasn't sure what the action should be in case the hotel doesn't
                    # exist in the db, if I should create it, or raise an error.
                    pass 

                try:
                    reservation_guest_details = api_call_with_retries(
                        get_guest_details,
                        reservation_guest_id
                    )
                except Exception as e:
                    logging.error(f"API call for get_guest_details failed with Exception '{e}'. "
                        "Stopping Webhook processing.")
                    return False

                reservation_guest_name = reservation_guest_details["Name"]
                if reservation_guest_name == "":
                    reservation_guest_name = None
                reservation_guest_phone = reservation_guest_details["Phone"]

                if not phoneisvalid(reservation_guest_phone, True):
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
                except IntegrityError as e:
                    logging.error(f"Encountered integrity error when get_or_create on guest: {e}"
                        "Stopping webhook processing")
                    return False
                except:
                    return False

                # But we can still catch the name mismatch:
                if guest.name != reservation_guest_name:
                    # Maybe update the name in the db to be the name in the latest reservation
                    # Or notify an admin or raise an exception
                    pass

                # Create or Update Stay

                reservation_status = reservation_details["Status"]

                if reservation_status in RESERVATION_STATUS_MAPPING:
                    status = RESERVATION_STATUS_MAPPING[reservation_status]
                else:
                    # log error and return false
                    return False

                reservation_checkin = reservation_details["CheckInDate"]
                checkin_isvalid = dateisvalid(reservation_checkin, "%Y-%m-%d", True)
                reservation_checkout = reservation_details["CheckOutDate"]
                checkout_isvalid = dateisvalid(reservation_checkout, "%Y-%m-%d", True)

                # In the following cases process a warning or error:
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

                # Get Stay if it exists:
                stay_exists = Stay.objects.filter(
                    pms_reservation_id=reservation_id, 
                    hotel=hotel,
                ).exists()

                if not stay_exists:
                    # I'm not sure if CANCEL, INSTAY and AFTER for a stay that
                    # doesn't exist should be allowed or we should
                    # raise an error and return false

                    new_stay = Stay(
                        pms_reservation_id=reservation_id,
                        hotel=hotel,
                        guest=guest,
                        pms_guest_id=reservation_guest_id,
                        status=status,
                        checkin=reservation_checkin,
                        checkout=reservation_checkout,
                    )
                    new_stay.save()

                else:
                    try:
                        old_stay = Stay.objects.get(
                            pms_reservation_id=reservation_id, 
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


                    except Exception as e:
                        print(e)
                        pass
                        # Handle exception 

        return True

    def update_tomorrows_stays(self) -> bool:
        # TODO: Implement the method
        return True

    def stay_has_breakfast(self, stay: Stay) -> Optional[bool]:
        # TODO: Implement the method
        return None


def get_pms(name):
    fullname = "PMS_" + name.capitalize()

    # find all class names in this module
    # from https://stackoverflow.com/questions/1796180/

    current_module = sys.modules[__name__]
    clsnames = [x[0] for x in inspect.getmembers(current_module, inspect.isclass)]

    # if we have a PMS class for the given name, return an instance of it
    return getattr(current_module, fullname)() if fullname in clsnames else False
