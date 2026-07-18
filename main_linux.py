import os
import subprocess
import time
import sys
import redis


os.environ["CUDA_HOME"] = "/usr/local/cuda"
os.environ["PATH"] = "/usr/local/cuda/bin:" + os.environ.get("PATH", "")
os.environ["LD_LIBRARY_PATH"] = "/usr/local/cuda/lib64:" + os.environ.get("LD_LIBRARY_PATH", "")


# 1. PFADE FÜR LINUX ANPASSEN (WSL2 greift über /mnt/ auf deine Windows-Platten zu)
LLM_ORDNER = "/mnt/f/AI_Bot/src/Backend/llm_config"
BOT_ORDNER = "/mnt/f/AI_Bot/Discord_bot"

print("🚀 Starte System unter WSL2...")

# 2. REDIS-CHECK (Wir prüfen, ob dein Linux-Redis-Dienst aktiv ist)
try:
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True, socket_timeout=3.0, protocol=2)
    r.ping() # Testet die Verbindung
    print("✅ Linux-Redis-Dienst läuft und ist bereit.")
except redis.exceptions.ConnectionError:
    print("❌ Fehler: Der Linux-Redis-Server läuft im Hintergrund noch nicht!")
    print("💡 Bitte tippe zuerst im WSL-Terminal ein: sudo service redis-server start")
    sys.exit(1)

try:
    # Test-Eintrag in die Datenbank schreiben
    r.set('projekt', 'Läuft jetzt mit 5080-Lichtgeschwindigkeit in WSL2!')
    print("Meldung aus der Datenbank:", r.get('projekt'))
    
    print("🧠 Lade LLM Worker...")
    # Schaut nun im richtigen Linux-Verzeichnis nach der Datei
    llm_process = subprocess.Popen([sys.executable, "llm_loader_linux.py"], cwd=LLM_ORDNER)

    # 5 Sekunden warten, bis die 5080 das Modell im RAM/VRAM vorbereitet hat
    time.sleep(5) 

    print("🤖 Starte Discord Bot...")
    bot_process = subprocess.Popen([sys.executable, "my_bot_linux.py"], cwd=BOT_ORDNER)

    # Hält das Skript offen, solange Bot und LLM laufen
    llm_process.wait()
    bot_process.wait()

except KeyboardInterrupt:
    print("\n🛑 Beende das System. Schalte KI und Bot ab...")
    if 'llm_process' in locals(): llm_process.terminate()
    if 'bot_process' in locals(): bot_process.terminate()
    print("Fertig.")