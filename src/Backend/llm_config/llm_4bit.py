# llm_loader.py
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import torch
import threading
import json

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

# =====================================================================
# MODELL INIT (einmalig beim Start)
# =====================================================================
model_path = config["Trigger_model"]

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16
)

print("Lade Tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(model_path)

print("Lade LLM-Instanz 1 (Mic)...")
llm_mic    = AutoModelForCausalLM.from_pretrained(model_path, quantization_config=bnb_config, device_map="auto")

print("Lade LLM-Instanz 2 (Device)...")
llm_device = AutoModelForCausalLM.from_pretrained(model_path, quantization_config=bnb_config, device_map="auto")

print("Alle Modelle geladen.")

# =====================================================================
# CACHED SYSTEM PROMPT — einmalig tokenisiert, bleibt im KV-Cache
# =====================================================================
SYSTEM_PROMPT = (
    "Du bist ein extrem schneller Trigger-Filter. "
    "Du bekommst Live-Wörter eines Sprechers in Echtzeit. "
    "Erkennst du ein Schimpfwort, Beleidigung oder Roast-Anlass, antworte NUR mit '1'. "
    "Sonst antworte NUR mit '0'. Keine Erklärung."
)

PROMPT_PREFIX = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n"
PREFIX_IDS = tokenizer(PROMPT_PREFIX, return_tensors="pt").input_ids.to("cuda")

# =====================================================================
# KEEPALIVE — hält beide LLM Instanzen warm damit keine Latenz entsteht
# =====================================================================
def keepalive_worker(llm_instance):
    dummy_ids = tokenizer("0", return_tensors="pt").input_ids.to("cuda")
    while True:
        with torch.no_grad():
            llm_instance.generate(dummy_ids, max_new_tokens=1, do_sample=False)
        threading.Event().wait(30)  # alle 30 Sekunden warm halten

threading.Thread(target=keepalive_worker, args=(llm_mic,),    daemon=True).start()
threading.Thread(target=keepalive_worker, args=(llm_device,), daemon=True).start()