from abc import ABC, abstractmethod
import inspect
import logging
import json
import sys

from json.decoder import JSONDecodeError

from typing import Optional, Dict, List
from pydantic import BaseModel, ValidationError

from hotel.external_api import (
    get_reservations_for_given_checkin_date,
    get_reservation_details,
    get_guest_details,
    APIError,
)

from hotel.models import Stay, Guest, Hotel


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


    def handle_webhook(self, webhook_data: dict) -> bool:
        # TODO: Implement the method
        return True

    def update_tomorrows_stays(self) -> bool:
        # TODO: Implement the method
        return True

    def stay_has_breakfast(self, stay: Stay) -> Optional[bool]:
        # TODO: Implement the method
        return None


def get_pms(name):
    fullname = "PMS_" + name.capitalize()

    print("fullname", fullname)

    # find all class names in this module
    # from https://stackoverflow.com/questions/1796180/

    current_module = sys.modules[__name__]

    print("current_module", current_module)

    clsnames = [x[0] for x in inspect.getmembers(current_module, inspect.isclass)]

    print("clsnames", clsnames)

    # if we have a PMS class for the given name, return an instance of it
    return getattr(current_module, fullname)() if fullname in clsnames else False
