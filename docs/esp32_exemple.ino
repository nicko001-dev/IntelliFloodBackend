/*
 * Exemple ESP32 + 2 capteurs HC-SR04 -> POST /api/v1/measurements (IntelliFlood).
 * NON TESTÉ sur matériel : à adapter (broches, Wi-Fi, URL, clé d'API du point).
 * Le serveur filtre les lectures aberrantes, on envoie donc les lectures brutes de plusieurs mesures.
 * En production : utiliser HTTPS (WiFiClientSecure) plutôt que HTTP en clair.
 */
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "VOTRE_WIFI";
const char* WIFI_PASS = "MOT_DE_PASSE";
const char* API_URL   = "http://192.168.1.10:8000/api/v1/measurements";
const char* API_KEY   = "if_xxx";          // clé affichée à la création du point
const char* POINT_CODE = "COC-001";

const int TRIG_AMONT = 5, ECHO_AMONT = 18;  // capteur amont (niveau d'eau)
const int TRIG_AVAL  = 19, ECHO_AVAL = 21;  // capteur aval (blocage)
const int N_READINGS = 5;
const unsigned long PERIOD_MS = 30000;

float readCm(int trig, int echo) {
  digitalWrite(trig, LOW); delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10); digitalWrite(trig, LOW);
  long us = pulseIn(echo, HIGH, 30000);
  return us == 0 ? 0 : us * 0.0343 / 2.0;     // 0 = pas d'écho : sera écarté par le serveur
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_AMONT, OUTPUT); pinMode(ECHO_AMONT, INPUT);
  pinMode(TRIG_AVAL, OUTPUT);  pinMode(ECHO_AVAL, INPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
}

void loop() {
  String readings = "[";
  float aval = 0;
  for (int i = 0; i < N_READINGS; i++) {
    readings += String(readCm(TRIG_AMONT, ECHO_AMONT), 1);
    if (i < N_READINGS - 1) readings += ",";
    aval += readCm(TRIG_AVAL, ECHO_AVAL);
    delay(60);
  }
  readings += "]";
  aval /= N_READINGS;

  String body = String("{\"point_code\":\"") + POINT_CODE + "\",\"readings_cm\":" + readings;
  if (aval >= 2) body += ",\"distance_aval_cm\":" + String(aval, 1);
  body += "}";

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(API_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-API-Key", API_KEY);
    int code = http.POST(body);
    Serial.printf("POST -> %d\n", code);
    http.end();
  }
  delay(PERIOD_MS);
}
