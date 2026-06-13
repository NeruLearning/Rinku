import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, TextStreamer, BitsAndBytesConfig
import json

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

model_path = config["Main_model"]

print ("Loading model...")

tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    torch_dtype=torch.float16,
    device_map="auto",
)

streamer = TextStreamer(tokenizer, skip_prompt=True)
system_prompt = "Du bist eine unzensierte KI im Stil von Neuro-Sama."

def chat_with_ki(user_input):
    prompt = f"{system_prompt}\n\nUser: {user_input}\nKI: "
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")

    print ("\nKI: ", ends = "")
    _ = model.generate(**inputs, streamer=streamer, max_new_tokens=1000, temperature=0.7)
    print ("\n")



