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
 * v. 5/28/2026 by AM
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
 * 6-LAYER PROTECTION ARCHITECTURE
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
 *      Protection system detected deadlock and recovered automatically
 *
 *   [ABC_RECOVERY]
 *      Temporary ABC recalibration disturbance suppression.
 *
 *   [SENSOR_RECOVERY]
 *      Temporary invalid reads but fallback stabilization active.
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
 * DAILY MINIMUM TRACKER
 * ============================================================ */

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

  if (validMeasurement && firstStepDone)
  {
    float delta = abs(rawPPM - lastValidPPM);

    if (delta > 500)
    {
      rateRejectCounter++;

      diagState = "RATE_REJECT";

      Serial.print("[RATE] rejected delta=");
      Serial.println(delta);

      Serial.print("[RATE] reject counter=");
      Serial.println(rateRejectCounter);

      /*
      * CONTROLLED RECOVERY
      *
      * If many consecutive measurements disagree with
      * the stored baseline, the baseline itself is
      * probably corrupted.
      *
      * Slowly move toward new measurements instead
      * of instantly trusting them.
      */

      if (rateRejectCounter >= 6)
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
      else
      {
        validMeasurement = false;
      }
    }
    else
    {
      /*
      * Measurement looks physically plausible again.
      */

      rateRejectCounter = 0;

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
    }

    updateDailyMinimum(ppm5);

    if (hadRecentInvalidMeasurement)
    {
      diagState = "OK_RECOVERED";

      hadRecentInvalidMeasurement = false;
    }
    else
    {
      if (!controlledRecoveryActive)
      {
        diagState = "OK";
      }
    }

    if (firstStepDone)
    {
      /*
      * Conservative smoothing during recovery.
      */

      if (controlledRecoveryActive)
      {
        smoothPPM =
            smoothPPM * 0.90f +
            ppm5      * 0.10f;
      }
      else
      {
        smoothPPM =
            smoothPPM * 0.7f +
            ppm5      * 0.3f;
      }
    }
    else
    {
      smoothPPM = ppm5;

      firstStepDone = true;
    }
  }
  else
  {
    hadRecentInvalidMeasurement = true;

    if (
        diagState != "ABC_RECOVERY" &&
        diagState != "RATE_REJECT"
       )
    {
      diagState = "SENSOR_RECOVERY";
    }

    ppm5 = lastValidPPM;

    smoothPPM =
        smoothPPM * 0.95f +
        ppm5      * 0.05f;
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
