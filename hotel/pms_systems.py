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

from hotel.external_api import (
    get_reservations_for_given_checkin_date,
    get_reservation_details,
)

from hotel.models import Stay, Guest, Hotel
from hotel.pms_functions import (
    api_call_with_retries,
    get_guest_from_reservation_guest_id,
    create_or_update_stay,
    checkin_and_checkout_are_valid,
)


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

                print("rservation_id", reservation_id)

                try:
                    reservation_details = api_call_with_retries(
                        get_reservation_details,
                        reservation_id
                    )
                except Exception as e:
                    logging.error(f"Api call for get_reservation_details failed with Exception '{e}'. "
                        "Stopping webhook processing")
                    return False

                # Check dates:
                reservation_checkin = reservation_details["CheckInDate"]
                reservation_checkout = reservation_details["CheckOutDate"]
                if not checkin_and_checkout_are_valid(reservation_checkin, reservation_checkout):
                    raise Exception(f"Invalid checkin {reservation_checkin}"
                        f"or checkout {checkout}")

                # Get Hotel
                reservation_hotel_id = reservation_details["HotelId"]
                if reservation_hotel_id != webhook_hotel_id:
                    logging.error(f"hotel id in webhook payload {webhook_hotel_id} "
                        f"and in api response payload {reservation_hotel_id} doesn't "
                        "match. Preparing a bug report and stopping webhook processing")
                    # Call function/method to prepare and post bug report or store
                    # information somewhere for later processing
                    return False

                try:
                    hotel = Hotel.objects.get(pms_hotel_id=reservation_hotel_id)
                except Hotel.DoesNotExist:
                    # I wasn't sure what the action should be in case the hotel doesn't
                    # exist in the db, if I should create it, or raise an error.
                    pass 

                print("hotel", hotel)

                # Get Guest:
                reservation_guest_id = reservation_details["GuestId"]
                try:
                    guest = get_guest_from_reservation_guest_id(reservation_guest_id)
                except Exception as e:
                    logging.error(f"Exception when getting guest data: {e}")
                    return False

                print("guest", guest)

                # Create or Update Stay
                reservation_status = reservation_details["Status"]

                try: 
                    stay = create_or_update_stay(
                        pms_reservation_id=reservation_id,
                        reservation_status=reservation_status, 
                        reservation_checkin=reservation_checkin, 
                        reservation_checkout=reservation_checkout, 
                        pms_guest_id=reservation_guest_id,
                        guest=guest, 
                        hotel=hotel,)
                except Exception as e:
                    raise Exception(f"Exception creating or updating stay({e})")

                print("stay", stay)

        return True


    def update_tomorrows_stays(self) -> bool:
        tomorrow = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        print(type(tomorrow), tomorrow)
        api_stays = api_call_with_retries(get_reservations_for_given_checkin_date, tomorrow)

        for api_stay in api_stays[0:3]:
            logging.info("Processing reservation update for tomorrow stay with reservation Id {reservation_id}")

            # Check dates:
            if not checkin_and_checkout_are_valid(api_stay["CheckInDate"], api_stay["CheckOutDate"]):
                raise Exception(f"Invalid checkin {api_stay['CheckInDate']}"
                    f"or checkout {api_stay['CheckOutDate']}")
            # Validate checkin is tomorrow

            print(api_stay)

            try:
                guest = get_guest_from_reservation_guest_id(api_stay["GuestId"])
            except Exception as e:
                logging.error(f"Exception when getting guest data: {e}")
                return False

            print("guest", guest)

            try:
                hotel = Hotel.objects.get(pms_hotel_id=api_stay['HotelId'])
            except Hotel.DoesNotExist:
                # I wasn't sure what the action should be in case the hotel doesn't
                # exist in the db, if I should create it, or raise an error.
                pass 

            print("hotel", hotel)

            try: 
                stay = create_or_update_stay(
                    pms_reservation_id=api_stay["ReservationId"],
                    reservation_status=api_stay["Status"], 
                    reservation_checkin=api_stay["CheckInDate"], 
                    reservation_checkout=api_stay["CheckOutDate"],
                    pms_guest_id=api_stay["GuestId"],
                    guest=guest, 
                    hotel=hotel,
                    )
            except Exception as e:
                print("Exception", e)
                return False

            print("stay", stay, stay.status, stay.checkin, stay.checkout)

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
