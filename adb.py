import sys
sys.path.append('.')

import json
import random
import re
import threading
import time
import traceback
import subprocess as s
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests
import uiautomator2 as u2
from flask import Flask, request
from playsound import playsound

from core import buy_mail
from core import get_gmail_code
from core import get_phone_only
from core import wait_for_code
from com import check_c
from com import check_card_timers
from com import config_lock
from com import submit_code


with open('config.json', 'r', encoding='utf-8') as file:
    con = json.load(file)

api = con["api_bitrix"]
checker_api = con.get("checker_api", "")
start = con["ui_elements"]["start"]
done = con["ui_elements"]["done"]
ban = con["ui_elements"]["ban"]
checkmail = con["ui_elements"]["check_email"]
mail = con["ui_elements"]["add_email"]
mail1 = con["ui_elements"]["choose_login"]
yes = con["ui_elements"]["yes"]
get_prem = con["ui_elements"]["get_premium"]
terms = con["ui_elements"]["terms_of_service"]
continue1 = con["ui_elements"]["continue"]
ok = con["ui_elements"]["ok"]
owner_id = con["owner_id"]
pseudocode = con["ui_elements"]["pseudocode"]
profinfo = con["ui_elements"]["profinfo"]
password = con["ui_elements"]["password"]
words1 = con["words1"]
words2 = con["words2"]
input1 = con["ui_elements"]["input1"]
input2 = con["ui_elements"]["input2"]
invalid_phone_number = con["ui_elements"]["invalid_phone_number"]

SCRCPY_FOLDER = con.get("scrcpy_folder", "")

code_data = {}  # {device_serial: {"code": None, "time": 0, "bank131": False}}
code_lock = threading.Lock()

devices_list = con.get("devices", [])

if not devices_list:
    print("No devices in config! Add 'devices' to config.json")
    exit(1)

print(f"Found devices: {len(devices_list)}")
for dev in devices_list:
    print(f"   - {dev}")

app = Flask(__name__)


# ==================== ADB / uiautomator2 PRIMITIVES ====================

def adb_cmd(device_serial, cmd):
    """Run ADB command for a specific device"""
    try:
        return s.run(
            f"adb -s {device_serial} {cmd}",
            shell=True,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"ADB error on {device_serial}: {e}")
        return None


def clear_app_data(device_serial, package_name="org.telegram.messenger"):
    """Clear app data on a specific device"""
    try:
        d = u2.connect(device_serial)
        d.app_stop(package_name)

        result = adb_cmd(device_serial, f"shell pm clear {package_name}")
        if result and "Success" in result.stdout:
            print(f"[{device_serial}] Data for {package_name} cleared")
        else:
            print(f"[{device_serial}] Clear error: {result.stdout if result else 'No result'}")
        time.sleep(3)
    except Exception as e:
        print(f"[{device_serial}] Error while clearing: {e}")


def enable_telegram_permissions(device_serial):
    """Enable Telegram permissions on a specific device"""
    adb_cmd(device_serial, "shell pm grant org.telegram.messenger android.permission.POST_NOTIFICATIONS")
    adb_cmd(device_serial, "shell pm grant org.telegram.messenger android.permission.READ_CONTACTS")
    adb_cmd(device_serial, "shell pm grant org.telegram.messenger android.permission.WRITE_CONTACTS")

    check_notif = adb_cmd(device_serial, "shell dumpsys package org.telegram.messenger | findstr POST_NOTIFICATIONS")
    check_contacts_read = adb_cmd(device_serial, "shell dumpsys package org.telegram.messenger | findstr READ_CONTACTS")
    check_contacts_write = adb_cmd(device_serial, "shell dumpsys package org.telegram.messenger | findstr WRITE_CONTACTS")

    print(f"[{device_serial}] Telegram permissions:")
    if check_notif and "granted=true" in check_notif.stdout:
        print(f"  [{device_serial}] Notifications: ON")
    else:
        print(f"  [{device_serial}] Notifications: OFF")

    if check_contacts_read and "granted=true" in check_contacts_read.stdout:
        print(f"  [{device_serial}] Read contacts: ON")
    else:
        print(f"  [{device_serial}] Read contacts: OFF")

    if check_contacts_write and "granted=true" in check_contacts_write.stdout:
        print(f"  [{device_serial}] Write contacts: ON")
    else:
        print(f"  [{device_serial}] Write contacts: OFF")
    time.sleep(2)


