import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessorList, LogitsProcessor
from transformers.generation.streamers import BaseStreamer
import json
import time
import os
import copy
import queue
import threading
import redis
from transformers import TextIteratorStreamer
import sys
# from awq import AutoAWQForCausalLM
 
if "GPTQMODEL_DISABLE_MARLIN" in os.environ: del os.environ["GPTQMODEL_DISABLE_MARLIN"]
if "AUTOAWQ_DISABLE_MARLIN" in os.environ: del os.environ["AUTOAWQ_DISABLE_MARLIN"]

print("👓 Setze virtuelles Hardware-Snoofing auf (7, 5) für den Ladevorgang...")
# Temporär auf eine Turing-Karte (7, 5) fälschen, damit gptqmodel Marlin ignoriert


# ==============================================================================

# ==============================================================================

# ... ab hier läuft dein restlicher Skript-Code wie gewohnt weiter

# ===================================================================
# Konfiguration laden
# ===================================================================
script_dir = os.path.dirname(os.path.abspath(__file__))
main_project_dir = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
config_path = os.path.join(main_project_dir, "config.json")
system_prompt_path = os.path.join(main_project_dir, "system_prompt.json")
print(f"Suche Konfiguration unter: {config_path}")

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

with open(system_prompt_path, "r", encoding="utf-8") as f:
    system_prompt = json.load(f)

model_path = config["Main_model"]
BLACKLISTED_WORDS = config["blacklisted_words"]
SYSTEM_PROMPT = (
    f"### IDENTITY & PERSONALITY:\n{system_prompt['personality']}\n\n"
    f"### STRICT RULES & NEGATIVE TRAITS:\n{system_prompt['Negative traits']}"
) # Kombiniere Persönlichkeit und negative Eigenschaften

# Redis ist jetzt die einzige Schnittstelle nach außen: Rust <-> Redis <-> Python <-> LLM.
# Diese Werte können optional in der config.json überschrieben werden (sonst greifen Defaults).
REDIS_HOST = config.get("redis_host", "localhost")
REDIS_PORT = config.get("redis_port", 6379)
REDIS_DB = config.get("redis_db", 0)
REDIS_PASSWORD = config.get("redis_password", None)
REQUEST_QUEUE = config.get("redis_request_queue", "llm:requests")
RESPONSE_KEY_PREFIX = config.get("redis_response_prefix", "llm:response:")
RESPONSE_TTL_SECONDS = config.get("redis_response_ttl_seconds", 3600)
MAX_INPUT_TOKENS = config.get("max_input_tokens", 4096)
MAX_NEW_TOKENS = config.get("max_new_tokens", 4096)
TEMPERATURE = config.get("temperature", 0.7)

MODEL_DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
if MODEL_DEVICE.type == "cuda":
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.set_float32_matmul_precision("high")

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    protocol=2,
    socket_keepalive=True,
    health_check_interval=60
)


# ===================================================================
# Modell laden (selbst implementiert via transformers, kein Ollama/vLLM)
# ===================================================================
print("Berechne globalen System-Prompt-Cache im VRAM...")
print(f"Loading model onto {MODEL_DEVICE}...")
tokenizer = AutoTokenizer.from_pretrained(model_path, fix_mistral_regex=True)

# Wir nutzen das native SDPA (Scaled Dot Product Attention) von PyTorch.
# Das ist unter Windows quasi "FlashAttention light" und läuft ohne Installation out-of-the-box!
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    dtype=torch.float16,
    device_map={"": MODEL_DEVICE} if MODEL_DEVICE.type == "cuda" else None,
    low_cpu_mem_usage=True,
    attn_implementation="sdpa"  # <--- SDPA statt flash_attention_2 nutzen!
)
torch.cuda.get_device_capability = echte_capability
torch.cuda.get_device_properties = echte_properties
model = model.to(MODEL_DEVICE)
model.eval()
print(f"Modellgerät: {next(model.parameters()).device}")


# ===================================================================
# Permanenter System-Prompt-KV-Cache
# Wird EINMAL beim Start berechnet und danach für JEDE Anfrage wiederverwendet,
# damit das Modell den System-Prompt nicht bei jeder Anfrage neu "durchdenken" muss.
# ===================================================================
system_templated = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
system_inputs = tokenizer(system_templated, return_tensors="pt").to(MODEL_DEVICE)
with torch.inference_mode():
    system_outputs = model(**system_inputs, use_cache=True)
    GLOBAL_SYSTEM_CACHE = system_outputs.past_key_values
    GLOBAL_SYSTEM_LEN = system_inputs.input_ids.shape[-1]

print(f"System-Prompt erfolgreich gecacht ({GLOBAL_SYSTEM_LEN} Tokens fixiert).")


