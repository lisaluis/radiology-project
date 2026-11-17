"""Web page that joins Model A (simplify + translate) and Model B (report chatbot).
Run:  streamlit run app.py
Settings come from environment variables (see the defaults below)."""
import os
import re
import html
import datetime
import requests
import streamlit as st

MODEL_A_URL = os.getenv("MODEL_A_URL", "http://127.0.0.1:8000").rstrip("/")
MODEL_B_URL = os.getenv("MODEL_B_URL", "http://127.0.0.1:8001").rstrip("/")
HEADERS = {"X-API-Key": os.getenv("SERVICE_API_KEY", "")}
LANGS = {"English": "en", "हिन्दी (Hindi)": "hi", "कोंकणी (Konkani)": "kok"}
LANG_NAMES = {v: k for k, v in LANGS.items()}
DISCLAIMER = ("This tool only explains your report in simple words. It is not a diagnosis. "
              "Please show the report to your doctor.")
CONSENT_TEXT = ("I understand that this tool does not diagnose, and that my report is sent to "
                "Google's Gemini service to be explained. I will show the report to my doctor.")
MAX_IMAGE_MB = 5
MAX_TEXT_CHARS = 20000

# These must match the fixed sentences in Model B
NOT_FOUND_TEXTS = {
    "This is not in your report. Please ask your doctor.",
    "यह आपकी रिपोर्ट में नहीं है। कृपया अपने डॉक्टर से पूछें।",
    "हें तुमच्या रिपोर्टांत ना. उपकार करून तुमच्या डॉक्टराक विचारात.",
}

SAMPLE_REPORT = (
    "HOSPITAL: City Care Hospital   PATIENT: Ramesh Naik   AGE/SEX: 52/M\n"
    "PATIENT ID: CC-10231   PHONE: 98XXXXXX01   REFERRED BY: Dr. A. Kamat   DATE: 02-11-2026\n"
    "EXAM: Chest X-ray PA view\n"
    "FINDINGS: Both lungs are clear. No focal opacity is seen. The heart size is normal. "
    "Both costophrenic angles are sharp. The bony thorax and soft tissues are normal.\n"
    "IMPRESSION: Normal chest X-ray."
)

# Question chips: if the report contains the word, offer the question
CHIP_RULES = [
    ("impression", "What is the final impression?"),
    ("fracture", "Is there any fracture?"),
    ("dislocation", "Is any bone out of place?"),
    ("listhesis", "Have any bones slipped out of place?"),
    ("swelling", "What swelling is mentioned?"),
    ("pneumothorax", "What does the report say about the pneumothorax?"),
    ("effusion", "Is there fluid around the lungs?"),
    ("consolidation", "What does the report say about the consolidation?"),
    ("opacity", "What does the report say about the opacity?"),
    ("hyperinflat", "Do the lungs look hyperinflated?"),
    ("heart", "Is my heart size normal?"),
    ("lung", "What does the report say about my lungs?"),
    ("disc", "Which discs are affected?"),
    ("osteophyte", "Are there any bone growths?"),
    ("lordosis", "Is the curve of my back normal?"),
    ("mediastin", "Has the middle of the chest shifted?"),
    ("correlation", "What does the report advise?"),
    ("normal", "What does the report say is normal?"),
]
GENERIC_CHIPS = [
    "What is the final impression?",
    "What are the main findings?",
    "What does the report say is normal?",
    "What does the report advise?",
]

DOCTOR_QUESTIONS = [
    "What do these results mean for me?",
    "Do I need any more tests or scans?",
    "What should I do next, and how soon?",
    "Is there anything in this report I should be worried about?",
    "When should I come back for a follow-up?",
]

