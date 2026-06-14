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
from transformers import AutoTokenizer, AutoModelForCausalLM, DynamicCache
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

SYSTEM_PROMPT = config["prompt"]
print("Berechne globalen System-Prompt-Cache im VRAM...")

system_templated = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
system_inputs = tokenizer(system_templated, return_tensors="pt").to("cuda")
with torch.no_grad():
    outputs = model(**system_inputs, use_cache=True)
    GLOBAL_SYSTEM_CACHE = outputs.past_key_values
    GLOBAL_SYSTEM_LEN = system_inputs.input_ids.shape[-1]

print(f"System-Prompt erfolgreich gecacht ({GLOBAL_SYSTEM_LEN} Tokens fixiert).")

# Der C-Streamer: Holt das Token aus CUDA und schießt es direkt in den gRPC-Kanal zu Rust
class GrpcTokenStreamer(BaseStreamer):
    def __init__(self, tokenizer, context):
        self.tokenizer = tokenizer
        self.context = context

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
                past_key_values=local_cache, # HIER übergeben wir den eingefrorenen System-Prompt!
                streamer=streamer, 
                max_new_tokens=1000, 
                temperature=0.7,
                use_cache=True
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