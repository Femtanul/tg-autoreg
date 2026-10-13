# tg-autoreg

> A multithreaded automation system for physical Android devices driven via ADB and uiautomator2, featuring event-driven webhook processing, a stateful resource pool with rotation and recovery, and a fault-tolerant UI automation flow.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📖 Table of Contents

- [English](#english)
  - [Overview](#overview)
  - [Architecture](#architecture)
  - [What's in the repo and what's not](#whats-in-the-repo-and-whats-not)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Managing the Card Pool](#managing-the-card-pool)
  - [Running](#running)
  - [Webhook Setup](#webhook-setup)
  - [Troubleshooting](#troubleshooting)
- [Русский](#русский)
  - [Обзор](#обзор)
  - [Архитектура](#архитектура)
  - [Что в репе, а что нет](#что-в-репе-а-что-нет)
  - [Требования](#требования)
  - [Установка](#установка)
  - [Настройка конфига](#настройка-конфига)
  - [Работа с картами](#работа-с-картами)
  - [Запуск](#запуск)
  - [Вебхук](#вебхук)
  - [Частые проблемы](#частые-проблемы)

---

# English

## Overview

**tg-autoreg** drives multiple physical Android devices in parallel through a complex, multi-step UI flow. It coordinates shared, rate-limited resources (phone numbers, email addresses, payment cards) via a stateful pool with automatic rotation, blocking timers, and recovery.

The system automates a repetitive account-registration workflow: launching a target app, injecting a purchased phone number, waiting for an SMS confirmation code delivered via webhook, confirming an email address, completing a payment flow with a rotating pool of bank cards, and linking the created account to an upstream API.

It is a reference implementation of a physical-device automation farm. The same architecture applies to QA device farms, mobile app testing at scale, and any orchestration task where physical devices cannot be replaced by emulators.

## Architecture

The system is organized into three concerns:

```
┌─────────────────────────────────────────────────────────────┐
│  Orchestration layer  (adb.py)                              │
│                                                             │
│   ┌───────────────┐      ┌──────────────────────────────┐   │
│   │ Flask webhook │─────▶│  thread-safe per-device      │   │
│   │  (producer)   │      │  SMS code buffer             │   │
│   └───────────────┘      └──────────────┬───────────────┘   │
│                                         │                   │
│                          ┌──────────────▼───────────────┐   │
│                          │ ThreadPoolExecutor           │   │
│                          │  one worker per device       │   │
│                          └──────────────┬───────────────┘   │
└─────────────────────────────────────────┼───────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      ┌───────────────┐           ┌───────────────┐           ┌───────────────┐
      │ Device layer  │           │ Device layer  │           │ Device layer  │
      │  (adb.py)     │           │  (adb.py)     │           │  (adb.py)     │
      │   ADB + u2    │           │   ADB + u2    │           │   ADB + u2    │
      └───────┬───────┘           └───────┬───────┘           └───────┬───────┘
              │                           │                           │
              └───────────────────────────┼───────────────────────────┘
                                          ▼
                              ┌───────────────────────┐
                              │  Providers layer      │
                              │  (core.py, com.py)    │
                              │  SMS / email / cards  │
                              └───────────────────────┘
```

| Layer | File | Responsibility |
|---|---|---|
| **Orchestration + device** | `adb.py` | Worker threads, Flask webhook, UI flow, ADB primitives, SMS buffer |
| **Resource management** | `com.py` | Card pool rotation, cooldown timers, config locking, upstream API calls |
| **Providers** | `core.py` | SMS provider client, email acquisition, phone number filtering |

### Notable engineering decisions

- **Per-device isolation of external events.** The webhook accepts an optional `device` parameter, so a single shared endpoint can serve a farm of devices without cross-talk.
- **Timestamp-based code matching.** The SMS code buffer stores the arrival timestamp alongside the code; consumers only accept codes that arrived *after* they started waiting.
- **Cooldown-based resource recovery.** Blocked cards are stored with an absolute unblock timestamp. On every cycle, the runner reactivates cards whose cooldown has expired.
- **Config-as-shared-state.** `config.json` is the single source of truth for the resource pool, protected by an in-process `threading.Lock`.
- **Defensive UI automation.** Every device interaction goes through `swait` (element polling) and `click_xpath` (click with retry).

## What's in the repo and what's not

This repository contains the **orchestration layer only**. It defines the interfaces and the flow, but some external modules are **not included** and are expected to be supplied by the user:

| Module | Included? | Purpose |
|---|---|---|
| Flask webhook (producer) | ✅ Yes | Receives SMS codes over HTTP |
| Card pool + rotation | ✅ Yes | Manages the payment card inventory |
| SMS provider client | ✅ Yes | Interfaces with HeroSMS / SMSBower |
| Email provider client | ✅ Yes | Interfaces with an external email API |
| **Phone number checker** | ❌ No | Queries upstream to determine if a phone number is already registered, to avoid wasting money on used numbers |
| **Unlimited email generator** | ❌ No | Backend service that produces an unlimited supply of email addresses |
| **Session bot** | ❌ No | Service that receives pre-built app sessions and performs account operations |

The three excluded modules are external services with proprietary implementations. The repo calls them over HTTP through configurable endpoints (`checker_api`, `email_api`), so a user can plug in their own implementations without touching the orchestrator.

## Requirements

### 1. Python 3.10+

```bash
# Ubuntu / Debian
sudo apt install python3 python3-pip python3-venv

# macOS
brew install python@3.12

# Windows
# Download from python.org, check "Add Python to PATH"
```

### 2. ADB (Android Platform Tools)

```bash
# Ubuntu / Debian
sudo apt install android-tools-adb android-tools-fastboot

# macOS
brew install android-platform-tools

# Windows
# Download from https://developer.android.com/tools/releases/platform-tools
# Extract, add to PATH
```

Verify: `adb version`

### 3. scrcpy (optional — for live device mirroring)

Download from https://github.com/Genymobile/scrcpy/releases and extract. Set `scrcpy_folder` in `config.json` to the extracted folder.

### 4. Android devices

- Enable **Developer Options** (tap Build Number 7 times).
- Enable **USB Debugging**.
- Connect via USB. Verify with `adb devices`.
- **Rooted devices are recommended.** Root (e.g. via Magisk modules) allows bypassing the security flag dynamically. Without root, the UI flow relies on hardcoded coordinates and artificial delays — less stable across devices. The choice is yours.

### 5. Payment cards

You will need your own payment cards that pass the target app's payment flow. Where to get them is up to you — the repo only provides the rotation mechanism, not the cards.

## Installation

```bash
git clone https://github.com/Femtanul/tg-autoreg.git
cd tg-autoreg

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

```bash
cp config.example.json config.json
```

Then edit `config.json` and fill in your values.

### Key fields

| Field | Purpose |
|---|---|
| `devices` | List of ADB serials (from `adb devices`) |
| `api_key`, `api_key_herosms` | SMS provider API keys |
| `api_url`, `api_url_herosms` | SMS provider endpoints |
| `web_api`, `web_api_herosms` | SMS provider web API endpoints |
| `api_bitrix` | Upstream API endpoint for account linking |
| `checker_api` | Endpoint for phone number availability check |
| `email_api` | Endpoint for email acquisition |
| `owner_id` | Target account identifier on the upstream API |
| `country`, `service` | SMS provider country and service codes |
| `max_price` | Maximum price per number |
| `blocked_prefixes` | Phone number prefixes to reject |
| `pref` | Allowed prefixes (empty = allow all) |
| `cards` | Primary card pool |
| `mastercards` | Backup card pool |
| `ui_elements` | XPath selectors for the target app |
| `scrcpy_folder` | Path to local scrcpy installation |
| `debug_mode` | Verbose logging |

### Finding device serials

```bash
adb devices
```

Example output:
```
List of devices attached
RF8R90WBZLP     device
emulator-5554   device
```

Copy serials into the `devices` array.

## Managing the Card Pool

Cards live in `config.json` under two arrays: `cards` (primary) and `mastercards` (backup). Each card is:

```json
{
  "number": "0000000000000000",
  "cvv": "000",
  "mmYY": "0000",
  "active": true,
  "use": 50
}
```

| Field | Meaning |
|---|---|
| `number` | Card number (16 digits, no spaces) |
| `cvv` | CVV / verification code |
| `mmYY` | Expiry date in MMYY format (e.g. `0231` = Feb 2031) |
| `active` | `true` = available, `false` = blocked |
| `use` | Remaining uses before auto-block |

### How cards are used

1. On each cycle, the worker reads `config.json` and picks a random **active** card from `cards`.
2. If no active cards in `cards`, it falls back to `mastercards`.
3. After a successful payment, `use` is decremented.
4. When `use` reaches `0`, the card is marked `active: false` and gets a `blocked_until` timestamp (25 hours from now).
5. On every subsequent cycle, `check_card_timers()` scans the config and reactivates cards whose cooldown has expired.

### Adding a card

Append a new object to the `cards` array:

```json
{
  "number": "1234567890123456",
  "cvv": "123",
  "mmYY": "1229",
  "active": true,
  "use": 50
}
```

Save the file — no restart needed, the orchestrator re-reads on every cycle.

### Removing or disabling

- **Remove:** delete the object from the array.
- **Temporarily disable:** set `"active": false`.
- **Manual unblock:** set `"active": true` and remove the `blocked_until` field, or wait for the cooldown to expire.

## Running

```bash
python adb.py
```

The orchestrator will:

1. Read `config.json` and validate `devices`.
2. Launch `scrcpy` for each device (if `scrcpy_folder` is set).
3. Start the Flask webhook on `0.0.0.0:5000`.
4. Spawn one worker thread per device.
5. Drive each device through the full flow, retrying on failure.

Stop with `Ctrl+C`.

## Webhook Setup

The webhook receives SMS codes from an external forwarding service.

### Endpoint

```
POST http://<your-host>:5000/
Content-Type: application/x-www-form-urlencoded
```

### Parameters

| Field | Required | Purpose |
|---|---|---|
| `key` | Yes | The SMS message body. A 4-digit code is extracted via regex. |
| `device` | No | ADB serial of the target device. If omitted, the code is broadcast to all devices. |

### Example

```bash
curl -X POST http://localhost:5000/ \
  -d "key=Your Telegram code is 1234" \
  -d "device=RF8R90WBZLP"
```

### Forwarding options

The webhook must receive SMS messages from the phone that holds the SIM card. Two common setups:

- **Same network (LAN):** the phone runs a local SMS-forwarder app that POSTs to your PC's local IP. Works only if phone and PC are on the same Wi-Fi/LAN.
- **Via Telegram bot:** the phone forwards SMS to a Telegram bot, the bot POSTs to the orchestrator over the internet. Works across networks — recommended if devices are remote.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `adb: command not found` | ADB not in PATH | Install platform-tools, add to PATH |
| `No devices in config!` | `devices` array empty | Run `adb devices`, copy serials |
| `Failed to connect: <serial>` | USB debugging off / cable issue | Re-enable, replug |
| `NO ACTIVE CARDS AT ALL!` | All cards blocked / `use` exhausted | Add cards or wait for cooldown |
| SMS code never arrives | Webhook not reachable | Test with `curl`, check forwarder |
| `uiautomator2` connect timeout | Device locked / app crashed | Unlock, clear app data |
| scrcpy window doesn't open | Wrong `scrcpy_folder` | Check path, run scrcpy standalone |

---

# Русский

## Обзор

**tg-autoreg** — многопоточная система автоматизации физических Android-устройств. Управляет фермой устройств параллельно, координируя общие ресурсы (номера телефонов, email-адреса, банковские карты) через stateful-пул с автоматической ротацией, таймерами блокировки и восстановлением.

Система автоматизирует повторяющийся процесс регистрации аккаунтов: запуск целевого приложения, ввод купленного номера телефона, ожидание SMS-кода через вебхук, подтверждение email-адреса, прохождение платёжного флоу с ротацией банковских карт и привязка созданного аккаунта к внешнему API.

Это референсная реализация фермы физических устройств. Та же архитектура применима к QA-фермам, массовому тестированию мобильных приложений и любым задачам, где физические устройства нельзя заменить эмуляторами.

## Архитектура

Система разделена на три слоя:

```
┌─────────────────────────────────────────────────────────────┐
│  Слой оркестрации  (adb.py)                                 │
│                                                             │
│   ┌───────────────┐      ┌──────────────────────────────┐   │
│   │ Flask webhook │─────▶│  потокобезопасный буфер      │   │
│   │  (producer)   │      │  SMS-кодов по устройствам    │   │
│   └───────────────┘      └──────────────┬───────────────┘   │
│                                         │                   │
│                          ┌──────────────▼───────────────┐   │
│                          │ ThreadPoolExecutor           │   │
│                          │  один воркер на устройство   │   │
│                          └──────────────┬───────────────┘   │
└─────────────────────────────────────────┼───────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      ┌───────────────┐           ┌───────────────┐           ┌───────────────┐
      │ Device layer  │           │ Device layer  │           │ Device layer  │
      │  (adb.py)     │           │  (adb.py)     │           │  (adb.py)     │
      │   ADB + u2    │           │   ADB + u2    │           │   ADB + u2    │
      └───────┬───────┘           └───────┬───────┘           └───────┬───────┘
              │                           │                           │
              └───────────────────────────┼───────────────────────────┘
                                          ▼
                              ┌───────────────────────┐
                              │  Слой провайдеров     │
                              │  (core.py, com.py)    │
                              │  SMS / email / карты  │
                              └───────────────────────┘
```

| Слой | Файл | Ответственность |
|---|---|---|
| **Оркестрация + устройство** | `adb.py` | Воркеры, Flask-вебхук, UI-флоу, ADB-примитивы, буфер SMS |
| **Управление ресурсами** | `com.py` | Пул карт, таймеры кулдауна, лок конфига, вызовы upstream API |
| **Провайдеры** | `core.py` | Клиент SMS-провайдера, покупка email, фильтрация номеров |

### Ключевые инженерные решения

- **Изоляция внешних событий по устройствам.** Вебхук принимает опциональный параметр `device`, поэтому один общий эндпоинт обслуживает ферму без перекрёстных помех.
- **Сопоставление кода по времени.** Буфер SMS-кодов хранит время прихода вместе с кодом; воркер принимает только коды, пришедшие *после* начала ожидания.
- **Восстановление ресурсов по таймеру.** Заблокированные карты хранятся с абсолютной меткой разблокировки. На каждом цикле оркестратор восстанавливает карты с истёкшим кулдауном.
- **Config как общее состояние.** `config.json` — единственный источник правды для пула ресурсов, защищён `threading.Lock`.
- **Отказоустойчивая автоматизация UI.** Каждое взаимодействие идёт через `swait` (поллинг элемента) и `click_xpath` (клик с ретраем).

## Что в репе, а что нет

В репозитории лежит **только слой оркестрации**. Он определяет интерфейсы и флоу, но некоторые внешние модули **не включены** и подразумевают самостоятельную реализацию:

| Модуль | Есть в репе? | Назначение |
|---|---|---|
| Flask-вебхук (producer) | ✅ Да | Принимает SMS-коды по HTTP |
| Пул карт + ротация | ✅ Да | Управляет инвентарём платёжных карт |
| Клиент SMS-провайдера | ✅ Да | Работа с HeroSMS / SMSBower |
| Клиент email-провайдера | ✅ Да | Работа с внешним email API |
| **Чекер номеров** | ❌ Нет | Проверяет у upstream, занят ли номер, чтобы не тратить деньги на уже использованные |
| **Бесконечный генератор почт** | ❌ Нет | Backend-сервис, выдающий неограниченное количество email-адресов |
| **Бот для сессий** | ❌ Нет | Сервис, принимающий готовые сессии приложения и выполняющий операции с аккаунтами |

Три исключённых модуля — внешние сервисы с проприетарной реализацией. Репа обращается к ним по HTTP через конфигурируемые эндпоинты (`checker_api`, `email_api`), поэтому пользователь может подключить свои реализации без правок оркестратора.

## Требования

### 1. Python 3.10+

```bash
# Ubuntu / Debian
sudo apt install python3 python3-pip python3-venv

# macOS
brew install python@3.12

# Windows
# Скачай с python.org, отметь "Add Python to PATH"
```

### 2. ADB (Android Platform Tools)

```bash
# Ubuntu / Debian
sudo apt install android-tools-adb android-tools-fastboot

# macOS
brew install android-platform-tools

# Windows
# Скачай с https://developer.android.com/tools/releases/platform-tools
# Распакуй, добавь в PATH
```

Проверь: `adb version`

### 3. scrcpy (опционально — для отображения экрана)

Скачай с https://github.com/Genymobile/scrcpy/releases и распакуй. Укажи `scrcpy_folder` в `config.json`.

### 4. Android-устройства

- Включи **Режим разработчика** (тапни по номеру сборки 7 раз).
- Включи **Отладку по USB**.
- Подключи по USB. Проверь: `adb devices`.
- **Рекомендуются рутированные устройства.** Рут (например, через модули Magisk) позволяет динамически обходить security flag. Без рута UI-флоу опирается на хардкод координат и искусственные задержки — менее стабильно между устройствами. Выбор за тобой.

### 5. Платёжные карты

Нужны свои карты, которые проходят платёжный флоу целевого приложения. Где их брать — твоя задача. Репа даёт только механизм ротации, не карты.

## Установка

```bash
git clone https://github.com/Femtanul/tg-autoreg.git
cd tg-autoreg

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Настройка конфига

```bash
cp config.example.json config.json
```

Открой `config.json` и впиши свои значения.

### Основные поля

| Поле | Назначение |
|---|---|
| `devices` | Список ADB-серийников (из `adb devices`) |
| `api_key`, `api_key_herosms` | API-ключи SMS-провайдеров |
| `api_url`, `api_url_herosms` | Эндпоинты SMS-провайдеров |
| `web_api`, `web_api_herosms` | Web API эндпоинты SMS-провайдеров |
| `api_bitrix` | Эндпоинт upstream API для привязки аккаунтов |
| `checker_api` | Эндпоинт чекера доступности номеров |
| `email_api` | Эндпоинт для получения email |
| `owner_id` | ID аккаунта на upstream API |
| `country`, `service` | Коды страны и сервиса у SMS-провайдера |
| `max_price` | Максимальная цена за номер |
| `blocked_prefixes` | Префиксы номеров, которые отклонять |
| `pref` | Разрешённые префиксы (пусто = разрешить все) |
| `cards` | Основной пул карт |
| `mastercards` | Резервный пул карт |
| `ui_elements` | XPath-селекторы для целевого приложения |
| `scrcpy_folder` | Путь к локальной установке scrcpy |
| `debug_mode` | Подробный лог |

### Как узнать серийники устройств

```bash
adb devices
```

Пример:
```
List of devices attached
RF8R90WBZLP     device
emulator-5554   device
```

Скопируй серийники в массив `devices`.

## Работа с картами

Карты лежат в `config.json` в двух массивах: `cards` (основные) и `mastercards` (резервные). Формат карты:

```json
{
  "number": "0000000000000000",
  "cvv": "000",
  "mmYY": "0000",
  "active": true,
  "use": 50
}
```

| Поле | Значение |
|---|---|
| `number` | Номер карты (16 цифр, без пробелов) |
| `cvv` | CVV / код подтверждения |
| `mmYY` | Срок действия в формате ММГГ (например, `0231` = февраль 2031) |
| `active` | `true` = доступна, `false` = заблокирована |
| `use` | Остаток использований до автоблокировки |

### Как используются карты

1. На каждом цикле воркер читает `config.json` и берёт случайную **активную** карту из `cards`.
2. Если активных в `cards` нет — переключается на `mastercards`.
3. После успешного платежа счётчик `use` уменьшается на 1.
4. Когда `use` доходит до `0` — карта помечается `active: false` и получает метку `blocked_until` (25 часов от текущего момента).
5. На каждом следующем цикле `check_card_timers()` сканирует конфиг и восстанавливает карты с истёкшим кулдауном.

### Как добавить карту

Добавь новый объект в массив `cards`:

```json
{
  "number": "1234567890123456",
  "cvv": "123",
  "mmYY": "1229",
  "active": true,
  "use": 50
}
```

Сохрани файл — перезапуск не нужен, оркестратор перечитывает конфиг на каждом цикле.

### Как удалить или отключить

- **Удалить:** убери объект из массива.
- **Временно отключить:** поставь `"active": false`.
- **Ручная разблокировка:** поставь `"active": true` и удали поле `blocked_until`, либо дождись истечения кулдауна.

## Запуск

```bash
python adb.py
```

Оркестратор:

1. Прочитает `config.json` и проверит `devices`.
2. Запустит `scrcpy` для каждого устройства (если `scrcpy_folder` задан).
3. Поднимет Flask-вебхук на `0.0.0.0:5000`.
4. Создаст по одному воркеру на устройство.
5. Проведёт каждое устройство по полному флоу с ретраями.

Остановка: `Ctrl+C`.

## Вебхук

Вебхук принимает SMS-коды от внешнего сервиса пересылки.

### Эндпоинт

```
POST http://<твой-хост>:5000/
Content-Type: application/x-www-form-urlencoded
```

### Параметры

| Поле | Обязательно | Назначение |
|---|---|---|
| `key` | Да | Текст SMS. 4-значный код извлекается регуляркой. |
| `device` | Нет | ADB-серийник целевого устройства. Если не указан — код рассылается всем устройствам. |

### Пример

```bash
curl -X POST http://localhost:5000/ \
  -d "key=Your Telegram code is 1234" \
  -d "device=RF8R90WBZLP"
```

### Способы форвардинга

Вебхук должен получать SMS с телефона, где стоит SIM-карта. Два частых варианта:

- **Одна сеть (LAN):** телефон запускает локальное приложение-форвардер, которое шлёт POST на локальный IP ПК. Работает только если телефон и ПК в одной Wi-Fi/LAN.
- **Через Telegram-бота:** телефон шлёт SMS боту, бот форвардит в оркестратор через интернет. Работает между сетями — рекомендовано для удалённых устройств.

## Частые проблемы

| Симптом | Причина | Решение |
|---|---|---|
| `adb: command not found` | ADB не в PATH | Установи platform-tools, добавь в PATH |
| `No devices in config!` | Массив `devices` пуст | Запусти `adb devices`, скопируй серийники |
| `Failed to connect: <serial>` | Отладка по USB выключена / кабель | Включи отладку, переподключи |
| `NO ACTIVE CARDS AT ALL!` | Все карты заблокированы / `use` = 0 | Добавь карты или дождись кулдауна |
| SMS-код не приходит | Вебхук недоступен | Проверь `curl`-ом, проверь форвардер |
| `uiautomator2` connect timeout | Устройство заблокировано / приложение упало | Разблокируй, очисти данные приложения |
| Окно scrcpy не открывается | Неверный `scrcpy_folder` | Проверь путь, запусти scrcpy отдельно |

---

## License

MIT — see [LICENSE](LICENSE).

---

## Disclaimer

This project is provided for educational and research purposes. Users are responsible for complying with all applicable laws, platform terms of service, and regulations in their jurisdiction. The authors assume no liability for misuse.