def clone_kv_cache(orig_cache):
    """
    generate() erweitert den übergebenen past_key_values-Cache IN-PLACE um die
    neu generierten Tokens. Da GLOBAL_SYSTEM_CACHE aber dauerhaft ist und für
    JEDE Anfrage erneut verwendet wird, darf niemals das Original an generate()
    übergeben werden - sonst wächst der globale Cache mit jeder Anfrage weiter
    bzw. wird zwischen parallelen Anfragen korrumpiert. Deshalb hier immer eine
    Tiefenkopie anfertigen.
    """
    new_cache = DynamicCache()
    for k, v in zip(orig_cache.key_cache, orig_cache.value_cache):
        new_cache.key_cache.append(k.clone())
        new_cache.value_cache.append(v.clone())
    return new_cache
    # return copy.deepcopy(past_key_values)


# ===================================================================
# Wortfilter: verbotene Wörter werden im Vokabular auf -inf gesetzt,
# damit sie beim Sampling gar nicht erst gewählt werden können.
# (Unverändert gegenüber der ursprünglichen Implementierung.)
# ===================================================================
def levenshtein_distanz(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distanz(s2, s1)
    if len(s2) == 0:
        return len(s1)

    vorherige_zeile = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        aktuelle_zeile = [i + 1]
        for j, c2 in enumerate(s2):
            einfügungen = vorherige_zeile[j + 1] + 1
            löschungen = aktuelle_zeile[j] + 1
            ersetzungen = vorherige_zeile[j] + (c1 != c2)
            aktuelle_zeile.append(min(einfügungen, löschungen, ersetzungen))
        vorherige_zeile = aktuelle_zeile

    return vorherige_zeile[-1]

class StopOnBadWords(LogitsProcessor):
    def __init__(self, tokenizer, bad_words):
        self.tokenizer = tokenizer
        self.bad_word_ids = set()

        vocab = tokenizer.get_vocab()
        print(f"[Filter] Initialisiere VRAM-Schutzwall. Scanne {len(vocab)} Tokens...")

        for word in bad_words:
            word_clean = word.lower().strip()
            for token_text, token_id in vocab.items():
                token_clean = token_text.replace("Ġ", "").replace(" ", "").lower()
                if len(token_clean) < 3 and len(token_clean) < len(word_clean):
                    continue

                if word_clean in token_clean:
                    self.bad_word_ids.add(token_id)
                    continue

                if abs(len(token_clean) - len(word_clean)) <= 2:
                    distanz = levenshtein_distanz(token_clean, word_clean)
                    if distanz <= 2:
                        self.bad_word_ids.add(token_id)

        self.bad_word_ids = list(self.bad_word_ids)
        self.bad_word_tensor = None  # NEU: Bereitet den GPU-Speicher vor
        print(f"[Filter] Fertig! {len(self.bad_word_ids)} Token-IDs blockiert.")

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        # NEU: Einmalig auf die Grafikkarte laden
        if self.bad_word_tensor is None:
            valid_ids = [tid for tid in self.bad_word_ids if tid < scores.shape[-1]]
            self.bad_word_tensor = torch.tensor(valid_ids, device=scores.device, dtype=torch.long)
        
        # NEU: Alle verbotenen Wörter gleichzeitig mit einem GPU-Befehl blockieren
        if len(self.bad_word_tensor) > 0:
            scores[:, self.bad_word_tensor] = float("-inf")
            
        return scores


logits_filter = StopOnBadWords(tokenizer, BLACKLISTED_WORDS)
global_logits_processor = LogitsProcessorList([logits_filter])


# ===================================================================
# Token-Streamer: sammelt die generierten Tokens und legt fertige
# Text-Häppchen in eine interne Queue (die dann Richtung Redis weitergereicht wird).
# ===================================================================
class TokenStreamer:
    def __init__(self, tokenizer, input_len):
        self.tokenizer = tokenizer
        self.input_len = input_len
        self.token_queue = queue.Queue()
        self.stop_signal = object()
        self.error = None  
        self.token_ids = []
        self.printed_len = 0

    def put(self, value):
        # Falls Batch-Dimension vorhanden ist, flachdrücken
        if len(value.shape) > 1:
            value = value[0]

        # Wir fügen die Tokens fortlaufend zu unserer Liste hinzu
        self.token_ids.extend(value.tolist())

        # Solange wir noch im Bereich des Eingabe-Prompts sind: Ignorieren
        if len(self.token_ids) <= self.input_len:
            return

        # 1. Nur die Tokens heraustrennen, die NACH dem Prompt generiert wurden
        generated_tokens = self.token_ids[self.input_len:]
        
        # 2. Den gesamten bisherigen Antworttext sauber decodieren
        full_text = self.tokenizer.decode(generated_tokens, skip_special_tokens=True)
        
        # 3. NUR das herausschneiden, was seit dem ALLERLETZTEN Aufruf neu dazu kam
        new_text = full_text[self.printed_len:]
        
        # 4. Die neue Textlänge für die nächste Runde merken
        self.printed_len = len(full_text)

        # Wenn wirklich neuer Text da ist, ab in die Redis-Queue damit!
        if new_text:
            self.token_queue.put(new_text)

    def end(self):
        self.token_queue.put(self.stop_signal)

# ===================================================================
# Verarbeitung einer einzelnen Anfrage aus Redis
# ===================================================================
def process_request(request_id, channel_id, prompt):
    response_key = f"{RESPONSE_KEY_PREFIX}{request_id}"
    history_key = f"llm:history:{channel_id}"
    print(f"[Anfrage {request_id}] {prompt[:80]!r}...")

    try:
        # 1. Historie aus Redis holen
        raw_history = redis_client.lrange(history_key, 0, -1)
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        
        for item in raw_history:
            messages.append(json.loads(item))
            
        messages.append({"role": "user", "content": prompt})

        # 2. Prompt-Formatting
        full_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(full_prompt, return_tensors="pt").to("cuda")
        input_len = inputs.input_ids.shape[1]

        # 3. Cache-Sicherheitscheck & Korrektes Slicing
        cached_prefix = inputs.input_ids[:, :GLOBAL_SYSTEM_LEN]

        print(f"🔍 DEBUG CACHE: Erwartete Länge = {GLOBAL_SYSTEM_LEN}, Live-Länge = {cached_prefix.shape[1]}")
        print(f"🔍 LIVE TOKENS (Erste 6): {inputs.input_ids[0, :6].tolist()}")
        print(f"🔍 CACHE TOKENS (Erste 6): {system_inputs.input_ids[0, :6].tolist()}")

        if cached_prefix.shape[1] == GLOBAL_SYSTEM_LEN and torch.equal(cached_prefix, system_inputs.input_ids):
            past_kv = clone_kv_cache(GLOBAL_SYSTEM_CACHE)
            
            # 🔥 DER ENTSCHEIDENDE FIX FÜR DIE RTX 5080:
            # Wir müssen alle drei Tensoren synchron anpassen, um SDPA zu erhalten.
            
            # 1. Die originale, volle Maske sichern (System + neue Tokens)
            full_attention_mask = inputs.attention_mask 
            
            # 2. input_ids auf die neuen Tokens kürzen
            inputs["input_ids"] = inputs["input_ids"][:, GLOBAL_SYSTEM_LEN:]
            
            # 3. Die volle Maske wieder zuweisen (damit das Modell in die Vergangenheit schauen kann)
            inputs["attention_mask"] = full_attention_mask
            
            # 4. ZWINGEND ERFORDERLICH: Die Positionen im Raum exakt ab dem Cache fortführen
            new_len = inputs["input_ids"].shape[1]
            inputs["position_ids"] = torch.arange(
                GLOBAL_SYSTEM_LEN, 
                GLOBAL_SYSTEM_LEN + new_len, 
                dtype=torch.long, 
                device="cuda"
            ).unsqueeze(0)
            
        else:
            print(f"[Warnung] Cache-Präfix passt nicht (Request {request_id}), rechne ohne Cache.")
            past_kv = None
            # Sicherstellen, dass keine alten Positions-IDs stören, wenn wir ohne Cache rechnen
            inputs.pop("position_ids", None)

        # 4. Stop-Tokens setzen
        stop_token_ids = [tokenizer.eos_token_id]
        chatml_end_id = tokenizer.convert_tokens_to_ids("<|im_end|")
        if chatml_end_id is not None and chatml_end_id not in stop_token_ids:
            stop_token_ids.append(chatml_end_id)

        streamer = TokenStreamer(tokenizer, input_len)

        def generate_async():
            try:
                with torch.no_grad():
                    model.generate(
                        **inputs,
                        past_key_values=past_kv,                  
                        streamer=streamer,
                        # 💡 TESTWEISE AUSBLENDEN: Falls es danach rast, blockiert dieser Prozessor deine GPU!
                        # logits_processor=global_logits_processor, 
                        max_new_tokens=150,
                        temperature=0.7,
                        do_sample=True,
                        pad_token_id=tokenizer.eos_token_id,
                        eos_token_id=stop_token_ids,
                    )
            except Exception as e:
                streamer.error = str(e)
            finally:
                streamer.end()

        # Thread starten
        threading.Thread(target=generate_async).start()

        # Tokens an Redis streamen
        full_response = ""
        while True:
            try:
                token_chunk = streamer.token_queue.get(timeout=0.1)
                if token_chunk is streamer.stop_signal:
                    break
                
                full_response += token_chunk
                redis_client.rpush(response_key, json.dumps({"token": token_chunk}))
            except queue.Empty:
                if streamer.error:
                    redis_client.rpush(response_key, json.dumps({"error": streamer.error}))
                    break
                continue

        # Generierung abschließen
        redis_client.rpush(response_key, json.dumps({"done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

        # 5. Gedächtnis aktualisieren
        if full_response.strip() and not full_response.startswith("❌"):
            redis_client.rpush(history_key, json.dumps({"role": "user", "content": prompt}))
            redis_client.rpush(history_key, json.dumps({"role": "assistant", "content": full_response}))
            redis_client.ltrim(history_key, -10, -1)

    except Exception as e:
        print(f"[FEHLER] in process_request: {e}")
        redis_client.rpush(response_key, json.dumps({"error": str(e), "done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

# ===================================================================
# Haupt-Loop: wartet blockierend auf neue Aufträge in Redis und arbeitet sie ab
# ===================================================================
def serve():
    print(f"Python-LLM-Worker lauscht auf Redis-Liste '{REQUEST_QUEUE}'")
    while True:
        try:
            # Unendliches Blockieren im Leerlauf (0% CPU), robuster Schutz gegen Windows-Timeouts
            _, raw_job = redis_client.blpop(REQUEST_QUEUE, timeout=0)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, TimeoutError):
            continue

        try:
            job = json.loads(raw_job)
            request_id = job["request_id"]
            channel_id = job["channel_id"] # <-- Ausgelesen!
            prompt = job["prompt"]         # <-- Ausgelesen!
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[Fehler] Ungültige Nachricht aus Redis: {e}")
            continue

        process_request(request_id, channel_id, prompt)

def process_request(request_id, channel_id, prompt):
    response_key = f"llm:response:{request_id}"
    history_key = f"llm:history:{channel_id}"
    request_started_at = time.perf_counter()
    print(f"[Anfrage {request_id}] {prompt[:80]!r}...")

    try:
        raw_history = redis_client.lrange(history_key, 0, -1)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in raw_history:
            messages.append(json.loads(item))
        messages.append({"role": "user", "content": prompt})

        full_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(
            full_prompt,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_INPUT_TOKENS,
        ).to(MODEL_DEVICE)

        input_token_count = int(inputs.input_ids.shape[1])
        print(f"[Anfrage {request_id}] input tokens: {input_token_count}, max_new_tokens: {MAX_NEW_TOKENS}")

        stop_token_ids = [tokenizer.eos_token_id]
        chatml_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        if chatml_end_id is not None and chatml_end_id not in stop_token_ids:
            stop_token_ids.append(chatml_end_id)

        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

        def generate_async():
            try:
                with torch.inference_mode():
                    model.generate(
                        **inputs,
                        streamer=streamer,
                        logits_processor=global_logits_processor,
                        max_new_tokens=MAX_NEW_TOKENS,
                        temperature=TEMPERATURE,
                        do_sample=True,
                        eos_token_id=stop_token_ids,
                        pad_token_id=tokenizer.eos_token_id,
                    )
            except Exception as e:
                streamer.error = str(e)
            finally:
                streamer.end()

        threading.Thread(target=generate_async, daemon=True).start()

        full_response = ""
        generated_token_count = 0
        first_chunk_at = None
        for token_chunk in streamer:
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
                print(f"[Anfrage {request_id}] erstes Token nach {first_chunk_at - request_started_at:.2f}s")
            full_response += token_chunk
            generated_token_count += 1
            redis_client.rpush(response_key, json.dumps({"token": token_chunk}))

        if getattr(streamer, "error", None):
            redis_client.rpush(response_key, json.dumps({"error": streamer.error}))

        print(
            f"[Anfrage {request_id}] generation ended after {generated_token_count} chunks; "
            f"stop ids={stop_token_ids}; error={getattr(streamer, 'error', None)!r}"
        )

        redis_client.rpush(response_key, json.dumps({"done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

        if full_response.strip() and not full_response.startswith("❌"):
            redis_client.rpush(history_key, json.dumps({"role": "user", "content": prompt}))
            redis_client.rpush(history_key, json.dumps({"role": "assistant", "content": full_response}))
            redis_client.ltrim(history_key, -10, -1)

        total_seconds = time.perf_counter() - request_started_at
        print(f"[Anfrage {request_id}] fertig in {total_seconds:.2f}s")

    except Exception as e:
        print(f"[FEHLER] in process_request: {e}")
        redis_client.rpush(response_key, json.dumps({"error": str(e), "done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

if __name__ == '__main__':
    serve()