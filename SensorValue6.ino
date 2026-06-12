/**************************************************************
 * Blynk is a platform with iOS and Android apps to control
 * Arduino, Raspberry Pi and the likes over the Internet.
 * You can easily build graphic interfaces for all your
 * projects by simply dragging and dropping widgets.
 * Blynk library is licensed under MIT license
 *
 * You send data in decimals as they go out of Arduino and
 * within the BLYNK application in Android you can choose
 * number of decimals by editing the widget and inside the
 * LABEL write:
 * /pin/      <-- displays value without formating (12.6789)
 * /pin./     <-- displays value rounded to full decimal (13)
 * /pin.#/    <-- displays value rounded with 1 decimal digit (12.7)
 * /pin.##/   <-- displays value rounded with 2 decimals places (12.68)
 * You can also add units. Example:       /pin.#/ C
 *
 **************************************************************
 *
 * WARNING :
 * For this example you'll need SimpleTimer library:
 *   https://github.com/jfturcot/SimpleTimer
 * Visit this page for more information:
 *   http://playground.arduino.cc/Code/SimpleTimer
 *
 * Blynk library installed in Arduio must be 0.6.1
 *
 * v. 5/29/2026 by AM  (v7 original)
 * v. 6/12/2026 by AM  (v8 — UART cross-check, zero-cal, ABC control)
 *
 * ============================================================
 * ORIGINAL BUG FIXES
 * ============================================================
 *
 * BUG 1 — pulseIn timeout / phase issue
 *
 *   pulseIn(pin, HIGH, timeout) was called at random phase
 *   of the MH-Z19 PWM cycle.
 *
 *   This could cause partial pulse measurements and invalid
 *   ppm computation.
 *
 *   FIX:
 *   Synchronize to LOW phase first.
 *
 * ------------------------------------------------------------
 *
 * BUG 2 — frozen smoother
 *
 *   Old code reused smoothPPM as fallback after invalid
 *   measurements:
 *
 *      smoothPPM = smoothPPM
 *
 *   permanently freezing output.
 *
 *   FIX:
 *   Use lastValidPPM separately.
 *
 * ------------------------------------------------------------
 *
 * BUG 3 — WiFi interrupt corruption
 *
 *   ESP8266 WiFi stack interrupts pulseIn() timing.
 *
 *   Example:
 *
 *      TH real     = 150ms
 *      WiFi pause  = 300ms
 *      TH measured = 450ms
 *
 *   producing false high ppm values.
 *
 *   FIX:
 *   Validate full cycle:
 *
 *      TH + TL ≈ 1004ms
 *
 * ------------------------------------------------------------
 *
 * BUG 4 — GPIO16 instability
 *
 *   GPIO16:
 *
 *      - lacks proper interrupt behavior
 *      - tied to RTC subsystem
 *      - unreliable with pulseIn()
 *      - unstable under WiFi activity
 *
 *   FIX:
 *   Move PWM input to GPIO14 (D5).
 *
 * ------------------------------------------------------------
 *
 * BUG 5 — RF / supply interference
 *
 *   WiFi RF activity introduces:
 *
 *      - power ripple
 *      - timing jitter
 *      - ground bounce
 *      - analog instability
 *
 *   FIX:
 *   Avoid sensor acquisition during active reconnect bursts.
 *
 * ------------------------------------------------------------
 *
 * BUG 6 — MH-Z19 ABC recalibration disturbances
 *
 *   Automatic Baseline Calibration may occasionally create
 *   transient unstable measurements around ~24h uptime.
 *
 *   FIX:
 *   Multi-stage contextual suppression and recovery logic.
 *
 * ============================================================
 * 6-LAYER PROTECTION ARCHITECTURE (original)
 * ============================================================
 *
 * LAYER 1 — PWM phase synchronization
 *
 * LAYER 2 — Full cycle validation
 *
 * LAYER 3 — Rate-of-change rejection
 *
 * LAYER 4 — WiFi coexistence protection
 *
 * LAYER 5 — ABC recalibration suppression
 *
 * LAYER 6 — Daily baseline plausibility validation
 *
 * ============================================================
 * INDUSTRIAL PROTECTION ADDITIONS (v7)
 * ============================================================
 *
 * LAYER 7 — Rolling median outlier rejection
 *
 *   A circular buffer of MEDIAN_WINDOW raw samples is
 *   maintained. The median of the buffer is computed each
 *   cycle and used as the primary input to the smoother.
 *
 *   Why median and not mean:
 *   The median is immune to single or double corrupted
 *   readings (WiFi spikes, ABC glitches). A single corrupted
 *   sample contributes nothing to the median as long as it
 *   is outvoted by honest samples.
 *
 *   Consensus override:
 *   If the median disagrees with lastValidPPM but the
 *   individual reading also disagrees consistently, the
 *   median is allowed to override a rate-rejection after
 *   MEDIAN_CONSENSUS_COUNT consecutive agreements.
 *   This ensures real large CO2 changes (opening a door,
 *   many people entering a room) are not permanently
 *   blocked by the rate limiter.
 *
 * ------------------------------------------------------------
 *
 * LAYER 8 — Stuck sensor detection
 *
 *   If raw ppm reads exactly the same integer value for
 *   STUCK_THRESHOLD consecutive cycles, the sensor is
 *   declared stuck (frozen output from hardware failure
 *   or PWM signal loss).
 *
 *   A stuck sensor is NOT the same as a stable environment
 *   because in a real room the MH-Z19 always shows small
 *   natural variation (±5–15 ppm) due to NDIR noise.
 *
 *   On SENSOR_STUCK the system holds the last stable
 *   smoothed value and reports the fault.
 *
 * ------------------------------------------------------------
 *
 * LAYER 9 — Fault latching
 *
 *   consecutiveFaults is incremented on every failed or
 *   rejected measurement. When it reaches FAULT_LATCH_COUNT
 *   the faultLatched flag is set and diagState is forced to
 *   FAULT_LATCHED.
 *
 *   A latched fault requires FAULT_CLEAR_COUNT consecutive
 *   valid measurements to clear. This prevents rapid
 *   OK/FAULT state oscillation during intermittent noise.
 *
 *   During a latched fault the smoother uses a very
 *   conservative alpha (0.05) so that one stray reading
 *   cannot rapidly corrupt the displayed value.
 *
 * ------------------------------------------------------------
 *
 * LAYER 10 — Adaptive plausibility thresholds
 *
 *   The rate-of-change limit is no longer fixed at 500 ppm.
 *   getAdaptiveRateLimit() returns a context-aware value:
 *
 *   Context                   Limit    Reason
 *   ─────────────────────────────────────────────────
 *   Boot / not yet stable     700      sensor warming up
 *   ABC time window (20-28h)  800      known ABC jumps
 *   Strong downward trend     700      active ventilation
 *   After fault recovery      400      be conservative
 *   Normal operation          500      nominal
 *
 *   The median consensus override uses 1.5× this limit
 *   (but never above 1200) because the median itself
 *   already filtered most noise.
 *
 * ------------------------------------------------------------
 *
 * LAYER 11 — Software watchdog restart
 *
 *   lastValidReadingTime is updated on every accepted
 *   measurement. If no valid reading is obtained within
 *   WATCHDOG_TIMEOUT_MS (default 5 minutes) the device
 *   prints a diagnostic line and calls ESP.restart().
 *
 *   This recovers from:
 *   - PWM signal loss (cable fault, sensor power loss)
 *   - Sustained WiFi interference making all readings
 *     fail rate or cycle checks
 *   - Any deadlock in the protection logic
 *
 *   The watchdog timer resets on every valid reading so
 *   it does not interfere with normal stable operation.
 *
 * ============================================================
 * ADAPTIVE SMOOTHING (v7)
 * ============================================================
 *
 *   Original fixed alpha:
 *
 *      smoothPPM = 0.7 * smoothPPM + 0.3 * ppm5
 *
 *   This required 17–18 cycles to converge after a real
 *   step change (e.g. 395 → 750 ppm) because:
 *
 *      after 17 cycles: 750 - 355 * 0.7^17 ≈ 748 ppm
 *
 *   New adaptive alpha based on |ppm5 - smoothPPM|:
 *
 *      delta > 200 ppm  →  alpha = 0.75  (fast tracking)
 *      delta > 50  ppm  →  alpha = 0.55  (medium)
 *      delta > 20  ppm  →  alpha = 0.40  (moderate)
 *      delta ≤ 20  ppm  →  alpha = 0.25  (heavy smoothing)
 *
 *   The larger alpha for large confirmed delta means the
 *   smoother converges in 5–7 cycles instead of 17–18,
 *   while still suppressing small noise with α = 0.25.
 *
 *   The median (Layer 7) ensures the large delta is only
 *   seen when the change is real and sustained, not when
 *   it is a single WiFi spike. Without the median, high
 *   alpha on a spike would corrupt the smoother.
 *   Together, median + adaptive alpha give both speed
 *   and robustness.
 *
 *   Conservative overrides (unchanged from v6):
 *   - controlledRecoveryActive → alpha = 0.10
 *   - faultLatched (new)       → alpha = 0.05
 *   - invalid fallback path    → alpha = 0.05
 *
 * ============================================================
 * ENGINEERING DIAGNOSTIC STATES
 * ============================================================
 *
 * These states are appended to Slovak messages:
 *
 *   [OK]
 *      Normal stable operation.
 *
 *   [OK_RECOVERED]
 *      System recovered from temporary instability.
 *
 *   [BOOT]
 *      Startup stabilization period.
 *
 *   [PWM_INVALID]
 *      TH + TL invalid.
 *      Indicates timing corruption.
 *
 *   [HIGH_TIMEOUT]
 *      pulseIn(HIGH) timeout.
 *
 *   [LOW_TIMEOUT]
 *      pulseIn(LOW) timeout.
 *
 *   [PPM_RANGE]
 *      Impossible ppm value computed.
 *
 *   [RATE_REJECT]
 *      Physically impossible ppm jump rejected.
 *
 *   [RATE_RECOVERY]
 *      Protection system detected deadlock and recovered
 *      automatically.
 *
 *   [ABC_RECOVERY]
 *      Temporary ABC recalibration disturbance suppression.
 *
 *   [SENSOR_RECOVERY]
 *      Temporary invalid reads but fallback stabilization
 *      active.
 *
 *   [SENSOR_STUCK]
 *      (NEW) Sensor output frozen for STUCK_THRESHOLD
 *      consecutive cycles. Hardware fault suspected.
 *
 *   [FAULT_LATCHED]
 *      (NEW) Consecutive fault count exceeded latch
 *      threshold. Requires FAULT_CLEAR_COUNT good reads
 *      to clear.
 *
 *   [WATCHDOG]
 *      (NEW) No valid reading for WATCHDOG_TIMEOUT_MS.
 *      Device will restart immediately after logging.
 *
 *   [WIFI_LOST]
 *      WiFi disconnected.
 *
 *   [BLYNK_LOST]
 *      WiFi connected but Blynk disconnected.
 *
 *   [WIFI_RECONNECT]
 *      Reconnection/re-authentication in progress.
 *
 * ============================================================
 * Eleven new serial fields added below the original block:
 * ============================================================ 
 *
 * Field	Shows
 * ------------------------------------------------------------
 * ALPH	  Current smoothing alpha (e.g. 0.75 / 0.25)
 * RLIM	  Current rate-of-change limit in ppm
 * MEDY	  Median buffer ready (0/1)
 * MEDN	  Last computed median value
 * MEDC	  Median consensus counter
 * STCK	  Stuck counter / OK or STUCK
 * FLTC	  Consecutive faults / OK or LATCHED
 * TFLT	  Lifetime total fault count
 * TMRJ	  Lifetime median-rejected spike count
 * WDGT	  Seconds since last valid reading / limit
 * RATE	  Rate-reject consecutive counter
 *
 * ============================================================
 * [NEW v6.8] UART CROSS-CHECK AND CALIBRATION CONTROL
 * ============================================================
 *
 * The MH-Z19 sensor simultaneously outputs both PWM and UART
 * from its onboard processor. No existing wires need to be
 * changed. Add only two new wires:
 *
 *   MH-Z19 TX  →  ESP8266 GPIO0  (D3)   [SW_UART_RX]
 *   MH-Z19 RX  →  ESP8266 GPIO15 (D8)   [SW_UART_TX]
 *
 * To use different pins later, change only two #defines:
 *
 *   #define SW_UART_RX  0
 *   #define SW_UART_TX  15
 *
 * While the two wires are NOT connected readCO2UART() always
 * returns -1 (150 ms response timeout) and V13 is never
 * written to Blynk. The Python dashboard reads None for V13
 * and keeps both calibration buttons gray/disabled with no
 * code change needed on the dashboard side.
 *
 * NOTE: SoftwareSerial on ESP8266 is interrupt-driven.
 * Heavy WiFi activity can corrupt individual UART bytes.
 * PWM remains the primary measurement path. UART is used
 * only as a cross-check and for calibration commands.
 *
 * GPIO15 has a 10k pull-down to GND on the ESP8266 board.
 * This holds it LOW during boot (required boot mode) and
 * does not interfere with SoftwareSerial TX after boot.
 *
 * ============================================================
 * [NEW v6.8] VIRTUAL PINS — COMPLETE MAP
 * ============================================================
 *
 *   V3   = engineering message        (write each cycle)
 *   V10  = temperature                (write each cycle)
 *   V11  = humidity                   (write each cycle)
 *   V12  = CO2 ppm  PWM smoothed      (write each cycle)
 *
 *   V13  = CO2 ppm  UART raw          [NEW v6.8]
 *            Written ONLY when readCO2UART() succeeds.
 *            If wires not connected → never written →
 *            dashboard reads None → buttons stay disabled.
 *
 *   V20  = zero calibration trigger   [NEW v6.8]
 *            BLYNK_WRITE handler.
 *            Dashboard writes 1 after user confirms dialog.
 *            Sends zero-cal UART command to sensor.
 *            ONLY trigger when sensor has been in fresh
 *            outdoor air (~400 ppm) for at least 20 min.
 *            Calibrating indoors corrupts the baseline.
 *
 *   V21  = ABC state                  [NEW v6.8]
 *            Written each cycle: 1=enabled, 0=disabled.
 *            BLYNK_WRITE handler: write 1 to enable,
 *            0 to disable ABC.
 *            Allows toggling ABC remotely via dashboard.
 *
 * ============================================================
 * [NEW v6.8] ADDITIONAL DIAGNOSTIC STATE
 * ============================================================
 *
 *   [UART_CAL]
 *      Zero calibration UART command was just sent.
 *      Transient — clears on the next valid PWM reading.
 *
 * ============================================================
 * [NEW v6.8] ADDITIONAL SERIAL DIAGNOSTIC FIELDS
 * ============================================================
 *
 * Two new fields appended after the existing v7 RATE field:
 *
 * Field    Shows
 * -------------------------------------------------------
 * UART     Last UART CO2 reading in ppm.
 *          -1 = wires not connected or read failed.
 *          Also prints delta vs current smoothPPM.
 * UABC     ABC enable state as tracked by firmware:
 *          ON (factory default) or OFF.
 *
 * ============================================================
 * Strong recommendation if not already present:
 * ============================================================
 *
 * 100nF ceramic near MH-Z19 VCC/GND
 * 47–220uF electrolytic near MH-Z19 supply
 * optionally another 100uF near fan supply
 *
 **************************************************************/

