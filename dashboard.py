# Blynk CO2 Dashboard for Windows PC
# v1.6

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
# └── co2_log.csv

# IMPORTANT
# Update this line in the Python code:
# BLYNK_SERVER = "192.168.3.9"
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

============================================================
 REQUIRED FILES
============================================================

 Place near this script:

   secrets.h

 containing:

   #define WIFI_SSID   "yourssid"
   #define WIFI_PASS   "yourpass"
   #define BLYNK_AUTH  "yourtoken"

============================================================
 REQUIRED PYTHON PACKAGES
============================================================

 pip install requests pandas matplotlib customtkinter

============================================================
 BLYNK VIRTUAL PINS
============================================================

 V10 = temperature
 V11 = humidity
 V12 = CO2 ppm
 V3  = engineering message

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
import requests
import pandas as pd
import customtkinter as ctk

from datetime import datetime, timedelta

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np

# ============================================================
# CONFIGURATION
# ============================================================

BLYNK_SERVER = "192.168.3.9"
BLYNK_PORT   = 8084

UPDATE_INTERVAL_MS = 5000

CSV_LOG_FILE = "co2_log.csv"
TXT_LOG_FILE = "engineering_log.txt"

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
    """

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    line = f"[{ts}] {message}"

    print(line)

    with open(TXT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
# GUI
# ============================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()

app.title("MH-Z19 CO2 Dashboard")
app.geometry("1600x1200")

# ============================================================
# TITLE
# ============================================================

label_title = ctk.CTkLabel(
    app,
    text="MH-Z19 Engineering Dashboard",
    font=("Arial", 36, "bold")
)

label_title.pack(pady=20)

# ============================================================
# TOP FRAME
# ============================================================

frame_top = ctk.CTkFrame(app)
frame_top.pack(fill="x", padx=20, pady=10)

# ============================================================
# TEMPERATURE
# ============================================================

frame_temp = ctk.CTkFrame(frame_top)
frame_temp.pack(side="left", expand=True, fill="both", padx=10, pady=10)

label_temp_title = ctk.CTkLabel(
    frame_temp,
    text="Temperature",
    font=("Arial", 20)
)
label_temp_title.pack(pady=10)

label_temp_value = ctk.CTkLabel(
    frame_temp,
    text="N/A °C",
    font=("Arial", 48, "bold")
)
label_temp_value.pack(pady=30)

# ============================================================
# HUMIDITY
# ============================================================

frame_hum = ctk.CTkFrame(frame_top)
frame_hum.pack(side="left", expand=True, fill="both", padx=10, pady=10)

label_hum_title = ctk.CTkLabel(
    frame_hum,
    text="Humidity",
    font=("Arial", 20)
)
label_hum_title.pack(pady=10)

label_hum_value = ctk.CTkLabel(
    frame_hum,
    text="N/A %",
    font=("Arial", 48, "bold")
)
label_hum_value.pack(pady=30)

# ============================================================
# CO2
# ============================================================

frame_co2 = ctk.CTkFrame(frame_top)
frame_co2.pack(side="left", expand=True, fill="both", padx=10, pady=10)

label_co2_title = ctk.CTkLabel(
    frame_co2,
    text="CO2",
    font=("Arial", 20)
)
label_co2_title.pack(pady=10)

label_co2_value = ctk.CTkLabel(
    frame_co2,
    text="N/A ppm",
    font=("Arial", 56, "bold"),
    text_color=CO2_COLOR_DEFAULT
)
label_co2_value.pack(pady=30)

# ============================================================
# MESSAGE
# ============================================================

label_msg = ctk.CTkLabel(
    app,
    text="N/A",
    font=("Arial", 22)
)
label_msg.pack(pady=20)

# ============================================================
# GRAPH
# ============================================================

figure = Figure(figsize=(14, 6), dpi=100)
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

figure.patch.set_facecolor("#2b2b2b")
ax.set_facecolor("#3a3a3a")

ax.tick_params(colors="white")

ax.xaxis.label.set_color("white")
ax.yaxis.label.set_color("white")
ax.title.set_color("white")

for spine in ax.spines.values():
    spine.set_color("white")

canvas = FigureCanvasTkAgg(figure, master=app)
canvas.get_tk_widget().pack(fill="both", expand=True, padx=20, pady=20)

# ============================================================
# HISTORY SELECTION
# ============================================================

history_var = ctk.StringVar(value="24")

frame_bottom = ctk.CTkFrame(app)
frame_bottom.pack(fill="x", padx=20, pady=10)

label_hist = ctk.CTkLabel(
    frame_bottom,
    text="Graph History"
)
label_hist.pack(side="left", padx=10)

option_history = ctk.CTkOptionMenu(
    frame_bottom,
    variable=history_var,
    values=["1", "3", "24", "48", "168"]
)
option_history.pack(side="left", padx=10)

label_hist_desc = ctk.CTkLabel(
    frame_bottom,
    text="Hours (168h = 7 days)"
)
label_hist_desc.pack(side="left", padx=10)

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

label_status = ctk.CTkLabel(
    frame_bottom,
    text="⬤  CONNECTING...",
    font=("Arial", 14),
    text_color="gray"
)
label_status.pack(side="right", padx=20)

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
    font=("Arial", 13),
    text_color="gray"
)
label_last_update.pack(side="right", padx=20)

# ============================================================
# UPDATE LOOP
# ============================================================


def update_dashboard():
    """
    Main acquisition loop.

    Reads:
      - temperature
      - humidity
      - CO2
      - engineering message

    Updates:
      - GUI
      - CSV logs
      - graph
      - engineering TXT logs
      - connection status indicator
      - last updated timestamp
      - CO2 colour coding
    """

    try:
        log_txt("Starting sensor acquisition cycle")

        temp = read_virtual_pin(10)
        hum  = read_virtual_pin(11)
        co2  = read_virtual_pin(12)
        msg  = read_virtual_pin(3)

        if temp is not None:
            label_temp_value.configure(text=f"{float(temp):.1f} °C")

        if hum is not None:
            label_hum_value.configure(text=f"{float(hum):.1f} %")

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
            co2_ppm = int(float(co2))
            label_co2_value.configure(
                text=f"{co2_ppm} ppm",
                text_color=co2_color(co2_ppm)
            )

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

                figure.patch.set_facecolor("#2b2b2b")
                ax.set_facecolor("#3a3a3a")

                ax.tick_params(colors="white")

                ax.xaxis.label.set_color("white")
                ax.yaxis.label.set_color("white")
                ax.title.set_color("white")

                for spine in ax.spines.values():
                    spine.set_color("white")

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
                    facecolor="#3a3a3a",
                    edgecolor="white",
                    labelcolor="white"
                )

                ax.set_title(f"CO2 History ({hours}h)", fontsize=20, fontweight="bold")
                ax.set_ylabel("ppm", fontsize=16, fontweight="bold")
                ax.set_xlabel("Time", fontsize=16, fontweight="bold")
                ax.tick_params(axis="both", labelsize=13)
                ax.grid(True, color="#555555", linestyle="--", linewidth=0.5)

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

    app.after(UPDATE_INTERVAL_MS, update_dashboard)


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

# ============================================================
# START LOOP
# ============================================================

update_dashboard()

app.mainloop()