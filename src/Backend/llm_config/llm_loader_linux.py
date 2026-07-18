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
import os
from transformers import DynamicCache
from torch.profiler import profile, ProfilerActivity



if "GPTQMODEL_DISABLE_MARLIN" in os.environ: del os.environ["GPTQMODEL_DISABLE_MARLIN"]
if "AUTOAWQ_DISABLE_MARLIN" in os.environ: del os.environ["AUTOAWQ_DISABLE_MARLIN"]


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
)


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

print("Berechne globalen System-Prompt-Cache im VRAM...")
print(f"Loading model onto {MODEL_DEVICE}...")
tokenizer = AutoTokenizer.from_pretrained(model_path, fix_mistral_regex=True)

model = AutoModelForCausalLM.from_pretrained(
    model_path,
    dtype=torch.float16,
    device_map={"": MODEL_DEVICE} if MODEL_DEVICE.type == "cuda" else None,
    low_cpu_mem_usage=True,
    attn_implementation="sdpa",
)


model = model.to(MODEL_DEVICE)
model.eval()
print(f"Modellgerät: {next(model.parameters()).device}")
print("🔮 Bereite globalen System-Prompt-Cache über Template-Slicing vor...")



# simulation einer Start-Unterhaltung
init_messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "INIT_DUMMY_PROMPT"}
]
system_templated = tokenizer.apply_chat_template(init_messages, tokenize=False, add_generation_prompt=True)
inputs_dummy = tokenizer(system_templated, return_tensors="pt").to(MODEL_DEVICE)
prefix_str = system_templated.split("INIT_DUMMY_PROMPT")[0]



GLOBAL_SYSTEM_LEN = len(tokenizer(prefix_str, add_special_tokens=True).input_ids)


# Jetzt schneiden wir uns die exakten System-IDs und die Maske aus
sliced_input_ids = inputs_dummy.input_ids[:, :GLOBAL_SYSTEM_LEN]
sliced_attention_mask = inputs_dummy.attention_mask[:, :GLOBAL_SYSTEM_LEN]
 
# Einmalig durch das Modell jagen, um den perfekten Basis-Cache im VRAM zu verankern
with torch.inference_mode():
    system_outputs = model(
        input_ids=sliced_input_ids,
        attention_mask=sliced_attention_mask,
        use_cache=True
    )
    GLOBAL_SYSTEM_CACHE = system_outputs.past_key_values


# Ein kleiner Helfer, damit process_request() die Attribute wie gewohnt auslesen kann
class SystemInputsMock:
    def __init__(self, ids, mask):
        self.input_ids = ids
        self.attention_mask = mask
    def keys(self): return ["input_ids", "attention_mask"]
    def __getitem__(self, key):
        if key == "input_ids": return self.input_ids
        if key == "attention_mask": return self.attention_mask
        raise KeyError(key)

system_inputs = SystemInputsMock(sliced_input_ids, sliced_attention_mask)
print(f"✅ System-Prompt erfolgreich gecacht ({GLOBAL_SYSTEM_LEN} Tokens fixiert).")
print(model.config.quantization_config)
qc = model.config.quantization_config
print("bits:", getattr(qc, "bits", "???"))
print("group_size:", getattr(qc, "group_size", "???"))
print("version:", getattr(qc, "version", "???"))
print("zero_point:", getattr(qc, "zero_point", "???"))
print("do_fuse:", getattr(qc, "do_fuse", "???"))
print("backend:", getattr(qc, "backend", "???"))
print("--- vollständiges Dict ---")
print(qc.to_dict())
for name, module in model.named_modules():
    cls_name = type(module).__name__
    if "WQLinear" in cls_name or "Awq" in cls_name or "AWQ" in cls_name:
        print(name, "->", cls_name)
        break
def clone_kv_cache(orig_cache):
    """
    generate() erweitert den übergebenen past_key_values-Cache IN-PLACE. Da
    GLOBAL_SYSTEM_CACHE dauerhaft ist und für JEDE Anfrage wiederverwendet wird,
    braucht jeder generate()-Aufruf eine eigene Kopie - sonst wächst der globale
    Cache mit jeder Anfrage weiter bzw. wird zwischen parallelen Anfragen
    korrumpiert.

    Nutzt to_legacy_cache()/from_legacy_cache() statt copy.deepcopy(): Das klont
    NUR die eigentlichen Tensoren (ein .clone()-Call pro Layer, läuft auf der
    GPU) - ohne den generischen Python-Overhead von deepcopy (Objektgraph-
    Traversierung, memo-Dict, __reduce_ex__-Pickling für die Wrapper-Objekte).
    Bleibt außerdem stabil, egal ob die interne Cache-Struktur gerade
    key_cache/value_cache-Listen oder ein layers-Array ist.
    """
    legacy = orig_cache.to_legacy_cache()          # Tuple aus (key, value)-Tupeln, eins pro Layer
    cloned = tuple((k.clone(), v.clone()) for k, v in legacy)
    return DynamicCache.from_legacy_cache(cloned)