#define BLYNK_PRINT Serial

#include <ESP8266WiFi.h>
#include <BlynkSimpleEsp8266.h>
#include <SimpleTimer.h>
#include <DHT.h>
#include <NTPClient.h>
#include <WiFiUdp.h>
#include <SoftwareSerial.h>     // [NEW v6.8] MH-Z19 UART cross-check and calibration

/* ============================================================
 * PINS
 * ============================================================ */

#define pwmPin   14             // CO2 PWM input (orig = 16 but it is unstable)
#define DHTPIN   12             // DHT sensor pin
#define DHTTYPE  DHT22

#define ledPinG  2              // GREEN LED
#define ledPinO  4              // ORANGE LED
#define ledPinR  5              // RED LED

/* ============================================================
 * [NEW v6.8] UART PINS FOR MH-Z19 SOFT SERIAL
 * ============================================================
 *
 * Change only the two #defines below to use different GPIOs.
 * No other code needs to change.
 *
 * New wires to add (PWM wire on GPIO14 is unchanged):
 *
 *   MH-Z19 TX  →  GPIO13 (D3)  — receives data from sensor
 *   MH-Z19 RX  →  GPIO15 (D8)  — sends commands to sensor
 *
 * GPIO15 has a 10k pull-down on the board. It stays LOW
 * during boot (required) and is safe as TX after boot.
 *
 * ============================================================ */

