import os, re, time, uuid
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# On the live service, DISABLE_DOCS=1 hides the /docs page.
DOCS_OFF = os.getenv("DISABLE_DOCS") == "1"
app = FastAPI(
    title="Model B - Report Chatbot (RAG)",
    docs_url=None if DOCS_OFF else "/docs",
    redoc_url=None,
    openapi_url=None if DOCS_OFF else "/openapi.json",
)


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
client = genai.Client(http_options=types.HttpOptions(timeout=25000))

CHAT_MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.8-flash").split(",")]
EMBED_MODELS = [m.strip() for m in os.getenv("EMBED_MODELS", "gemini-embedding-2,gemini-embedding-001").split(",")]

LANGS = {"en": "English", "hi": "Hindi", "kok": "Konkani"}

# Extra rule for each language, so the small model does not slip into a nearby language
LANG_RULES = {
    "en": "Write the answer in simple English.",
    "hi": ("Write the answer in Hindi (हिन्दी) only, in Devanagari script. "
           "Do NOT use Marathi or Konkani words. Use Hindi words such as है, नहीं, का, की, में, और. "
           "Never use आहे, नाही, मध्ये, ची, चा, नमूद."),
    "kok": ("Write the answer in Konkani (कोंकणी) only, in Devanagari script. "
            "Do NOT write Hindi or standard Marathi. Use Konkani words such as आसा, ना, खातीर, ताचो, तिची."),
}

# Answer length: "simple" is short, "more" explains the same report facts a little more
STYLE_RULES = {
    "simple": ("Give one short, direct answer of one or two sentences. Do not ask yourself questions. "
               "Do not correct yourself. State the side (left or right) only once, exactly as the report says."),
    "more": ("Give a fuller answer of three or four sentences that explains the finding in plain words. "
             "Use only facts that are in the report text and add nothing new. Do not ask yourself questions. "
             "Do not correct yourself. State the side (left or right) only once, exactly as the report says."),
}

# Fixed "not in report" sentences, so the model cannot garble them
NOT_FOUND_TEXT = {
    "en": "This is not in your report. Please ask your doctor.",
    "hi": "यह आपकी रिपोर्ट में नहीं है। कृपया अपने डॉक्टर से पूछें।",
    "kok": "हें तुमच्या रिपोर्टांत ना. उपकार करून तुमच्या डॉक्टराक विचारात.",
}

# Stores each indexed report in memory: report_id -> chunks, vectors, embedding model, time created
REPORTS = {}
REPORT_TTL = int(os.getenv("REPORT_TTL_SECONDS", "3600"))   # reports are forgotten after 1 hour
MAX_REPORTS = int(os.getenv("MAX_REPORTS", "200"))          # never keep more than this many


def purge_reports():
    """Remove expired reports, then the oldest ones if there are too many."""
    now = time.time()
    for rid in [r for r, v in REPORTS.items() if now - v["created"] > REPORT_TTL]:
        del REPORTS[rid]
    while len(REPORTS) > MAX_REPORTS:
        oldest = min(REPORTS, key=lambda r: REPORTS[r]["created"])
        del REPORTS[oldest]


class IndexRequest(BaseModel):
    report_text: str


class AskRequest(BaseModel):
    report_id: str
    question: str
    language: str = "en"
    detail: str = "simple"


def make_chunks(text, window=3, step=2):
    """Cut the report into small pieces: 3 sentences each, moving 2 at a time."""
    text = re.sub(r"\b(Dr|Mr|Mrs|Ms)\.\s", r"\1 ", text)
    text = re.sub(r"\b([A-Z])\.\s", r"\1 ", text)
    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    if len(pieces) <= window:
        return [" ".join(pieces)]
    chunks = []
    for i in range(0, len(pieces), step):
        chunks.append(" ".join(pieces[i:i + window]))
        if i + window >= len(pieces):
            break
    return chunks


