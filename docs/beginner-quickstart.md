# Beginner Setup Guide

This guide walks you through getting OpenDQV running from scratch. No prior experience
with Git or Docker is assumed.

---

## Step 1: Install Python 3.11+

You need Python 3.11 or higher. To check if you have it:

- **Windows:** open the Start menu, search for "cmd", open it, and type `python --version`.
  If it says 3.11 or higher you're good. If not, download from
  [python.org/downloads](https://www.python.org/downloads/) — make sure to check
  **"Add Python to PATH"** during installation.
- **Mac:** open Spotlight (⌘ Space), search for "Terminal", open it, and type
  `python3 --version`. To install: [python.org/downloads](https://www.python.org/downloads/).
- **Linux:** type `python3 --version` in a terminal. To install:
  `sudo apt install python3.11` (Ubuntu/Debian).

---

## Step 2: Download OpenDQV

👉 Go to [github.com/OpenDQV/OpenDQV/releases/latest](https://github.com/OpenDQV/OpenDQV/releases/latest)

Scroll down to **Assets** and click **Source code (zip)**.

Unzip it somewhere you can find it (your Desktop is fine). You'll see a folder called
`OpenDQV-1.x.y` (where 1.x.y is the version number).

---

## Step 3: Install and run

**Windows** — open the unzipped folder, then double-click `install.bat`. A command window
will open and text will scroll — this is normal. First run takes 2–3 minutes.

**Mac** — open Spotlight (⌘ Space), search for Terminal, and type:

```bash
cd ~/Desktop/OpenDQV-1.x.y
bash install.sh
```

*(replace `Desktop/OpenDQV-1.x.y` with the actual folder path)*

**Linux:**

```bash
cd /path/to/OpenDQV-1.x.y
bash install.sh
```

When the install finishes, the onboarding wizard launches automatically. It will walk you
through creating your first contract and validating your first record.

---

## Step 4: Your first validation (after the wizard)

The onboarding wizard creates a starter contract for you. Once the server is running, you
can validate a record from the wizard's guided prompts, or try the Streamlit workbench in
your browser at [http://localhost:8501](http://localhost:8501).

---

## What next?

- [Quickstart guide](quickstart.md) — build your own contract in 15 minutes
- [README](../README.md) — full feature overview
- [FAQ](faq.md) — common questions
- [Troubleshooting](troubleshooting.md) — if something doesn't work