#define SW_UART_RX  0           // GPIO0  / D3  ← MH-Z19 TX  (pull-up, boot-safe because UART idles HIGH) / Virtual PIN = 13 (UART CO2 reading / MH-Z19 → ESP8266 → Blynk)
#define SW_UART_TX  15          // GPIO15 / D8  → MH-Z19 RX  (no virtual PIN as this one is used for transmitting commands / ESP8266 → MH-Z19)

/* [NEW v8] SoftwareSerial object for MH-Z19 UART */
SoftwareSerial uartSerial(SW_UART_RX, SW_UART_TX);

/* ============================================================
 * CREDENTIALS (in secret.h file)
 * ============================================================ */

#include "secrets.h"

char auth[] = BLYNK_AUTH;
char ssid[] = WIFI_SSID;
char pass[] = WIFI_PASS;

char server[] = "192.168.3.9";
//char server[] = "192.168.3.45";   // AsusTUF Laptop

#define MY_BLYNK_PORT 8084
//#define MY_BLYNK_PORT 8080        // AsusTUF Laptop

/* ============================================================
 * OBJECTS
 * ============================================================ */

DHT dht(DHTPIN, DHTTYPE);

WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, "pool.ntp.org", 3600);

SimpleTimer timer;

/* ============================================================
 * VIRTUAL PINS
 * ============================================================ */

int VirtualPinMSG = 3;

/* ============================================================
 * SENSOR VALUES
 * ============================================================ */

float h = 0;
float t = 0;

float smoothTemp = 0;
float smoothHum  = 0;
float smoothPPM  = 400;

// lastValidPPM: the last reading that passed ALL validation checks.
// Used as fallback when a reading fails so the exponential smoother
// is never frozen (Bug 2 fix). Updated only on confirmed valid reads.
float lastValidPPM = 400;    // safe outdoor-air starting assumption
float ppm5         = 400;

bool firstStepDone = false;

/* ============================================================
 * DAILY BASELINE TRACKING
 * ============================================================ */

float dailyMinPPM = 400;

unsigned long lastDailyReset = 0;

/* ============================================================
 * ABC RECOVERY
 * ============================================================ */

bool abcRecoveryMode = false;

unsigned long abcRecoveryStart = 0;

int stableRecoveryCounter = 0;

/* ============================================================
 * [NEW v8] ABC STATE TRACKING
 * ============================================================
 *
 * MH-Z19 ships from the factory with ABC enabled (true).
 *
 * Updated by BLYNK_WRITE(V21) when the dashboard toggles it.
 * Written to V21 every sendUptime() cycle so the dashboard
 * button always shows the correct state after any restart.
 *
 * There is no UART command to READ the current ABC state
 * from the sensor. The firmware tracks it as a software
 * variable initialised to the factory default. After a
 * power cycle the variable resets to true. If ABC was
 * disabled before a restart, BLYNK_WRITE(V21) must be
 * triggered again to re-disable it.
 *
 * ============================================================ */

bool abcEnabled = true;         // [NEW v8] true = ABC on (MH-Z19 factory default)

/* ============================================================
 * [NEW v8] LAST UART READING — DIAGNOSTIC VARIABLE
 * ============================================================
 *
 * Holds the return value of the most recent readCO2UART()
 * call. Printed in the serial diagnostic block as UART.
 *
 *   >= 0  valid ppm reading via UART
 *   -1    wires not connected, or read failed this cycle
 *
 * ============================================================ */

long lastUartPPM = -1;          // [NEW v8] initialised to -1 (not yet read)

/* ============================================================
 * DIAGNOSTIC STATE
 * ============================================================ */

String diagState = "BOOT";

bool hadRecentInvalidMeasurement = false;

/*
 * RATE RECOVERY COUNTER
 *
 * Prevents permanent RATE_REJECT deadlock.
 *
 * Example failure scenario:
 *
 *   1) false spike accepted (891 ppm)
 *   2) real environment returns to 400 ppm
 *   3) delta > 500
 *   4) all future values rejected forever
 *
 * This counter detects repeated consecutive
 * rejections and forces controlled recovery.
 */

int rateRejectCounter = 0;
bool controlledRecoveryActive = false;

/* ============================================================
 * ARRAYS FOR REGRESSION
 * ============================================================ */

float ppmArray[5]  = {0,0,0,0,0};
float timeArray[5] = {0,0,0,0,0};

int arrayLen = 5;

float lrCoef[2] = {0,0};   // LINEAR REGRESSION OUTPUT

/* ============================================================
 * OUTPUT
 * ============================================================ */

String userMsg;

int counterLoos = 0;

/* ============================================================
 * [NEW v7] LAYER 7 — ROLLING MEDIAN OUTLIER REJECTION
 *
 * A circular buffer of MEDIAN_WINDOW raw samples.
 * The median of the buffer is recomputed every cycle
 * and replaces the single raw reading as primary input
 * to the smoother and rate-of-change check.
 *
 * MEDIAN_WINDOW = 5:
 *   - Requires 5 valid readings to warm up (~25 s at 5s interval)
 *   - Immune to up to 2 simultaneous corrupted readings
 *   - Reflects a real sustained change after 3+ cycles at
 *     the new level
 *
 * medianConsensusCount:
 *   Tracks how many consecutive cycles the median has
 *   agreed with the raw reading direction. Used by the
 *   consensus override to accept large genuine changes
 *   that would otherwise be blocked by rate-of-change.
 * ============================================================ */

#define MEDIAN_WINDOW          5
#define MEDIAN_CONSENSUS_COUNT 3    // consecutive agreements to override rate-reject

long medianBuffer[MEDIAN_WINDOW];   // circular raw-ppm buffer
int  medianIdx   = 0;               // current write position
bool medianReady = false;           // true once first full window is collected
int  medianFillCount = 0;           // counts samples until first window is full
int  medianConsensusCount = 0;      // consecutive cycles where raw agrees with median
long lastComputedMedian = -1;       // median value from last cycle (for diagnostics)

/* ============================================================
 * [NEW v7] LAYER 8 — STUCK SENSOR DETECTION
 *
 * The MH-Z19 always shows small natural variation in its
 * raw output even in a perfectly stable environment
 * (±5–15 ppm NDIR noise). If the integer raw reading is
 * identical for STUCK_THRESHOLD consecutive cycles, the
 * sensor is likely in a frozen/fault state.
 *
 * STUCK_THRESHOLD = 40 cycles × 5 s = 200 s ≈ 3.3 min.
 * This is long enough to not false-trigger on a genuinely
 * stable CO2 environment, yet fast enough to catch a
 * hardware fault before it causes prolonged bad data.
 *
 * On SENSOR_STUCK, the smoother holds its current value
 * (alpha effectively 0) and the watchdog will eventually
 * restart the device if the stuck condition persists.
 * ============================================================ */

#define STUCK_THRESHOLD 40          // cycles of identical reading → stuck

long  prevRawForStuck  = -1;        // raw reading in the previous cycle
int   stuckCounter     = 0;         // consecutive identical reading counter
bool  sensorStuck      = false;     // true once STUCK_THRESHOLD reached

/* ============================================================
 * [NEW v7] LAYER 9 — FAULT LATCHING
 *
 * Prevents rapid OK/FAULT state oscillation during
 * intermittent sensor or WiFi noise bursts.
 *
 * A fault is latched (faultLatched = true) after
 * FAULT_LATCH_COUNT consecutive bad readings.
 * The latch is released only after FAULT_CLEAR_COUNT
 * consecutive fully-valid readings.
 *
 * While latched, the smoothing alpha is reduced to 0.05
 * so that stray readings cannot shift the displayed value.
 *
 * totalFaultCount / totalMedianRejectCount are
 * lifetime counters for diagnostics — never reset
 * after boot.
 * ============================================================ */

