import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import requests


with open('config.json', 'r', encoding='utf-8') as f:
    con = json.load(f)

WEB_API = con.get("web_api", "https://smsbower.online/api")
API_KEY = con["api_key"]
ICLOUD = con["icloud"]
CHECKER_API = con.get("checker_api", "")
EMAIL_API = con.get("email_api", "")

OPERATOR_IDS = {
    "indosat": "indosat",
    "telkomsel": "telkomsel",
}


def get_current_max_price():
    """Get the current max price from config"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            return float(config.get("max_price", 0.135))
    except Exception:
        return 0.135


class SMSCore:

    def __init__(self):
        self.service_provider = con.get("service_provider", "herosms")
        self.country = con["country"]
        self.service = con["service"]
        self.allowed_prefixes = con.get("pref", [])
        self.blocked_prefixes = con.get("blocked_prefixes", [])

        self.desired_operator = con.get("operator", "indosat")
        self.operator_id = OPERATOR_IDS.get(self.desired_operator, self.desired_operator)

        self.api_key = con.get("api_key_herosms", con["api_key"])
        self.api_url = con.get("api_url_herosms", "https://hero-sms.com/stubs/handler_api.php")

        self.rent_duration = con.get("rent_duration", 0)
        self.use_rent = con.get("use_rent", False)
        self.currency = con.get("currency", 840)
        self.fixed_price = con.get("fixed_price", False)
        self.ref = con.get("ref", "")
        self.timeout_get_number = con.get("timeout_get_number", 300)
        self.timeout_wait_code = con.get("timeout_wait_code", 360)
        self.max_attempts = con.get("max_attempts", 500)
        self.debug_mode = con.get("debug_mode", True)

        os.environ['NO_PROXY'] = 'hero-sms.com,smsbower.online'
        os.environ['http_proxy'] = ''
        os.environ['https_proxy'] = ''

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Connection': 'keep-alive',
        }

        if self.debug_mode:
            print(f"Core initialized. Number provider mode: {self.service_provider}")
            print(f"   Operator: {self.desired_operator} (ID: {self.operator_id})")

    def _log(self, message: str):
        if self.debug_mode:
            print(f"[DEBUG] {message}")

    def _check_prefix(self, phone_number: str) -> bool:
        for prefix in self.blocked_prefixes:
            if phone_number.startswith(prefix):
                self._log(f"Number {phone_number} blocked (prefix {prefix})")
                return False

        if self.allowed_prefixes:
            for prefix in self.allowed_prefixes:
                if phone_number.startswith(prefix):
                    self._log(f"Number {phone_number} matched (prefix {prefix})")
                    return True
            self._log(f"Number {phone_number} matched no allowed prefix")
            return False

        return True

    def get_number(self) -> Optional[Dict[str, Any]]:
        current_max_price = get_current_max_price()

        attempt = 0
        pref_attempt = 0

        print(f"Requesting number via {self.service_provider}")
        print(f"   Service: {self.service}, Country: {self.country}")
        print(f"   Operator: {self.desired_operator}, Max price: {current_max_price}")

        while attempt < self.max_attempts:
            try:
                params = {
                    "api_key": self.api_key,
                    "action": "getNumberV2",
                    "service": self.service,
                    "country": self.country,
                }

                if self.operator_id:
                    params["operator"] = self.operator_id

                if current_max_price:
                    params["maxPrice"] = current_max_price
                    if self.fixed_price:
                        params["fixedPrice"] = "true"

                if self.blocked_prefixes:
                    params["phoneException"] = ",".join(self.blocked_prefixes[:20])

                if self.ref:
                    params["ref"] = self.ref

                if self.use_rent and self.rent_duration > 0:
                    params["action"] = "getRentNumber"
                    params["duration"] = self.rent_duration
                    params["currency"] = self.currency

                self._log(f"Request: {params}")

                response = requests.get(
                    self.api_url,
                    params=params,
                    headers=self.headers,
                    timeout=30,
                )

                if response.status_code != 200:
                    self._log(f"HTTP error: {response.status_code}")
                    attempt += 1
                    time.sleep(1)
                    continue

                try:
                    data = response.json()

                    if "error" in data or "errorCode" in data:
                        error_msg = data.get("error", data.get("errorCode", "Unknown error"))
                        self._log(f"API error: {error_msg}")

                        attempt += 1
                        time.sleep(1)
                        continue

                    activation_id = data.get("activationId")
                    phone_number = data.get("phoneNumber")

                    if activation_id and phone_number:
                        if self._check_prefix(phone_number):
                            print(f"Got number: {phone_number} (ID: {activation_id})")
                            if "activationCost" in data:
                                print(f"   Cost: {data['activationCost']} {data.get('currency', 'USD')}")
                            return {
                                "activationId": str(activation_id),
                                "phoneNumber": phone_number,
                                "full_data": data,
                            }
                        else:
                            pref_attempt += 1
                            print(f"Number: {phone_number} | Attempt: {pref_attempt} | Not matching, cancelling...")
                            self.cancel_activation(activation_id)
                            continue
                    else:
                        self._log(f"Unexpected response: {data}")
                        attempt += 1
                        time.sleep(1)

                except json.JSONDecodeError:
                    text = response.text
                    self._log(f"Text response: {text}")

                    if "ACCESS_NUMBER:" in text:
                        parts = text.split(":")
                        if len(parts) >= 3:
                            activation_id = parts[1]
                            phone_number = parts[2]

                            if self._check_prefix(phone_number):
                                return {"activationId": activation_id, "phoneNumber": phone_number}
                            else:
                                pref_attempt += 1
                                print(f"Number: {phone_number} | Attempt: {pref_attempt} | Not matching, cancelling...")
                                self.cancel_activation(activation_id)
                                continue

                    elif "NO_NUMBER" in text or "NO NUMBER" in text:
                        attempt += 1
                        print(f"No numbers, attempt {attempt}/{self.max_attempts}")
                        time.sleep(1)
                        continue

                    else:
                        attempt += 1
                        time.sleep(1)

            except Exception as e:
                self._log(f"Error: {e}")
                attempt += 1
                time.sleep(1)

        print("Failed to get number")
        return None

    def get_code(self, activation_id, phone_number: str, timeout: int = None) -> Optional[str]:
        if timeout is None:
            timeout = self.timeout_wait_code

        end_time = time.time() + timeout
        interval = 3

        print(f"Waiting for code for {phone_number}...")

        while time.time() < end_time:
            try:
                params = {
                    "api_key": self.api_key,
                    "action": "getStatusV2",
                    "id": activation_id,
                }

                response = requests.get(self.api_url, params=params, headers=self.headers, timeout=10)

                try:
                    data = response.json()

                    if data.get("sms") and data["sms"].get("code"):
                        code = data["sms"]["code"]
                        print(f"\nGot code: {code}")
                        return code

                    if data.get("code"):
                        print(f"\nGot code: {data['code']}")
                        return data["code"]

                    if data.get("status") == "wait_code":
                        print(".", end="", flush=True)
                    elif data.get("status") == "ok" and data.get("code"):
                        code = data["code"]
                        print(f"\nGot code: {code}")
                        return code

                except json.JSONDecodeError:
                    text = response.text

                    if text.startswith("STATUS_OK:"):
                        code = text.split(":")[1].strip()
                        print(f"\nGot code: {code}")
                        return code

                    elif text == "STATUS_WAIT_CODE":
                        print(".", end="", flush=True)

                    elif text in ["STATUS_CANCEL", "BAD_KEY", "NO_ACTIVATION"]:
                        raise Exception(f"Error: {text}")

                time.sleep(interval)

            except Exception as e:
                self._log(f"Error: {e}")
                time.sleep(interval)

        print(f"\nTimeout ({timeout} sec)")
        return None

    def set_status(self, activation_id: str, status: int) -> bool:
        try:
            params = {
                "api_key": self.api_key,
                "action": "setStatus",
                "id": activation_id,
                "status": status,
            }

            response = requests.get(self.api_url, params=params, headers=self.headers, timeout=30)

            if response.status_code == 204:
                return True

            text = response.text

            if "ACCESS" in text or "success" in text.lower() or "RETRY" in text:
                return True
            else:
                self._log(f"Status error: {text}")
                return False

        except Exception as e:
            self._log(f"Error: {e}")
            return False

    def complete_activation(self, activation_id: str) -> bool:
        try:
            params = {
                "api_key": self.api_key,
                "action": "finishActivation",
                "id": activation_id,
            }

            response = requests.get(self.api_url, params=params, headers=self.headers, timeout=30)
            return response.status_code == 204
        except Exception:
            return self.set_status(activation_id, 6)

    def cancel_activation(self, activation_id: str) -> bool:
        try:
            params = {
                "api_key": self.api_key,
                "action": "cancelActivation",
                "id": activation_id,
            }

            response = requests.get(self.api_url, params=params, headers=self.headers, timeout=30)
            return response.status_code == 204
        except Exception:
            return self.set_status(activation_id, 8)

    def send_sms_sent(self, activation_id: str) -> bool:
        return self.set_status(activation_id, 1)

    def get_balance(self) -> Optional[float]:
        try:
            params = {
                "api_key": self.api_key,
                "action": "getBalance",
            }

            response = requests.get(self.api_url, params=params, headers=self.headers, timeout=10)
            text = response.text

            if "ACCESS_BALANCE:" in text:
                balance = float(text.split(":")[1])
                print(f"Balance: {balance}")
                return balance

            return None

        except Exception as e:
            self._log(f"Balance error: {e}")
            return None


def buy_mail(service: str) -> Tuple[Optional[str], Optional[str]]:
    if ICLOUD is True:
        try:
            resp = requests.get(f"{EMAIL_API}/getEmail")
            resp.raise_for_status()
            data = resp.json()
            return data.get("mail")
        except Exception as err:
            print(f"[SMS] Failed to get email: {err}")
            return None
    else:
        try:
            url = f"{WEB_API}/mail/getActivation"
            params = {
                "api_key": API_KEY,
                "service": service,
                "domain": "gmail.com",
                "alias": 0,
            }

            response = requests.get(url, params=params, timeout=30)
            data = response.json()

            gmail = data.get("mail")
            mail_id = data.get("mailId")

            if gmail and mail_id:
                print(f"Bought email: {gmail}")
                return gmail, str(mail_id)
            else:
                print(f"Email purchase error: {data}")
                return None, None

        except Exception as e:
            print(f"Error while buying email: {e}")
            return None, None


def get_gmail_code(mail_id: str, gmail: str, timeout: int = 120) -> Optional[str]:
    try:
        resp = requests.get(
            f"{EMAIL_API}/getSms",
            params={"mailTo": gmail},
            timeout=95,
        )
        resp.raise_for_status()
        return resp.json().get("code")
    except Exception as err:
        print(f"[SMS] Failed to get code from email: {err}")
        return None


def get_phone_only() -> Tuple[Optional[str], Optional[str]]:
    sms = SMSCore()
    result = sms.get_number()

    if result:
        return result["phoneNumber"], result["activationId"]

    return None, None


def wait_for_code(activation_id: str, timeout: int = 120) -> Optional[str]:
    sms = SMSCore()
    return sms.get_code(activation_id, None, timeout)


def cancel_phone_activation(activation_id: str) -> bool:
    sms = SMSCore()
    return sms.cancel_activation(activation_id)


def complete_phone_activation(activation_id: str) -> bool:
    sms = SMSCore()
    return sms.complete_activation(activation_id)


class SmsBower(SMSCore):
    """Backward compatibility wrapper"""
    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key