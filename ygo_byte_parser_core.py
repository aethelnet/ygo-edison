import struct
from loguru import logger

class YGOByteParser:
    def __init__(self):
        # Zeiger, der angibt, wo wir uns gerade im Bytestream befinden
        self.offset = 0
        
        # Die wichtigsten C++ Message Codes von EDOPro
        self.MSG = {
            1: "MSG_RETRY",
            11: "MSG_DRAW",
            15: "MSG_SELECT_IDLECMD",
            16: "MSG_SELECT_EFFECTYN",
            20: "MSG_NEW_TURN",
            21: "MSG_NEW_PHASE",
            50: "MSG_MOVE",
            90: "MSG_START"
        }

    def read_byte(self, buffer):
        """Liest 1 Byte (z.B. Player-ID oder Message-Type)"""
        val = struct.unpack_from("B", buffer, self.offset)[0]
        self.offset += 1
        return val

    def read_int32(self, buffer):
        """Liest 4 Bytes (z.B. Karten-Passcode / ATK Werte)"""
        val = struct.unpack_from("I", buffer, self.offset)[0]
        self.offset += 4
        return val

    def parse_buffer(self, buffer, length):
        """Die Haupt-Schleife, die den C++ Speicherblock frisst"""
        self.offset = 0
        parsed_events = []
        
        logger.info(f"🔍 Parser startet (Puffergröße: {length} Bytes)...")
        
        while self.offset < length:
            msg_code = self.read_byte(buffer)
            msg_name = self.MSG.get(msg_code, f"UNKNOWN_{msg_code}")
            
            if msg_name == "MSG_DRAW":
                player = self.read_byte(buffer)
                count = self.read_byte(buffer)
                cards_drawn = [self.read_int32(buffer) for _ in range(count)]
                parsed_events.append({"event": "DRAW", "player": player, "cards": cards_drawn})
                logger.info(f"🃏 {msg_name}: Spieler {player} zieht {count} Karte(n) -> {cards_drawn}")
                
            elif msg_name == "MSG_NEW_TURN":
                player = self.read_byte(buffer)
                parsed_events.append({"event": "NEW_TURN", "player": player})
                logger.success(f"🔄 {msg_name}: Spieler {player} ist jetzt am Zug.")
                
            elif msg_name == "MSG_SELECT_IDLECMD":
                # Das ist die wichtigste Nachricht: Die KI MUSS jetzt eine Entscheidung treffen
                player = self.read_byte(buffer)
                
                # Wie viele Monster dürfen beschworen werden?
                summon_count = self.read_byte(buffer)
                for _ in range(summon_count):
                    self.read_int32(buffer) # ID überspringen
                    self.read_byte(buffer)  # Position überspringen
                    
                # Wie viele Effekte dürfen aktiviert werden?
                activate_count = self.read_byte(buffer)
                for _ in range(activate_count):
                    self.read_int32(buffer)
                    self.read_byte(buffer)
                
                # Wir skippen den Rest für den Mock, aber hier merkt der Parser, dass Aktion nötig ist
                parsed_events.append({"event": "ACTION_REQUIRED", "summonable": summon_count, "activatable": activate_count})
                logger.warning(f"⚠️ {msg_name}: KI muss handeln! ({summon_count} Beschwörungen, {activate_count} Effekte möglich)")
                
                # IdleCmd ist eine blockierende Message. Wir müssen den Loop hier stoppen und auf die KI warten!
                break
                
            else:
                # Unbekannte oder unwichtige Message -> Wir skippen einfach weiter
                # In der Realität müssten wir wissen, wie lang jede Message ist, um den Offset korrekt zu erhöhen.
                # Für diesen Prototyp brechen wir sicherheitshalber ab.
                logger.debug(f"Überspringe restliche Bytes von {msg_name}...")
                break
                
        return parsed_events

if __name__ == "__main__":
    parser = YGOByteParser()
    
    # Wir faken hier einen rohen C++ Bytestream, wie ihn die Engine bei einem neuen Zug senden würde:
    # [MSG_NEW_TURN (20), Player (0)] + [MSG_DRAW (11), Player (0), Count (1), CardID (89631139)] + [MSG_SELECT_IDLECMD (15)...]
    fake_cpp_buffer = bytearray()
    
    # 1. NEW TURN
    fake_cpp_buffer.extend(struct.pack("B", 20)) # MSG_NEW_TURN
    fake_cpp_buffer.extend(struct.pack("B", 0))  # Player 0
    
    # 2. DRAW
    fake_cpp_buffer.extend(struct.pack("B", 11)) # MSG_DRAW
    fake_cpp_buffer.extend(struct.pack("B", 0))  # Player 0
    fake_cpp_buffer.extend(struct.pack("B", 1))  # 1 Card
    fake_cpp_buffer.extend(struct.pack("I", 89631139)) # Blue-Eyes ID
    
    # 3. IDLECMD (Main Phase)
    fake_cpp_buffer.extend(struct.pack("B", 15)) # MSG_SELECT_IDLECMD
    fake_cpp_buffer.extend(struct.pack("B", 0))  # Player 0
    fake_cpp_buffer.extend(struct.pack("B", 1))  # 1 Beschwörung möglich
    fake_cpp_buffer.extend(struct.pack("I", 89631139)) # ID
    fake_cpp_buffer.extend(struct.pack("B", 0))  # Pos
    fake_cpp_buffer.extend(struct.pack("B", 0))  # 0 Effekte möglich
    
    events = parser.parse_buffer(fake_cpp_buffer, len(fake_cpp_buffer))
    
    print("\n--- Python Dictionary für das LGNN ---")
    for e in events:
        print(e)
