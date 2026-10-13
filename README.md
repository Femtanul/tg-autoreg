# tg-autoreg
Android Device Farm Orchestrator — a multithreaded automation system for physical Android devices driven via ADB and uiautomator2, featuring event-driven webhook processing, a stateful resource pool with rotation and recovery, and a fault-tolerant UI automation flow.


# Android Device Farm Orchestrator

> A multithreaded automation system for physical Android devices driven via ADB and uiautomator2, featuring event-driven webhook processing, a stateful resource pool with rotation and recovery, and a fault-tolerant UI automation flow.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Overview

**Android Device Farm Orchestrator** drives multiple physical Android devices in parallel through a complex, multi-step UI flow. It coordinates shared, rate-limited resources (phone numbers, email addresses, payment cards) via a stateful pool with automatic rotation, blocking timers, and recovery.

The system was built to automate a repetitive account-registration workflow: launching a target app, injecting a purchased phone number, waiting for an SMS confirmation code delivered via webhook, confirming an email address, completing a payment flow with a rotating pool of bank cards, and linking the created account to an upstream API.

It is a reference implementation of a physical-device automation farm. The same architecture applies to QA device farms, mobile app testing at scale, and any orchestration task where physical devices cannot be replaced by emulators.

---

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
      │  (com.py)     │           │  (com.py)     │           │  (com.py)     │
      │   ADB + u2    │           │   ADB + u2    │           │   ADB + u2    │
      └───────┬───────┘           └───────┬───────┘           └───────┬───────┘
              │                           │                           │
              └───────────────────────────┼───────────────────────────┘
                                          ▼
                              ┌───────────────────────┐
                              │  Providers layer      │
                              │  (core.py)            │
                              │  SMS / email APIs     │
                              └───────────────────────┘
```

| Layer | File | Responsibility |
|---|---|---|
| **Orchestration** | `adb.py` | Worker threads, Flask webhook, UI flow phases, retry/fallback |
| **Device interaction** | `com.py` | ADB/uiautomator2 wrappers, UI polling, card pool logic |
| **Providers** | `core.py` | SMS and email service clients, activation lifecycle, prefix filtering |

---

## Notable engineering decisions

- **Per-device isolation of external events.** The webhook accepts an optional `device` parameter, so a single shared endpoint can serve a farm of devices without cross-talk.
- **Timestamp-based code matching.** The SMS code buffer stores the arrival timestamp alongside the code; consumers only accept codes that arrived *after* they started waiting. This prevents stale codes from a previous iteration from being mistakenly consumed.
- **Cooldown-based resource recovery.** Blocked cards are stored with an absolute unblock timestamp in the config. On every cycle, the runner scans the config and reactivates cards whose cooldown has expired — no external scheduler needed.
- **Config-as-shared-state.** `config.json` is the single source of truth for the resource pool, protected by an in-process `threading.Lock`.
- **Defensive UI automation.** Every device interaction goes through `swait` (element polling with configurable timeout) and `click_xpath` (click with retry). The flow tolerates slow devices, transient dialogs, and unexpected screens.

---

## Tech stack

| Concern | Technology |
|---|---|
| Language | Python 3.10+ |
| Device control | ADB, uiautomator2 |
| Concurrency | `threading`, `concurrent.futures.ThreadPoolExecutor` |
| Web layer | Flask (embedded webhook server) |
| HTTP client | `requests` |
| External services | SMSBower / HeroSMS, Gmail API, iCloud alias |

---

## Installation

### Prerequisites

- Python 3.10 or newer
- ADB installed and available in `PATH`
- One or more Android devices connected via USB with USB debugging enabled
- `scrcpy` (optional, for live device mirroring)

### Setup

```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>

python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

Copy the example config and fill in your own values:

```bash
cp config.example.json config.json
```

Then edit `config.json`:

- `devices` — list of ADB device serials
- `api_key`, `api_key_herosms` — SMS provider API keys
- `api_bitrix` — upstream API endpoint
- `owner_id` — target account identifier on the upstream API
- `cards` / `mastercards` — payment card pool
- `ui_elements` — XPath selectors for the target app (adjust if the app UI changes)
- `scrcpy_folder` — path to a local scrcpy installation

### Run

```bash
python adb.py
```

The orchestrator will:

1. Start the Flask webhook on `0.0.0.0:5000`
2. Spin up one worker thread per device in `devices`
3. Drive each device through the full flow, retrying on failure

---

## Use cases

This project is a reference implementation of a physical-device automation farm. The same architecture applies to any workflow that requires driving many Android devices through a multi-step UI flow with shared, rate-limited, recoverable resources:

- QA device farms
- Mobile app testing at scale
- Orchestration tasks where physical devices cannot be replaced by emulators

---

## License

MIT — see [LICENSE](LICENSE).

### Optional: scrcpy for device mirroring

To see live device screens while the orchestrator runs, install
[scrcpy](https://github.com/Genymobile/scrcpy/releases) and point
`scrcpy_folder` in `config.json` to the extracted folder.

The launcher `cmdd.bat` (Windows) iterates over connected ADB devices
and spawns a scrcpy window for each one.