def build_inputs_with_system_cache(tokenizer, history_messages, prompt, device):
    """
    System-Prompt wird NIE erneut gerendert - der bleibt exakt der gecachte,
    permanente Prefix aus dem Setup. Hier wird NUR Verlauf + neue Nachricht
    gerendert, bewusst OHNE System-Message in der Liste, damit das Template
    seine "System an die letzte Nachricht hängen"-Logik gar nicht auslöst.
    """

    conv_messages = history_messages + [{"role": "user", "content": prompt}]
    suffix_str = tokenizer.apply_chat_template(conv_messages, tokenize=False, add_generation_prompt=True)


    if suffix_str.startswith(tokenizer.bos_token):
        suffix_str = suffix_str[len(tokenizer.bos_token):]
    suffix_ids = tokenizer(suffix_str, add_special_tokens=False, return_tensors="pt").input_ids.to(device)
    suffix_len = suffix_ids.shape[1]
    total_len = GLOBAL_SYSTEM_LEN + suffix_len

    attention_mask = torch.ones((1, total_len), dtype=torch.long, device=device)
    position_ids = torch.arange(GLOBAL_SYSTEM_LEN, total_len, dtype=torch.long, device=device).unsqueeze(0)


    return suffix_ids, attention_mask, position_ids, suffix_len




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


        self.bad_word_ids = list(self.bad_word_ids)
        self.bad_word_tensor = None  # NEU: Bereitet den GPU-Speicher vor
        print(f"[Filter] Fertig! {len(self.bad_word_ids)} Token-IDs blockiert.")

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:


        if self.bad_word_tensor is None:
            valid_ids = [tid for tid in self.bad_word_ids if tid < scores.shape[-1]]
            self.bad_word_tensor = torch.tensor(valid_ids, device=scores.device, dtype=torch.long)

        if len(self.bad_word_tensor) > 0:
            scores[:, self.bad_word_tensor] = float("-inf")
            
        return scores


logits_filter = StopOnBadWords(tokenizer, BLACKLISTED_WORDS)
global_logits_processor = LogitsProcessorList([logits_filter])


class TokenStreamer:
    def __init__(self, tokenizer, input_len):
        self.tokenizer = tokenizer
        self.input_len = input_len
        self.token_queue = queue.Queue()
        self.stop_signal = object()
        self.error = None
        self.token_ids = []
        self.prefix_offset = 0
        self.read_offset = 0


    def put(self, value):
        if len(value.shape) > 1:
            value = value[0]

        self.token_ids.extend(value.tolist())

        if len(self.token_ids) <= self.input_len:
            return

        generated_tokens = self.token_ids[self.input_len:]


        prefix_text = self.tokenizer.decode(
            generated_tokens[self.prefix_offset:self.read_offset], skip_special_tokens=True
        )
        new_text = self.tokenizer.decode(
            generated_tokens[self.prefix_offset:], skip_special_tokens=True
        )


        if new_text.endswith("\ufffd"):
            return

        if len(new_text) <= len(prefix_text):
            return

        delta = new_text[len(prefix_text):]
        self.prefix_offset = self.read_offset
        self.read_offset = len(generated_tokens)

        if delta:
            self.token_queue.put(delta)

    def end(self):
        print(f"⏱️ Generierte Tokens: {len(self.token_ids) - self.input_len}")
        self.token_queue.put(self.stop_signal)


