"""Runs every question in questions.csv against Model B and saves results.csv.
Usage:  python run_tests.py            (Model B must be running on port 8001)
"""
import csv, os, sys, time
import requests
 
BASE = os.getenv("MODEL_B_URL", "http://127.0.0.1:8001")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.getenv("REPORTS_DIR", os.path.join(HERE, "..", "model_a", "fake_reports", "text"))
NOT_FOUND = "not in your report"
 
rows = list(csv.DictReader(open(os.path.join(HERE, "questions.csv"), encoding="utf-8-sig")))
ids = {}  # report number -> report_id on Model B
out = []
passed = checked = 0
 
for row in rows:
    n = int(row["report"])
    if n not in ids:
        text = open(os.path.join(REPORTS, f"report_{n:02d}.txt"), encoding="utf-8").read()
        r = requests.post(f"{BASE}/index", json={"report_text": text}, timeout=60)
        r.raise_for_status()
        ids[n] = r.json()["report_id"]
    answer = None
    for attempt in range(3):            # retry when Gemini is busy (503 / 429)
        try:
            r = requests.post(f"{BASE}/ask", json={"report_id": ids[n], "question": row["question"],
                                                  "language": row["language"]}, timeout=120)
            if r.status_code == 200:
                answer = r.json().get("answer")
                break
            answer = f"ERROR {r.status_code}: {r.text[:100]}"
        except Exception as e:
            answer = f"ERROR: {e}"
        time.sleep(15)
    low = (answer or "").lower()
 
    if row["language"] != "en":
        verdict = "MANUAL"            # Hindi/Konkani answers are checked by a human
    elif row["answerable"] == "no":
        verdict = "PASS" if NOT_FOUND in low else "CHECK"
    else:
        keys = [k for k in row["keywords"].split("|") if k]
        verdict = "PASS" if any(k in low for k in keys) else "CHECK"
 
    if verdict == "PASS": passed += 1
    if row["language"] == "en": checked += 1
    out.append({**row, "answer": answer, "verdict": verdict})
    print(f'{row["id"]:>3} [{row["language"]}] {verdict:6} {row["question"][:50]}')
    time.sleep(float(os.getenv("SLEEP", "2")))   # be gentle with the free quota
 
with open("results.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)
print(f"\nEnglish questions auto-passed: {passed}/{checked}")
print("Open results.csv. Check every CHECK and MANUAL row by reading the answer.")
 
 