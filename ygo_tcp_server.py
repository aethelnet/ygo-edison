import socket
import struct
from loguru import logger

HOST = '0.0.0.0'
PORT = 7911

logger.info(f"🔥 Starte experimentellen YGOPro TCP Server auf {HOST}:{PORT}...")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind((HOST, PORT))
    s.listen()
    logger.info("Warte auf Stefans EDOPro Client (Sag ihm, er soll deine IP in EDOPro eingeben und verbinden)...")
    
    conn, addr = s.accept()
    with conn:
        logger.success(f"🌐 VERBINDUNG HERGESTELLT VON {addr}!")
        
        while True:
            # 1. Lese 2 Bytes für die Paketlänge (YGOPro Standard)
            length_bytes = conn.recv(2)
            if not length_bytes:
                break
                
            length = struct.unpack('<H', length_bytes)[0]
            logger.debug(f"Empfange Paket mit Länge: {length} Bytes")
            
            # 2. Lese den eigentlichen Payload
            payload = conn.recv(length)
            if not payload:
                break
                
            msg_type = payload[0]
            
            # Versuch, Text aus dem Payload zu extrahieren (z.B. Spielername oder Passwort)
            try:
                # EDOPro nutzt UTF-16LE für Strings
                text = payload[1:].decode('utf-16le', errors='ignore').strip('\x00')
                text_display = f" | Text: {text}" if text else ""
            except:
                text_display = ""
                
            logger.info(f"📦 [CTOS] Message Type: {hex(msg_type)} | Raw: {payload.hex()}{text_display}")
            
            # Wenn Stefan den Namen schickt (CTOS_PLAYER_INFO = 0x10)
            if msg_type == 0x10:
                logger.warning(f"Stefans Client hat 'Player Info' geschickt!")
            
            # Wenn Stefan dem Raum beitreten will (CTOS_JOIN_GAME = 0x11)
            elif msg_type == 0x11:
                logger.warning(f"Stefans Client will dem Spiel beitreten!")
                # Hier müssten wir jetzt theoretisch mit STOC_JOIN_GAME antworten,
                # aber für den ersten Test lassen wir es crashen, um die Pakete zu sehen!
                break
                
        logger.error("Verbindung geschlossen. (Erwartet, da wir nicht geantwortet haben).")
