import numpy as np
import torch
from transformers import pipeline
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad, get_speech_timestamps
import asyncio
import pyaudio
import zmq
import zmq.asyncio

vad_model, utils = load_silero_vad()
model = WhisperModel("deepdml/faster-whisper-large-v3-turbo-ct2", device="cuda", compute_type="float16")



FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024

async def get_wasapi_loopback_device(p):
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')


    for i in range(0, numdevices):
        device_info = p.get_device_info_by_host_api_device_index(0, i)
        if device_info.get('maxInputChannels') > 0:
            name = device_info.get('name').lower()
            if "loopback" in name or "stereomix" in name or "wave out" in name:
                return i

    return p.get_default_input_device_info()['index']

async def stream_device_audio():
    context = zmq.asyncio.Context()
    zmq_socket = context.socket(zmq.PUB)
    zmq_socket.connect("tcp://127.0.0.1:5556")

    p = pyaudio.PyAudio()
    device_index = await get_wasapi_loopback_device(p)

    queue = asyncio.Queue()




    def audio_callback(in_data, frame_count, time_info, status):
        queue.put_nowait(in_data)
        return (None, pyaudio.paContinue)
    
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=CHUNK,
                    stream_callback=audio_callback
                    )
    stream.start_stream()

    try: 
        while stream.is_active():
            audio_chunk = await queue.get()
            await zmq_socket.send(audio_chunk)

        await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        zmq_socket.close()
        context.term()








# # Konfiguration für Text und Audio-Analyse
# VOLUME_QIUET = 0.01
# VOLUME_LOUD = 0.1
# WORD_TARGET = 70
# WORD_HARD_LIMIT = 85
# SILENCE_FLUSH_WORDS = 3
# SPEECH_RATE_SLOW = 1.5
# SPEECH_RATE_FAST = 3.5

# # ==========================
# # MODELLE LADEN
# # ==========================
# print("Lade Whisper Modell...")
# whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
# # emotion_classifier = pipeline("text-classification", model="SamLowe/roberta-base-go_emotions", device=0)

# # ==========================
# # ANALYSE FUNKTIONEN
# # ==========================
# def analyze_volume(audio: np.ndarray) -> str:
#     rms = float(np.sqrt(np.mean(audio**2)))
#     if rms < VOLUME_QIUET:
#         return "quiet"
#     elif rms > VOLUME_LOUD:
#         return "loud"
#     return "normal"

# # def detect_emotion_wrapper(text: str):
#     result = emotion_classifier(text)
#     print(result)

#     EMOTION_THRESHOLD = 0.1
#     significant = [r for r in result if r['score'] > EMOTION_THRESHOLD]
#     if not significant:
#         significant = [max(result, key=lambda x: x['score'])]
#     return "/".join([r['label'] for r in significant][:3])

# # def combine_emotion(emotion: list[str]):
# #     seen = set()
# #     unique = []
# #     for e in emotion:
# #         if e not in seen:
# #             seen.add(e)
# #             unique.append(e)
# #     return "/".join(unique)

# def transcribe_and_process_audio(audio: np.ndarray) -> tuple[str, str]:
#     segments, _ = whisper_model.transcribe(
#         audio, 
#         language="en",
#         beam_size=1,
#         condition_on_previous_text=False,
#         no_speech_threshold=0.6
#     )

#     segments = list(segments)
#     text = " ".join([segment.text for segment in segments]).strip()

#     total_words = 0
#     total_duration = 0.0
#     for seg in segments:
#         seg_duration = seg.end - seg.start
#         seg_words = len(seg.text.split())
#         if seg_words > 0:
#             total_words += seg_words
#             total_duration += seg_duration

#     if total_duration > 0:
#         wps = total_words / total_duration
#         if wps < SPEECH_RATE_SLOW:
#             speed = "slow"
#         elif wps > SPEECH_RATE_FAST:
#             speed = "fast"
#         else:
#             speed = "normal"
#     else:
#         speed = "normal"

#     return text, speed

