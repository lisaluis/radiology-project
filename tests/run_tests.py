"""Runs the questions in questions.csv against Model B and saves results.csv.
Usage: $env:START=1; $env:END=30; python run_tests.py
"""
import csv, os, sys, time
import requests

BASE = os.getenv("MODEL_B_URL", "http://127.0.0.1:8001")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.getenv("REPORTS_DIR", os.path.join(HERE, "..", "model_a", "fake_reports", "text"))
START = int(os.getenv("START", "1"))
END = int(os.getenv("END", "100000"))
PAUSE = float(os.getenv("SLEEP", "4"))
NOT_FOUND = "not in your report"

rows = list(csv.DictReader(open(os.path.join(HERE, "questions.csv"), encoding="utf-8-sig")))
rows = [r for r in rows if START <= int(r["id"]) <= END]
ids = {}
out = []
passed = checked = bad_in_a_row = 0

for row in rows:
    n = int(row["report"])
    try:
        if n not in ids:
            text = open(os.path.join(REPORTS, f"report_{n:02d}.txt"), encoding="utf-8").read()
            r = requests.post(f"{BASE}/index", json={"report_text": text}, timeout=120)
            r.raise_for_status()
            ids[n] = r.json()["report_id"]
        answer = None
        for attempt in range(3):
            r = requests.post(f"{BASE}/ask", json={"report_id": ids[n], "question": row["question"],
                                                  "language": row["language"]}, timeout=120)
            if r.status_code == 200:
                answer = r.json().get("answer")
                break
            answer = f"ERROR {r.status_code}: {r.text[:100]}"
            if "RESOURCE_EXHAUSTED" in r.text or "429" in r.text:
                break
            time.sleep(15)
    except Exception as e:
        answer = f"ERROR: {str(e)[:100]}"

    low = (answer or "").lower()
    if answer.startswith("ERROR"):
        verdict = "ERROR"
        bad_in_a_row += 1
    else:
        bad_in_a_row = 0
        if row["language"] != "en":
            verdict = "MANUAL"
        elif row["answerable"] == "no":
            verdict = "PASS" if NOT_FOUND in low else "CHECK"
        else:
            keys = [k for k in row["keywords"].split("|") if k]
            verdict = "PASS" if any(k in low for k in keys) else "CHECK"
        if row["language"] == "en":
            checked += 1
            passed += verdict == "PASS"

    out.append({**row, "answer": answer, "verdict": verdict})
    print(f'{row["id"]:>3} [{row["language"]}] {verdict:6} {row["question"][:50]}')

    if bad_in_a_row >= 3:
        print("\nSTOPPED: 3 errors in a row. Gemini is probably refusing requests (quota used up).")
        print("Wait, then run again from the next id, for example:  $env:START=%s; python run_tests.py" % row["id"])
        break
    time.sleep(PAUSE)

if out:
    with open("results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
print(f"\nEnglish questions auto-passed: {passed}/{checked} (errors are not counted)")
print("Open results.csv. Read every CHECK, MANUAL and ERROR row.")
