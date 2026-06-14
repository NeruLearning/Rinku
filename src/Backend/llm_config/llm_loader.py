# import torch
# from transformers import AutoTokenizer, AutoModelForCausalLM, TextStreamer, BitsAndBytesConfig
# import json

# with open("config.json", "r", encoding="utf-8") as f:
#     config = json.load(f)

# model_path = config["Main_model"]

# print ("Loading model...")

# tokenizer = AutoTokenizer.from_pretrained(model_path)
# model = AutoModelForCausalLM.from_pretrained(
#     model_path,
#     torch_dtype=torch.float16,
#     device_map="auto",
# )

# streamer = TextStreamer(tokenizer, skip_prompt=True)
# system_prompt = "Du bist eine unzensierte KI im Stil von Neuro-Sama."

# def chat_with_ki(user_input):
#     prompt = f"{system_prompt}\n\nUser: {user_input}\nKI: "
#     inputs = tokenizer(prompt, return_tensors="pt").to("cuda")

#     print ("\nKI: ", ends = "")
#     _ = model.generate(**inputs, streamer=streamer, max_new_tokens=1000, temperature=0.7)
#     print ("\n")




import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessorList, LogitsProcessor 
from transformers.generation.streamers import BaseStreamer
import json
import time
from concurrent import futures
import grpc
import os
import copy


# Diese Dateien generieren wir im nächsten Schritt, sie steuern die C-Verbindung
import llm_service_pb2
import llm_service_pb2_grpc


BLACKLISTED_WORDS = config["blacklisted_words"]
  

script_dir = os.path.dirname(os.path.abspath(__file__))
main_project_dir = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
config_path = os.path.join(main_project_dir, "config.json")
print(f"Suche Konfiguration unter: {config_path}")

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

model_path = config["Main_model"]

print("Loading model onto 5080...")
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    torch_dtype=torch.float16,
    device_map="auto",
)
logits_filter = StopOnBadWords(tokenizer, BLACKLISTED_WORDS)
global_logits_processor = LogitsProcessorList([logits_filter])



SYSTEM_PROMPT = config["prompt"]
print("Berechne globalen System-Prompt-Cache im VRAM...")

system_templated = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
system_inputs = tokenizer(system_templated, return_tensors="pt").to("cuda")
with torch.no_grad():
    outputs = model(**system_inputs, use_cache=True)
    GLOBAL_SYSTEM_CACHE = outputs.past_key_values
    GLOBAL_SYSTEM_LEN = system_inputs.input_ids.shape[-1]

print(f"System-Prompt erfolgreich gecacht ({GLOBAL_SYSTEM_LEN} Tokens fixiert).")


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
        self.bad_word_ids = set() # Wir nutzen ein Set, um Duplikate automatisch zu verhindern
        
        # Hol dir das komplette Vokabular des Modells (z.B. 32.000 oder 128.000 Tokens)
        vocab = tokenizer.get_vocab()
        print(f"[Filter] Initialisiere VRAM-Schutzwall. Scanne {len(vocab)} Tokens...")

        for word in bad_words:
            word_clean = word.lower().strip()
            
            for token_text, token_id in vocab.items():
                # Metazeichen des Tokenizers entfernen (z.B. Llama/Mistral nutzen 'Ġ' oder ' ')
                token_clean = token_text.replace("Ġ", "").replace(" ", "").lower()
                
                # Zu kurze Fragmente überspringen wir, sonst sperren wir aus Versehen einzelne Buchstaben
                if len(token_clean) < 3 and len(token_clean) < len(word_clean):
                    continue
                
                # --- STRATEGIE 1: Direktes Matching ---
                # Wenn das Wort im Token vorkommt (z.B. "holocaust" in "holocaust-leugnung")
                if word_clean in token_clean:
                    self.bad_word_ids.add(token_id)
                    continue
                
                # --- STRATEGIE 2: Levenshtein-Ähnlichkeit (Leetspeak / Typos) ---
                # Wir prüfen nur Tokens, die eine ähnliche Länge wie das Zielwort haben (+/- 2 Zeichen)
                if abs(len(token_clean) - len(word_clean)) <= 2:
                    distanz = schnelle_levenshtein(token_clean, word_clean)
                    
                    # Distanz von <= 2 bedeutet: Maximal 2 Buchstaben getauscht/hinzugefügt
                    # Findet automatisch: "n1gger" (Distanz 1 zu "nigger") oder "holokoust" (Distanz 1)
                    if distanz <= 2:
                        self.bad_word_ids.add(token_id)

        # Zurück in eine Liste konvertieren für schnellere Iteration im __call__
        self.bad_word_ids = list(self.bad_word_ids)
        print(f"[Filter] Fertig! {len(self.bad_word_ids)} Token-IDs permanent im VRAM blockiert.")


    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        # Bleibt exakt so performant wie dein Original, da die Liste fertig berechnet ist!
        for token_id in self.bad_word_ids:
            if token_id < scores.shape[-1]:
                scores[:, token_id] = float("-inf")
        return scores






# Der C-Streamer: Holt das Token aus CUDA und schießt es direkt in den gRPC-Kanal zu Rust
class GrpcTokenStreamer(BaseStreamer):
    def __init__(self, tokenizer, context):
        self.tokenizer = tokenizer
        self.context = context
        self.generated_tokens_buffer = ""
        self.is_aborted = False

    def put(self, value):
        if len(value.shape) > 1:
            return
        token_text = self.tokenizer.decode(value, skip_special_tokens=True)
        if token_text:
            # Schickt das Token direkt als gRPC-Stream-Chunk an Rust
            self.context.write(llm_service_pb2.TokenResponse(token=token_text))

    def end(self):
        pass

# Das ist die Inferenz-Schnittstelle, die Rust direkt anspricht
class LLMServiceServicer(llm_service_pb2_grpc.LLMServiceServicer):
    def StreamInference(self, request, context):
        prompt = request.prompt
        user_templated = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        inputs = tokenizer(user_templated, return_tensors="pt").to("cuda")

        local_cache = copy.deepcopy(GLOBAL_SYSTEM_CACHE) 
        # Unser Streamer kriegt den direkten Netzwerk-Kontext zu Rust übergeben
        streamer = GrpcTokenStreamer(tokenizer, context)
        
        with torch.no_grad():
            _ = model.generate(
                **inputs, 
                past_key_values=local_cache,
                streamer=streamer, 
                logits_processor=global_logits_processor,
                max_new_tokens=1000, 
                temperature=0.7,
                use_cache=True,
                do_sample=True
            )
        

def serve():
    # Startet einen hochoptimierten C-basierten Server-Thread
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    llm_service_pb2_grpc.add_LLMServiceServicer_to_server(LLMServiceServicer(), server)
    
    # Port 50051 wird geöffnet. Hier dockt Rust an.
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Python-LLM-Engine läuft als stummer Dienst auf Port 50051. Python-Arbeit beendet.")
    print(SYSTEM_PROMPT)
    
    try:
        while True:
            time.sleep(86400) # Hält den Prozess am Leben, tut sonst nichts
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == '__main__':
    serve()