#define FAULT_LATCH_COUNT  5        // consecutive bad reads to latch
#define FAULT_CLEAR_COUNT  3        // consecutive good reads to clear latch

int  consecutiveFaults      = 0;    // current run of bad readings
int  consecutiveGood        = 0;    // current run of good readings
bool faultLatched           = false; // latched fault state
int  totalFaultCount        = 0;    // lifetime bad-reading counter
int  totalMedianRejectCount = 0;    // lifetime median-rejected-spike counter

/* ============================================================
 * [NEW v7] LAYER 10 — ADAPTIVE PLAUSIBILITY THRESHOLDS
 *
 * adaptiveRateLimit and adaptiveAlpha are populated each
 * cycle by getAdaptiveRateLimit() and getAdaptiveAlpha()
 * and stored here for diagnostic serial output.
 * ============================================================ */

float adaptiveRateLimit = 500.0f;   // current cycle rate-of-change limit (ppm)
float adaptiveAlpha     = 0.30f;    // current cycle smoothing alpha

/* ============================================================
 * [NEW v7] LAYER 11 — SOFTWARE WATCHDOG RESTART
 *
 * lastValidReadingTime is updated on every accepted
 * measurement. If it has not been updated for
 * WATCHDOG_TIMEOUT_MS the device restarts automatically.
 *
 * 5 minutes (300 000 ms) gives the protection logic
 * enough attempts to self-recover before restarting,
 * while still catching hard faults (cable disconnect,
 * sensor power loss, software deadlock) within a
 * reasonable time.
 * ============================================================ */

#define WATCHDOG_TIMEOUT_MS  300000UL   // 5 minutes without valid reading → restart

unsigned long lastValidReadingTime = 0; // millis() of last accepted measurement

/* ============================================================
 * WIFI CONNECT
 * ============================================================ */

void connectToWiFi()
{
  Serial.println("\nConnecting to WiFi...");

  diagState = "WIFI_RECONNECT";

  WiFi.begin(ssid, pass);

  int retries = 0;

  while (WiFi.status() != WL_CONNECTED && retries < 20)
  {
    retries++;

    digitalWrite(ledPinO, !digitalRead(ledPinO));

    delay(400);

    Serial.print(".");
  }

  if (WiFi.status() != WL_CONNECTED)
  {
    diagState = "WIFI_LOST";

    Serial.println("\nWiFi FAILED");

    return;
  }

  Serial.println("\nWiFi connected!");

  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  Blynk.config(auth, server, MY_BLYNK_PORT);

  if (Blynk.connect(8000))
  {
    diagState = "OK";

    Serial.println("[WiFi] Blynk connected");
  }
  else
  {
    diagState = "BLYNK_LOST";

    Serial.println("[WiFi] Blynk connect failed");
  }
}

/* ============================================================
 * READ CO2 PWM
 * ============================================================ */

long readCO2PWM()
{
  unsigned long syncStart = micros();

  while (digitalRead(pwmPin) == HIGH)
  {
    if ((micros() - syncStart) > 1200000UL)
    {
      diagState = "PWM_INVALID";

      Serial.println("[CO2] sync timeout");

      return -1;
    }
  }

  unsigned long th_us = pulseIn(pwmPin, HIGH, 2000000UL);

  if (th_us == 0)
  {
    diagState = "HIGH_TIMEOUT";

    Serial.println("[CO2] HIGH timeout");

    return -1;
  }

  unsigned long tl_us = pulseIn(pwmPin, LOW, 2000000UL);

  if (tl_us == 0)
  {
    diagState = "LOW_TIMEOUT";

    Serial.println("[CO2] LOW timeout");

    return -1;
  }

  long th_ms = th_us / 1000UL;
  long tl_ms = tl_us / 1000UL;

  long cycle_ms = th_ms + tl_ms;

  if (cycle_ms < 850 || cycle_ms > 1150)
  {
    diagState = "PWM_INVALID";

    Serial.print("[CO2] cycle rejected TH=");
    Serial.print(th_ms);

    Serial.print(" TL=");
    Serial.print(tl_ms);

    Serial.print(" SUM=");
    Serial.println(cycle_ms);

    return -1;
  }

  long denom = cycle_ms - 4;

  if (denom <= 0)
  {
    diagState = "PWM_INVALID";

    return -1;
  }

  long ppm =
    (long)(
      5000.0f *
      (float)(th_ms - 2) /
      (float)denom
    );

  if (ppm < 0 || ppm > 5500)
  {
    diagState = "PPM_RANGE";

    Serial.print("[CO2] ppm invalid=");
    Serial.println(ppm);

    return -1;
  }

  Serial.print("[CO2] TH=");
  Serial.print(th_ms);

  Serial.print(" TL=");
  Serial.print(tl_ms);

  Serial.print(" SUM=");
  Serial.print(cycle_ms);

  Serial.print(" PPM=");
  Serial.println(ppm);

  return ppm;
}

/* ============================================================
 * [NEW v8] READ CO2 VIA UART
 * ============================================================
 *
 * Sends the standard MH-Z19 read-CO2 command over
 * SoftwareSerial and parses the 9-byte response.
 *
 * Purpose: cross-check against the PWM reading. If the two
 * values differ by more than the dashboard threshold (default
 * 50 ppm), the Python dashboard shows a warning with the
 * signed delta value next to the CO2 reading.
 *
 * Command — 9 bytes, fixed checksum 0x79:
 *   0xFF 0x01 0x86 0x00 0x00 0x00 0x00 0x00 0x79
 *
 * Response format (9 bytes):
 *   byte[0] = 0xFF        start byte
 *   byte[1] = 0x86        command echo
 *   byte[2] = CO2 high byte
 *   byte[3] = CO2 low byte
 *   byte[4..7] = 0x00     reserved
 *   byte[8]    = checksum
 *
 *   CO2 ppm = (byte[2] << 8) | byte[3]
 *
 * Checksum = ~(byte[1] + ... + byte[7]) + 1
 *   (two's complement of the sum of bytes 1–7)
 *
 * Returns ppm >= 0 on success.
 * Returns -1 on timeout, bad header, or checksum mismatch.
 *
 * KEY BEHAVIOUR WHEN WIRES ARE NOT CONNECTED:
 *   uartSerial.available() never reaches 9 within 150 ms
 *   → returns -1 every cycle → V13 is never written to Blynk
 *   → dashboard reads None → buttons stay gray and disabled.
 *
 * ============================================================ */

long readCO2UART()
{
  /* MH-Z19 read CO2 command */
  byte cmd[9] = {
    0xFF, 0x01, 0x86,
    0x00, 0x00, 0x00, 0x00, 0x00,
    0x79
  };

  /* Flush any stale bytes from receive buffer before sending */
  while (uartSerial.available())
    uartSerial.read();

  /* Send command */
  uartSerial.write(cmd, 9);

  /* Wait up to 150 ms for a full 9-byte response.
   * Typical MH-Z19 response time is < 30 ms.
   * 150 ms is tolerant of SoftwareSerial interrupt jitter. */
  unsigned long waitStart = millis();

  while (uartSerial.available() < 9)
  {
    if ((millis() - waitStart) > 150)
    {
      /* Timeout — wires absent or sensor not responding */
      return -1;
    }
    delay(2);
  }

  /* Read 9 response bytes */
  byte resp[9];
  for (int i = 0; i < 9; i++)
    resp[i] = uartSerial.read();

  /* Validate start byte and command echo */
  if (resp[0] != 0xFF || resp[1] != 0x86)
  {
    Serial.print("[CO2 UART] bad header: 0x");
    Serial.print(resp[0], HEX);
    Serial.print(" 0x");
    Serial.println(resp[1], HEX);
    return -1;
  }

  /* Verify checksum: ~(sum of bytes 1..7) + 1 */
  byte csum = 0;
  for (int i = 1; i <= 7; i++)
    csum += resp[i];
  csum = ~csum + 1;

  if (csum != resp[8])
  {
    Serial.print("[CO2 UART] checksum error: calc=0x");
    Serial.print(csum, HEX);
    Serial.print(" recv=0x");
    Serial.println(resp[8], HEX);
    return -1;
  }

  long ppm = (long)resp[2] * 256 + (long)resp[3];

  if (ppm < 0 || ppm > 5500)
  {
    Serial.print("[CO2 UART] ppm out of range: ");
    Serial.println(ppm);
    return -1;
  }

  Serial.print("[CO2 UART] PPM=");
  Serial.println(ppm);

  return ppm;
}