URGENT_WORDS = [
    "chest pain", "can't breathe", "cannot breathe", "difficulty breathing", "trouble breathing",
    "short of breath", "shortness of breath", "severe bleeding", "heavy bleeding",
    "coughing blood", "coughing up blood", "unconscious", "fainted", "fainting",
    "heart attack", "stroke", "seizure", "severe pain",
    "सीने में दर्द", "सांस नहीं", "सांस लेने में तकलीफ", "बेहोश",
]
URGENT_HTML = ('<div class="urgent">🚨 If you have chest pain, trouble breathing, heavy bleeding or '
               'feel faint, <b>call 112 now</b> or go to the nearest hospital. '
               'Do not wait for this tool.</div>')

STOP_WORDS = {"what", "does", "this", "that", "with", "from", "have", "there", "your", "report",
              "which", "about", "were", "into", "them", "they", "been", "also", "say", "says",
              "mentioned", "seen"}

AVATARS = {"user": "🧑", "assistant": "🩺"}

st.set_page_config(page_title="Radiology Report Helper", page_icon="🩻", layout="centered")

# ---------- Session state ----------
for key, default in {"result": None, "report_id": None, "chat": [], "up_n": 0}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------- Look and feel ----------
st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
[data-testid="stHeader"] {background: transparent;}
.block-container {padding-top: 4.5rem; padding-bottom: 8rem; max-width: 820px;}

