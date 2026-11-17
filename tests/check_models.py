import time
from google import genai
from google.genai import types
 
client = genai.Client(http_options=types.HttpOptions(timeout=20000))
 
models = [
    "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
    "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]
 
for name in models:
    try:
        start = time.time()
        r = client.models.generate_content(model=name, contents="Say hello in one short sentence")
        print("WORKS ", name, round(time.time() - start, 1), "s")
    except Exception as e:
        print("FAILED", name, "-", str(e)[:70])
 