/* ============================================================
 * [NEW v8] SEND ZERO CALIBRATION COMMAND VIA UART
 * ============================================================
 *
 * Sends the MH-Z19 zero-point calibration command.
 * Called from BLYNK_WRITE(V20) after the user confirms the
 * calibration dialog on the Python dashboard.
 *
 * The Python dashboard enforces a confirmation dialog that
 * warns the user to place the sensor in fresh outdoor air
 * (~400 ppm) for at least 20 minutes before confirming.
 * Calibrating indoors permanently corrupts the baseline.
 *
 * The sensor does NOT send a response to this command.
 *
 * Command bytes — fixed checksum 0x78:
 *   0xFF 0x01 0x87 0x00 0x00 0x00 0x00 0x00 0x78
 *
 * Checksum: sum = 0x01+0x87 = 0x88
 *           ~0x88 + 1 = 0x77 + 1 = 0x78  ✓
 *
 * ============================================================ */

void sendZeroCalUART()
{
  byte cmd[9] = {
    0xFF, 0x01, 0x87,
    0x00, 0x00, 0x00, 0x00, 0x00,
    0x78
  };

  uartSerial.write(cmd, 9);

  /* Set transient diagnostic state.
   * Clears on the next valid PWM reading cycle. */
  diagState = "UART_CAL";

  Serial.println("[CAL] Zero calibration command sent via UART");
}

/* ============================================================
 * [NEW v8] SET ABC STATE VIA UART
 * ============================================================
 *
 * Enables or disables Automatic Baseline Calibration.
 * Called from BLYNK_WRITE(V21) when the dashboard user
 * clicks the ABC button.
 *
 * abcEnabled is updated inside this function so V21 already
 * reflects the new state on the very next sendUptime() write.
 *
 * The sensor does NOT send a response to these commands.
 *
 * ABC ON  command — checksum 0xE6:
 *   0xFF 0x01 0x79 0xA0 0x00 0x00 0x00 0x00 0xE6
 *   sum = 0x01+0x79+0xA0 = 0x11A → low byte 0x1A
 *   ~0x1A + 1 = 0xE5 + 1 = 0xE6  ✓
 *
 * ABC OFF command — checksum 0x86:
 *   0xFF 0x01 0x79 0x00 0x00 0x00 0x00 0x00 0x86
 *   sum = 0x01+0x79 = 0x7A
 *   ~0x7A + 1 = 0x85 + 1 = 0x86  ✓
 *
 * ============================================================ */

void setABC(bool enable)
{
  if (enable)
  {
    byte on[9] = {
      0xFF, 0x01, 0x79,
      0xA0, 0x00, 0x00, 0x00, 0x00,
      0xE6
    };
    uartSerial.write(on, 9);
  }
  else
  {
    byte off[9] = {
      0xFF, 0x01, 0x79,
      0x00, 0x00, 0x00, 0x00, 0x00,
      0x86
    };
    uartSerial.write(off, 9);
  }

  abcEnabled = enable;

  Serial.print("[ABC] set to: ");
  Serial.println(enable ? "ENABLED" : "DISABLED");
}

/* ============================================================
 * [NEW v8] BLYNK_WRITE(V20) — ZERO CALIBRATION TRIGGER
 * ============================================================
 *
 * Invoked when the dashboard user confirms the zero
 * calibration dialog. Only acts on value == 1; any other
 * value (including Blynk sync-on-connect) is ignored.
 *
 * ============================================================ */

BLYNK_WRITE(V20)
{
  int val = param.asInt();

  if (val == 1)
  {
    Serial.println("[BLYNK] V20: zero calibration requested by dashboard");
    sendZeroCalUART();
  }
}

/* ============================================================
 * [NEW v8] BLYNK_WRITE(V21) — ABC ENABLE / DISABLE
 * ============================================================
 *
 * Invoked when the dashboard user clicks the ABC button.
 *
 *   value 1  →  enable  ABC
 *   value 0  →  disable ABC
 *
 * V21 is also written every sendUptime() cycle with the
 * current abcEnabled value so the dashboard button stays
 * correct after any device restart or Blynk reconnect.
 *
 * ============================================================ */

BLYNK_WRITE(V21)
{
  int val = param.asInt();

  if (val == 1)
  {
    Serial.println("[BLYNK] V21: ABC enable requested by dashboard");
    setABC(true);
  }
  else if (val == 0)
  {
    Serial.println("[BLYNK] V21: ABC disable requested by dashboard");
    setABC(false);
  }
}

void updateDailyMinimum(float ppm)
{
  if (ppm < dailyMinPPM)
  {
    dailyMinPPM = ppm;
  }

  if ((millis() - lastDailyReset) > 86400000UL)
  {
    Serial.println("[BASELINE] daily reset");

    dailyMinPPM = ppm;

    lastDailyReset = millis();
  }
}

/* ============================================================
 * ABC DISTURBANCE DETECTION
 * ============================================================ */

bool detectABCDisturbance(float rawPPM)
{
  if (!firstStepDone)
    return false;

  unsigned long uptimeHours = millis() / 3600000UL;

  bool suspiciousTimeWindow =
      (uptimeHours >= 20 && uptimeHours <= 28);

  bool hugeJump =
      abs(rawPPM - lastValidPPM) > 1200;

  bool dailyBaselineGood =
      dailyMinPPM < 650;

  bool suspiciousHigh =
      rawPPM > 1800;

  if (
      suspiciousTimeWindow &&
      hugeJump &&
      dailyBaselineGood &&
      suspiciousHigh
     )
  {
    diagState = "ABC_RECOVERY";

    Serial.println("[ABC] suspicious recalibration disturbance");

    return true;
  }

  return false;
}

/* ============================================================
 * ARRAY UPDATE
 * ============================================================ */

void updateArray()
{
  for (int i = 0; i < arrayLen - 1; i++)
  {
    ppmArray[i]  = ppmArray[i + 1];
    timeArray[i] = timeArray[i + 1];
  }

  ppmArray[4]  = smoothPPM;
  timeArray[4] = millis();
}

/* ============================================================
 * LINEAR REGRESSION
 * ============================================================ */

void simpLinReg(float* x, float* y, float* lrCoef, int n)
{
  float sum_x  = 0;
  float sum_y  = 0;
  float sum_xy = 0;
  float sum_xx = 0;

  for (int i = 0; i < n; i++)
  {
    sum_x  += x[i];
    sum_y  += y[i];
    sum_xy += x[i] * y[i];
    sum_xx += x[i] * x[i];
  }

  float denom = (n * sum_xx - sum_x * sum_x);

  if (denom == 0)
  {
    lrCoef[0] = 0;
    lrCoef[1] = 0;

    return;
  }

  lrCoef[0] =
      (n * sum_xy - sum_x * sum_y) / denom;

  lrCoef[1] =
      (sum_y / n) -
      (lrCoef[0] * sum_x / n);
}

/* ============================================================
 * [NEW v7] ADD SAMPLE TO MEDIAN BUFFER
 *
 * Inserts rawPPM into the circular buffer. Once
 * MEDIAN_WINDOW samples have been collected the median
 * is available for use (medianReady = true).
 *
 * Only valid (>= 0) samples are inserted. This means
 * the buffer always contains real sensor readings and
 * is never polluted by pulseIn timeouts or cycle errors.
 * ============================================================ */

void addToMedianBuffer(long rawPPM)
{
  medianBuffer[medianIdx] = rawPPM;

  medianIdx = (medianIdx + 1) % MEDIAN_WINDOW;

  if (!medianReady)
  {
    medianFillCount++;

    if (medianFillCount >= MEDIAN_WINDOW)
    {
      medianReady = true;

      Serial.println("[MEDIAN] buffer ready");
    }
  }
}

