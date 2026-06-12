import threading
import queue
import time
import keyboard
import requests
import torch
from transformers import WhisperModel
import zmq
import pyaudio
from faster_whisper import WhisperModel
import json
from silero_vad import load_silero_vad, get_speech_timestamps
import numpy as np

vad_model, _ = load_silero_vad()
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000



with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

language = config["language"]
together_api_key = config["together_api_key"]
prompt_template = config["prompt"]





model = WhisperModel(config["whisper_model"], device="cuda", compute_type="float16")
audio = pyaudio.PyAudio()

def whisper_stream_process(audio_buffer: bytes) -> tuple[str, bool]:
    import numpy as np
    
    audio_np = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
    
    segments, _ = model.transcribe(audio_np, language=language)
    
    text = " ".join([s.text for s in segments]).strip()
    
    is_final = any(text.endswith(p) for p in [".", "!", "?", "..."])
    
    return text, is_final


def whisper_transcribe_complete(audio_buffer: bytes) -> str:
    import numpy as np
    
    audio_np = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
    
    segments, _ = model.transcribe(audio_np, language=language)
    
    return " ".join([s.text for s in segments]).strip()

def is_speech(audio_buffer: bytes) -> bool:
    audio_np = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
    audio_tensor = torch.from_numpy(audio_np)
    
    timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=RATE)
    
    return len(timestamps) > 0

def get_device_index() -> int:
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if "Stereo Mix" in info["name"]:
            return i
    raise Exception("Stereo Mix nicht gefunden — bitte in mmsys.cpl aktivieren")

def record_audio_chunk(source: str) -> bytes:
    if source == "mic":
        stream = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )
    elif source == "device":
        stream = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
            input_device_index=get_device_index()
        )

    data = stream.read(CHUNK)
    stream.stop_stream()
    stream.close()
    return data

# =====================================================================
# PIPELINE FACTORY
# Gibt ein plain Dict zurück — keine Klasse, kein self.
# Einfach zweimal aufrufen für zwei isolierte Pipelines.
# =====================================================================
def create_pipeline(source: str, speaker: str, zqm_port: int) -> dict:
    socket = zmq.Context().socket(zmq.PUSH)
    socket.connect(f"tcp://localhost:{zqm_port}")
    return {
        # Identität
        "source":  source,   # "mic" | "device"
        "speaker": speaker,  # "streamer" | "device"
        "zqm_socket" : socket,
        "last_speech_time": time.time(),

        # JSON 1 — Short-Term-Memory
        # [0]    = live fließender Halbsatz (vorläufig, wird ständig überschrieben)
        # [1..9] = fertige, kontextverbesserte Sätze (neueste zuerst)
        "short_term_memory": [
            {"speaker": speaker, "text": ""}
        ],

        # Queues — komplett isoliert pro Pipeline
        "audio_chunk_queue": queue.Queue(),   # Rohdaten Mikro/Device → Whisper
        "token_stream_queue": queue.Queue(),  # Live-Wörter → 4-Bit-Modell
        "rust_zmq_queue":     queue.Queue(),  # Fertige Sätze → ZMQ

        # Audio-Buffer akkumuliert bytes seit Satzanfang
        "audio_sentence_buffer": bytearray(),
        "audio_buffer_lock":     threading.Lock(),

        # Cooldown-Flag: verhindert parallele Roast-Interrupts
        "interrupt_triggered": False,
    }


# =====================================================================
# PIPELINES — einfach neue Zeile für neuen Kanal
# =====================================================================
pipeline_mic    = create_pipeline(source="mic",    speaker="streamer", zqm_port=5555)
pipeline_device = create_pipeline(source="device", speaker="device", zqm_port=5557)


# =====================================================================
# THREAD 1: Audio-Eingang
# =====================================================================
def audio_capture_worker(p: dict):
    while True:
        raw_audio = record_audio_chunk(p["source"])

        p["audio_chunk_queue"].put(raw_audio)

        with p["audio_buffer_lock"]:
            p["audio_sentence_buffer"].extend(raw_audio)