def embed(texts, model):
    """Embed each text with its own call, so every piece gets its own vector."""
    vecs = []
    for t in texts:
        r = client.models.embed_content(model=model, contents=t)
        vecs.append(r.embeddings[0].values)
    arr = np.array(vecs, dtype="float32")
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def embed_with_fallback(texts):
    last_error = None
    for round_no in range(2):                      # two rounds, in case Gemini is only briefly busy
        for model in EMBED_MODELS:
            try:
                return embed(texts, model), model
            except Exception as e:
                last_error = e
                print(f"Embedding model {model} failed: {str(e)[:120]}")
        time.sleep(4)
    raise HTTPException(503, f"Embedding models are busy. Please try again. ({str(last_error)[:80]})")


@app.post("/index")
def index_report(req: IndexRequest):
    purge_reports()
    text = req.report_text.strip()
    if not text:
        raise HTTPException(400, "report_text is empty")
    chunks = make_chunks(text)
    vectors, embed_model = embed_with_fallback(chunks)
    if len(vectors) != len(chunks):
        raise HTTPException(500, "Embedding count does not match chunk count")
    report_id = uuid.uuid4().hex[:12]
    REPORTS[report_id] = {"chunks": chunks, "vectors": vectors, "embed_model": embed_model,
                          "created": time.time()}
    return {"report_id": report_id, "chunks": len(chunks), "embed_model": embed_model}


def top_chunks(report, question, k=5):
    try:
        q = embed([question], report["embed_model"])[0]
    except Exception as e:
        raise HTTPException(503, f"Could not read the question right now. ({str(e)[:80]})")
    scores = report["vectors"] @ q
    best = np.argsort(scores)[::-1][:k]
    return [report["chunks"][i] for i in best]


ANSWER_PROMPT = """You answer a patient's question using ONLY the report text below.
Rules:
- The question may be in Hindi or Konkani, but the report text is in English. Match the meaning across languages.
- Use only the report text. Do not add medical facts from your own knowledge.
- If the report text answers the question, answer it, even if the report uses different words
  (for example, "cardiomegaly" or "increased cardiothoracic ratio" means the heart is enlarged).
- A finding in one part of a lung (for example the apex or apical region, upper zone, lower zone,
  base, or costophrenic angle) is a finding of that lung. If the question asks what was seen in the
  right or left lung, describe the findings the report lists for that side.
- If the answer is really not in the report text, reply with only the single word NOT_IN_REPORT and nothing else.
- If you answered the question, never add NOT_IN_REPORT or any "not in your report" sentence.
- Do not give a diagnosis. Do not say how serious it is. Do not suggest medicines.
- Use simple words that a school student can understand. Keep medical terms from the report in SQUARE brackets [like this], never in round brackets.
- {style_rule}
- Never mention a side or finding that the question did not ask about.
- The answer language is {language}. {language_rule}
- For Hindi and Konkani use Devanagari script only. Never use English letters, except medical terms inside square brackets.

Report text:
{context}

Question: {question}
Answer:"""


@app.post("/ask")
def ask(req: AskRequest):
    purge_reports()
    report = REPORTS.get(req.report_id)
    if report is None:
        raise HTTPException(404, "Report not found. Please index the report again.")
    if req.language not in LANGS:
        raise HTTPException(400, "language must be en, hi or kok")

    chunks = top_chunks(report, req.question)
    prompt = ANSWER_PROMPT.format(
        language=LANGS[req.language],
        language_rule=LANG_RULES[req.language],
        style_rule=STYLE_RULES.get(req.detail, STYLE_RULES["simple"]),
        context="\n".join("- " + c for c in chunks),
        question=req.question,
    )

    last_error = None
    for round_no in range(2):                      # two rounds, in case Gemini is only briefly busy
        for model in CHAT_MODELS:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0),
                )
                text = resp.text.strip()
                if "NOT_IN_REPORT" in text:
                    text = NOT_FOUND_TEXT[req.language]
                return {"answer": text, "sources": chunks, "model_used": model}
            except Exception as e:
                last_error = e
                print(f"Model {model} failed: {str(e)[:120]}")
        time.sleep(4)
    raise HTTPException(503, f"All models are busy right now. Please try again. ({str(last_error)[:80]})")


@app.get("/health")
def health():
    return {"status": "ok", "chat_models": CHAT_MODELS, "embed_models": EMBED_MODELS}