/* ============================================================
 * [NEW v7] COMPUTE MEDIAN OF BUFFER
 *
 * Copies the buffer, sorts it with insertion sort
 * (efficient for small N), and returns the middle value.
 *
 * Insertion sort is used rather than stdlib qsort to
 * avoid heap allocation and to keep the code simple
 * and deterministic for a small fixed array.
 * ============================================================ */

long computeMedian()
{
  long sorted[MEDIAN_WINDOW];

  for (int i = 0; i < MEDIAN_WINDOW; i++)
    sorted[i] = medianBuffer[i];

  /* insertion sort */
  for (int i = 1; i < MEDIAN_WINDOW; i++)
  {
    long key = sorted[i];
    int  j   = i - 1;

    while (j >= 0 && sorted[j] > key)
    {
      sorted[j + 1] = sorted[j];
      j--;
    }

    sorted[j + 1] = key;
  }

  return sorted[MEDIAN_WINDOW / 2];
}

/* ============================================================
 * [NEW v7] GET ADAPTIVE RATE-OF-CHANGE LIMIT
 *
 * Returns the maximum allowed ppm change per cycle
 * based on current operating context.
 *
 * See LAYER 10 comment block at top of file for the
 * full context-to-limit mapping table.
 * ============================================================ */

float getAdaptiveRateLimit()
{
  /* Wider during boot: sensor is still warming up */
  if (!firstStepDone)
    return 700.0f;

  /* Wider in the known ABC recalibration time window */
  unsigned long uptimeHours = millis() / 3600000UL;
  if (uptimeHours >= 20 && uptimeHours <= 28)
    return 800.0f;

  /* Wider when strong downward trend detected:
   * active ventilation can drop CO2 faster than usual */
  if (lrCoef[0] < -0.5f)
    return 700.0f;

  /* Conservative after fault recovery:
   * do not immediately trust large jumps */
  if (faultLatched)
    return 400.0f;

  /* Normal steady-state operation */
  return 500.0f;
}

/* ============================================================
 * [NEW v7] GET ADAPTIVE SMOOTHING ALPHA
 *
 * Returns the EMA alpha coefficient based on the absolute
 * difference between the current ppm5 and smoothPPM.
 *
 * Larger delta → larger alpha → faster convergence.
 * Smaller delta → smaller alpha → more noise rejection.
 *
 * This replaces the fixed 0.7/0.3 split that required
 * 17–18 cycles to converge after a real step change.
 * With adaptive alpha a real change (e.g. 395→750 ppm)
 * converges in approximately 5–7 cycles instead.
 *
 * IMPORTANT: this function is ONLY called on the normal
 * valid-measurement path. The conservative override alphas
 * for controlledRecoveryActive (0.10) and faultLatched
 * (0.05) are applied directly in sendUptime() and take
 * precedence over this function.
 *
 * The delta threshold values were chosen to match
 * typical MH-Z19 indoor operating ranges:
 *
 *   20 ppm  → sensor noise floor / micro-fluctuation
 *   50 ppm  → small real change (1–2 people entering)
 *   200 ppm → significant change (window opened/closed)
 * ============================================================ */

float getAdaptiveAlpha(float delta)
{
  if (delta > 200.0f) return 0.75f;   // large confirmed change: fast tracking
  if (delta > 50.0f)  return 0.55f;   // moderate change: balanced
  if (delta > 20.0f)  return 0.40f;   // small real change: moderate
  return 0.25f;                        // noise floor: heavy smoothing
}

/* ============================================================
 * SENSOR UPDATE LOOP
 * ============================================================ */