# =====================================================================
# THREAD 2: Whisper-Engine
#
# MODUS A — LIVE:
#   Läuft auf dem wachsenden Buffer seit Satzanfang.
#   Mehr Kontext = bessere Erkennung → Whisper korrigiert sich selbst.
#   Neue Wörter gehen live in Index [0] und in die token_stream_queue.
#
# MODUS B — FINAL (nach Satzpause):
#   Whisper läuft nochmal auf dem kompletten Satz-Buffer.
#   Das Ergebnis ist die kontextverbesserte, finale Version.
#   → Geht in Index [1] des Short-Term-Memory und an ZMQ.
#   → Index [0] wird sofort geleert, Audio-Buffer zurückgesetzt.
# =====================================================================
def whisper_worker(p: dict):
    while True:
        audio_chunk = p["audio_chunk_queue"].get()
        if not is_speech(audio_chunk):
            silence_duration = time.time() - p["last_speech_time"]
            if silence_duration > p["silence_threshold"] and p["short_term_memory"][0]["text"]:
                final_text = p["short_term_memory"][0]["text"]
                p["rust_zmq_queue"].put(final_text)
                p["short_term_memory"][0]["text"] = ""
                p["short_term_memory"].insert(1, {"speaker": p["speaker"], "text": final_text})
                if len(p["short_term_memory"]) > 10:
                    p["short_term_memory"].pop()
                with p["audio_buffer_lock"]:
                    p["audio_sentence_buffer"] = bytearray()
            elif not p["short_term_memory"][0]["text"]:
                with p["audio_buffer_lock"]:
                    p["audio_sentence_buffer"] = bytearray()
            continue


        p["last_speech_time"] = time.time()

        new_words, is_sentence_final = whisper_stream_process(current_buffer)

        if new_words:
            # Vorläufig in Index [0] streamen
            p["short_term_memory"][0]["text"] = (
                p["short_term_memory"][0]["text"] + " " + new_words
            ).strip()

            # Sofort ans 4-Bit-Modell weitergeben
            p["token_stream_queue"].put(new_words)

        # --- MODUS B: Final ---
        if is_sentence_final:
            with p["audio_buffer_lock"]:
                complete_audio = bytes(p["audio_sentence_buffer"])
                p["audio_sentence_buffer"] = bytearray()

            # Kontextverbesserter Finaldurchlauf
            final_text = whisper_transcribe_complete(complete_audio)

            # Finale Version → ZMQ-Queue
            p["rust_zmq_queue"].put(final_text)

            # JSON 1 rotieren:
            # Index [0] leeren (bereit für nächsten Live-Satz)
            # final_text (NICHT der vorläufige [0]-Text!) → Index [1]
            p["short_term_memory"][0]["text"] = ""
            p["short_term_memory"].insert(1, {"speaker": p["speaker"], "text": final_text})

            # Ringpuffer: max. 10 gesamt (1 Live-Slot + 9 fertige Sätze)
            if len(p["short_term_memory"]) > 10:
                p["short_term_memory"].pop()


# =====================================================================
# THREAD 3: 4-Bit-Modell (KI-Zensor)
#
# Schema ans Modell: nur das Nötigste
# {
#   "speaker":    "streamer",
#   "text_array": ["live Halbsatz", "letzter fertiger Satz", ...]
# }
# =====================================================================
def local_4bit_trigger_worker(p: dict):
    while True:
        incoming_tokens = p["token_stream_queue"].get()

        context_for_model = {
            "speaker":    p["speaker"],
            "text_array": incoming_tokens,
            # [0] = aktueller Halbsatz (live, vorläufig)
            # [1..9] = fertige Sätze, neueste zuerst
        }

        decision = local_4bit_eval(context_for_model)

        if decision == "1" and not p["interrupt_triggered"]:
            p["interrupt_triggered"] = True

            memory_snapshot = list(p["short_term_memory"])
            threading.Thread(
                target=execute_together_ai_roast,
                args=(p, memory_snapshot),
                daemon=True
            ).start()


# =====================================================================
# ASYNCHRONER TASK: Together.ai + TTS
# =====================================================================
def call_together_ai_api(memory_snapshot: list) -> str:
    trigger = memory_snapshot[0]["text"]
    context_lines = [
        f"[{i}] {entry['text']}"
        for i, entry in enumerate(memory_snapshot[1:], start=1)
        if entry["text"]
    ]
    context_block = "\n".join(context_lines) if context_lines else "kein Kontext"

    prompt = config["prompt"].format(
    trigger=trigger,
    context=context_block
)

# --- API-Aufruf --- muss API KEYS und URL anpassen, damit es funktioniert --- 
# WICHTIG 
    response = requests.post(
        "https://api.together.xyz/v1/chat/completions",
        headers={"Authorization": f"Bearer {together_api_key}"},
        json={
            "model": config["together_model"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
        }
    )
    return response.json()["choices"][0]["message"]["content"].strip()

def execute_together_ai_roast(p: dict, memory_snapshot: list):
    # Snapshot: Index [0] kann unvollständiger Halbsatz sein → gewollt
    roast_text = call_together_ai_api(memory_snapshot)

    time.sleep(10)
    p["interrupt_triggered"] = False


# =====================================================================
# THREAD 4: Rust ZMQ Sender
#
# ZMQ-Schema:
# {
#   "source":    "mic" | "device",
#   "speaker":   "streamer" | "device",
#   "text":      "kontextverbesserter finaler Satz",
#   "timestamp": 1218024641200
# }
# Nur finale Texte — nie der vorläufige Live-Text aus Index [0].
# =====================================================================
def rust_zmq_sender_worker(p: dict):
    while True:
        final_sentence = p["rust_zmq_queue"].get()

        payload = {
            "source":    p["source"],
            "speaker":   p["speaker"],
            "text":      final_sentence,
            "timestamp": int(time.time() * 1000),
        }
        p["zmq_socket"].send_json(payload)

# =====================================================================
# SYSTEMSTART
# Jede Pipeline: 4 Threads, komplett isoliert.
# Neue Pipeline = create_pipeline() + 4 Thread.start()-Zeilen.
# =====================================================================
def start_pipeline(p: dict):
    threading.Thread(target=audio_capture_worker,      args=(p,), daemon=True).start()
    threading.Thread(target=whisper_worker,            args=(p,), daemon=True).start()
    threading.Thread(target=local_4bit_trigger_worker, args=(p,), daemon=True).start()
    threading.Thread(target=rust_zmq_sender_worker,    args=(p,), daemon=True).start()


start_pipeline(pipeline_mic)
start_pipeline(pipeline_device)

while True:
    keyboard.wait("Ctrl+L")