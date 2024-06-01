import time
import json
import logging
import datetime

from json.decoder import JSONDecodeError

from hotel.external_api import APIError


def api_call_with_retries(apiname, *args):
    i = 0
    while i < 20:
        try:
            payload = apiname(*args)
            try:
                payload_dict = json.loads(payload)
                return payload_dict
            except JSONDecodeError as j:
                logging.error(f"A JSONDecodeError occured on retry {i}: {j}. Retrying again...")
            except Exception as e:
                logging.error(f"An Exception occured on retry {i}: {e}. Retrying again...")

        except APIError as a:
            logging.error(f"APIError occured on retry {i}: {a}. Retrying again...")
        except Exception as e:
            logging.error(f"An API Exception occured on retry {i}: {e}. Retrying again...")

        time.sleep(1)
        i = i + 1
    
    raise Exception(f"Api Call for {apiname.__name__}. failed after 20 retries. "
        "Raising Exception and preparing report")
    # Store data or send a notification in the system about failed API call


def dateisvalid(datestr, dateformat, propertyallowsnull):
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


def phoneisvalid(phone, e164formatted):
    try:
        x = phonenumbers.parse(phone, None)
    except:
        return False
    # Not supplying a country because maybe the guest doesn't have a
    # phone from his country because he is travelling a lot
    return(phonenumbers.is_valid_number(x))