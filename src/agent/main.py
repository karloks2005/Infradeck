import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from prompts import SYSTEM_PROMPT
import time

# 1. Učitaj enviroment varijable (.env)
load_dotenv()

# 2. Inicijaliziraj klijent
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

for model in client.models.list():
    print(model.name)

# 3. Inicijaliziraj novu chat sesiju s postavljenim System Promptom
chat = client.chats.create(
    model="models/gemini-3.5-flash",
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2, # Niža temperatura za preciznije i manje "kreativne" sysadmin odgovore
    )
)

log_file = open("src/logs/chat.log", "w").close() # Očisti log datoteku prije početka nove sesije
log_file = open("src/logs/chat.log", "a") # Otvori log datoteku za dodavanje novih poruka

print("Agent started\n")
automated_input = ""
firstRun = True
# 4. Simulacija razgovora
while True:
    if firstRun:
        user_input = input("[USER - SET TASK]" + "-"*30 + "\n")
    
    if not automated_input and not firstRun:
        user_input = input("[USER]" + "-"*30 + "\n")
    else:
        user_input = automated_input
        automated_input = ""

    if user_input.lower() in ["exit", "quit"]:
        log_file.close()
        break
    
    firstRun = False

    # Slanje poruke u unutar kreirane chat sesije
    response = chat.send_message(user_input)
    
    log_file.write(f"[USER]" + "-"*30 + f"\n {user_input} \n")

    response_text = getattr(response, "text", "")
    if response_text and "[PARTIAL]" in response_text:
        print(f"\n[AGENT]" + "-"*30 + f"\n {response_text} \n")
        automated_input = "[CONTINUE]"

    agent_response = f"\n[AGENT]" + "-"*30 + f"\n {response_text} \n"
    log_file.write(agent_response)
    print(agent_response)
    time.sleep(6)