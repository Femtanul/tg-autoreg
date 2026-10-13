import json
import threading
import time
from datetime import datetime, timedelta

import requests


with open('config.json', 'r', encoding='utf-8') as f:
    con = json.load(f)

api = con["api_bitrix"]
owner_id = con["owner_id"]

# Single shared lock for config.json across the whole project.
# Import this from other modules instead of creating a new one.
config_lock = threading.Lock()


def check_c(card_number, con):
    """Return which pool a card number belongs to"""
    for card in con["cards"]:
        if card_number == card["number"]:
            return "cards"

    for card in con["mastercards"]:
        if card_number == card["number"]:
            return "mastercards"


def card_block(card_number, hours_to_add=25):
    """Block a card for N hours"""
    with config_lock:
        with open('config.json', 'r', encoding='utf-8') as f:
            con_local = json.load(f)

        now = datetime.now()
        unblock_date = now + timedelta(hours=hours_to_add)
        blocked_until_str = unblock_date.strftime("%d.%m.%Y %H:%M")

        card_found = False
        for card in con_local["cards"]:
            if card["number"] == card_number:
                card["blocked_until"] = blocked_until_str
                card["active"] = False
                print(f"Card {card_number[-6:]} blocked until {blocked_until_str}")
                card_found = True
                break

        if not card_found:
            for card in con_local["mastercards"]:
                if card["number"] == card_number:
                    card["blocked_until"] = blocked_until_str
                    card["active"] = False
                    print(f"Backup card {card_number[-6:]} blocked until {blocked_until_str}")
                    break

        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(con_local, f, indent=2, ensure_ascii=False)


def check_card_timers():
    """Restore cards whose cooldown has expired"""
    with config_lock:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)

        now = datetime.now()
        cards_updated = False

        for card in config["cards"]:
            if not card.get("active", True) and "blocked_until" not in card:
                print(f"Old card {card['number'][-6:]} - unblocking (no block date)")
                card["active"] = True
                card["use"] = 3
                cards_updated = True

        for card in config["mastercards"]:
            if not card.get("active", True) and "blocked_until" not in card:
                print(f"Old backup card {card['number'][-6:]} - unblocking")
                card["active"] = True
                card["use"] = 2
                cards_updated = True

        for card in config["cards"]:
            if not card.get("active", True) and "blocked_until" in card:
                try:
                    blocked_until_str = card["blocked_until"]

                    try:
                        blocked_until = datetime.strptime(blocked_until_str, "%d.%m.%Y %H:%M")
                    except ValueError:
                        blocked_until = datetime.strptime(blocked_until_str, "%d.%m %H:%M")
                        blocked_until = blocked_until.replace(year=now.year)

                        if blocked_until < now and (now - blocked_until).days > 300:
                            blocked_until = blocked_until.replace(year=now.year + 1)

                    if now > blocked_until:
                        print(f"Card {card['number'][-6:]} unblocked (was blocked until {blocked_until_str})")
                        card["active"] = True
                        card["use"] = 3
                        card.pop("blocked_until", None)
                        cards_updated = True
                    else:
                        remaining = (blocked_until - now).seconds // 3600
                        print(f"Card {card['number'][-6:]} still blocked until {blocked_until_str} (~{remaining}h left)")

                except Exception as e:
                    print(f"Error checking card {card['number'][-6:]}: {e}")

        for card in config["mastercards"]:
            if not card.get("active", True) and "blocked_until" in card:
                try:
                    blocked_until_str = card["blocked_until"]

                    try:
                        blocked_until = datetime.strptime(blocked_until_str, "%d.%m.%Y %H:%M")
                    except ValueError:
                        blocked_until = datetime.strptime(blocked_until_str, "%d.%m %H:%M")
                        blocked_until = blocked_until.replace(year=now.year)

                        if blocked_until < now and (now - blocked_until).days > 300:
                            blocked_until = blocked_until.replace(year=now.year + 1)

                    if now > blocked_until:
                        print(f"Backup card {card['number'][-6:]} unblocked (was blocked until {blocked_until_str})")
                        card["active"] = True
                        card["use"] = 2
                        card.pop("blocked_until", None)
                        cards_updated = True
                    else:
                        remaining = (blocked_until - now).seconds // 3600
                        print(f"Backup card {card['number'][-6:]} still blocked until {blocked_until_str} (~{remaining}h left)")

                except Exception as e:
                    print(f"Error checking backup card {card['number'][-6:]}: {e}")

        if cards_updated:
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print("Config updated after timer check")

        return config


def request_code(phone_number, retries=60, interval=1):
    """Ask the external API to send the login code"""
    for _ in range(retries):
        try:
            resp = requests.post(api, json={"phone": phone_number}, timeout=30)
            data = resp.json()

            if data.get("status") == "code_sent":
                print(f"[API] code_sent received for {phone_number}")
                return True
            else:
                print(f"[API] Waiting for code_sent for {phone_number}...", data)

        except Exception as err:
            print(f"[API] POST /login error: {err}")

        time.sleep(interval)

    raise Exception(f"[API] Failed to get code_sent for {phone_number}")


def submit_code(phone_number, code_tg, retries=3):
    """Submit the received login code to the external API"""
    for _ in range(retries):
        try:
            resp = requests.patch(
                api,
                json={"phone": phone_number, "code": code_tg, "ownerID": owner_id},
                timeout=30,
            )
            data = resp.json()

            if data.get("status") == "logged_in":
                print(f"[API] Account successfully authorized for {phone_number}")
                return True
            else:
                print(f"[API] Authorization error for {phone_number}", data)

        except Exception as err:
            print(f"[API] PATCH /login error: {err}")

        time.sleep(1)

    raise Exception(f"[API] Failed to authorize account for {phone_number}")