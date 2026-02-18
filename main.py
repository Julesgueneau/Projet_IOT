from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
import sqlite3
import uvicorn
import base64
import json

# --- CONFIGURATION ---
DB_NAME = "wifi.db"

app = FastAPI(title="Polytech IoT Geolocation")

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# --- INITIALISATION TABLE ---
def init_tables():
    conn = get_db_connection()
    # On crée juste l'historique
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL,
            lon REAL,
            nb_bornes INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

init_tables()

# --- ALGO BARYCENTRE ---
def calculate_position(ap_data):
    if not ap_data: return None
    lat_num = 0.0
    lon_num = 0.0
    weight_den = 0.0
    for lat, lon, rssi in ap_data:
        # Poids pour favoriser les bornes proches (RSSI fort)
        val = abs(rssi) if abs(rssi) > 0 else 100
        weight = 1.0 / (val ** 2)
        
        lat_num += lat * weight
        lon_num += lon * weight
        weight_den += weight
        
    if weight_den == 0: return None
    return (lat_num / weight_den, lon_num / weight_den)

# --- WEBHOOK TTN ---
@app.post("/ttn-webhook")
async def ttn_uplink(request: Request):
    try:
        body = await request.json()
        raw_payload_b64 = body.get("uplink_message", {}).get("frm_payload")
        
        if not raw_payload_b64: return {"status": "ignored"}

        payload_bytes = base64.b64decode(raw_payload_b64)
        
        chunk_size = 7
        if len(payload_bytes) % chunk_size != 0: return {"status": "error_len"}

        nb_ap = len(payload_bytes) // chunk_size
        detected_aps = []

        # Décodage du binaire LoRa
        for i in range(nb_ap):
            offset = i * chunk_size
            mac_bytes = payload_bytes[offset : offset+6]
            rssi_byte = payload_bytes[offset+6]
            
            mac_str = ":".join("{:02x}".format(b) for b in mac_bytes)
            rssi_val = rssi_byte if rssi_byte < 128 else rssi_byte - 256
            
            detected_aps.append((mac_str, rssi_val))

        print(f"Reçu de TTN : {detected_aps}")

        conn = get_db_connection()
        valid_aps_for_calc = []

        for mac, rssi in detected_aps:
            query = "SELECT lat, lon FROM wiglenetwork WHERE lower(mac) = lower(?)"
            row = conn.execute(query, (mac,)).fetchone()
            
            if row:
                valid_aps_for_calc.append((row['lat'], row['lon'], rssi))
                print(f"   Match BDD: {mac} -> ({row['lat']}, {row['lon']})")
            else:
                print(f"   Inconnu BDD: {mac}")

        if valid_aps_for_calc:
            final_pos = calculate_position(valid_aps_for_calc)
            if final_pos:
                est_lat, est_lon = final_pos
                conn.execute("INSERT INTO user_positions (lat, lon, nb_bornes) VALUES (?, ?, ?)",
                             (est_lat, est_lon, len(valid_aps_for_calc)))
                conn.commit()
                print(f" POSITION CALCULÉE ET SAUVEGARDÉE : {est_lat}, {est_lon}")
        else:
            print(" Aucune borne connue trouvée dans wiglenetwork.")
        
        conn.close()
        return {"status": "success"}

    except Exception as e:
        print(f"ERREUR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- FRONTEND (Tableau Interactif + Heure Locale) ---
@app.get("/", response_class=HTMLResponse)
async def read_map():
    conn = get_db_connection()
    
    # Récupération avec conversion 'localtime' pour avoir l'heure française
    query = """
        SELECT id, lat, lon, datetime(timestamp, 'localtime') as timestamp, nb_bornes 
        FROM user_positions 
        ORDER BY id DESC 
        LIMIT 50
    """
    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        return "<h2>En attente de données...</h2><script>setTimeout(()=>location.reload(), 3000)</script>"

    points_data = []
    table_rows = ""
    
    for row in rows:
        rid, lat, lon, time, nb = row['id'], row['lat'], row['lon'], row['timestamp'], row['nb_bornes']
        
        # JSON pour le JS
        points_data.append({"id": rid, "lat": lat, "lon": lon, "time": str(time), "nb": nb})
        
        # Ligne HTML cliquable
        table_rows += f"""
        <tr onclick="focusPoint({lat}, {lon}, {rid})" style="cursor: pointer;">
            <td>{time}</td>
            <td>{lat:.5f}</td>
            <td>{lon:.5f}</td>
            <td>{nb}</td>
        </tr>
        """

    js_points_json = json.dumps(points_data)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Suivi Polytech IoT</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <style>
            body {{ margin: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; flex-direction: column; height: 100vh; }}
            #map {{ flex: 2; width: 100%; }}
            #panel {{ flex: 1; overflow-y: auto; background: #f8f9fa; border-top: 3px solid #007bff; }}
            
            table {{ width: 100%; border-collapse: collapse; }}
            th {{ background-color: #343a40; color: white; padding: 12px; position: sticky; top: 0; }}
            td {{ padding: 10px; border-bottom: 1px solid #ddd; text-align: center; }}
            
            tr:hover {{ background-color: #b8daff !important; font-weight: bold; }}
            tr:nth-child(even) {{ background-color: #e9ecef; }}
        </style>
        <meta http-equiv="refresh" content="15"> 
    </head>
    <body>
        <div id="map"></div>
        
        <div id="panel">
            <h3 style="padding-left: 10px; margin: 10px 0;">Historique (Cliquez sur une ligne pour zoomer)</h3>
            <table>
                <thead><tr><th>Heure</th><th>Latitude</th><th>Longitude</th><th>Bornes</th></tr></thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>

        <script>
            var points = {js_points_json};
            var map = L.map('map');
            var markers = {{}}; 

            if (points.length > 0) {{
                // Vue initiale sur le dernier point
                map.setView([points[0].lat, points[0].lon], 18);

                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    maxZoom: 19,
                    attribution: '© OpenStreetMap'
                }}).addTo(map);

                points.forEach(function(p) {{
                    var marker = L.circleMarker([p.lat, p.lon], {{
                        color: 'red',
                        fillColor: '#f03',
                        fillOpacity: 0.8,
                        radius: 8
                    }}).addTo(map)
                    .bindPopup("<b>" + p.time + "</b><br>Lat: " + p.lat + "<br>Lon: " + p.lon);
                    
                    markers[p.id] = marker;
                }});
            }} else {{
                document.getElementById('map').innerHTML = "<h2 style='text-align:center; padding-top:20px;'>Pas de données</h2>";
            }}

            function focusPoint(lat, lon, id) {{
                // Animation de zoom vers le point sélectionné
                map.flyTo([lat, lon], 19, {{
                    animate: true,
                    duration: 1.5
                }});
                
                if(markers[id]) {{
                    markers[id].openPopup();
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html_content

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