def process_request(request_id, channel_id, prompt):
    response_key = f"{RESPONSE_KEY_PREFIX}{request_id}"
    history_key = f"llm:history:{channel_id}"
    request_started_at = time.perf_counter()
    print(f"[Anfrage {request_id}] {prompt[:80]!r}...")
    first_token_time = None
    token_count = 0 
    try:
        # 1. Historie aus Redis holen (OHNE System-Message - die bleibt für immer im Cache)
        raw_history = redis_client.lrange(history_key, 0, -1)
        history_messages = [json.loads(item) for item in raw_history]

        print(f"🔍 HISTORY MESSAGES: {json.dumps(history_messages, ensure_ascii=False)}")


        t_before_cache = time.perf_counter()

        # 2. Nur Verlauf + neue Nachricht rendern und hinter den gecachten
        #    System-Prompt-Prefix hängen - kein Vergleich, kein Fallback mehr nötig.
        suffix_ids, attention_mask, position_ids, suffix_len = build_inputs_with_system_cache(
            tokenizer, history_messages, prompt, MODEL_DEVICE
        )

        past_kv = clone_kv_cache(GLOBAL_SYSTEM_CACHE)

        inputs = {
            "input_ids": suffix_ids,
            "attention_mask": attention_mask,
            "position_ids": position_ids,
        }

        total_len = GLOBAL_SYSTEM_LEN + suffix_len
        print(f"🔍 DEBUG CACHE: System-Prefix (gecacht) = {GLOBAL_SYSTEM_LEN} Tokens, Suffix (neu) = {suffix_len} Tokens, Gesamt = {total_len}")
        print(f"🔍 SUFFIX TOKENS (Erste 6): {suffix_ids[0, :6].tolist()}")
        print(f"🔍 [HARD-DEBUG] Suffix-Klartext (komplett): {tokenizer.decode(suffix_ids[0])}")
        print(f"🔍 [HARD-DEBUG] Position-IDs (Start/Ende): {position_ids[0, 0].item()} .. {position_ids[0, -1].item()}")

        t_after_cache = time.perf_counter()

        # 3. Stop-Tokens setzen
        stop_token_ids = [tokenizer.eos_token_id]
        chatml_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        if chatml_end_id is not None and chatml_end_id not in stop_token_ids and chatml_end_id != tokenizer.unk_token_id:
            stop_token_ids.append(chatml_end_id)

        input_len = suffix_len

        print(tokenizer.chat_template)
        streamer = TokenStreamer(tokenizer, input_len)

        def generate_async():
            global first_token_time, token_count
            try:
                with torch.no_grad():
                    model.generate(
                        **inputs,
                        past_key_values=past_kv,                  
                        streamer=streamer,
                        # logits_processor=global_logits_processor, # Bleibt deaktiviert, um CPU-Bremsen zu vermeiden
                        max_new_tokens=MAX_NEW_TOKENS,
                        temperature=TEMPERATURE,
                        do_sample=True,
                        pad_token_id=tokenizer.eos_token_id,
                        eos_token_id=stop_token_ids,
                    )
            except Exception as e:
                streamer.error = str(e)
            finally:
                streamer.end()

        # Thread starten
        t_start_gen = time.perf_counter()
        threading.Thread(target=generate_async).start()
        # Tokens an Redis streamen
        full_response = ""
        while True:
            try:
                token_chunk = streamer.token_queue.get(timeout=0.1)
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                    print(f"⏱️ Time-to-first-token: {(first_token_time - t_start_gen)*1000:.1f}ms")
                    token_count += 1
                if token_chunk is streamer.stop_signal:
                    break
                
                full_response += token_chunk
                redis_client.rpush(response_key, json.dumps({"token": token_chunk}))
            except queue.Empty:

                continue
        if streamer.error:
            redis_client.rpush(response_key, json.dumps({"error": streamer.error}))
        else:
            # Generierung abschließen
            redis_client.rpush(response_key, json.dumps({"done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

        # 5. Gedächtnis aktualisieren
        if full_response.strip() and not full_response.startswith("❌"):
            redis_client.rpush(history_key, json.dumps({"role": "user", "content": prompt}))
            redis_client.rpush(history_key, json.dumps({"role": "assistant", "content": full_response}))
            redis_client.ltrim(history_key, -10, -1)

        total_seconds = time.perf_counter() - request_started_at
        print(f"[Anfrage {request_id}] fertig in {total_seconds:.2f}s")

        t_end = time.perf_counter()
        print(f"⏱️ Vorbereitung: {(t_before_cache - request_started_at)*1000:.1f}ms")
        print(f"⏱️ KV-Cache Klonen: {(t_after_cache - t_before_cache)*1000:.1f}ms")
        print(f"⏱️ Reine GPU-Generierung: {(t_end - t_start_gen)*1000:.1f}ms")
        print(f"🔍 HISTORY MESSAGES ({len(history_messages)} Einträge):")
        print(f"🔍 SYSTEM_PROMPT (Anfrage {request_id}):\n{SYSTEM_PROMPT}\n{'-'*40}")
        for m in history_messages:
            print(f"   [{m['role']}] {m['content']}")
        

    except Exception as e:
        print(f"[FEHLER] in process_request: {e}")
        redis_client.rpush(response_key, json.dumps({"error": str(e), "done": True}))
        redis_client.expire(response_key, RESPONSE_TTL_SECONDS)

def serve():
    # test_suffix_ids, test_attention_mask, test_position_ids, test_suffix_len = build_inputs_with_system_cache(
    #     tokenizer, [], "Erzähl mir eine kurze Geschichte über einen Drachen.", MODEL_DEVICE
    # )
    # test_inputs = {
    #     "input_ids": test_suffix_ids,
    #     "attention_mask": test_attention_mask,
    #     "position_ids": test_position_ids,
    # }

    # torch.cuda.synchronize()
    # t0 = time.perf_counter()
    # with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA], record_shapes=True) as prof:
    #     with torch.no_grad():
    #         out = model.generate(
    #             **test_inputs,
    #             past_key_values=clone_kv_cache(GLOBAL_SYSTEM_CACHE),
    #             max_new_tokens=70,
    #             do_sample=True,
    #             temperature=0.7,
    #             pad_token_id=tokenizer.eos_token_id,
    #         )
    # print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=20))
    # torch.cuda.synchronize()
    # dt = time.perf_counter() - t0
    # new_tokens = out.shape[1] - test_suffix_ids.shape[1]
    # print(f"🔍 BENCHMARK: {new_tokens} Tokens in {dt:.2f}s -> {new_tokens/dt:.1f} Tok/s")

    # print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=20))


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


if __name__ == '__main__':
    serve()