.hero {
    background: linear-gradient(135deg, #0F766E 0%, #14B8A6 100%);
    color: white; padding: 30px 34px; border-radius: 18px; margin-bottom: 18px;
    box-shadow: 0 6px 18px rgba(15, 118, 110, 0.25);
}
.hero .hero-title {font-size: 2rem; font-weight: 800; line-height: 1.2; margin: 0; color: white;}
.hero .hero-sub {font-size: 1.05rem; line-height: 1.4; margin: 8px 0 0 0; color: white; opacity: 0.95;}

.disclaimer {
    background: #FFF7E0; border-left: 6px solid #F59E0B; color: #6B4A00;
    padding: 14px 18px; border-radius: 10px; margin-bottom: 18px; font-size: 0.98rem;
}
.urgent {
    background: #FEE2E2; border-left: 6px solid #DC2626; color: #7F1D1D;
    padding: 12px 16px; border-radius: 10px; margin-bottom: 10px; font-size: 0.98rem;
}

.how-row {display: flex; gap: 12px; margin: 6px 0 4px 0; flex-wrap: wrap;}
.how-card {
    flex: 1; min-width: 180px; background: #F4F8F8; border: 1px solid #D5E6E4;
    border-radius: 14px; padding: 14px 16px; color: #12302E; font-size: 0.95rem;
}
.how-card .icon {font-size: 1.6rem; display: block; margin-bottom: 4px;}

.step {display: flex; align-items: center; gap: 12px; margin: 28px 0 12px 0;}
.step .num {
    background: #0F766E; color: white; width: 36px; height: 36px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center; font-weight: 700; flex-shrink: 0;
}
.step .title {font-size: 1.4rem; font-weight: 700; color: #12302E;}

.stButton > button, .stDownloadButton > button {border-radius: 12px; padding: 0.55rem 1.4rem; font-weight: 600;}
.stButton > button[kind="primary"] {
    background-color: #0F766E; border: 1px solid #0F766E; color: white;
}
.stButton > button[kind="primary"]:hover {
    background-color: #0B5F58; border-color: #0B5F58; color: white;
}
.stButton > button[kind="primary"]:disabled {
    background-color: #9FBFBC; border-color: #9FBFBC; color: white;
}
.stButton > button[kind="secondary"], .stDownloadButton > button {
    background-color: white; border: 1px solid #0F766E; color: #0F766E;
}
.stButton > button[kind="secondary"]:hover, .stDownloadButton > button:hover {
    background-color: #E3F0EF; color: #0B5F58; border-color: #0B5F58;
}

.badge, .tag {
    display: inline-block; border-radius: 20px; padding: 3px 12px;
    font-size: 0.82rem; font-weight: 600; margin-bottom: 8px;
}
.badge {background: #E3F0EF; color: #0B5F58;}
.tag-found {background: #DCFCE7; color: #14532D;}
.tag-missing {background: #FEF3C7; color: #78350F;}

.report-view {
    background: #F8FAFA; border: 1px solid #D5E6E4; border-radius: 12px;
    padding: 14px 16px; line-height: 1.7; font-size: 0.95rem; color: #12302E;
}
.small-note {color: #5B7774; font-size: 0.9rem; margin-bottom: 10px;}
.footer-box {
    text-align: center; color: #5B7774; font-size: 0.85rem;
    margin-top: 40px; padding-top: 16px; border-top: 1px solid #D5E6E4;
}
</style>
""", unsafe_allow_html=True)

SHEET_CSS = """
body{font-family:"Segoe UI","Noto Sans","Noto Sans Devanagari",Arial,sans-serif;font-size:13px;
color:#12302E;max-width:760px;margin:18px auto;padding:0 16px;line-height:1.5}
h1{font-size:22px;margin:0 0 2px 0;color:#0F766E}
h2{font-size:15px;margin:14px 0 4px 0;color:#0F766E;border-bottom:1px solid #D5E6E4;padding-bottom:2px}
h3{font-size:13px;margin:8px 0 2px 0}
.meta{color:#5B7774;margin:0 0 8px 0;font-size:12px}
.note{background:#FFF7E0;border-left:5px solid #F59E0B;padding:8px 12px;margin:8px 0}
.qa{margin:4px 0 6px 0}
.q{font-weight:600}
ol{margin:4px 0;padding-left:20px}
.foot{color:#5B7774;font-size:11px;margin-top:12px}
button{margin-top:10px;padding:8px 16px;border:0;border-radius:8px;background:#0F766E;
color:#fff;font-size:13px;cursor:pointer}
@page{margin:14mm}
@media print{.noprint{display:none}}
"""


# ---------- Helpers ----------
def step_header(number, title):
    st.markdown(
        f'<div class="step"><div class="num">{number}</div><div class="title">{title}</div></div>',
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=10)
def is_online(url):
    try:
        return requests.get(f"{url}/health", timeout=20).ok
    except Exception:
        return False


def split_sentences(text):
    """Same sentence splitting as Model B, so sentences match its source chunks."""
    text = re.sub(r"\b(Dr|Mr|Mrs|Ms)\.\s", r"\1 ", text)
    text = re.sub(r"\b([A-Z])\.\s", r"\1 ", text)
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]


def make_chips(report_text):
    low = report_text.lower()
    chips = []
    for word, question in CHIP_RULES:
        if word in low and question not in chips:
            chips.append(question)
    for g in GENERIC_CHIPS:
        if len(chips) >= 6:
            break
        if g not in chips:
            chips.append(g)
    return chips[:6]


def is_urgent(question):
    low = question.lower()
    return any(w in low for w in URGENT_WORDS)


def pick_highlight(report_text, sources, question, answer):
    """Pick the report sentence that best matches the question, from the best source chunk."""
    sentences = split_sentences(report_text)
    if not sources or not sentences:
        return []
    candidates = [s for s in sentences if s in sources[0]]
    if not candidates:
        return []
    brackets = " ".join(re.findall(r"\[([^\]]+)\]", answer))
    wanted = set(re.findall(r"[a-z]{4,}", f"{question} {brackets} {answer}".lower())) - STOP_WORDS
    scored = [(len(wanted & set(re.findall(r"[a-z]{4,}", s.lower()))), s) for s in candidates]
    best = max(score for score, _ in scored)
    if best == 0:
        return [candidates[0]]
    return [s for score, s in scored if score == best][:2]


def render_highlight(report_text, highlights):
    lines_html = []
    for line in report_text.splitlines():
        parts = []
        for s in split_sentences(line):
            safe = html.escape(s)
            if s in highlights:
                safe = ('<mark style="background:#FDE68A;color:#12302E;padding:2px 4px;'
                        f'border-radius:4px;">{safe}</mark>')
            parts.append(safe)
        if parts:
            lines_html.append(" ".join(parts))
    return '<div class="report-view">' + "<br>".join(lines_html) + "</div>"


def build_sheet(res, chat, res_lang_name):
    e = html.escape
    res_lang = res.get("language", "en")
    today = datetime.date.today().strftime("%d %B %Y")

    qa_html = ""
    unanswered = []
    for user_m, bot_m in zip(chat[0::2], chat[1::2]):
        if bot_m.get("found") is False:
            answer = "Not in the report. Please ask your doctor."
            unanswered.append(user_m["content"])
        elif bot_m.get("found"):
            answer = bot_m["content"]
        else:
            continue
        qa_html += (f'<div class="qa"><div class="q">Q: {e(user_m["content"])}</div>'
                    f'<div>A: {e(answer)}</div></div>')
    if not qa_html:
        qa_html = "<p>No questions asked yet.</p>"

    doctor_list = [f'About my question: "{q}"' for q in unanswered] + DOCTOR_QUESTIONS

    body = [f"<h1>Doctor visit sheet</h1><p class='meta'>Prepared on {today} with Radiology Report Helper</p>",
            f"<div class='note'>{e(DISCLAIMER)}</div>",
            "<h2>My report in simple words</h2>",
            f"<p>{e(res.get('simple_text', ''))}</p>"]
    if res_lang != "en":
        body.append(f"<h3>{e(res_lang_name)}</h3><p>{e(res.get('translated_text', ''))}</p>")
    body.append("<h2>What I asked, and what the report says</h2>" + qa_html)
    body.append("<h2>Questions to ask my doctor</h2><ol>"
                + "".join(f"<li>{e(x)}</li>" for x in doctor_list) + "</ol>")
    body.append("<p class='foot'>Private details were removed before the report was explained. "
                "Translations may contain errors. Please bring the original report to your appointment.</p>")
    body.append("<button class='noprint' onclick='window.print()'>Print or save as PDF</button>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Doctor visit sheet</title><style>"
            + SHEET_CSS + "</style></head><body>" + "".join(body) + "</body></html>")


def load_sample():
    st.session_state["report_box"] = SAMPLE_REPORT


def start_over():
    st.session_state["report_box"] = ""
    st.session_state.result = None
    st.session_state.report_id = None
    st.session_state.chat = []
    st.session_state.up_n += 1          # a new key resets the photo uploader


def use_suggestion(question):
    st.session_state["pending_q"] = question


# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### 🩻 About this tool")
    st.write("Paste or upload a radiology report. It is explained in simple words, "
             "and you can then ask questions about it.")
    st.markdown("**How it works**")
    st.markdown("1. Private details are removed.\n"
                "2. The report is explained in simple words.\n"
                "3. You can read it in English, Hindi or Konkani.\n"
                "4. The chatbot answers only from your report.")
    st.markdown("**Please remember**")
    st.markdown("- It does not diagnose.\n"
                "- It does not advise medicines.\n"
                "- Translations can have mistakes.\n"
                "- Always show the report to your doctor.\n"
                "- In an emergency, call 112.")
    st.markdown("**Service status**")
    st.write(("🟢" if is_online(MODEL_A_URL) else "🔴") + " Explain service")
    st.write(("🟢" if is_online(MODEL_B_URL) else "🔴") + " Question service")

# ---------- Header ----------
st.markdown(
    '<div class="hero">'
    '<div class="hero-title">🩻 Radiology Report Helper</div>'
    '<div class="hero-sub">Understand your report in simple words, in your own language.</div>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown(f'<div class="disclaimer">⚠️ {DISCLAIMER}</div>', unsafe_allow_html=True)

if st.session_state.result is None:
    st.markdown(
        '<div class="how-row">'
        '<div class="how-card"><span class="icon">📋</span><b>1. Add your report</b><br>Paste the text or upload a photo.</div>'
        '<div class="how-card"><span class="icon">📝</span><b>2. Read it simply</b><br>Private details are removed first.</div>'
        '<div class="how-card"><span class="icon">💬</span><b>3. Ask questions</b><br>Answers come only from your report.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

with st.container(border=True):
    lang_name = st.selectbox("🌐 Choose your language", list(LANGS))
lang = LANGS[lang_name]

# ---------- Step 1: Model A ----------
step_header(1, "Your report")
with st.container(border=True):
    text = st.text_area("Paste the report text", height=170, key="report_box",
                        placeholder="Paste the text of your radiology report here...")
    image = st.file_uploader(f"...or upload a photo of the report (PNG or JPG, up to {MAX_IMAGE_MB} MB)",
                             type=["png", "jpg", "jpeg"],
                             key=f"uploader_{st.session_state.up_n}")
    if image:
        st.image(image.getvalue(), caption="Your uploaded photo", width=220)
    consent = st.checkbox(CONSENT_TEXT, key="consent")
    c1, c2, c3 = st.columns([1.3, 1.2, 1])
    explain_clicked = c1.button("Explain my report", type="primary", disabled=not consent)
    c2.button("Try a sample report", on_click=load_sample)
    c3.button("Start over", on_click=start_over)
    if not consent:
        st.caption("Tick the box above to enable the Explain button.")

if explain_clicked:
    if not text.strip() and image is None:
        st.error("Please paste the report or upload a photo.")
    elif len(text) > MAX_TEXT_CHARS:
        st.error(f"The pasted text is too long. Please keep it under {MAX_TEXT_CHARS:,} characters.")
    elif image is not None and image.size > MAX_IMAGE_MB * 1024 * 1024:
        st.error(f"The photo is too large. Please upload a photo under {MAX_IMAGE_MB} MB.")
    else:
        files = {"image": (image.name, image.getvalue(), image.type)} if image else None
        with st.spinner("Reading your report... (the first request can be a little slower)"):
            try:
                r = requests.post(f"{MODEL_A_URL}/simplify", data={"text": text, "language": lang},
                                  files=files, headers=HEADERS, timeout=120)
                r.raise_for_status()
                st.session_state.result = r.json()
                st.session_state.report_id = None      # report must be indexed again
                st.session_state.chat = []
            except Exception as e:
                st.error(f"Could not explain the report. Please try again. ({str(e)[:120]})")

res = st.session_state.result
if res:
    res_lang = res.get("language", "en")
    res_lang_name = LANG_NAMES.get(res_lang, res_lang)

    st.markdown('<span class="badge">🔒 Private details removed</span>', unsafe_allow_html=True)
    if res_lang != lang:
        st.info(f"You changed the language to {lang_name}. Click **Explain my report** again to translate.")

    if res_lang != "en":
        tab_en, tab_tr = st.tabs(["📝 Simple English", f"🌐 {res_lang_name}"])
        with tab_en:
            st.write(res.get("simple_text", ""))
        with tab_tr:
            st.write(res.get("translated_text", ""))
    else:
        with st.container(border=True):
            st.markdown("#### 📝 In simple words")
            st.write(res.get("simple_text", ""))

    with st.expander("🔒 Report text (private details removed)"):
        st.text(res.get("report_text", ""))

    download = "Simple explanation:\n" + res.get("simple_text", "") + "\n\n"
    if res_lang != "en":
        download += f"{res_lang_name}:\n" + res.get("translated_text", "") + "\n\n"
    download += DISCLAIMER + "\n"
    st.download_button("⬇️ Download explanation", download, file_name="report_explanation.txt")
    st.caption(f"Model used: {res.get('model_used')}")

    # ---------- Step 2: Model B ----------
    step_header(2, "Ask a question about your report")
    st.markdown('<div class="small-note">The answers come only from your report. '
                'If it is not in the report, you will be asked to check with your doctor.</div>',
                unsafe_allow_html=True)

    style = st.radio("Answer style", ["Simple", "More detail"], horizontal=True, key="answer_style")
    detail = "more" if style == "More detail" else "simple"

    def index_report():
        r = requests.post(f"{MODEL_B_URL}/index", json={"report_text": res["report_text"]},
                          headers=HEADERS, timeout=120)
        r.raise_for_status()
        st.session_state.report_id = r.json()["report_id"]

    def ask(question, detail_level):
        for _ in range(2):                              # second try = index again if the server forgot the report
            if not st.session_state.get("report_id"):
                index_report()
            r = requests.post(f"{MODEL_B_URL}/ask", headers=HEADERS, timeout=120,
                              json={"report_id": st.session_state.report_id,
                                    "question": question, "language": lang,
                                    "detail": detail_level})
            if r.status_code == 404:
                st.session_state.report_id = None
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Report not found")

    st.markdown("**Questions about your report. Tap one:**")
    cols = st.columns(2)
    for i, s in enumerate(make_chips(res["report_text"])):
        cols[i % 2].button(s, key=f"chip_{i}", on_click=use_suggestion, args=(s,))

    chat = st.session_state.chat
    for idx, m in enumerate(chat):
        with st.chat_message(m["role"], avatar=AVATARS[m["role"]]):
            if m.get("urgent"):
                st.markdown(URGENT_HTML, unsafe_allow_html=True)
            if m["role"] == "assistant" and "found" in m:
                if m["found"]:
                    st.markdown('<span class="tag tag-found">✓ Found in your report</span>',
                                unsafe_allow_html=True)
                else:
                    st.markdown('<span class="tag tag-missing">⚠ Not in your report</span>',
                                unsafe_allow_html=True)
            st.write(m["content"])
            if m.get("highlight"):
                with st.expander("🔎 See it in your report", expanded=(idx == len(chat) - 1)):
                    st.markdown(render_highlight(res["report_text"], m["highlight"]),
                                unsafe_allow_html=True)
            if m.get("sources"):
                with st.expander("📄 Where this answer came from"):
                    for s in m["sources"]:
                        st.write("• " + s)

    st.download_button("📄 Download doctor visit sheet", build_sheet(res, chat, res_lang_name),
                       file_name="doctor_visit_sheet.html", mime="text/html")
    st.caption("Open the file and click Print or save as PDF, then choose Save as PDF.")

    pending = st.session_state.pop("pending_q", None)
    typed = st.chat_input("Type your question")
    q = typed or pending
    if q:
        urgent = is_urgent(q)
        st.chat_message("user", avatar=AVATARS["user"]).write(q)
        if urgent:
            st.markdown(URGENT_HTML, unsafe_allow_html=True)
        st.session_state.chat.append({"role": "user", "content": q})
        try:
            with st.spinner("Looking in your report..."):
                out = ask(q, detail)
            answer = out["answer"]
            found = answer.strip() not in NOT_FOUND_TEXTS
            highlight = pick_highlight(res["report_text"], out["sources"], q, answer) if found else []
            st.session_state.chat.append(
                {"role": "assistant", "content": answer, "sources": out["sources"],
                 "found": found, "highlight": highlight, "urgent": urgent})
        except Exception as e:
            st.session_state.chat.append(
                {"role": "assistant", "urgent": urgent,
                 "content": f"Could not answer right now. Please try again. ({str(e)[:120]})"})
        st.rerun()

# ---------- Footer ----------
st.markdown(f'<div class="footer-box">{DISCLAIMER}<br>'
            'Translations may contain errors. Konkani has limited training data.</div>',
            unsafe_allow_html=True)