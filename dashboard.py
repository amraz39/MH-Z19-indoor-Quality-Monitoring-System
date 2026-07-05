# Blynk CO2 Dashboard for Windows PC
# v2.6 — Reset CO2 sensor + WiFi RSSI signal strength display in bottom bar (V14)

# This Python application connects to your local Blynk server running on Raspberry Pi 5 and displays:

# The application reads:
# * WiFi credentials
# * Blynk AUTH token
# from your existing `secrets.h` file.

# Project Structure
# project_folder/
# │
# ├── dashboard.py
# ├── secrets.h
# ├── abc_preferences.txt
# └── co2_log.csv

# IMPORTANT
# Update this line in the Python code:
# BLYNK_SERVER = "192.168.xx.xx" #<-- RPi5 (x9) and Rpi5 new (x4)
# if your Raspberry Pi IP changes.

# Port is automatically set to: 8080

"""
============================================================
 MH-Z19 ENGINEERING DASHBOARD
============================================================

 Windows Python dashboard for:

   - Temperature
   - Humidity
   - CO2 ppm
   - Engineering diagnostic MSG
   - Historical graphing
   - CSV historical logging
   - TXT engineering diagnostic logging

============================================================
 FEATURES
============================================================

 - Reads data from LOCAL Blynk server
 - Supports older local Blynk server versions
 - Stores all measurements into CSV
 - Stores detailed engineering logs into TXT
 - Displays live CO2 graph
 - Graph ranges:

      1 hour
      3 hours
      24 hours
      48 hours
      7 days

 - Connection diagnostics
 - Automatic reconnect
 - GUI dashboard
 - CO2 colour coding by air quality threshold
 - 1000 ppm threshold reference line on graph
 - Min / Max CO2 annotation for visible window
 - Connection status indicator
 - Last updated timestamp
 - Zero calibration trigger button
 - ABC enable/disable button with 3-state colour indicator
 - UART CO2 cross-check vs PWM with delta warning
 - ABC preference persistence — if user set ABC OFF, dashboard
   automatically re-enforces OFF if board reboots and resets to ON
 - Reset CO2 sensor button
 - All HTTP fetches run in background thread — UI never freezes

============================================================
 REQUIRED FILES
============================================================

 Place near this script:

   secrets.h

 containing:

   #define WIFI_SSID   "yourssid"
   #define WIFI_PASS   "yourpass"
   #define BLYNK_AUTH  "yourtoken"
   #define BLYNK_SERVER "192.xxx.xx.xx"

============================================================
 REQUIRED PYTHON PACKAGES
============================================================

 pip install requests pandas matplotlib customtkinter

============================================================
 BLYNK VIRTUAL PINS
============================================================

 V10 = temperature
 V11 = humidity
 V12 = CO2 ppm  (PWM reading)
 V3  = engineering message

 V13 = CO2 ppm  (UART reading — requires INO support)
 V20 = zero calibration trigger (write 1 — requires INO support)
 V21 = ABC state  (read: 1=enabled 0=disabled;
                   write: 1=enable 0=disable — requires INO support)
 V22 = reset CO2 sensor (write 1 — requires INO support)

============================================================
 INO REQUIREMENTS FOR NEW FEATURES
============================================================

 The following BLYNK_WRITE handlers must be added to the INO:

   BLYNK_WRITE(V20)  — on value 1: trigger zero calibration
   BLYNK_WRITE(V21)  — on value 1: enable ABC
                        on value 0: disable ABC

 The following must be written each cycle from the INO:

   Blynk.virtualWrite(13, uartPPM);   // UART CO2 reading
   Blynk.virtualWrite(21, abcEnabled ? 1 : 0);  // ABC state

============================================================
 CO2 AIR QUALITY THRESHOLDS
============================================================

 < 800 ppm   GOOD        green
 800-1000    MODERATE    yellow
 1000-1500   POOR        orange
 > 1500 ppm  BAD         red

 Reference line drawn at 1000 ppm on graph.

============================================================
"""

import os
import re
import queue
import threading
import requests
import csv
import pandas as pd
import customtkinter as ctk

from datetime import datetime, timedelta
from tkinter import messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np

# ============================================================
# READ IP ADDRESS OF BLYNK SERVER FROM secrets.h
# ============================================================

