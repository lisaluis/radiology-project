import time
import os, json
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types

app = FastAPI(title="Model A - Simplify and Translate")


# If SERVICE_API_KEY is set, every request must send the same value in the X-API-Key header.
# If it is not set (like on your laptop), the check is skipped.
@app.middleware("http")
async def check_api_key(request: Request, call_next):
    required = os.getenv("SERVICE_API_KEY")
    open_paths = ("/health", "/docs", "/openapi.json")
    if required and request.url.path not in open_paths:
        if request.headers.get("X-API-Key") != required:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
    return await call_next(request)


# Wait at most 25 seconds for each model, then try the next one
client = genai.Client(http_options=types.HttpOptions(timeout=60000))
# Models are tried in this order. You can change the list with the GEMINI_MODELS setting.
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.8-flash").split(",")]

LANGS = {"en": "English", "hi": "Hindi", "kok": "Konkani"}

# Extra rule for the translation, so the small model uses the right language and script
LANG_RULES = {
    "en": "Write translated_text in simple English.",
    "hi": ("Write translated_text in Hindi (हिन्दी), using Devanagari script only. "
           "Do NOT use Roman (English) letters, except medical terms inside square brackets. "
           "Do NOT use Marathi or Konkani words. Use Hindi words such as है, नहीं, का, की, में, और."),
    "kok": ("Write translated_text in Konkani (कोंकणी), using Devanagari script only. "
            "Do NOT use Roman (English) letters, except medical terms inside square brackets. "
            "Do NOT write Hindi or standard Marathi. Use Konkani words such as आसा, ना, खातीर, ताचो, तिची."),
}

PROMPT = """You are helping a patient understand a radiology report.
Rules:
- Use only what is written in the report. Do not add anything new.
- Do not give a diagnosis. Do not say how serious it is.
- Use simple words that a school student can understand.
- Remove any patient name, ID or phone number.
- LANGUAGE_RULE
Return JSON with these keys:
report_text: the report text as written, with names, IDs and phone numbers removed
simple_text: the explanation in simple English
translated_text: the same explanation in LANGUAGE_NAME
"""


def ask_gemini(parts):
    """Try each model in order. Retry each one twice before moving on."""
    last_error = None
    for name in MODELS:
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=name,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                return json.loads(resp.text), name
            except Exception as e:
                last_error = e
                print(f"Model {name} attempt {attempt + 1} failed: {str(e)[:150]}")
                time.sleep(2)
    raise HTTPException(503, f"All models are busy right now. Please try again. ({str(last_error)[:80]})")
@app.post("/simplify")
async def simplify(
    text: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    language: str = Form("hi"),
):
    if language not in LANGS:
        raise HTTPException(400, "language must be en, hi or kok")

    parts = [PROMPT.replace("LANGUAGE_NAME", LANGS[language]).replace("LANGUAGE_RULE", LANG_RULES[language])]

    has_image = image is not None and image.filename
    if has_image:
        data = await image.read()
        parts.append(types.Part.from_bytes(data=data, mime_type=image.content_type))
    if text:
        parts.append("Report:\n" + text)
    if not text and not has_image:
        raise HTTPException(400, "Send report text or an image")

    result, model_used = ask_gemini(parts)
    result["language"] = language
    result["model_used"] = model_used
    return result


@app.get("/health")
def health():
    return {"status": "ok", "models": MODELS}