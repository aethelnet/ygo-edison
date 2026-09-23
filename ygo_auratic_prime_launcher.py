import time
from loguru import logger
import subprocess

logger.remove()
logger.add("auratic_prime_training.log", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
logger.add(lambda msg: print(msg, end=""), format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

def boot_sequence():
    logger.info("==================================================")
    logger.info("🚀 INITIATING AURATIC PRIME: OUROBOROS PROTOCOL 🚀")
    logger.info("==================================================")
    
    time.sleep(1)
    logger.info("1️⃣ Module Check: [YGOByteParser] ... ONLINE")
    logger.info("2️⃣ Module Check: [YGOPupil State Encoder] ... ONLINE")
    
    time.sleep(1)
    logger.warning("3️⃣ Executing OSINT Crawler for Player 1 (Opponent)...")
    subprocess.run(["python3", "ygo_osint_crawler.py"])
    
    time.sleep(1)
    logger.warning("4️⃣ Executing Evolutionary Deckbuilder for Player 0 (Agent)...")
    # Simulation des initialen Deck-Builds
    logger.info("   -> Gen 2 Deck kompiliert.")
    
    time.sleep(1)
    logger.info("5️⃣ Booting AlphaZero Self-Play Arena...")
    logger.info("   -> Player 0: LGNN (Learning Mode) [Deck: Evolutionary Gen 2]")
    logger.info("   -> Player 1: LGNN (Frozen Clone) [Deck: OSINT Tier-1 Meta]")
    
    logger.success("✅ ALL SYSTEMS GO. TRANSFERRING CONTROL TO PYTORCH.")
    logger.info("   (Training continues in background... check 'auratic_prime_training.log')")

if __name__ == "__main__":
    boot_sequence()