def load_IP_server():
    """
    Extract BLYNK_SERVER from secrets.h
    """

    if not os.path.exists("secrets.h"):
        raise FileNotFoundError("secrets.h not found")

    with open("secrets.h", "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'BLYNK_SERVER\s+"([^"]+)"', content)

    if not match:
        raise ValueError("BLYNK_SERVER not found in secrets.h")

    return match.group(1)

BLYNK_SERVER = load_IP_server()

# ============================================================
# CONFIGURATION
# ============================================================

#BLYNK_SERVER = "192.xxx.xx.xx"
BLYNK_PORT   = 8084

UPDATE_INTERVAL_MS = 5000

CSV_LOG_FILE = "co2_log.csv"
TXT_LOG_FILE = "engineering_log.txt"

# ============================================================
# LOG RETENTION POLICY
# ============================================================
#
# Both co2_log.csv and engineering_log.txt are trimmed once
# per hour to keep only the last LOG_RETENTION_DAYS days of
# data. This matches the maximum graph display window (7 days)
# and prevents the files from growing indefinitely over long
# unattended operation.
#
# The trim is done in-memory (read → filter → write) so it
# is safe to call while the dashboard is running. The hourly
# schedule means at most one extra day of data can accumulate
# between trims.
#
# ============================================================

LOG_RETENTION_DAYS = 7          # days of data to keep in log files
LOG_TRIM_INTERVAL_MS = 3600000  # trim once per hour (ms)

# ============================================================
# ABC PREFERENCE FILE
# ============================================================
#
# Stores the user's last explicit ABC choice on disk so it
# survives dashboard restarts as well as board reboots.
#
# File contains a single line: "enabled" or "disabled".
# If the file does not exist no preference is enforced.
#
# ============================================================

ABC_PREFERENCE_FILE = "abc_preference.txt"

# ============================================================
# CO2 AIR QUALITY COLOUR THRESHOLDS
# ============================================================
#
# Thresholds based on standard indoor air quality guidelines.
# The CO2 value label colour updates live every cycle.
#
# ============================================================

CO2_GOOD     = 800    # below this  -> green
CO2_MODERATE = 1000   # below this  -> yellow
CO2_POOR     = 1500   # below this  -> orange
                      # above 1500  -> red

CO2_COLOR_GOOD     = "#00cc44"
CO2_COLOR_MODERATE = "#ffd700"
CO2_COLOR_POOR     = "#ff8800"
CO2_COLOR_BAD      = "#ff2222"
CO2_COLOR_DEFAULT  = "white"

# ============================================================
# CALIBRATION VIRTUAL PIN ASSIGNMENTS
# ============================================================
#
# These virtual pins require matching BLYNK_WRITE handlers
# and Blynk.virtualWrite() calls in the INO sketch.
# See the INO REQUIREMENTS section in the header docstring.
#
# ============================================================

V_CO2_UART  = 13   # UART CO2 reading sent each cycle by INO
V_WIFI_RSSI = 14   # WiFi RSSI in dBm         [NEW v6.12]
V_ZERO_CAL     = 20   # write 1 to trigger zero calibration
V_ABC_STATE    = 21   # read 1=enabled/0=disabled; write to toggle
V_SENSOR_RESET = 22   # write 1 to trigger manual sensor soft reset  [NEW v6.14]

# ============================================================
# UART CROSS-CHECK THRESHOLD
# ============================================================
#
# Minimum ppm difference between PWM and UART readings before
# a warning is shown. Small differences (< threshold) are
# considered normal sensor noise and displayed quietly.
# Larger differences indicate a calibration problem.
#
# ============================================================

UART_DIFF_THRESHOLD = 50   # ppm

# ============================================================
# READ AUTH TOKEN FROM secrets.h
# ============================================================


def load_auth_token():
    """
    Extract BLYNK_AUTH from secrets.h
    """

    if not os.path.exists("secrets.h"):
        raise FileNotFoundError("secrets.h not found")

    with open("secrets.h", "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'BLYNK_AUTH\s+"([^"]+)"', content)

    if not match:
        raise ValueError("BLYNK_AUTH not found in secrets.h")

    return match.group(1)


AUTH_TOKEN = load_auth_token()

# ============================================================
# ENGINEERING LOGGING
# ============================================================


def log_txt(message):
    """
    Detailed engineering TXT log.

    Used for:
      - startup diagnostics
      - connection issues
      - reconnect attempts
      - sensor fetch problems
      - parsing problems

    Newest entries are written at the TOP of the file so the
    most recent data is always visible without scrolling.
    Existing content is read, the new line is prepended, and
    the whole file is rewritten each call.
    """

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    line = f"[{ts}] {message}"

    print(line)

    # Read existing content (empty string if file does not exist yet)
    if os.path.exists(TXT_LOG_FILE):
        with open(TXT_LOG_FILE, "r", encoding="utf-8") as f:
            existing = f.read()
    else:
        existing = ""

    # Prepend new line so newest entry is always at the top
    with open(TXT_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(line + "\n" + existing)


# ============================================================
# CREATE CSV FILE IF NEEDED
# ============================================================

if not os.path.exists(CSV_LOG_FILE):
    df = pd.DataFrame(columns=[
        "timestamp",
        "temperature",
        "humidity",
        "co2",
        "message"
    ])

    df.to_csv(CSV_LOG_FILE, index=False)

    log_txt("Created CSV log file")

# ============================================================
# BLYNK API
# ============================================================


def read_virtual_pin(pin):
    """
    Read value from local Blynk server.

    Older Blynk local servers typically use:

      /AUTH_TOKEN/get/Vx
    """

    url = (
        f"http://{BLYNK_SERVER}:{BLYNK_PORT}/"
        f"{AUTH_TOKEN}/get/V{pin}"
    )

    try:
        response = requests.get(url, timeout=5)

        log_txt(f"HTTP GET {url} -> {response.status_code}")

        if response.status_code != 200:
            return None

        text = response.text.strip()

        log_txt(f"PIN V{pin} RAW RESPONSE: {text}")

        # Old Blynk servers return values like:
        # ["23.5"]

        cleaned = text.replace('["', '').replace('"]', '')

        return cleaned

    except Exception as e:
        log_txt(f"PIN V{pin} ERROR: {e}")
        return None


# ============================================================
# BLYNK API — WRITE VIRTUAL PIN
# ============================================================
#
# Sends a value to a virtual pin on the local Blynk server.
# Used for:
#   - zero calibration trigger  (V20)
#   - ABC enable / disable      (V21)
#
# Older Blynk local servers use:
#
#   /AUTH_TOKEN/update/Vx?value=y
#
# ============================================================

def write_virtual_pin(pin, value):
    """
    Write a value to a virtual pin on the local Blynk server.
    Returns True on success, False on failure.
    """

    url = (
        f"http://{BLYNK_SERVER}:{BLYNK_PORT}/"
        f"{AUTH_TOKEN}/update/V{pin}"
        f"?value={value}"
    )

    try:
        response = requests.get(url, timeout=5)

        log_txt(
            f"HTTP GET {url} -> {response.status_code}"
        )

        return response.status_code == 200

    except Exception as e:
        log_txt(f"PIN V{pin} WRITE ERROR: {e}")
        return False

# ============================================================
#
# Downloads readable historical graph data from the local
# Blynk server and converts timestamps into human-readable
# date/time format.
#
# Older Blynk servers return history lines like:
#
#   value,timestamp,flag
#
# Example:
#
#   760.4,1777566480000,0
#
# Which means:
#
#   CO2 ppm = 760.4
#   timestamp = unix milliseconds
#
# ============================================================

def trim_log_files():
    """
    Remove entries older than LOG_RETENTION_DAYS from both
    co2_log.csv and engineering_log.txt.

    Called once per hour via app.after() so that neither file
    grows without bound during long unattended operation.

    co2_log.csv:
        Parsed with pandas. Rows whose 'timestamp' column
        is older than the retention cutoff are dropped and
        the file is rewritten. If the file is absent or the
        timestamp column cannot be parsed, the function logs
        the issue and skips that file without raising.

    engineering_log.txt:
        Each line starts with a [YYYY-MM-DD HH:MM:SS] prefix
        (written by log_txt). Lines whose embedded timestamp
        is older than the cutoff are dropped and the file is
        rewritten. Lines that cannot be parsed (e.g. blank
        lines or legacy format) are kept to avoid silent data
        loss.

    Both rewrites are atomic within Python: the filtered
    content is assembled in memory and written in one call,
    so a crash mid-write leaves the previous file intact
    (the OS write may be partial, but no data is deleted
    before the new content is ready).
    """

    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)

    log_txt(f"[TRIM] Running log trim — keeping data after {cutoff.strftime('%Y-%m-%d %H:%M:%S')}")

    # ====================================================
    # TRIM co2_log.csv
    # ====================================================

    try:

        if os.path.exists(CSV_LOG_FILE):

            df = pd.read_csv(CSV_LOG_FILE)

            if 'timestamp' in df.columns:

                df['timestamp'] = pd.to_datetime(
                    df['timestamp'],
                    errors='coerce'
                )

                before = len(df)

                df = df[df['timestamp'] >= cutoff]

                after = len(df)

                df.to_csv(CSV_LOG_FILE, index=False)

                log_txt(
                    f"[TRIM] co2_log.csv: "
                    f"removed {before - after} rows, "
                    f"{after} remaining"
                )

            else:

                log_txt("[TRIM] co2_log.csv: no 'timestamp' column — skipped")

    except Exception as e:

        log_txt(f"[TRIM] co2_log.csv error: {e}")

    # ====================================================
    # TRIM engineering_log.txt
    # ====================================================

    try:

        if os.path.exists(TXT_LOG_FILE):

            with open(TXT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

            kept   = []
            removed = 0

            for line in lines:

                # Each line written by log_txt starts with:
                # [YYYY-MM-DD HH:MM:SS]
                stripped = line.strip()

                if stripped.startswith("[") and len(stripped) > 21:

                    try:

                        ts = datetime.strptime(
                            stripped[1:20],
                            "%Y-%m-%d %H:%M:%S"
                        )

                        if ts >= cutoff:
                            kept.append(line)
                        else:
                            removed += 1

                    except ValueError:

                        # Line has [ prefix but unparseable timestamp — keep it
                        kept.append(line)

                else:

                    # Blank line or non-timestamped line — keep it
                    kept.append(line)

            with open(TXT_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(kept)

            log_txt(
                f"[TRIM] engineering_log.txt: "
                f"removed {removed} lines, "
                f"{len(kept)} remaining"
            )

    except Exception as e:

        log_txt(f"[TRIM] engineering_log.txt error: {e}")


def _schedule_trim():
    """
    Run trim_log_files() then reschedule itself for the next
    hour. Using app.after() keeps the trim on the main thread
    so no locking is needed around file I/O that other main-
    thread callbacks also perform.
    """

    trim_log_files()

    app.after(LOG_TRIM_INTERVAL_MS, _schedule_trim)


def download_blynk_history():
    """
    Download historical graph data from local Blynk server.

    Handles:
      - gzip-compressed responses
      - plain text responses
      - converts timestamps to readable date/time
      - exports readable TXT + CSV files

    V3 (engineering/diagnostic message) history is downloaded first
    from the new _text.txt endpoint added to the server. Its lines
    use the format:
        message,unix_ms_timestamp
    (message first, then timestamp — opposite of numeric pins).

    A timestamp-keyed lookup dict is built from V3 so that each CO2
    record can be annotated with the nearest diagnostic message within
    MSG_MATCH_TOLERANCE_S seconds. The message column appears in both
    the CO2 TXT file and the global CSV export. Temperature and
    humidity exports are not annotated (not relevant to those sensors).
    """

    import gzip

    # ====================================================
    # MESSAGE MATCH TOLERANCE
    # ====================================================
    #
    # Maximum gap in seconds between a CO2 record timestamp
    # and a V3 message timestamp for them to be considered
    # a match. Sensor cycles run every 5 seconds so 30 s
    # is generous without risking wrong-cycle matches.
    #
    # ====================================================

    MSG_MATCH_TOLERANCE_S = 30

    def _fetch_raw(pin):
        """
        Download and decompress history for a single pin.
        Returns raw text or None on failure.
        """
        url = (
            f"http://{BLYNK_SERVER}:{BLYNK_PORT}/"
            f"{AUTH_TOKEN}/data/V{pin}"
        )
        log_txt(f"Downloading history from: {url}")
        try:
            response = requests.get(url, timeout=30)
            log_txt(f"HTTP STATUS V{pin}: {response.status_code}")
            if response.status_code != 200:
                log_txt(f"Failed to download V{pin}")
                return None
            try:
                text = gzip.decompress(
                    response.content
                ).decode("utf-8", errors="ignore")
                log_txt(f"V{pin} decompressed using gzip")
            except Exception:
                text = response.text
                log_txt(f"V{pin} was plain text")
            log_txt(f"FIRST 500 CHARS:\n{text[:500]}")
            return text
        except Exception as e:
            log_txt(f"V{pin} download error: {e}")
            return None

    try:

        log_txt("Starting historical data download")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # ====================================================
        # STEP 1 — DOWNLOAD V3 DIAGNOSTIC MESSAGE HISTORY
        # ====================================================
        #
        # V3 text history file format (from updated server):
        #   message,unix_ms_timestamp
        #
        # Note: message is FIRST, timestamp is SECOND.
        # This is the opposite of numeric pins where the
        # value comes first. The server stores it this way
        # because the value is the text message itself.
        #
        # Build a lookup dict: { unix_ms (int) -> message (str) }
        # ====================================================

        msg_history = {}    # { unix_ms: message_string }

        v3_text = _fetch_raw(3)

        if v3_text:
            for line in v3_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Split on LAST comma to handle messages
                # that may contain commas internally
                idx = line.rfind(",")
                if idx == -1:
                    continue
                try:
                    msg_val = line[:idx].strip()
                    ts_ms   = int(line[idx + 1:].strip())
                    msg_history[ts_ms] = msg_val
                except Exception as e:
                    log_txt(f"V3 parse error: {line} -> {e}")

            log_txt(
                f"V3 message history loaded: "
                f"{len(msg_history)} entries"
            )
        else:
            log_txt(
                "V3 message history unavailable — "
                "message column will be empty"
            )

        def _lookup_message(ts_ms):
            """
            Find the closest V3 message to ts_ms within
            MSG_MATCH_TOLERANCE_S. Returns message string
            or empty string if no match found.
            """
            if not msg_history:
                return ""
            tolerance_ms = MSG_MATCH_TOLERANCE_S * 1000
            best_key  = None
            best_diff = float("inf")
            for k in msg_history:
                diff = abs(k - ts_ms)
                if diff < best_diff:
                    best_diff = diff
                    best_key  = k
            if best_diff <= tolerance_ms:
                return msg_history[best_key]
            return ""

        # ====================================================
        # STEP 2 — DOWNLOAD NUMERIC SENSOR HISTORY
        # ====================================================

        pins = {
            "temperature": 10,
            "humidity": 11,
            "co2": 12
        }

        # ====================================================
        # GLOBAL CSV EXPORT
        # ====================================================
        #
        # CO2 rows include a "message" column sourced from V3.
        # Temperature and humidity rows leave message blank.
        #
        # ====================================================

        csv_file = f"blynk_history_export_{timestamp}.csv"

        rows_written = 0

        with open(csv_file, "w", newline="", encoding="utf-8") as csvf:

            writer = csv.writer(csvf)

            writer.writerow([
                "sensor",
                "date_time",
                "value",
                "message"
            ])

            # ====================================================
            # DOWNLOAD EACH SENSOR
            # ====================================================

            for sensor_name, pin in pins.items():

                try:

                    text = _fetch_raw(pin)

                    if text is None:
                        continue

                    # ====================================================
                    # TXT FILE
                    # ====================================================

                    txt_file = (
                        f"blynk_history_"
                        f"{sensor_name}_"
                        f"{timestamp}.txt"
                    )

                    with open(
                        txt_file,
                        "w",
                        encoding="utf-8"
                    ) as txtf:

                        if sensor_name == "co2":
                            txtf.write(
                                f"===== CO2 HISTORY =====\n\n"
                                f"{'Date/Time':<22}  "
                                f"{'CO2 ppm':>10}  "
                                f"Diagnostic Message\n"
                                f"{'-'*22}  "
                                f"{'-'*10}  "
                                f"{'-'*40}\n"
                            )
                        else:
                            txtf.write(
                                f"===== "
                                f"{sensor_name.upper()} HISTORY "
                                f"=====\n\n"
                            )

                        # ====================================================
                        # PARSE HISTORY LINES
                        # ====================================================
                        #
                        # Numeric pin format:
                        #   value,timestamp_ms,flag
                        #
                        # ====================================================

                        lines = text.splitlines()

                        log_txt(
                            f"{sensor_name} returned "
                            f"{len(lines)} lines"
                        )

                        sensor_rows = 0

                        for line in lines:

                            line = line.strip()

                            if not line:
                                continue

                            parts = line.split(",")

                            # Expected:
                            # value,timestamp,flag

                            if len(parts) < 2:

                                log_txt(
                                    f"Skipping malformed line: "
                                    f"{line}"
                                )

                                continue

                            try:

                                value = float(parts[0])

                                ts_ms = int(parts[1])

                                # ============================================
                                # CONVERT UNIX MS -> READABLE DATETIME
                                # ============================================

                                dt = datetime.fromtimestamp(
                                    ts_ms / 1000
                                )

                                dt_str = dt.strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )

                                # ============================================
                                # HUMAN READABLE TXT LINE
                                # ============================================

                                if sensor_name == "co2":
                                    # Look up nearest V3 diagnostic message
                                    diag_msg = _lookup_message(ts_ms)
                                    readable_line = (
                                        f"{dt_str}  "
                                        f"{value:>10.2f}  "
                                        f"{diag_msg}"
                                    )
                                else:
                                    diag_msg = ""
                                    readable_line = (
                                        f"{dt_str}    "
                                        f"{value:.2f}"
                                    )

                                txtf.write(
                                    readable_line + "\n"
                                )

                                # ============================================
                                # CSV EXPORT
                                # ============================================

                                writer.writerow([
                                    sensor_name,
                                    dt_str,
                                    f"{value:.2f}",
                                    diag_msg
                                ])

                                rows_written += 1
                                sensor_rows += 1

                            except Exception as e:

                                log_txt(
                                    f"Parse error: "
                                    f"{line} -> {e}"
                                )

                        log_txt(
                            f"{sensor_name}: "
                            f"{sensor_rows} rows exported"
                        )

                    log_txt(
                        f"Saved history TXT: {txt_file}"
                    )

                except Exception as e:

                    log_txt(
                        f"Sensor download error "
                        f"{sensor_name}: {e}"
                    )

        log_txt(
            f"History export complete. "
            f"Rows written: {rows_written}"
        )

        label_click_info.configure(
            text=(
                f"History exported successfully: "
                f"{csv_file}"
            ),
            text_color="#00ff99"
        )

    except Exception as e:

        log_txt(
            f"HISTORY DOWNLOAD ERROR: {e}"
        )

        label_click_info.configure(
            text=f"History download failed: {e}",
            text_color="#ff4444"
        )

# ============================================================
# GRAPH CLICK STATE
# ============================================================
#
# Holds the currently displayed DataFrame (filtered to the
# selected history window) so the click handler can look up
# temperature, humidity and CO2 for any clicked timestamp.
# Updated every graph redraw cycle.
#
# ============================================================

current_df = None

# ============================================================
# CO2 COLOUR HELPER
# ============================================================
#
# Returns the appropriate label colour string for a given
# CO2 reading based on the configured threshold levels.
#
# ============================================================

def co2_color(ppm):
    """
    Return display colour for a CO2 ppm value.
    """

    if ppm < CO2_GOOD:
        return CO2_COLOR_GOOD
    elif ppm < CO2_MODERATE:
        return CO2_COLOR_MODERATE
    elif ppm < CO2_POOR:
        return CO2_COLOR_POOR
    else:
        return CO2_COLOR_BAD


# ============================================================
# ABC STATE TRACKING
# ============================================================
#
# Tracks the current ABC (Automatic Baseline Calibration)
# state as reported by the INO via V21.
#
# Values:
#   "unknown"   — not yet queried, or V21 returned None
#   "enabled"   — V21 returned "1"
#   "disabled"  — V21 returned "0"
#
# The ABC button colour reflects this state:
#   gray   — unknown
#   green  — enabled
#   red    — disabled
#
# ============================================================

abc_state = "unknown"

# Counter used inside update_dashboard() to refresh the ABC
# state periodically without adding a separate timer.
# Refreshes every 12 cycles (= every ~60 seconds at 5s interval).
#
# Set to ABC_REFRESH_EVERY at startup so UART + ABC state is
# checked immediately on the very first fetch cycle.

abc_refresh_counter = 0
ABC_REFRESH_EVERY   = 12

# ============================================================
# UART AVAILABILITY FLAG
# ============================================================
#
# Set to True when V13 returns a valid numeric CO2 reading,
# confirming that:
#   - the two UART wires are physically connected
#   - the INO is writing UART CO2 data to V13
#
# While False:
#   - Zero Calibration button is gray and disabled
#   - ABC button is gray and disabled
#   - UART cross-check label shows "UART: not wired"
#
# Checked at startup and every ~60 seconds.
# Automatically enables buttons the moment UART is detected.
#
# ============================================================

uart_available = False

def _apply_abc_button_style():
    """
    Update button_abc colour and label to match abc_state.
    Safe to call at any time after button_abc is created.
    """

    global abc_state

    if abc_state == "enabled":
        button_abc.configure(
            text="ABC: ON",
            fg_color="#1a6b2e",
            hover_color="#2a8b3e"
        )
    elif abc_state == "disabled":
        button_abc.configure(
            text="ABC: OFF",
            fg_color="#6b1a1a",
            hover_color="#8b2a2a"
        )
    else:
        button_abc.configure(
            text="ABC: ?",
            fg_color="gray40",
            hover_color="gray50"
        )


# ============================================================
# ABC USER PREFERENCE
# ============================================================
#
# Tracks what the user explicitly chose last time they clicked
# the ABC button. Independent of what the board currently
# reports via V21.
#
# Values:
#   None         — user has never explicitly set it this session
#                  (no enforcement; board state is accepted)
#   "enabled"    — user explicitly chose ON
#   "disabled"   — user explicitly chose OFF
#
# Loaded from ABC_PREFERENCE_FILE at startup so the preference
# survives dashboard restarts.
#
# When the board reboots and V21 flips back to 1 (ON), but the
# stored preference is "disabled", refresh_abc_state() detects
# the mismatch and automatically writes 0 to V21 to restore OFF.
#
# ============================================================

abc_user_preference = None    # populated by load_abc_preference() at startup

# Timestamp of the last successful ABC preference enforcement.
# Used as a cooldown so repeated writes are not sent on every
# cycle while waiting for the board to acknowledge the write.
# A value of 0.0 means no enforcement has been sent yet.
_abc_last_enforce_time = 0.0    # seconds since epoch (time.time())

# Minimum interval between enforcement writes in seconds.
# 120 s gives the board two full 60-second refresh cycles to
# acknowledge the write before another attempt is made.
ABC_ENFORCE_COOLDOWN_S = 120


def load_abc_preference():
    """
    Load the last user ABC preference from disk.
    Returns "enabled", "disabled", or None if file absent/invalid.
    """

    if not os.path.exists(ABC_PREFERENCE_FILE):
        return None

    try:
        with open(ABC_PREFERENCE_FILE, "r", encoding="utf-8") as f:
            val = f.read().strip()

        if val in ("enabled", "disabled"):
            log_txt(f"ABC preference loaded from file: {val}")
            return val

    except Exception as e:
        log_txt(f"ABC preference load error: {e}")

    return None


def save_abc_preference(preference):
    """
    Save the user's ABC preference to disk.
    Called every time the user explicitly clicks the ABC button.

    preference: "enabled" or "disabled"
    """

    try:
        with open(ABC_PREFERENCE_FILE, "w", encoding="utf-8") as f:
            f.write(preference + "\n")

        log_txt(f"ABC preference saved to file: {preference}")

    except Exception as e:
        log_txt(f"ABC preference save error: {e}")


def refresh_abc_state():
    """
    Query V21 from the Blynk server and update abc_state
    and the button colour accordingly.

    If the board reports a state that conflicts with the
    user's stored preference (e.g. board rebooted and reset
    ABC back to ON, but user had set it to OFF), this function
    automatically re-enforces the preference by writing back
    to V21 and logs the enforcement event.
    """

    global abc_state, abc_user_preference

    val = read_virtual_pin(V_ABC_STATE)

    if val == "1":
        abc_state = "enabled"
        log_txt("ABC state: ENABLED")
    elif val == "0":
        abc_state = "disabled"
        log_txt("ABC state: DISABLED")
    else:
        abc_state = "unknown"
        log_txt(f"ABC state: UNKNOWN (raw={val})")

    # ============================================================
    # ABC PREFERENCE ENFORCEMENT
    # ============================================================
    #
    # If the board reports a state that contradicts the user's
    # last explicit choice, the dashboard re-enforces the
    # preference automatically.
    #
    # Typical trigger:
    #   User set ABC OFF → board rebooted → board reset ABC to ON
    #   → V21 now reads "1" → mismatch detected → write 0 to V21
    #
    # Only enforces when:
    #   - preference is explicitly set ("enabled" or "disabled")
    #   - board state is known (not "unknown")
    #   - board state differs from preference
    #
    # ============================================================

    # ============================================================
    # ABC PREFERENCE ENFORCEMENT WITH COOLDOWN
    # ============================================================
    #
    # A cooldown (ABC_ENFORCE_COOLDOWN_S = 120 s) prevents
    # repeated write attempts while the board is still processing
    # the previous enforcement write. Without the cooldown, each
    # refresh cycle re-reads a stale V21 value and sends another
    # write before the board has acknowledged the first one.
    #
    # The label_click_info is updated every call:
    #   - Normal (board matches preference): green confirmation
    #   - Enforcement sent: yellow warning with action taken
    #   - Enforcement failed: red error
    #   - No preference set: grey neutral text
    # ============================================================

    import time as _time   # local alias — time already imported globally

    if (
        abc_user_preference is not None and
        abc_state != "unknown" and
        abc_state != abc_user_preference
    ):
        now = _time.time()

        if (now - _abc_last_enforce_time) >= ABC_ENFORCE_COOLDOWN_S:

            enforce_value = 0 if abc_user_preference == "disabled" else 1

            log_txt(
                f"ABC PREFERENCE MISMATCH: board={abc_state} "
                f"preference={abc_user_preference} — "
                f"re-enforcing preference (writing {enforce_value} to V{V_ABC_STATE})"
            )

            ok = write_virtual_pin(V_ABC_STATE, enforce_value)

            if ok:
                # Update local state to match what we just enforced
                abc_state = abc_user_preference

                _abc_last_enforce_time = now

                log_txt(
                    f"ABC preference re-enforced successfully: {abc_user_preference}"
                )

                # Show enforcement message so user sees what happened
                label_click_info.configure(
                    text=(
                        f"⚠  ABC re-enforced to {abc_user_preference.upper()} "
                        f"after board reset"
                    ),
                    text_color="#ffd700"
                )
            else:
                log_txt("ABC preference re-enforcement FAILED — will retry next cycle")

                label_click_info.configure(
                    text="⚠  ABC re-enforcement FAILED — check server connection",
                    text_color="#ff4444"
                )
        else:
            # Still within cooldown — show pending message
            remaining = int(ABC_ENFORCE_COOLDOWN_S - (_time.time() - _abc_last_enforce_time))
            log_txt(
                f"ABC enforcement cooldown active — {remaining}s remaining"
            )

            label_click_info.configure(
                text=(
                    f"⚠  ABC re-enforcement pending "
                    f"({abc_user_preference.upper()}) — "
                    f"waiting for board confirmation..."
                ),
                text_color="#ffd700"
            )

    elif abc_user_preference is not None and abc_state != "unknown":
        # Board state matches preference — show confirmation
        color = "#00cc44" if abc_state == "disabled" else "#94a3b8"
        label_click_info.configure(
            text=f"✓  ABC is {abc_state.upper()} (matches preference)",
            text_color=color
        )

    else:
        # No preference set or state unknown — show neutral text
        label_click_info.configure(
            text=f"ABC state: {abc_state.upper() if abc_state else '—'}",
            text_color="#475569"
        )

    _apply_abc_button_style()


def toggle_abc():
    """
    Toggle ABC state on user button click.

    If currently enabled  → write 0 to V21 (disable).
    If currently disabled → write 1 to V21 (enable).
    If unknown            → write 1 to V21 (enable as safe default).

    Saves the new preference to disk so it survives both
    dashboard restarts and board reboots.

    Updates button colour immediately on successful write,
    then re-reads V21 to confirm.
    """

    global abc_state, abc_user_preference

    if abc_state == "enabled":
        new_value  = 0
        action     = "DISABLE"
        preference = "disabled"
    else:
        new_value  = 1
        action     = "ENABLE"
        preference = "enabled"

    log_txt(f"ABC toggle: sending {action} (value={new_value})")

    ok = write_virtual_pin(V_ABC_STATE, new_value)

    if ok:
        # Save user preference to disk before refreshing state
        abc_user_preference = preference
        save_abc_preference(preference)

        log_txt(f"ABC toggle: write succeeded, refreshing state")
        refresh_abc_state()
    else:
        log_txt("ABC toggle: write FAILED")
        label_click_info.configure(
            text="ABC toggle failed — check server connection",
            text_color="#ff4444"
        )


def trigger_sensor_reset():
    """
    Trigger a manual MH-Z19 soft reset via V22.

    Shows a confirmation dialog before sending to prevent
    accidental resets during normal operation.

    The soft reset command restarts the sensor internal MCU
    without cutting power. The sensor goes silent for ~3-5
    seconds then resumes normal output. All firmware state
    on the ESP8266 is also reset (smoother, median buffer,
    stuck counter, fault latch) so the protection layers
    start fresh after recovery.

    Use when:
      - Sensor reads high but room air is clearly clean
      - Offset fault was not caught automatically by LAYER 12
      - Sensor behaves erratically and a power cycle is not
        possible (remote / unattended installation)

    Requires matching BLYNK_WRITE(V22) handler in the INO
    (added in v6.14).
    """

    confirmed = messagebox.askyesno(
        title="Manual Sensor Reset",
        message=(
            "Are you sure you want to send a soft reset to the CO2 sensor?\n\n"
            "This will:\n"
            "  \u2022 Restart the sensor internal MCU (no power cut)\n"
            "  \u2022 Reset all firmware protection state\n"
            "  \u2022 Cause ~3-5 seconds of silence then normal resume\n\n"
            "Use only when sensor reads incorrectly and\n"
            "automatic recovery has not triggered."
        )
    )

    if not confirmed:
        log_txt("Manual sensor reset cancelled by user")
        return

    log_txt("Manual sensor reset: sending trigger to V22")

    ok = write_virtual_pin(V_SENSOR_RESET, 1)

    if ok:
        log_txt("Manual sensor reset: trigger sent successfully")
        label_click_info.configure(
            text="\u27f3  Sensor soft reset sent \u2014 sensor will resume in ~5 s",
            text_color="#22d3ee"
        )
    else:
        log_txt("Manual sensor reset: write FAILED")
        label_click_info.configure(
            text="Manual sensor reset failed \u2014 check server connection",
            text_color="#ff4444"
        )


def trigger_zero_calibration():
    """
    Trigger MH-Z19 zero calibration via V20.

    Shows a confirmation dialog before sending.
    Zero calibration requires the sensor to be in fresh
    outdoor air (~400 ppm) for at least 20 minutes first.
    """

    confirmed = messagebox.askyesno(
        title="Zero Calibration",
        message=(
            "Are you sure you want to trigger ZERO CALIBRATION?\n\n"
            "Requirements before proceeding:\n"
            "  • Sensor must be in fresh outdoor air\n"
            "  • At least 20 minutes at ~400 ppm\n\n"
            "Calibrating indoors will corrupt the sensor baseline."
        )
    )

    if not confirmed:
        log_txt("Zero calibration cancelled by user")
        return

    log_txt("Zero calibration: sending trigger to V20")

    ok = write_virtual_pin(V_ZERO_CAL, 1)

    if ok:
        log_txt("Zero calibration: trigger sent successfully")
        label_click_info.configure(
            text="✓ Zero calibration triggered — sensor is recalibrating",
            text_color="#00cc44"
        )
    else:
        log_txt("Zero calibration: write FAILED")
        label_click_info.configure(
            text="Zero calibration failed — check server connection",
            text_color="#ff4444"
        )


# ============================================================
# UART BUTTON ENABLE / DISABLE HELPERS
# ============================================================
#
# Called when UART availability changes.
# These are the only places that change button state
# (normal / disabled) so the logic stays in one place.
#
# ============================================================

def _enable_uart_buttons():
    """
    Activate Zero Calibration and ABC buttons.
    Called when V13 first returns a valid reading.
    Also triggers an immediate ABC state refresh.
    """

    log_txt("UART detected — enabling calibration buttons")

    button_zero_cal.configure(
        fg_color="#555500",
        hover_color="#888800",
        state="normal"
    )

    # ABC button state is set by refresh_abc_state()
    button_abc.configure(state="normal")

    # Enable manual sensor reset button (requires UART)
    button_sensor_reset.configure(
        fg_color="#5f1e1e",
        hover_color="#ff4444",
        state="normal"
    )

    # Immediately read actual ABC state from INO
    refresh_abc_state()


def _disable_uart_buttons():
    """
    Gray out and disable Zero Calibration and ABC buttons.
    Called at startup and if UART signal is lost.
    """

    log_txt("UART unavailable — disabling calibration and reset buttons")

    button_sensor_reset.configure(
        fg_color="gray40",
        hover_color="gray50",
        state="disabled"
    )

    button_zero_cal.configure(
        fg_color="gray40",
        hover_color="gray50",
        state="disabled"
    )

    button_abc.configure(
        text="ABC: ?",
        fg_color="gray40",
        hover_color="gray50",
        state="disabled"
    )

    label_co2_uart.configure(
        text="UART: not wired",
        text_color="gray"
    )


# ============================================================
# UART AVAILABILITY CHECK
# ============================================================
#
# Reads V13 and checks whether the INO is writing valid
# UART CO2 data. Enables or disables buttons accordingly.
#
# Logic:
#   V13 = None      → not wired, keep / set disabled
#   V13 = number    → wired and working, enable buttons
#
# Transition events:
#   unavailable → available : enable buttons, read ABC state
#   available → unavailable : disable buttons
#   no change               : reapply current state (idempotent)
#
# Called at startup and every ~60 seconds from update loop.
#
# In the threaded version, the V13 value is passed in from
# the already-fetched result dict so no extra HTTP call is made.
#
# ============================================================

def check_uart_availability(uart_val):
    """
    Update uart_available and enable/disable buttons based on
    a V13 value already fetched by the background thread.
    """

    global uart_available

    # Valid UART signal: V13 must return a parseable number
    is_available = False

    if uart_val is not None:
        try:
            float(uart_val)
            is_available = True
        except (ValueError, TypeError):
            pass

    if is_available and not uart_available:
        # New: UART just became available
        uart_available = True
        _enable_uart_buttons()

    elif not is_available and uart_available:
        # New: UART just went away
        uart_available = False
        _disable_uart_buttons()

    elif not is_available:
        # Still unavailable — reapply disabled state (idempotent)
        _disable_uart_buttons()

    else:
        # Still available — refresh ABC state only
        refresh_abc_state()


# ============================================================
# BACKGROUND FETCH — THREAD + QUEUE
# ============================================================
#
# All HTTP reads (5 pins per cycle) run in a daemon thread so
# the tkinter main loop is never blocked.
#
# Pattern:
#   _trigger_fetch()   — called by app.after() every interval;
#                        starts a thread if none is running
#   _fetch_worker()    — daemon thread; reads all pins, puts
#                        result dict into _result_queue
#   _poll_queue()      — called every 100 ms by app.after();
#                        drains _result_queue and calls
#                        _process_result() on the main thread
#
# Only the main thread touches tkinter widgets.
#
# ============================================================

_result_queue  = queue.Queue()
_fetch_running = False    # guard: only one fetch thread at a time


def _fetch_worker():
    """
    Runs in a daemon thread.
    Reads all Blynk pins then puts result dict into _result_queue.
    """

    global _fetch_running

    try:
        log_txt("Starting sensor acquisition cycle (background thread)")

        result = {
            "temp":     read_virtual_pin(10),
            "hum":      read_virtual_pin(11),
            "co2":      read_virtual_pin(12),
            "msg":      read_virtual_pin(3),
            "co2_uart": read_virtual_pin(V_CO2_UART),
            "rssi":     read_virtual_pin(V_WIFI_RSSI),   # [NEW v6.12] WiFi signal strength
        }

        _result_queue.put(result)

    except Exception as e:
        log_txt(f"Fetch worker error: {e}")
        _result_queue.put(None)

    finally:
        _fetch_running = False


def _trigger_fetch():
    """
    Scheduled every UPDATE_INTERVAL_MS by app.after().
    Starts a new daemon fetch thread only if none is running.
    """

    global _fetch_running

    if not _fetch_running:
        _fetch_running = True
        t = threading.Thread(target=_fetch_worker, daemon=True)
        t.start()

    app.after(UPDATE_INTERVAL_MS, _trigger_fetch)


def _poll_queue():
    """
    Called every 100 ms by app.after().
    Drains _result_queue and applies results to the GUI on the
    main thread — the only thread allowed to touch tkinter widgets.
    """

    try:
        while True:
            result = _result_queue.get_nowait()
            if result is not None:
                try:
                    _process_result(result)
                except Exception as e:
                    log_txt(f"PROCESS RESULT ERROR: {e}")
    except queue.Empty:
        pass

    app.after(100, _poll_queue)


# ============================================================
# GUI
# ============================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()

app.title("MH-Z19 CO2 Dashboard")
app.geometry("1350x980")

# ── App-level dark background ──────────────────────────────────────────────────
app.configure(fg_color="#090d16")

# ============================================================
# TITLE
# ============================================================

label_title = ctk.CTkLabel(
    app,
    text="MH-Z19  Engineering Dashboard",
    font=("Segoe UI", 28, "bold"),
    text_color="#f1f5f9"
)

label_title.pack(pady=(14, 6))

# ============================================================
# TOP FRAME
# ============================================================

frame_top = ctk.CTkFrame(app, fg_color="transparent")
frame_top.pack(fill="x", padx=16, pady=(4, 6))

# ============================================================
# TEMPERATURE
# ============================================================

frame_temp = ctk.CTkFrame(frame_top, fg_color="#0f1623", corner_radius=14,
                           border_width=1, border_color="#1a2332")
frame_temp.pack(side="left", expand=True, fill="both", padx=8, pady=6)

label_temp_title = ctk.CTkLabel(
    frame_temp,
    text="🌡  TEMPERATURE",
    font=("Segoe UI", 11, "bold"),
    text_color="#475569"
)
label_temp_title.pack(pady=(10, 2))

label_temp_value = ctk.CTkLabel(
    frame_temp,
    text="N/A °C",
    font=("Segoe UI", 44, "bold"),
    text_color="#f1f5f9"
)
label_temp_value.pack(pady=(4, 12))

# ============================================================
# HUMIDITY
# ============================================================

frame_hum = ctk.CTkFrame(frame_top, fg_color="#0f1623", corner_radius=14,
                          border_width=1, border_color="#1a2332")
frame_hum.pack(side="left", expand=True, fill="both", padx=8, pady=6)

label_hum_title = ctk.CTkLabel(
    frame_hum,
    text="💧  HUMIDITY",
    font=("Segoe UI", 11, "bold"),
    text_color="#475569"
)
label_hum_title.pack(pady=(10, 2))

label_hum_value = ctk.CTkLabel(
    frame_hum,
    text="N/A %",
    font=("Segoe UI", 44, "bold"),
    text_color="#f1f5f9"
)
label_hum_value.pack(pady=(4, 12))

# ============================================================
# CO2
# ============================================================

frame_co2 = ctk.CTkFrame(frame_top, fg_color="#0f1623", corner_radius=14,
                          border_width=1, border_color="#1a2332")
frame_co2.pack(side="left", expand=True, fill="both", padx=8, pady=6)

label_co2_title = ctk.CTkLabel(
    frame_co2,
    text="🌿  CO2",
    font=("Segoe UI", 11, "bold"),
    text_color="#475569"
)
label_co2_title.pack(pady=(10, 2))

label_co2_value = ctk.CTkLabel(
    frame_co2,
    text="N/A ppm",
    font=("Segoe UI", 52, "bold"),
    text_color=CO2_COLOR_DEFAULT
)
label_co2_value.pack(pady=(4, 4))

# ============================================================
# CO2 UART CROSS-CHECK LABEL
# ============================================================
#
# Shows the UART CO2 reading directly below the main PWM
# value for easy side-by-side comparison.
#
# States:
#   gray     "UART: N/A"              — V13 not available
#   green    "UART: XXX ppm ✓"        — within threshold
#   orange   "UART: XXX ppm  ⚠ Δ+YY" — significant difference
#
# ============================================================

label_co2_uart = ctk.CTkLabel(
    frame_co2,
    text="UART: N/A",
    font=("Segoe UI", 13),
    text_color="gray"
)
label_co2_uart.pack(pady=(2, 10))

# ============================================================
# MESSAGE
# ============================================================

label_msg = ctk.CTkLabel(
    app,
    text="N/A",
    font=("Segoe UI", 14),
    text_color="#64748b"
)
label_msg.pack(pady=(4, 6))

# ============================================================
# GRAPH
# ============================================================

figure = Figure(figsize=(12, 5), dpi=100)
ax = figure.add_subplot(111)

# ============================================================
# DARK GRAPH THEME
# ============================================================
#
# Configure matplotlib graph to visually match
# the engineering dashboard dark UI.
#
# This improves:
#   - readability
#   - night visibility
#   - engineering aesthetics
#   - long-term monitoring comfort
#
# ============================================================

figure.patch.set_facecolor("#090d16")
ax.set_facecolor("#0f1623")

ax.tick_params(colors="#94a3b8")

ax.xaxis.label.set_color("#94a3b8")
ax.yaxis.label.set_color("#94a3b8")
ax.title.set_color("#f1f5f9")

for spine in ax.spines.values():
    spine.set_color("#1a2332")

canvas = FigureCanvasTkAgg(figure, master=app)
canvas.get_tk_widget().pack(fill="both", expand=True, padx=16, pady=(4, 4))

# ============================================================
# GRAPH CLICK INFO LABEL
# ============================================================
#
# Displays sensor values for the point nearest to the click.
# Shown between the graph and the history selector bar.
# Updated by on_graph_click() on every mouse click.
#
# Format:
#   🕐 HH:MM:SS  |  🌡 XX.X °C  |  💧 XX.X %  |  CO2: XXXX ppm
#
# ============================================================

label_click_info = ctk.CTkLabel(
    app,
    text="Click any point on the graph to inspect sensor values",
    font=("Segoe UI", 13),
    text_color="#475569"
)
label_click_info.pack(pady=(2, 4))

# ============================================================
# HISTORY SELECTION
# ============================================================

history_var = ctk.StringVar(value="24")

# ============================================================
# WIFI SIGNAL HELPER
# ============================================================
#
# Converts raw RSSI dBm to a 0–100 % quality value and
# picks an icon reflecting signal strength:
#
#   ≥ -50 dBm  →  excellent  📶 100–75 %
#   ≥ -65 dBm  →  good       📶  74–50 %
#   ≥ -75 dBm  →  fair       📶  49–25 %
#    < -75 dBm  →  poor       📶  24–0  %
#
# Formula maps -30 dBm (best practical) → 100 %
#                       -90 dBm (edge)   →   0 %
#
# ============================================================

def rssi_to_quality(rssi_dbm):
    """
    Convert RSSI dBm to 0-100% quality.
    Clamps to [0, 100].
    """
    quality = max(0, min(100, 2 * (rssi_dbm + 100)))
    return quality


def rssi_icon(quality):
    """Return a WiFi bar icon based on quality %."""
    if quality >= 75:
        return "▂▄▆█"
    elif quality >= 50:
        return "▂▄▆░"
    elif quality >= 25:
        return "▂▄░░"
    else:
        return "▂░░░"


frame_bottom = ctk.CTkFrame(app, fg_color="#0f1623", corner_radius=12,
                             border_width=1, border_color="#2a3a52",
                             height=40)
frame_bottom.pack(fill="x", padx=16, pady=(4, 10))
frame_bottom.pack_propagate(False)

label_hist = ctk.CTkLabel(
    frame_bottom,
    text="Graph History",
    font=("Segoe UI", 12),
    text_color="#64748b"
)
label_hist.pack(side="left", padx=10)

option_history = ctk.CTkOptionMenu(
    frame_bottom,
    variable=history_var,
    values=["1", "3", "24", "48", "168"],
    fg_color="#1a2332",
    button_color="#1e3a5f",
    button_hover_color="#22d3ee",
    dropdown_fg_color="#0f1623",
    text_color="#f1f5f9",
    font=("Segoe UI", 12)
)
option_history.pack(side="left", padx=8)

label_hist_desc = ctk.CTkLabel(
    frame_bottom,
    text="Hours  (168h = 7 days)",
    font=("Segoe UI", 12),
    text_color="#475569"
)
label_hist_desc.pack(side="left", padx=8)


# ============================================================
# HISTORY DOWNLOAD BUTTON
# ============================================================
#
# Downloads historical graph data stored internally on the
# Blynk server and exports it to CSV/TXT files.
#
# Useful when:
#
#   - dashboard logging was not running
#   - ESP32 was offline
#   - Blynk mobile app still shows graph history
#
# ============================================================

button_download_history = ctk.CTkButton(
    frame_bottom,
    text="Download Server History",
    command=download_blynk_history,
    fg_color="#1e3a5f",
    hover_color="#22d3ee",
    text_color="#f1f5f9",
    font=("Segoe UI", 12),
    corner_radius=8
)

button_download_history.pack(side="left", padx=16)

# ============================================================
# ZERO CALIBRATION BUTTON
# ============================================================
#
# Sends a calibration trigger to the sensor via V20.
# Shows a confirmation dialog before sending to prevent
# accidental calibration while indoors.
#
# STARTS DISABLED (gray) until UART signal is confirmed on
# V13 — meaning the two UART wires are physically connected
# and the INO is running the UART code.
#
# Requires matching BLYNK_WRITE(V20) handler in the INO.
#
# ============================================================

button_zero_cal = ctk.CTkButton(
    frame_bottom,
    text="Zero Calibration",
    fg_color="gray40",
    hover_color="gray50",
    state="disabled",
    command=trigger_zero_calibration,
    font=("Segoe UI", 12),
    corner_radius=8
)

button_zero_cal.pack(side="left", padx=8)

# ============================================================
# ABC STATE BUTTON
# ============================================================
#
# Displays current ABC (Automatic Baseline Calibration) state
# and toggles it on click.
#
# Colour states:
#   gray   "ABC: ?"    — UART not wired / state unknown
#   green  "ABC: ON"   — ABC currently enabled
#   red    "ABC: OFF"  — ABC currently disabled
#
# STARTS DISABLED (gray) until UART signal is confirmed on
# V13. Once UART is detected, the actual ABC state is read
# from V21 and the button colour updates accordingly.
# Refreshed every ~60 seconds.
#
# Requires matching BLYNK_WRITE(V21) handler in the INO.
#
# ============================================================

button_abc = ctk.CTkButton(
    frame_bottom,
    text="ABC: ?",
    fg_color="gray40",
    hover_color="gray50",
    state="disabled",
    command=toggle_abc,
    font=("Segoe UI", 12),
    corner_radius=8
)

button_abc.pack(side="left", padx=8)

# ============================================================
# MANUAL SENSOR RESET BUTTON                      [NEW v6.14]
# ============================================================
#
# Sends a soft reset command to the MH-Z19 sensor via V22.
# Shows a confirmation dialog before sending.
#
# STARTS DISABLED (gray) until UART signal is confirmed on
# V13 — the soft reset is sent over UART by the INO so UART
# must be physically connected for it to work.
#
# Requires matching BLYNK_WRITE(V22) handler in the INO.
#
# ============================================================

button_sensor_reset = ctk.CTkButton(
    frame_bottom,
    text="Reset Sensor",
    fg_color="gray40",
    hover_color="gray50",
    state="disabled",
    command=trigger_sensor_reset,
    font=("Segoe UI", 12),
    corner_radius=8
)

button_sensor_reset.pack(side="left", padx=8)


# ============================================================
# CONNECTION STATUS INDICATOR
# ============================================================
#
# Displays a coloured dot and text on the right side of the
# bottom bar:
#
#   green  dot  ->  CONNECTED   (last fetch succeeded)
#   red    dot  ->  OFFLINE     (last fetch failed)
#
# Updated every acquisition cycle.
#
# ============================================================

# ============================================================
# WIFI SIGNAL STRENGTH DISPLAY
# ============================================================
#
# Shows live WiFi RSSI (dBm) from the ESP8266 sensor node
# and derived quality percentage. Updated every fetch cycle.
# Icon bars reflect signal tier visually.
#
# ============================================================

label_wifi = ctk.CTkLabel(
    frame_bottom,
    text="📡  WiFi: —",
    font=("Segoe UI", 12),
    text_color="#475569"
)
label_wifi.pack(side="right", padx=12)

label_status = ctk.CTkLabel(
    frame_bottom,
    text="⬤  CONNECTING...",
    font=("Segoe UI", 12, "bold"),
    text_color="#475569"
)
label_status.pack(side="right", padx=16)

# ============================================================
# LAST UPDATED TIMESTAMP
# ============================================================
#
# Shows the exact time data was last successfully received.
# Helps identify stale data if the connection drops silently.
#
# ============================================================

label_last_update = ctk.CTkLabel(
    frame_bottom,
    text="Last update: --:--:--",
    font=("Segoe UI", 12),
    text_color="#475569"
)
label_last_update.pack(side="right", padx=16)

# ============================================================
# GRAPH CLICK HANDLER
# ============================================================
#
# Triggered on every mouse click inside the graph axes.
#
# Steps:
#   1. Reject clicks outside the axes (no xdata)
#   2. Convert matplotlib float x to a Python datetime
#   3. Find the row in current_df with the nearest timestamp
#   4. Update label_click_info with that row's sensor values
#
# Uses absolute timedelta difference so it works correctly
# regardless of click direction or data density.
#
# ============================================================

def on_graph_click(event):
    """
    Handle mouse click on the graph canvas.
    Find the nearest logged data point and display its values.
    """

    global current_df

    # Ignore clicks outside the axes area
    if event.xdata is None or current_df is None or len(current_df) == 0:
        return

    # Convert matplotlib float timestamp to datetime
    click_dt = mdates.num2date(event.xdata).replace(tzinfo=None)

    # Find the row whose timestamp is closest to the click
    deltas = (current_df["timestamp"] - click_dt).abs()
    nearest_idx = deltas.idxmin()
    row = current_df.loc[nearest_idx]

    ts   = row["timestamp"].strftime("%H:%M:%S")
    temp = f"{row['temperature']:.1f}"
    hum  = f"{row['humidity']:.1f}"
    co2  = round(float(row["co2"]), 1)

    label_click_info.configure(
        text=f"🕐 {ts}    |    🌡 {temp} °C    |    💧 {hum} %    |    CO2: {co2} ppm",
        text_color=co2_color(co2)
    )

    log_txt(f"GRAPH CLICK: ts={ts} temp={temp} hum={hum} co2={co2}")


canvas.mpl_connect("button_press_event", on_graph_click)

# ============================================================
# PROCESS RESULT
# ============================================================
#
# Applies a result dict produced by _fetch_worker() to the GUI.
# Always called on the main thread via _poll_queue().
#
# Replaces the original update_dashboard() body — identical
# logic, but receives pre-fetched values instead of calling
# read_virtual_pin() itself.
#
# ============================================================

def _process_result(result):
    """
    Apply fetched sensor data to all GUI widgets, CSV log,
    and graph. Called on the main thread only.
    """

    global current_df, abc_refresh_counter

    try:
        log_txt("Processing sensor data on main thread")

        temp     = result.get("temp")
        hum      = result.get("hum")
        co2      = result.get("co2")
        msg      = result.get("msg")
        co2_uart = result.get("co2_uart")
        rssi     = result.get("rssi")   # [NEW v6.12] WiFi RSSI in dBm

        if temp is not None:
            label_temp_value.configure(text=f"{float(temp):.1f} °C")

        if hum is not None:
            label_hum_value.configure(text=f"{float(hum):.1f} %")

        # ====================================================
        # WIFI SIGNAL STRENGTH UPDATE                [v6.12]
        # ====================================================
        #
        # rssi is raw dBm from V14 (written by ESP8266 each
        # cycle). Converted to quality % for display.
        # Label goes grey if not available.
        #
        # ====================================================

        if rssi is not None:
            try:
                rssi_dbm  = int(float(rssi))
                quality   = rssi_to_quality(rssi_dbm)
                icon      = rssi_icon(quality)

                if quality >= 75:
                    wifi_color = "#22c55e"    # green — excellent
                elif quality >= 50:
                    wifi_color = "#22d3ee"    # cyan  — good
                elif quality >= 25:
                    wifi_color = "#f59e0b"    # amber — fair
                else:
                    wifi_color = "#ef4444"    # red   — poor

                label_wifi.configure(
                    text=f"{icon}  {rssi_dbm} dBm  ({quality}%)",
                    text_color=wifi_color
                )
            except (ValueError, TypeError):
                label_wifi.configure(text="📡  WiFi: —", text_color="#475569")
        else:
            label_wifi.configure(text="📡  WiFi: —", text_color="#475569")

        # ====================================================
        # CONNECTION STATUS UPDATE
        # ====================================================
        #
        # Consider connected if at least CO2 was received,
        # as it is the primary sensor value.
        #
        # ====================================================

        if co2 is not None:
            label_status.configure(
                text="⬤  CONNECTED",
                text_color="#00cc44"
            )
            label_last_update.configure(
                text=f"Last update: {datetime.now().strftime('%H:%M:%S')}",
                text_color="white"
            )
        else:
            label_status.configure(
                text="⬤  OFFLINE",
                text_color="#ff2222"
            )

        # ====================================================
        # CO2 VALUE + COLOUR CODING
        # ====================================================
        #
        # Label colour reflects current air quality level
        # based on configured thresholds. Updates every cycle.
        #
        # ====================================================

        if co2 is not None:
            co2_ppm = round(float(co2), 1)
            label_co2_value.configure(
                text=f"{co2_ppm} ppm",
                text_color=co2_color(co2_ppm)
            )

# ====================================================
# UART CO2 CROSS-CHECK
# ====================================================
#
# Compares PWM reading (V12) against UART reading (V13).
#
# If the difference exceeds UART_DIFF_THRESHOLD:
#   - orange warning shown with signed delta
#   - discrepancy logged to engineering TXT
#
# Small differences below threshold are shown quietly
# in green as confirmation that both paths agree.
#
# The delta sign convention:
#   Δ+ means PWM reads HIGHER than UART
#   Δ- means PWM reads LOWER  than UART
#
# ====================================================

            if co2_uart is not None:
                try:
                    co2_uart_ppm = round(float(co2_uart), 1)
                    diff         = co2_uart_ppm - co2_ppm

                    if abs(diff) >= UART_DIFF_THRESHOLD:
                        sign = "+" if diff > 0 else ""
                        label_co2_uart.configure(
                            text=(
                                f"UART: {co2_uart_ppm} ppm"
                                f"  ⚠ Δ{sign}{int(diff)}"
                            ),
                            text_color="#ff8800"
                        )
                        log_txt(
                            f"CO2 UART DISCREPANCY: "
                            f"PWM={co2_ppm} "
                            f"UART={co2_uart_ppm} "
                            f"DELTA={diff:+.1f}"
                        )
                    else:
                        label_co2_uart.configure(
                            text=f"UART: {co2_uart_ppm} ppm ✓",
                            text_color="#00cc44"
                        )

                except Exception as e:
                    log_txt(f"UART CO2 parse error: {e}")
                    label_co2_uart.configure(
                        text="UART: parse error",
                        text_color="gray"
                    )
            else:
                label_co2_uart.configure(
                    text="UART: N/A",
                    text_color="gray"
                )

# ====================================================
# PERIODIC UART AVAILABILITY + ABC STATE REFRESH
# ====================================================
#
# Every ABC_REFRESH_EVERY cycles (~60 seconds):
#   1. Re-check if UART signal is present on V13
#   2. If available: refresh ABC button state from V21
#   3. If unavailable: keep / set buttons disabled
#
# This means buttons automatically light up the first
# time UART wires are connected, without restarting.
#
# abc_refresh_counter is initialised to ABC_REFRESH_EVERY
# so the check runs immediately on the very first cycle,
# enabling buttons at startup if UART is already wired.
#
# ====================================================

        abc_refresh_counter += 1

        if abc_refresh_counter >= ABC_REFRESH_EVERY:
            abc_refresh_counter = 0
            check_uart_availability(co2_uart)

        if msg is not None:
            label_msg.configure(text=msg)

        # ====================================================
        # SAVE CSV LOG
        # ====================================================

        if (
            temp is not None and
            hum is not None and
            co2 is not None
        ):

            row = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "temperature": float(temp),
                "humidity": float(hum),
                "co2": float(co2),
                "message": msg
            }

            df = pd.DataFrame([row])

            df.to_csv(
                CSV_LOG_FILE,
                mode="a",
                header=False,
                index=False
            )

            log_txt(
                f"CSV LOGGED: TEMP={temp} HUM={hum} CO2={co2} MSG={msg}"
            )

        else:
            log_txt("WARNING: Some values are None")

        # ====================================================
        # UPDATE GRAPH
        # ====================================================

        try:
            df = pd.read_csv(CSV_LOG_FILE)

            if len(df) > 0:

                df["timestamp"] = pd.to_datetime(df["timestamp"])

                hours = int(history_var.get())

                cutoff = datetime.now() - timedelta(hours=hours)

                df = df[df["timestamp"] >= cutoff]

                # Keep a reference so on_graph_click() can look up values
                current_df = df.copy()

                ax.clear()

# ====================================================
# REAPPLY DARK THEME AFTER CLEAR
# ====================================================
#
# matplotlib clear() resets all visual properties back
# to defaults (black text on white/grey background).
# All dark theme settings must be explicitly reapplied
# every time ax.clear() is called.
#
# ====================================================

                figure.patch.set_facecolor("#090d16")
                ax.set_facecolor("#0f1623")

                ax.tick_params(colors="#94a3b8")

                ax.xaxis.label.set_color("#94a3b8")
                ax.yaxis.label.set_color("#94a3b8")
                ax.title.set_color("#f1f5f9")

                for spine in ax.spines.values():
                    spine.set_color("#1a2332")

# ====================================================
# PLOT CO2 DATA
# ====================================================
#
# Draw CO2 measurements as a line with circle markers.
# Markers make individual data points clearly visible,
# especially useful for sparse early-session datasets.
#
# ====================================================

                ax.plot(
                    df["timestamp"],
                    df["co2"],
                    color="#00bfff",
                    linewidth=1.5,
                    marker="o",
                    markersize=3,
                    label="CO2 ppm"
                )

# ====================================================
# 1000 PPM THRESHOLD REFERENCE LINE
# ====================================================
#
# A dashed red horizontal line at 1000 ppm marks the
# standard indoor air quality "ventilate now" level.
# Drawn across the full visible x range so it does
# not affect axis auto-scaling.
#
# ====================================================

                ax.axhline(
                    y=CO2_MODERATE,
                    color="#ff4444",
                    linewidth=1.2,
                    linestyle="--",
                    label=f"{CO2_MODERATE} ppm threshold"
                )

# ====================================================
# MIN / MAX ANNOTATION FOR VISIBLE WINDOW
# ====================================================
#
# Annotates the minimum and maximum CO2 values within
# the current graph window so engineering limits are
# immediately readable without inspecting the CSV.
#
# ====================================================

                if len(df) > 0:
                    co2_min = df["co2"].min()
                    co2_max = df["co2"].max()
                    ts_min  = df.loc[df["co2"].idxmin(), "timestamp"]
                    ts_max  = df.loc[df["co2"].idxmax(), "timestamp"]

                    ax.annotate(
                        f"MIN {int(co2_min)} ppm",
                        xy=(ts_min, co2_min),
                        xytext=(10, 12),
                        textcoords="offset points",
                        color="#00ff99",
                        fontsize=10,
                        fontweight="bold",
                        arrowprops=dict(
                            arrowstyle="->",
                            color="#00ff99",
                            lw=1.2
                        )
                    )

                    ax.annotate(
                        f"MAX {int(co2_max)} ppm",
                        xy=(ts_max, co2_max),
                        xytext=(10, -18),
                        textcoords="offset points",
                        color="#ff6666",
                        fontsize=10,
                        fontweight="bold",
                        arrowprops=dict(
                            arrowstyle="->",
                            color="#ff6666",
                            lw=1.2
                        )
                    )

                ax.legend(
                    facecolor="#0f1623",
                    edgecolor="#1a2332",
                    labelcolor="#94a3b8"
                )

                ax.set_title(f"CO2 History ({hours}h)", fontsize=15, fontweight="bold")
                ax.set_ylabel("ppm", fontsize=12, fontweight="bold")
                ax.set_xlabel("Time", fontsize=12, fontweight="bold")
                ax.tick_params(axis="both", labelsize=10)
                ax.grid(True, color="#1a2332", linestyle="--", linewidth=0.6)

# ====================================================
# FORCE EXACTLY 6 EVENLY-SPACED TIME TICKS ON X AXIS
# ====================================================
#
# AutoDateLocator treats minticks as a hint and can
# still produce fewer ticks for short time ranges.
# Instead, manually compute 6 evenly-spaced positions
# from the actual plotted x-axis limits — this is
# guaranteed regardless of session length or history
# window selected.
#
# ====================================================

                x_min, x_max = ax.get_xlim()
                tick_positions = np.linspace(x_min, x_max, 6)
                ax.set_xticks(tick_positions)

# ====================================================
# SMART RANGE-AWARE TICK LABEL FORMATTER
# ====================================================
#
# Chooses label format based on the visible time span:
#
#   same day          ->  HH:MM
#   multi-day,        ->  dd HH:MM
#     same month
#   multi-month       ->  dd/mm HH:MM
#
# This avoids the "2026" year-only labels produced
# when AutoDateFormatter loses locator scale context.
#
# ====================================================

                t_start = mdates.num2date(x_min)
                t_end   = mdates.num2date(x_max)

                if t_start.date() == t_end.date():
                    fmt = "%H:%M"
                elif t_start.month == t_end.month:
                    fmt = "%d %H:%M"
                else:
                    fmt = "%d/%m %H:%M"

                ax.xaxis.set_major_formatter(
                    mticker.FuncFormatter(
                        lambda x, _: mdates.num2date(x).strftime(fmt)
                    )
                )

                figure.autofmt_xdate(rotation=30)

                canvas.draw()

                log_txt(f"Graph updated for {hours}h range")

        except Exception as e:
            log_txt(f"GRAPH ERROR: {e}")

    except Exception as e:
        log_txt(f"MAIN LOOP ERROR: {e}")


# ============================================================
# STARTUP DIAGNOSTICS
# ============================================================

log_txt("================================================")
log_txt("MH-Z19 ENGINEERING DASHBOARD STARTING")
log_txt("================================================")

log_txt(f"BLYNK SERVER: {BLYNK_SERVER}:{BLYNK_PORT}")
log_txt("Testing initial connectivity...")

try:
    test = read_virtual_pin(12)

    if test is not None:
        log_txt("Initial Blynk communication SUCCESS")
    else:
        log_txt("Initial Blynk communication FAILED")

except Exception as e:
    log_txt(f"Startup connection test failed: {e}")

log_txt("Querying initial ABC state...")
abc_user_preference = load_abc_preference()

# ============================================================
# INITIALISE abc_refresh_counter TO TRIGGER UART CHECK
# ON THE VERY FIRST FETCH CYCLE
# ============================================================
#
# Setting the counter to ABC_REFRESH_EVERY means the UART
# availability check (and ABC button enable) fires immediately
# on the first data cycle rather than waiting ~60 seconds.
#
# ============================================================

abc_refresh_counter = ABC_REFRESH_EVERY

# ============================================================
# START BACKGROUND FETCH LOOP + QUEUE POLLER
# ============================================================

app.after(100, _poll_queue)
app.after(0,   _trigger_fetch)

# ============================================================
# SCHEDULE HOURLY LOG TRIM
# ============================================================
#
# First trim runs 60 seconds after startup so the dashboard
# has time to fully initialise before doing file I/O.
# _schedule_trim() reschedules itself after each run so no
# external timer management is needed.
#
# ============================================================

app.after(60000, _schedule_trim)

app.mainloop()