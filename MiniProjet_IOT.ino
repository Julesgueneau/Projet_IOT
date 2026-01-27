#include <WiFi.h>

// UART LoRa-E5
#define RX_PIN 16
#define TX_PIN 17
HardwareSerial LoRaSerial(1);

#define MAX_AP 3

// --- TES IDENTIFIANTS ---
String AppEUI = "0000000000000000"; 
String DevEUI = "70B3D57ED0073085";
String AppKey = "49ABA6DBD8239573799302CDE0624A3B";

String toHex(uint8_t value) {
    char buffer[3];
    sprintf(buffer, "%02X", value);
    return String(buffer);
}

String sendAT(String command, int timeout) {
    String response = "";
    while(LoRaSerial.available()) LoRaSerial.read(); // Flush
    
    LoRaSerial.println(command);
    Serial.println("[ESP -> LoRa] " + command);
    
    unsigned long start = millis();
    while (millis() - start < timeout) {
        while (LoRaSerial.available()) {
            char c = LoRaSerial.read();
            response += c;
        }
    }
    Serial.println("[LoRa -> ESP] " + response);
    return response;
}

void setup() {
    Serial.begin(115200);
    LoRaSerial.begin(9600, SERIAL_8N1, RX_PIN, TX_PIN);
    delay(2000);
    
    Serial.println("\n--- DÉMARRAGE V7 (JOIN BLOQUANT) ---");

    // 1. Reset Module 
    sendAT("AT+RESET", 3000);
    delay(1000);

    // 2. Configuration
    sendAT("AT+ID=DevEui,\"" + DevEUI + "\"", 1000);
    sendAT("AT+ID=AppEui,\"" + AppEUI + "\"", 1000);
    sendAT("AT+KEY=APPKEY,\"" + AppKey + "\"", 1000);
    sendAT("AT+DR=EU868", 1000);
    sendAT("AT+MODE=LWOTAA", 1000);

    // 3. BOUCLE DE CONNEXION (On ne sort pas d'ici tant que pas connecté)
    bool connected = false;
    while (!connected) {
        Serial.println(">>> Tentative de JOIN en cours...");
        String joinStatus = sendAT("AT+JOIN", 15000); // 15 secondes d'attente

        // Analyse de la réponse
        if (joinStatus.indexOf("Network joined") != -1 || 
            joinStatus.indexOf("Joined") != -1 || 
            (joinStatus.indexOf("Done") != -1 && joinStatus.indexOf("failed") == -1)) {
            
            Serial.println("SUCCÈS : Connecté à TTN !");
            connected = true;
        } else {
            Serial.println("ECHEC JOIN. Nouvelle tentative dans 10s...");
            Serial.println("   (Vérifie: Antenne branchée ? Couverture réseau ?)");
            delay(10000);
        }
    }

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
}

void loop() {
    // on est connecté 
    Serial.println("\n--- Scan WiFi ---");
    int nb = WiFi.scanNetworks(false, false); 
    
    if (nb > 0) {
        String payload = "";
        int count = 0;

        for (int i = 0; i < nb; i++) {
            if (count >= MAX_AP) break;
            const uint8_t *mac = WiFi.BSSID(i);
            
            // Note: On envoie tout pour le test
            for (int k = 0; k < 6; k++) payload += toHex(mac[k]);
            payload += toHex((uint8_t)WiFi.RSSI(i));
            count++;
        }
        
        Serial.println("Envoi LoRa : " + payload);
        
        // Envoi avec vérification sommaire
        String sendStatus = sendAT("AT+MSGHEX=\"" + payload + "\"", 10000);
        
        if (sendStatus.indexOf("Please join") != -1) {
            Serial.println("PERTE DE CONNEXION DETECTÉE ! Reset nécessaire.");
            ESP.restart(); // On redémarre l'ESP pour re-join
        }
        
    } else {
        Serial.println("Aucun réseau WiFi.");
    }

    Serial.println("Pause 30s...");
    delay(30000);
}