import os
import subprocess
import time
import sys
import redis
os.environ["DISTUTILS_USE_SDK"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["TORCH_DONT_CHECK_COMPILER_ABI"] = "1"
# HIER DEINE ECHTEN PFADE EINTRAGEN (das 'r' davor ist wichtig für Windows-Pfade)
LLM_ORDNER = r"F:\AI_Bot\src\Backend\llm_config"
BOT_ORDNER = r"F:\AI_Bot\Discord_bot"

print("🚀 Starte System...")

redis_prozess = subprocess.Popen(
    [r"C:\Redis\Redis-x64-5.0.14.1\redis-server.exe"], 
    stdout=subprocess.DEVNULL, 
    stderr=subprocess.PIPE,
    text=True
    )
time.sleep(1)
if redis_prozess.poll() is not None:
    fehler = redis_prozess.stderr.read()
    print(f"❌ Redis konnte nicht gestartet werden. Fehler:\n{fehler}")
else:
    print("✅ Redis-Prozess läuft im Hintergrund.")

try:
    r = redis.Redis(host='::1', port=6379, decode_responses=True,socket_timeout=5.0, protocol=2)
    r.set('projekt', 'Läuft super in VS Code!')
    print("Meldung aus der Datenbank:", r.get('projekt'))
    print("🧠 Lade LLM Worker...")
    # cwd=LLM_ORDNER zwingt das Skript, im richtigen Ordner zu starten
    llm_process = subprocess.Popen([sys.executable, "llm_loader.py"], cwd=LLM_ORDNER)

    # 5 Sekunden warten
    time.sleep(5) 

    print("🤖 Starte Discord Bot...")
    bot_process = subprocess.Popen([sys.executable, "my_bot_windows.py"], cwd=BOT_ORDNER)

    llm_process.wait()
    bot_process.wait()

except KeyboardInterrupt:
    print("\n🛑 Beende das System. Schalte KI und Bot ab...")
    llm_process.terminate()
    bot_process.terminate()
    print("Fertig.")