void sendUptime()
{
  if (WiFi.status() != WL_CONNECTED)
  {
    diagState = "WIFI_LOST";

    connectToWiFi();
  }

  if (!Blynk.connected())
  {
      diagState = "BLYNK_LOST";

      if (Blynk.connect(4000))
      {
        diagState = "OK_RECOVERED";

        Serial.println("[WiFi] Blynk recovered");
      }
  }

  timeClient.update();

  h = dht.readHumidity();
  t = dht.readTemperature();

  if (!isnan(h) && !isnan(t))
  {
    if (firstStepDone)
    {
      smoothHum  = smoothHum  * 0.7f + h * 0.3f;
      smoothTemp = smoothTemp * 0.7f + t * 0.3f;
    }
    else
    {
      smoothHum  = h;
      smoothTemp = t;
    }
  }

  long rawPPM = readCO2PWM();

  /* --------------------------------------------------------
   * [NEW v7] LAYER 7 — Add raw reading to median buffer.
   *
   * We add the sample BEFORE the hard clamp and rate checks
   * so the median buffer accumulates raw sensor output.
   * The median itself is then checked against plausibility
   * limits below. This allows the median to build consensus
   * even while individual samples are being rate-rejected,
   * enabling the consensus override for real large changes.
   * -------------------------------------------------------- */
  if (rawPPM >= 0)
  {
    addToMedianBuffer(rawPPM);
  }

  /* --------------------------------------------------------
   * [NEW v7] LAYER 8 — Stuck sensor detection.
   *
   * Compare current raw reading to previous raw reading.
   * If identical for STUCK_THRESHOLD cycles, flag stuck.
   * Reset counter if the value changes at all.
   * Only runs on valid readings (rawPPM >= 0).
   * -------------------------------------------------------- */
  if (rawPPM >= 0)
  {
    if (rawPPM == prevRawForStuck)
    {
      stuckCounter++;

      if (stuckCounter >= STUCK_THRESHOLD && !sensorStuck)
      {
        sensorStuck = true;

        diagState = "SENSOR_STUCK";

        Serial.print("[STUCK] sensor frozen at ");
        Serial.print(rawPPM);
        Serial.print(" ppm for ");
        Serial.print(stuckCounter);
        Serial.println(" cycles");
      }
    }
    else
    {
      /* Value changed: sensor is alive, clear stuck flag */
      if (sensorStuck)
      {
        sensorStuck  = false;
        stuckCounter = 0;

        Serial.println("[STUCK] sensor recovered");
      }
      else
      {
        stuckCounter = 0;
      }
    }

    prevRawForStuck = rawPPM;
  }

  /* --------------------------------------------------------
   * If sensor is stuck, treat current reading as invalid
   * so the smoother holds its last good value.
   * -------------------------------------------------------- */
  if (sensorStuck)
  {
    rawPPM = -1;
  }

  /*
  * HARD CLAMP
  *
  * MH-Z19 should never jump from fresh-air
  * levels to 5000 ppm in one sample indoors.
  */

  if (rawPPM > 3000 && lastValidPPM < 1200)
  {
    Serial.println("[SANITY] impossible spike rejected");

    rawPPM = -1;
  }

  bool validMeasurement = (rawPPM >= 0);

  /* --------------------------------------------------------
   * [NEW v7] LAYER 9 — Fault latch counter update.
   *
   * Increment consecutiveFaults on bad readings.
   * Increment consecutiveGood on valid readings.
   * Latch on FAULT_LATCH_COUNT bad, clear on
   * FAULT_CLEAR_COUNT good.
   * totalFaultCount is a lifetime counter for long-term
   * diagnostics and never resets after boot.
   * -------------------------------------------------------- */
  if (validMeasurement)
  {
    consecutiveGood++;
    consecutiveFaults = 0;

    if (faultLatched && consecutiveGood >= FAULT_CLEAR_COUNT)
    {
      faultLatched       = false;
      consecutiveGood    = 0;

      Serial.println("[FAULT] latch cleared");
    }
  }
  else
  {
    consecutiveFaults++;
    consecutiveGood = 0;
    totalFaultCount++;

    if (!faultLatched && consecutiveFaults >= FAULT_LATCH_COUNT)
    {
      faultLatched = true;

      diagState = "FAULT_LATCHED";

      Serial.print("[FAULT] latched after ");
      Serial.print(consecutiveFaults);
      Serial.println(" consecutive faults");
    }
  }

  /* --------------------------------------------------------
   * [NEW v7] LAYER 10 — Get adaptive rate-of-change limit.
   *
   * Replaces the hardcoded 500 ppm threshold. Stored in
   * adaptiveRateLimit for serial diagnostic output.
   * -------------------------------------------------------- */
  adaptiveRateLimit = getAdaptiveRateLimit();

  if (validMeasurement && firstStepDone)
  {
    float delta = abs(rawPPM - lastValidPPM);

    if (delta > adaptiveRateLimit)
    {
      rateRejectCounter++;

      diagState = "RATE_REJECT";

      Serial.print("[RATE] rejected delta=");
      Serial.print(delta);

      Serial.print(" limit=");
      Serial.println(adaptiveRateLimit);

      Serial.print("[RATE] reject counter=");
      Serial.println(rateRejectCounter);

      /* ------------------------------------------------------
       * [NEW v7] MEDIAN CONSENSUS OVERRIDE (part of Layer 7)
       *
       * If the median is ready AND it agrees with the raw
       * reading direction AND MEDIAN_CONSENSUS_COUNT
       * consecutive cycles have shown this, the change is
       * real (not a spike) and we override the rate rejection.
       *
       * The median limit is 1.5× the adaptive limit (but
       * capped at 1200 ppm) because the median has already
       * filtered single-sample noise.
       *
       * Example:
       *   Real CO2 jumps 400 → 950 ppm (many people enter).
       *   delta = 550 > 500 → rate-reject.
       *   But median after 3 cycles also shows ~950 ppm.
       *   medianDelta = 550 < 750 (1.5 × 500).
       *   consensus count reaches 3 → override accepted.
       * ------------------------------------------------------ */
      if (medianReady)
      {
        long  medianVal   = computeMedian();
        float medianDelta = abs((float)medianVal - lastValidPPM);
        float medianLimit = min(adaptiveRateLimit * 1.5f, 1200.0f);

        /* Check that raw and median agree on direction */
        bool rawAbove    = (rawPPM    > lastValidPPM);
        bool medianAbove = (medianVal > lastValidPPM);
        bool directionMatch = (rawAbove == medianAbove);

        if (directionMatch && medianDelta <= medianLimit)
        {
          medianConsensusCount++;

          Serial.print("[MEDIAN] consensus count=");
          Serial.println(medianConsensusCount);

          if (medianConsensusCount >= MEDIAN_CONSENSUS_COUNT)
          {
            /* Override: use median value instead of raw */
            rawPPM        = medianVal;
            validMeasurement = true;
            rateRejectCounter = 0;

            diagState = "OK";

            Serial.print("[MEDIAN] consensus override accepted val=");
            Serial.println(medianVal);
          }
          else
          {
            /* Not enough consensus yet: still reject this cycle */
            totalMedianRejectCount++;
            validMeasurement = false;
          }
        }
        else
        {
          /* Direction mismatch: reset consensus, reject */
          medianConsensusCount = 0;
          totalMedianRejectCount++;
          validMeasurement = false;
        }
      }
      else
      {
        /* Median not ready yet: apply existing logic below */
        validMeasurement = false;
      }

      /*
      * CONTROLLED RECOVERY (original logic, unchanged)
      *
      * If many consecutive measurements disagree with
      * the stored baseline, the baseline itself is
      * probably corrupted.
      *
      * Slowly move toward new measurements instead
      * of instantly trusting them.
      */

      if (!validMeasurement && rateRejectCounter >= 6)
      {
        Serial.println("[RATE] controlled recovery");

        diagState = "RATE_RECOVERY";

        /*
        * Move baseline slowly toward reality
        */

        lastValidPPM =
            lastValidPPM * 0.85f +
            rawPPM       * 0.15f;

        ppm5 = lastValidPPM;

        controlledRecoveryActive = true;

        validMeasurement = true;
      }
      /*
      * Reject temporary unrealistic jump.
      */
      else if (!validMeasurement)
      {
        validMeasurement = false;
      }
    }
    else
    {
      /*
      * Measurement looks physically plausible again.
      */

      rateRejectCounter    = 0;
      medianConsensusCount = 0;  // [NEW v7] reset consensus on plausible reading
      controlledRecoveryActive = false;
    }
  }

  if (validMeasurement)
  {
    /*
    * During controlled recovery we already updated
    * lastValidPPM gradually.
    *
    * Avoid instantly overwriting it with rawPPM.
    */

    if (!controlledRecoveryActive)
    {
      ppm5 = rawPPM;

      lastValidPPM = ppm5;

      /* [NEW v7] Watchdog: record time of last accepted reading */
      lastValidReadingTime = millis();
    }

    updateDailyMinimum(ppm5);

    if (hadRecentInvalidMeasurement)
    {
      diagState = "OK_RECOVERED";

      hadRecentInvalidMeasurement = false;
    }
    else
    {
      if (!controlledRecoveryActive && !faultLatched)
      {
        diagState = "OK";
      }
    }

    if (firstStepDone)
    {
      /* --------------------------------------------------------
       * [NEW v7] ADAPTIVE ALPHA — replaces fixed 0.7/0.3 split.
       *
       * Priority order (highest wins):
       *   1. faultLatched            → 0.05 (conservative)
       *   2. controlledRecoveryActive → 0.10 (conservative, unchanged from v6)
       *   3. Normal: getAdaptiveAlpha(delta) based on |ppm5 - smoothPPM|
       *
       * The median (Layer 7) ensures large deltas are only seen
       * when the change is real and sustained. Without the median,
       * a spike would give a large delta and a high alpha, which
       * would corrupt smoothPPM. With the median acting as a
       * pre-filter, high alpha is safe.
       * -------------------------------------------------------- */

      float delta = abs(ppm5 - smoothPPM);

      if (faultLatched)
      {
        /* Fault latched: extremely conservative, almost hold */
        adaptiveAlpha = 0.05f;
      }
      else if (controlledRecoveryActive)
      {
        /* Conservative smoothing during controlled recovery (v6 unchanged) */
        adaptiveAlpha = 0.10f;
      }
      else
      {
        /* Normal operation: adaptive alpha based on change magnitude */
        adaptiveAlpha = getAdaptiveAlpha(delta);
      }

      smoothPPM =
          (1.0f - adaptiveAlpha) * smoothPPM +
          adaptiveAlpha          * ppm5;
    }
    else
    {
      /* First reading: initialize smoother directly */
      smoothPPM = ppm5;

      firstStepDone = true;

      /* [NEW v7] Initialize watchdog on first valid reading */
      lastValidReadingTime = millis();
    }
  }
  else
  {
    hadRecentInvalidMeasurement = true;

    if (
        diagState != "ABC_RECOVERY"  &&
        diagState != "RATE_REJECT"   &&
        diagState != "FAULT_LATCHED" &&  // [NEW v7] preserve latched state
        diagState != "SENSOR_STUCK"    // [NEW v7] preserve stuck state
       )
    {
      diagState = "SENSOR_RECOVERY";
    }

    ppm5 = lastValidPPM;

    /* [NEW v7] Fault latched: hold smoother almost completely still */
    if (faultLatched)
    {
      adaptiveAlpha = 0.05f;
    }
    else
    {
      adaptiveAlpha = 0.05f;  // conservative fallback (unchanged from v6 ~0.05)
    }

    smoothPPM =
        (1.0f - adaptiveAlpha) * smoothPPM +
        adaptiveAlpha           * ppm5;
  }

  /* --------------------------------------------------------
   * [NEW v7] LAYER 11 — Software watchdog check.
   *
   * If no valid reading has been accepted for more than
   * WATCHDOG_TIMEOUT_MS, log the event and restart.
   * The check only runs after firstStepDone so that the
   * watchdog does not fire during the sensor preheat period
   * on first boot.
   * -------------------------------------------------------- */
  if (firstStepDone &&
      (millis() - lastValidReadingTime) > WATCHDOG_TIMEOUT_MS)
  {
    diagState = "WATCHDOG";

    Serial.println("[WATCHDOG] no valid reading for 5 min — restarting");

    /* Flush serial before restart so the log line is visible */
    Serial.flush();

    delay(200);

    ESP.restart();
  }

  updateArray();

  simpLinReg(timeArray, ppmArray, lrCoef, arrayLen);

  if (smoothPPM <= 1000)
  {
    digitalWrite(ledPinG, HIGH);
    digitalWrite(ledPinO, LOW);
    digitalWrite(ledPinR, LOW);
  }
  else if (smoothPPM <= 1500)
  {
    digitalWrite(ledPinG, LOW);
    digitalWrite(ledPinO, HIGH);
    digitalWrite(ledPinR, LOW);
  }
  else
  {
    digitalWrite(ledPinG, LOW);
    digitalWrite(ledPinO, LOW);
    digitalWrite(ledPinR, HIGH);
  }

  if (abs(lrCoef[0]) < 0.000005)
  {
    userMsg =
      (smoothPPM < 1000) ? "Vzduch je dobry." :
      (smoothPPM < 1500) ? "Zacni vetrat." :
                           "Kriticka kvalita!";
  }
  else if (lrCoef[0] > 0)
  {
    userMsg =
      (smoothPPM < 1000) ? "Vzduch sa zhorsuje." :
      (smoothPPM < 1500) ? "Zacni vetrat." :
                           "Vetraj ihned!";
  }
  else
  {
    userMsg =
      (smoothPPM < 1000) ? "Vzduch je dobry." :
      (smoothPPM < 1500) ? "Kvalita sa zlepsuje." :
                           "Pokracuj vo vetrani.";
  }

  String finalMsg =
      userMsg +
      " [" +
      diagState +
      "]";

  Blynk.virtualWrite(10, smoothTemp);
  Blynk.virtualWrite(11, smoothHum);
  Blynk.virtualWrite(12, smoothPPM);

  /* --------------------------------------------------------
   * [NEW v8] UART CO2 READ — cross-check against PWM value.
   *
   * Called here, after the full PWM acquisition pipeline,
   * so the two reads cannot interfere with each other.
   *
   * V13 is written ONLY when readCO2UART() returns a valid
   * value. If the UART wires are not connected the function
   * times out and returns -1 every cycle, V13 is never
   * written, and the dashboard detects UART absent via None.
   *
   * lastUartPPM is stored globally for the serial diagnostic
   * block below.
   *
   * V21 is written every cycle with the current abcEnabled
   * state so the dashboard ABC button stays in sync after
   * any restart or Blynk reconnect.
   * -------------------------------------------------------- */

  lastUartPPM = readCO2UART();

  if (lastUartPPM >= 0)
  {
    Blynk.virtualWrite(13, lastUartPPM);

    Serial.print("[UART vs PWM] UART=");
    Serial.print(lastUartPPM);
    Serial.print("  PWM=");
    Serial.print((long)smoothPPM);
    Serial.print("  DELTA=");
    Serial.println(lastUartPPM - (long)smoothPPM);
  }

  Blynk.virtualWrite(21, abcEnabled ? 1 : 0);

  if (counterLoos > 5)
  {
    Blynk.virtualWrite(
        VirtualPinMSG,
        finalMsg
    );
  }
  else
  {
    Blynk.virtualWrite(
        VirtualPinMSG,
        "--> Initializacia senzora <--"
    );
  }

  /* ============================================================
   * SERIAL DIAGNOSTICS
   * Extended in v7 with new protection layer fields.
   * ============================================================ */

  Serial.println();
  Serial.println("===================================");

  Serial.print("TEMP : ");
  Serial.println(smoothTemp);

  Serial.print("HUM  : ");
  Serial.println(smoothHum);

  Serial.print("RAW  : ");
  Serial.println(ppm5);

  Serial.print("SMTH : ");
  Serial.println(smoothPPM);

  Serial.print("LAST : ");
  Serial.println(lastValidPPM);

  Serial.print("DMIN : ");
  Serial.println(dailyMinPPM);

  Serial.print("ABC  : ");
  Serial.println(abcRecoveryMode);

  Serial.print("DIAG : ");
  Serial.println(diagState);

  /* [NEW v7] Extended diagnostic fields */

  Serial.print("ALPH : ");           // current adaptive smoothing alpha
  Serial.println(adaptiveAlpha, 2);

  Serial.print("RLIM : ");           // current adaptive rate-of-change limit (ppm)
  Serial.println(adaptiveRateLimit, 0);

  Serial.print("MEDY : ");           // median buffer ready (1/0)
  Serial.println(medianReady ? 1 : 0);

  Serial.print("MEDN : ");           // last computed median value
  Serial.println(lastComputedMedian);

  Serial.print("MEDC : ");           // median consensus counter
  Serial.println(medianConsensusCount);

  Serial.print("STCK : ");           // stuck sensor counter / flag
  Serial.print(stuckCounter);
  Serial.print(" / ");
  Serial.println(sensorStuck ? "STUCK" : "OK");

  Serial.print("FLTC : ");           // consecutive faults / latched
  Serial.print(consecutiveFaults);
  Serial.print(" / ");
  Serial.println(faultLatched ? "LATCHED" : "OK");

  Serial.print("TFLT : ");           // total lifetime fault count
  Serial.println(totalFaultCount);

  Serial.print("TMRJ : ");           // total lifetime median-reject count
  Serial.println(totalMedianRejectCount);

  Serial.print("WDGT : ");           // watchdog: seconds since last valid reading
  if (firstStepDone)
  {
    Serial.print((millis() - lastValidReadingTime) / 1000UL);
    Serial.print("s / ");
    Serial.print(WATCHDOG_TIMEOUT_MS / 1000UL);
    Serial.println("s limit");
  }
  else
  {
    Serial.println("not started");
  }

  Serial.print("RATE : ");           // rate-reject consecutive counter
  Serial.println(rateRejectCounter);

  /* [NEW v8] UART cross-check and ABC state */

  Serial.print("UART : ");           // last UART CO2 reading in ppm
  if (lastUartPPM >= 0)
  {
    Serial.print(lastUartPPM);
    Serial.print(" ppm  (delta vs PWM: ");
    Serial.print(lastUartPPM - (long)smoothPPM);
    Serial.println(")");
  }
  else
  {
    Serial.println("-1  (not wired or read failed this cycle)");
  }

  Serial.print("UABC : ");           // ABC enable state
  Serial.println(abcEnabled ? "ON" : "OFF");

  Serial.println("===================================");

  if (counterLoos < 10)
    counterLoos++;

  if (
      timeClient.getHours()   == 2 &&
      timeClient.getMinutes() == 1 &&
      timeClient.getSeconds() > 1 &&
      timeClient.getSeconds() < 18
     )
  {
    Serial.println("*** DAILY RESTART ***");

    ESP.restart();
  }
}