def click_xpath(d, xpath, wait=2, retry=5):
    """Click by xpath using uiautomator2"""
    time.sleep(wait)

    for _ in range(retry):
        try:
            element = d.xpath(xpath)
            if element.exists:
                element.click()
                time.sleep(1.5)
                return True
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)

    return False


def swait(d, *xpaths, timeout=120, interval=1):
    """Wait for element to appear"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        for xpath in xpaths:
            try:
                if d.xpath(xpath).exists:
                    time.sleep(0.5)
                    return xpath
            except Exception:
                pass
        time.sleep(interval)
    return None


# ==================== WEBHOOK ====================

@app.route('/', methods=['POST'])
def webhook():
    msg_body = request.form.get('key', '')
    device_serial = request.form.get('device', None)

    print(f"\n[WEBHOOK] Received: {msg_body}")

    if not device_serial:
        print("Device not specified in webhook, applying to all devices")
        with code_lock:
            for serial in devices_list:
                if "Bank131" in msg_body:
                    if serial not in code_data:
                        code_data[serial] = {"code": None, "time": 0, "bank131": False}
                    code_data[serial]["bank131"] = True
                    print(f"Bank131 on {serial} - code NOT saved")
                else:
                    code_match = re.search(r'\d{4}', msg_body)
                    if code_match:
                        if serial not in code_data:
                            code_data[serial] = {"code": None, "time": 0, "bank131": False}
                        code_data[serial]["code"] = code_match.group()
                        code_data[serial]["time"] = time.time()
                        print(f"WEBHOOK SAVED CODE for {serial}: {code_data[serial]['code']}")
        return "ok", 200

    with code_lock:
        if device_serial not in code_data:
            code_data[device_serial] = {"code": None, "time": 0, "bank131": False}

        if "Bank131" in msg_body:
            code_data[device_serial]["bank131"] = True
            print(f"Bank131 on {device_serial} - code NOT saved")
        else:
            code_match = re.search(r'\d{4}', msg_body)
            if code_match:
                code_data[device_serial]["code"] = code_match.group()
                code_data[device_serial]["time"] = time.time()
                print(f"WEBHOOK SAVED CODE for {device_serial}: {code_data[device_serial]['code']}")

    return "ok", 200


threading.Thread(
    target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False),
    daemon=True,
).start()


# ==================== SMS CODE BUFFER ====================

def get_current_sms_code(device_serial, timeout=60):
    """Get SMS code for a specific device from the buffer"""
    start_wait = time.time()
    print(
        f"\n>>> [{device_serial}] Waiting for code received AFTER "
        f"{time.strftime('%H:%M:%S', time.localtime(start_wait))}"
    )

    end_time = start_wait + timeout

    while time.time() < end_time:
        with code_lock:
            device_record = code_data.get(device_serial)

            if device_record:
                code = device_record.get("code")
                code_time = device_record.get("time", 0)

                if code and code_time >= start_wait:
                    res = code
                    code_data[device_serial]["code"] = None
                    print(f"[{device_serial}] SUCCESS! CODE RECEIVED: {res}")
                    return res

        time.sleep(0.5)

    print(f"[{device_serial}] TIMEOUT: No new code arrived.")
    return None


# ==================== PAYMENT HANDLER ====================

def yoo(d, device_serial, card_number):
    """Handle payment confirmation code"""
    result = check_c(card_number, con)
    if result == "mastercards":
        print(f"[{device_serial}] Mastercard")
        found = swait(
            d,
            "//*[@text='Store OTO*Telegram']",
            "//*[@text='Store Bank131']",
            '//*[@text="Purchase confirmation"]',
            timeout=33,
        )
        if found == "//*[@text='Store Bank131']":
            print(f"[{device_serial}] Bank131")
            with code_lock:
                if device_serial in code_data:
                    code_data[device_serial]["bank131"] = True
            return False
        else:
            code_yoo = get_current_sms_code(device_serial, timeout=180)
            click_xpath(d, "(//android.view.View)[7]")
            d.send_keys(code_yoo)
            return True
    else:
        print(f"[{device_serial}] Mir")
        code_yoo = get_current_sms_code(device_serial, timeout=180)
        time.sleep(2)
        click_xpath(d, "(//android.view.View)[14]")
        d.send_keys(code_yoo)
        return True


if SCRCPY_FOLDER:
    s.Popen(rf"{SCRCPY_FOLDER}/cmdd.bat", cwd=SCRCPY_FOLDER, shell=True)


# ==================== MAIN WORKER ====================

def run_device(device_serial):
    """Run session for a specific device"""
    print(f"\n{'='*60}")
    print(f"STARTING FOR DEVICE: {device_serial}")
    print(f"{'='*60}\n")

    try:
        d = u2.connect(device_serial)
        d.implicitly_wait(10)
        d.info
        print(f"[{device_serial}] Device connected")
    except Exception as e:
        print(f"[{device_serial}] Failed to connect: {e}")
        return

    while True:
        try:
            clear_app_data(device_serial)

            with config_lock:
                with open('config.json', 'r', encoding='utf-8') as f:
                    local_con = json.load(f)

            local_con = check_card_timers()

            active_cards = [c for c in local_con["cards"] if c.get("active", True)]
            if not active_cards:
                print(f"[{device_serial}] No active normal cards. Using backup.")
                active_master_cards = [c for c in local_con["mastercards"] if c.get("active", True)]
                if not active_master_cards:
                    print(f"[{device_serial}] NO ACTIVE CARDS AT ALL! All blocked!")
                    time.sleep(60)
                    continue
                card = random.choice(active_master_cards)
            else:
                card = random.choice(active_cards)

            card_number = card["number"]
            card_cvv = card["cvv"]
            card_mm = card["mmYY"]
            print(f"[{device_serial}] Using card: {card_number[-6:]}")

            d.app_stop("org.telegram.messenger")
            time.sleep(2)

            enable_telegram_permissions(device_serial)

            d.app_start("org.telegram.messenger")
            start_time = time.time()

            found = swait(d, start, timeout=30)
            if found:
                click_xpath(d, start)
            else:
                print(f"[{device_serial}] Start button not found, continuing...")

            phone, activation_id = get_phone_only()
            if not phone:
                print(f"[{device_serial}] Failed to get phone, retrying")
                continue

            print(f"[{device_serial}] Number issued at:", time.ctime())
            try:
                playsound("1.mp3")
            except Exception:
                pass

            found = swait(d, done, timeout=30)
            if found:
                click_xpath(d, '//*[@content-desc="Country code"]')
                adb_cmd(device_serial, f"shell input text {phone}")
                click_xpath(d, done)
            else:
                print(f"[{device_serial}] Input field not found")
                continue

            found = swait(d, yes, timeout=30)
            if found:
                time.sleep(2)
                click_xpath(d, yes)
            else:
                print(f"[{device_serial}] Confirmation button not found")
                continue

            found = swait(d, ban, checkmail, mail, mail1, invalid_phone_number, pseudocode, timeout=460)
            if found in (ban, checkmail, invalid_phone_number, pseudocode):
                print(f"[{device_serial}] Account banned or requires verification")
                continue
            else:
                print(f"[{device_serial}] All good, continuing")

            if checker_api:
                response = requests.get(checker_api, params={"phone": phone}, timeout=30)
                data = response.json()

                is_registered = data.get("isregistred")
                print(data)
                if is_registered is False:
                    print(f"Number {phone} is clean, can be used")
                else:
                    print(f"Number {phone} already in Telegram, cancelling")
                    continue

            email = buy_mail("tg")
            time.sleep(2)

            if email:
                gmail = email
                print(f"[{device_serial}] Got email: {gmail}")
            else:
                print(f"[{device_serial}] Error: failed to get email")
                continue

            swait(d, input1)
            click_xpath(d, input1)
            time.sleep(2)
            d.send_keys(gmail)
            click_xpath(d, done)

            gmail_code = get_gmail_code(device_serial, gmail)
            time.sleep(1)

            if gmail_code is None:
                continue
            else:
                click_xpath(d, input1)
                d.send_keys(str(gmail_code))

            found = swait(d, get_prem, pseudocode, timeout=60)
            if found == pseudocode:
                print(f"[{device_serial}] Pseudocode, skipping")
                continue
            elif found == get_prem:
                print(f"[{device_serial}] Premium available")
            else:
                click_xpath(d, input1)
                d.send_keys(gmail_code)

            if found == get_prem:
                click_xpath(d, get_prem)

            found = swait(d, terms, timeout=300)
            if found:
                click_xpath(
                    d,
                    '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]/android.widget.FrameLayout[1]/android.view.View[1]',
                )
                time.sleep(1)
                click_xpath(
                    d,
                    '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]',
                )

            found = swait(d, "//android.webkit.WebView", "(//android.view.View)[13]")
            if found:
                print("Card input")
                d.click(248, 260)
                d.send_keys(card_number)
                time.sleep(0.5)
                click_xpath(d, "(//android.widget.EditText)[2]")
                d.send_keys(card_mm)
                time.sleep(0.5)
                click_xpath(d, "(//android.widget.EditText)[3]")
                d.send_keys(card_cvv)
                time.sleep(1)
                click_xpath(d, "//*[@text='Done']")

            swait(d, "(//android.widget.FrameLayout)[37]")
            d.click(195, 1480)
            found = swait(d, continue1, timeout=30)
            click_xpath(d, continue1)
            found = swait(d, ok, '//*[@text="Purchase confirmation"]')

            if found == ok:
                while True:
                    for _ in range(3):
                        click_xpath(d, '//*[@resource-id="com.android.systemui:id/back"]')
                        time.sleep(1)
                        click_xpath(d, '//*[@resource-id="com.android.systemui:id/back"]')
                        time.sleep(1)
                        click_xpath(d, '//*[@resource-id="com.android.systemui:id/back"]')

                        found = swait(d, get_prem, timeout=60)
                        if found == get_prem:
                            click_xpath(d, get_prem)

                        found = swait(d, terms, timeout=300)
                        if found:
                            click_xpath(
                                d,
                                '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]/android.widget.FrameLayout[1]/android.view.View[1]',
                            )
                            time.sleep(1)
                            click_xpath(
                                d,
                                '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]/android.widget.FrameLayout[2]',
                            )

                        found = swait(d, "//android.webkit.WebView", "(//android.view.View)[13]")
                        if found:
                            print("Card input")
                            d.click(248, 260)
                            d.send_keys(card_number)

                            click_xpath(d, "(//android.widget.EditText)[2]")
                            d.send_keys(card_mm)

                            click_xpath(d, "(//android.widget.EditText)[3]")
                            d.send_keys(card_cvv)

                            click_xpath(d, "//*[@text='Done']")

                            swait(d, "(//android.widget.FrameLayout)[37]")

                            d.click(195, 1480)
                            print("Tap")
                            found = swait(d, continue1, timeout=30)
                            if found:
                                click_xpath(d, continue1)
                            found = swait(
                                d,
                                '//*[@text="Purchase confirmation"]',
                                '//*[@text="Purchase confirmation"]',
                                ok,
                                timeout=30,
                            )
                            if found == ok:
                                continue
                            elif found == '//*[@text="Purchase confirmation"]':
                                print("All good !1!")
                                yoo(d, device_serial, card_number)
                                break
                    else:
                        continue
                    break
            elif found == '//*[@text="Purchase confirmation"]':
                print("All good")
                yoo(d, device_serial, card_number)

            while True:
                adb_cmd(
                    device_serial,
                    "shell pm grant org.telegram.messenger android.permission.POST_NOTIFICATIONS",
                )
                adb_cmd(
                    device_serial,
                    "shell pm grant org.telegram.messenger android.permission.READ_MEDIA_IMAGES",
                )
                adb_cmd(
                    device_serial,
                    "shell pm grant org.telegram.messenger android.permission.READ_MEDIA_VIDEO",
                )

                found = swait(d, ok, get_prem, timeout=60)

                if found == ok:
                    print("TG retry")
                elif found == get_prem:
                    with config_lock:
                        with open('config.json', 'r', encoding='utf-8') as f:
                            config = json.load(f)

                        card_was_deactivated = False
                        card_type = None

                        for card_item in config['cards']:
                            if card_item['number'] == card_number:
                                card_type = 'cards'
                                card_item['use'] -= 1

                                if card_item['use'] <= 0:
                                    card_item['active'] = False
                                    unblock_date = datetime.now() + timedelta(hours=25)
                                    card_item["blocked_until"] = unblock_date.strftime("%d.%m.%Y %H:%M")
                                    print(
                                        f"[{device_serial}] Card {card_number[-6:]} burned out, "
                                        f"timer until {card_item['blocked_until']}"
                                    )
                                    card_was_deactivated = True
                                break

                        if not card_type:
                            for card_item in config['mastercards']:
                                if card_item['number'] == card_number:
                                    card_type = 'mastercards'
                                    card_item['use'] -= 1

                                    if card_item['use'] <= 0:
                                        card_item['active'] = False
                                        unblock_date = datetime.now() + timedelta(hours=25)
                                        card_item["blocked_until"] = unblock_date.strftime("%d.%m.%Y %H:%M")
                                        print(
                                            f"[{device_serial}] Backup card {card_number[-6:]} burned out, "
                                            f"timer until {card_item['blocked_until']}"
                                        )
                                        card_was_deactivated = True
                                    break

                        with open('config.json', 'w', encoding='utf-8') as f:
                            json.dump(config, f, indent=2)

                    if card_was_deactivated:
                        with config_lock:
                            with open('config.json', 'r', encoding='utf-8') as f:
                                local_con = json.load(f)

                        active_cards = [c for c in local_con["cards"] if c.get("active", True)]
                        active_master_cards = [c for c in local_con["mastercards"] if c.get("active", True)]

                        if not active_cards and not active_master_cards:
                            print(f"[{device_serial}] NO ACTIVE CARDS AT ALL! Waiting for unblock...")
                            check_card_timers()
                            time.sleep(60)
                            continue
                        elif not active_cards and active_master_cards:
                            print(f"[{device_serial}] Normal cards exhausted, switching to backup")
                            card = random.choice(active_master_cards)
                            card_number = card["number"]
                            card_cvv = card["cvv"]
                            card_mm = card["mmYY"]
                            print(f"[{device_serial}] Switched to backup card: {card_number[-6:]}")
                        elif active_cards:
                            card = random.choice(active_cards)
                            card_number = card["number"]
                            card_cvv = card["cvv"]
                            card_mm = card["mmYY"]
                            print(f"[{device_serial}] New normal card: {card_number[-6:]}")

                    print(f"[{device_serial}] Card passed")
                    break
                else:
                    print(f"[{device_serial}] Timeout waiting, continuing...")
                    break

            adb_cmd(device_serial, "shell input keyevent 4")
            time.sleep(1)

            found = swait(d, done, timeout=30)
            if found:
                click_xpath(d, done)

            time.sleep(1)
            found = swait(d, yes, timeout=30)
            if found:
                click_xpath(d, yes)

            time.sleep(3)
            sms = wait_for_code(activation_id)
            if sms is None:
                print("Bad. No code arrived")
                continue
            print(f"[{device_serial}] {sms}")
            click_xpath(d, input1)
            time.sleep(1)
            adb_cmd(device_serial, f"shell input text {sms}")

            found = swait(d, profinfo, password, timeout=30)
            if found == profinfo:
                click_xpath(d, input1)
                name = random.choice(words1)
                name2 = random.choice(words2)
                time.sleep(1)
                d.send_keys(name)
                time.sleep(0.5)
            elif found == password:
                print(f"[{device_serial}] Password required")
                continue
            else:
                print(f"[{device_serial}] Unexpected screen state")
                continue

            found = swait(d, input2, timeout=10)
            if found:
                click_xpath(d, input2)
                d.send_keys(name2)
                click_xpath(d, done)

            time.sleep(2)
            print(f"\n[{device_serial}] {'='*50}")

            print(f"[{device_serial}] [API] Requesting code for {phone}...")
            try:
                resp = requests.get(
                    api,
                    params={"phone": phone},
                    timeout=180,
                )
                data = resp.json()

                if data.get("status") == "code_sent":
                    print(f"[{device_serial}] [API] Server received code request")
                elif data.get("error") == "already_in_progress":
                    print(f"[{device_serial}] [API] Authorization already in progress")
                else:
                    print(f"[{device_serial}] [API] Unexpected response: {data}")
            except Exception as e:
                print(f"[{device_serial}] [API] Code request error: {e}")

            print(f"[{device_serial}] [API] Waiting for code in notifications...")
            code = None

            for _ in range(180):
                try:
                    code_elements = d.xpath(
                        '//*[contains(@text, "Login code") or contains(@text, "code")]'
                    ).all()

                    for elem in code_elements:
                        text = elem.text
                        codes = re.findall(r'\d{5}', str(text))
                        if codes:
                            code = codes[0]
                            print(f"[{device_serial}] [API] Found code in notification: {code}")
                            break

                    if code:
                        break
                except Exception:
                    pass

                time.sleep(0.5)

            if not code:
                print(f"[{device_serial}] [API] No code, trying any notifications")
                try:
                    xml = d.dump_hierarchy()
                    codes = re.findall(r'\b\d{5}\b', xml)
                    if codes:
                        code = codes[0]
                        print(f"[{device_serial}] [API] Found code in XML: {code}")
                except Exception:
                    pass

            if code:
                print(f"[{device_serial}] [API] Sending code {code} to server...")
                try:
                    resp = requests.get(
                        api,
                        params={"phone": phone, "code": code, "ownerID": owner_id},
                        timeout=20,
                    )
                    data = resp.json()

                    if data.get("status") == "logged_in":
                        print(f"[{device_serial}] Account {phone} linked to owner_id: {owner_id}")
                        end_time = time.time() - start_time
                        print(f"[{device_serial}] Execution time: {end_time:.2f} sec")
                    else:
                        print(f"[{device_serial}] [API] Server responded: {data}")

                except Exception as e:
                    print(f"[{device_serial}] [API] Code submit error: {e}")
            else:
                print(f"[{device_serial}] [API] Code not found, skipping linking")

        except Exception as e:
            print(f"[{device_serial}] CRITICAL ERROR: {e}")
            traceback.print_exc()
            time.sleep(10)
            continue


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"MULTITHREADED MODE STARTING")
    print(f"Devices: {devices_list}")
    print(f"{'='*60}\n")

    with ThreadPoolExecutor(max_workers=len(devices_list)) as executor:
        futures = [executor.submit(run_device, device) for device in devices_list]

        for future in futures:
            try:
                future.result()
            except Exception as e:
                print(f"Thread crashed: {e}")