# # ==========================
# # TEXT CHUCKER KLASSE
# # ==========================
# class TextChucker:
#     # Fehlendes Komma bei "?" eingefügt
#     SENTENCE_ENDINGS = [".", "!", "?", "..."]

#     def __init__(self):
#         self.buffer: list[str] = []
#         self.emotions: list[str] = []
#         self.last_speed: str = "normal"
#         self.last_volume: str = "normal"

#     @property
#     def word_count(self) -> int:
#         return len(self.buffer)

#     def _find_last_sentence_boundary(self) -> int:
#         for i in range(len(self.buffer)-1, -1, -1):
#             word = self.buffer[i].rstrip()  
#             if word and word[-1] in self.SENTENCE_ENDINGS:
#                 return i + 1
#         return -1

#     def _build_payload(self, words: list[str]) -> dict:
#         return {
#             "text": " ".join(words),
#             "emotion": combine_emotion(self.emotions),
#             "speed": self.last_speed,
#             "volume": self.last_volume
#         }

#     def add_text(self, text: str, emotion: str, speed: str, volume: str) -> list[dict]:
#         new_words = text.split()
#         if not new_words:
#             return []

#         self.buffer.extend(new_words)
#         self.emotions.append(emotion)
#         self.last_speed = speed
#         self.last_volume = volume

#         payloads = []

#         while self.word_count >= WORD_TARGET:
#             cut_index = self._find_last_sentence_boundary()

#             if cut_index == -1 or cut_index < WORD_TARGET:
#                 cut_index = WORD_TARGET

#             payload = self._build_payload(self.buffer[:cut_index])

#             self.buffer = self.buffer[cut_index:]
#             self.emotions = []

#             payloads.append(payload)

#         return payloads

#     def flush(self) -> dict | None:
#         print("FLUSH CALLED, WORDS:", self.word_count)
#         if self.word_count < SILENCE_FLUSH_WORDS:
#             return None
        
#         payload = self._build_payload(self.buffer)
#         self.buffer = []
#         self.emotions = []
#         return payload



# import numpy as np

# import sounddevice as sd
# import torch
# import json
# from transformers import pipeline
# from faster_whisper import WhisperModel
# import zmq
# import queue
# import warnings
# import threading
# import time
# import subprocess
# import sys
# import os

# # Rust bauen und starten
# rust_process = subprocess.Popen(
#     ["cargo", "run", "--release"],
#     cwd=r"F:\AI_Bot",
#     creationflags=subprocess.CREATE_NEW_CONSOLE
# )

# time.sleep(10)  # mehr zeit
# print(f"Rust PID: {rust_process.pid}")
# print(f"Rust returncode: {rust_process.poll()}")


# warnings.filterwarnings("ignore")

# # Konfiguration
# SAMPLE_RATE = 16000
# CHUNK_SIZE = 512
# VAD_THRESHOLD = 0.1
# SILENCE_DURATION = 0.8


# WORD_TARGET = 70
# WORD_HARD_LIMIT = 85
# SILENCE_FLUSH_WORDS = 3

# SPEECH_RATE_SLOW = 1.5
# SPEECH_RATE_FAST = 3.5

# VOLUME_QIUET = 0.01
# VOLUME_LOUD = 0.1

# # ZMQ Setup
# ZMQ_PORT = "tcp://127.0.0.1:5555"
# context = zmq.Context()
# sender = context.socket(zmq.PUSH)
# sender.bind(ZMQ_PORT)
# sender.setsockopt(zmq.SNDHWM, 1000)

# audio_queue = queue.Queue()

# #==========================
# # AUDIO - Modell laden
# #==========================

# print("Lade Whisper Modell...")
# whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
# vad_model, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False, trust_repo=True)
# vad_model = vad_model.to ('cpu')


# # Emotionserkennung - Modell laden
# print("Lade Emotionserkennungsmodell...")
# emotion_classifier = pipeline("text-classification", model="SamLowe/roberta-base-go_emotions", device=0)

# print (f"sende an {ZMQ_PORT}...")