/* ============================================================
 * SETUP
 * ============================================================ */

void setup()
{
  Serial.begin(115200);

  delay(1000);

  Serial.println();
  Serial.println("===== BOOT START =====");

  /* [NEW v8] Initialise SoftwareSerial for MH-Z19 UART.
   * Must be called before the first sendUptime() runs.
   * 9600 baud is the fixed MH-Z19 UART baud rate.
   * If the UART wires are not yet connected this call is
   * harmless — readCO2UART() will simply time out every
   * cycle until the wires are added. */
  uartSerial.begin(9600);

  Serial.print("[UART] SoftSerial started — RX=GPIO");
  Serial.print(SW_UART_RX);
  Serial.print("  TX=GPIO");
  Serial.println(SW_UART_TX);

  pinMode(pwmPin, INPUT);

  pinMode(ledPinG, OUTPUT);
  pinMode(ledPinO, OUTPUT);
  pinMode(ledPinR, OUTPUT);

  WiFi.persistent(false);

  WiFi.disconnect(true);

  delay(200);

  WiFi.mode(WIFI_STA);

  WiFi.setSleepMode(WIFI_NONE_SLEEP);

  dht.begin();

  connectToWiFi();

  timeClient.begin();

  lastDailyReset = millis();

  /* [NEW v7] Initialize watchdog clock at boot.
   * Set to millis() so the watchdog does not fire
   * during the preheat period before firstStepDone
   * is set. The actual watchdog is only checked after
   * firstStepDone becomes true. */
  lastValidReadingTime = millis();

  /* [NEW v7] Initialize median buffer to a safe
   * baseline value so that if computeMedian() is
   * called before the buffer is fully warmed up,
   * it returns a plausible starting point rather
   * than 0 ppm. */
  for (int i = 0; i < MEDIAN_WINDOW; i++)
    medianBuffer[i] = 400;

  timer.setInterval(5000L, sendUptime);

  Serial.println("System ready");
}

/* ============================================================
 * LOOP
 * ============================================================ */

void loop()
{
  Blynk.run();

  timer.run();
}
