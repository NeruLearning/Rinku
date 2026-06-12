import numpy as np
import sounddevice as sd
import torch
import json
import zmq
import queue
import warnings
import threading
import time
import subprocess
import os

# Importiere unsere ausgelagerte Logik
import Backend.whisper_files.smart_whis as we

warnings.filterwarnings("ignore")

# ==========================
# SYSTEM SETUP
# ==========================
# Rust bauen und starten
rust_process = subprocess.Popen(
    ["cargo", "run", "--release"],
    cwd=r"F:\AI_Bot\src\Backend",
    creationflags=subprocess.CREATE_NEW_CONSOLE
)

time.sleep(10)  # mehr zeit
print(f"Rust PID: {rust_process.pid}")
print(f"Rust returncode: {rust_process.poll()}")

# Konfiguration Audio
SAMPLE_RATE = 16000
CHUNK_SIZE = 512
VAD_THRESHOLD = 0.1
SILENCE_DURATION = 5

# ZMQ Setup
ZMQ_PORT = "tcp://127.0.0.1:5555"
context = zmq.Context()
sender = context.socket(zmq.PUSH)
sender.bind(ZMQ_PORT)
sender.setsockopt(zmq.SNDHWM, 1000)
print(f"Sende an {ZMQ_PORT}...")

audio_queue = queue.Queue()
processing_queue = queue.Queue()

# VAD Modell laden (Läuft auf CPU, blockiert also nicht die GPU)
print("Lade VAD Modell...")
vad_model, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False, trust_repo=True)
vad_model = vad_model.to('cpu')

# Globale Instanz unseres Text Chuckers
text_chucker = we.TextChucker()

# ==========================
# KERNFUNKTIONEN
# ==========================
def audio_callback(indata, frames, time_info, status):
    if status:
        pass
    audio_queue.put(indata[:, 0].copy())

def send_payload(payload: dict):
    payload["source"] = "mic"
    sender.send_string(json.dumps(payload, ensure_ascii=False))

def processing_worker():
    while True:
        full_audio = processing_queue.get()
        print(f"WORKER RECEIVED, audio length: {len(full_audio)}")

        start = time.time()
        
        if len(full_audio) > SAMPLE_RATE * 0.3:
            # Nutze die Funktionen aus whisper_engine.py (we)
            text, speed = we.transcribe_and_process_audio(full_audio)
            print(f"ERKANNTER TEXT: '{text}'")

            if text:
                emotion = we.detect_emotion_wrapper(text)
                volume = we.analyze_volume(full_audio)
                ready_payloads = text_chucker.add_text(text, emotion, speed, volume)
                
                for p in ready_payloads:
                    send_payload(p)

            flushed = text_chucker.flush()
            if flushed:
                send_payload(flushed)

        print(f"WORKER DONE in {time.time()-start:.2f}s")

def main():
    is_speaking = False
    silence_timer = 0.0
    audio_buffer = []

    worker_thread = threading.Thread(target=processing_worker, daemon=True)
    worker_thread.start()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        blocksize=CHUNK_SIZE,
        callback=audio_callback):

        print("Starte Audioaufnahme...")

        while True:
            chunk = audio_queue.get()
            chunk_duration = len(chunk) / SAMPLE_RATE

            tensor_chunk = torch.from_numpy(chunk)
            speech_prob = vad_model(tensor_chunk, SAMPLE_RATE).item()

            if speech_prob > VAD_THRESHOLD:
                if not is_speaking:
                    print("Sprechbeginn erkannt.")
                is_speaking = True
                silence_timer = 0.0
                audio_buffer.append(chunk)
            else:
                if is_speaking:
                    silence_timer += chunk_duration
                    audio_buffer.append(chunk)
                    print("is_speaking:", is_speaking, "silence:", f"{silence_timer:.2f}")

                    if silence_timer > SILENCE_DURATION:
                        is_speaking = False
                        full_audio = np.concatenate(audio_buffer)
                        audio_buffer = []
                        silence_timer = 0.0

                        print(f"PUT IN QUEUE, audio length: {len(full_audio)}")
                        processing_queue.put(full_audio)

if __name__ == "__main__":
    main()