# def audio_callback(indata, frames, time, status):
#     if status:
#         pass
#     audio_queue.put(indata[:, 0].copy())


# def analyze_volume(audio: np.ndarray) -> str:

#     rms = float(np.sqrt(np.mean(audio**2)))
#     if rms < VOLUME_QIUET:
#         return "quiet"
#     elif rms > VOLUME_LOUD:
#         return "loud"
#     return "normal"
        
# #EMOTION

# def detect_emotion_wrapper(text: str):
#     result = emotion_classifier(text)#[0]
#     print (result)

#     EMOTION_THRESHOLD = 0.1
#     significant = [r for r in result if r['score'] > EMOTION_THRESHOLD]
#     if not significant:
#         significant = [max(result, key=lambda x: x['score'])]
#     return "/".join([r['label'] for r in significant][:3])



# def combine_emotion (emotion: list[str]):

#     seen = set()
#     unique = []
#     for e in emotion:
#         if e not in seen:
#             seen.add(e)
#             unique.append(e)


#     return "/".join(unique)


# class TextChucker:

#     SENTENCE_ENDINGS = [".", "!", "?" "..."]

#     def __init__(self):
#         self.buffer: list[str] = []
#         self.emotions: list[str] = []
#         self.last_speed: str = "normal"
#         self.last_volume: str = "normal"



#     @property
#     def word_count(self) -> int:
#         return len(self.buffer)
    

#     def _find_last_sentence_boundary(self) -> int:
#         for i in range(len(self.buffer)-1, -1, -1):
#             word = self.buffer[i].rstrip()  
#             if word and word[-1] in self.SENTENCE_ENDINGS:
#                 return i + 1
#         return -1

#     def _build_payload(self, words: list[str]) -> dict:
#         return{
#             "text": " ".join(words),
#             "emotion": combine_emotion(self.emotions),
#             "speed": self.last_speed,
#             "volume": self.last_volume
#         }
        

#     def add_text(self, text: str, emotion: str, speed: str, volume: str) -> list[dict]:
#         new_words = text.split()
#         if not new_words:
#             return []

#         self.buffer.extend(new_words)
#         self.emotions.append(emotion)
#         self.last_speed = speed
#         self.last_volume = volume

#         payloads = []


#         while self.word_count >= WORD_TARGET:

#             cut_index = self._find_last_sentence_boundary()

#             if cut_index == -1 or cut_index < WORD_TARGET:
#                 cut_index = WORD_TARGET

#             payload = self._build_payload(self.buffer[:cut_index])

#             self.buffer = self.buffer[cut_index:]
#             self.emotions = []

#             payloads.append(payload)

#         return payloads

#         # while True:
#         #     if self.word_count >= WORD_HARD_LIMIT:
#         #         cut_index = self._find_last_sentence_boundary()
#         #         if cut_index == -1 or cut_index < WORD_TARGET:
#         #             cut_index = self.word_count
 
#         #         payload = self._build_payload(self.buffer[:cut_index])
#         #         self.buffer = self.buffer[cut_index:]
#         #         self.emotions = []
#         #         payloads.append(payload)
#         #         continue

#         #     if self.word_count >= WORD_TARGET:
#         #         cut_index = self._find_last_sentence_boundary()
#         #         if cut_index != -1:
#         #             payload = self._build_payload(self.buffer[:cut_index])
#         #             self.buffer = self.buffer[cut_index:]
#         #             self.emotions = []
#         #             payloads.append(payload)
#         #             continue

#         #     break

#         # return payloads
    


#     def flush(self) -> dict | None:
#         print("FLUSH CALLED, WORDS:", self.word_count)
#         if self.word_count < SILENCE_FLUSH_WORDS:
#             return None
        
#         payload = self._build_payload(self.buffer)
#         self.buffer = []
#         self.emotions = []
#         return payload
    



# def send_payload(payload: dict):
#     payload["source"] = "mic"
#     sender.send_string(json.dumps(payload, ensure_ascii=False))





# def transcribe_and_process_audio(audio: np.ndarray) -> tuple[str, str]:
    
#     segments, _ = whisper_model.transcribe(
#         audio, 
#         language="en",
#         beam_size=1,
#         condition_on_previous_text=False,
#         no_speech_threshold=0.6
#     )

#     segments = list(segments)
#     text = " ".join([segment.text for segment in segments]).strip()

#     total_words = 0
#     total_duration = 0.0
#     for seg in segments:
#         seg_duration = seg.end - seg.start
#         seg_words = len(seg.text.split())
#         if seg_words > 0:
#             total_words += seg_words
#             total_duration += seg_duration


#     if total_duration > 0:
#         wps = total_words / total_duration
#         if wps < SPEECH_RATE_SLOW:
#             speed = "slow"
#         elif wps > SPEECH_RATE_FAST:
#             speed = "fast"
#         else:
#             speed = "normal"
#     else:
#         speed = "normal"

#     return text, speed


# def processing_worker():
#     while True:
#         full_audio = processing_queue.get()
#         print(f"WORKER RECEIVED, audio length: {len(full_audio)}")

#         start = time.time()
        
#         if len(full_audio) > SAMPLE_RATE * 0.3:
#             text, speed = transcribe_and_process_audio(full_audio)
#             print(f"ERKANNTER TEXT: '{text}'")

#             if text:
#                 emotion = detect_emotion_wrapper(text)
#                 volume = analyze_volume(full_audio)
#                 ready_payloads = text_chucker.add_text(text, emotion, speed, volume)
#                 for p in ready_payloads:
#                     send_payload(p)

#             flushed = text_chucker.flush()
#             if flushed:
#                 send_payload(flushed)

#         print(f"WORKER DONE in {time.time()-start:.2f}s")


# def main():
#     is_speaking = False
#     silence_timer = 0.0
#     audio_buffer = []

#     text_chucker = TextChucker()

#     worker_thread = threading.Thread(target=processing_worker, daemon=True)
#     worker_thread.start()

#     with sd.InputStream(
#         samplerate=SAMPLE_RATE,
#         channels=1,
#         dtype='float32',
#         blocksize=CHUNK_SIZE,
#         callback=audio_callback):

#         print("Starte Audioaufnahme...")

#         while True:
#             chunk = audio_queue.get()
#             chunk_duration = len(chunk) / SAMPLE_RATE

#             tensor_chunk = torch.from_numpy(chunk)
#             speech_prob = vad_model(tensor_chunk, SAMPLE_RATE).item()
#            # print(f"VAD: {speech_prob:.2f}")

#             if speech_prob > VAD_THRESHOLD:
#                 if not is_speaking:
#                     print("Sprechbeginn erkannt.")
#                 is_speaking = True
#                 silence_timer = 0.0
#                 audio_buffer.append(chunk)
#             else:
#                 if is_speaking:
#                     silence_timer += chunk_duration
#                     audio_buffer.append(chunk)
#                     print("is_speaking:", is_speaking, "silence:", silence_timer)

#                     if silence_timer > SILENCE_DURATION:
#                         is_speaking = False
#                         full_audio = np.concatenate(audio_buffer)
#                         audio_buffer = []
#                         silence_timer = 0.0

#                         print(f"PUT IN QUEUE, audio length: {len(full_audio)}")
#                         processing_queue.put(full_audio)


# processing_queue = queue.Queue()
# text_chucker = TextChucker()

# if __name__ == "__main__":
#     main()



















#                     # if len(full_audio) > SAMPLE_RATE * 0.5:
#                     #     segmetens, _ = whisper_model.transcribe(full_audio, 
                                                                


#                     #     )
#                     # text = "".join([segment.text for segment in segmetens]).strip()

#                     # if text: emotion_result = emotion_classifier(text)[0]
#                     # emotion = emotion_result['label'] 

#                     # payload = {
#                     #     "text": text,
#                     #     "emotion": emotion
#                     # }
#                     # json_string = json.dumps(payload)

#                     # sender.send_string(json_string)

# # if __name__ == "__main__":
# #     try: 
# #         main()
# #     except KeyboardInterrupt:
# #         print("Beende...")