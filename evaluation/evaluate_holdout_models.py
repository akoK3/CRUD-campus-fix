
from __future__ import annotations
import json, os, re, time, urllib.request
from pathlib import Path
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

HERE=Path(__file__).resolve().parent
MODEL=os.getenv("OLLAMA_MODEL","deepseek-r1:8b")
ISSUE_TO_DEPT={"Plumbing":"Facilities","Electrical":"Facilities","Heating / Cooling":"Facilities","Lighting":"Facilities","Furniture":"Facilities","Door / Lock":"Facilities","Window":"Facilities","Structural":"Facilities","Cleaning / Custodial":"Facilities","Pest Control":"Facilities","Elevator":"Facilities","Wi-Fi / Network":"IT","Classroom Technology":"IT","Computer / Hardware":"IT","Account / Login":"IT","Software":"IT","Printing":"IT","Residence Maintenance":"Residence Life","Housing / Roommate":"Residence Life","Laundry":"Residence Life","Event Support":"Student Engagement","Club / Organization":"Student Engagement","Parking":"Campus Safety","ID Card":"Campus Safety","Safety Hazard":"Campus Safety","Lost and Found":"Campus Safety","Security Concern":"Campus Safety","Dining Service":"Dining","Meal Plan":"Dining","Vending":"IT","Billing":"Finance","Financial Aid":"Finance","Other":"Other / Review"}
PRIORITIES=["LOW","MEDIUM","HIGH","URGENT","NEEDS_REVIEW"]
SYSTEM="""Classify CampusFix university tickets. Return ONLY JSON:
{"issue_type":"...", "priority":"..."}
Allowed issue types: %s
Priorities: LOW, MEDIUM, HIGH, URGENT, NEEDS_REVIEW.
LOW=minor/cosmetic. MEDIUM=functional issue that can wait. HIGH=major disruption or serious near-term consequence. URGENT=immediate credible safety/security risk, active serious damage, active dorm lockout/access emergency, or critical large-scope outage. NEEDS_REVIEW=too vague, multiple distinct problems, or no matching category. Ignore emotional wording. Real deadlines, recurrence, accessibility and scope may raise priority. Planned outages lower priority. Dorm lockout/lost room key => Door / Lock + URGENT. Vending => Vending.""" % ", ".join(ISSUE_TO_DEPT)

def ask(text):
    payload={"model":MODEL,"stream":False,"think":False,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":text}],"options":{"temperature":0}}
    req=urllib.request.Request("http://localhost:11434/api/chat",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=180) as r:
        data=json.loads(r.read().decode())
    m=re.search(r"\{.*\}",data["message"]["content"].strip(),re.S)
    if not m: raise ValueError("No JSON")
    return json.loads(m.group(0))

def main():
    df=pd.read_csv(HERE/"campusfix_benchmark_v2.csv")
    h=df[df["split"].astype(str).str.lower()=="holdout"].reset_index(drop=True)
    outdir=HERE/"evaluation_results_holdout"; outdir.mkdir(exist_ok=True)
    safe=re.sub(r"[^A-Za-z0-9._-]+","_",MODEL); path=outdir/f"{safe}_holdout_predictions.csv"
    rows=[]
    for i,row in h.iterrows():
        t=time.perf_counter()
        try:
            obj=ask(str(row["text"]))
            issue=str(obj.get("issue_type","Other")); priority=str(obj.get("priority","NEEDS_REVIEW"))
            if issue not in ISSUE_TO_DEPT: issue="Other"
            if priority not in PRIORITIES: priority="NEEDS_REVIEW"
        except Exception as e:
            print("ERROR",row["case_id"],e); issue,priority="Other","NEEDS_REVIEW"
        dept=ISSUE_TO_DEPT[issue]; lat=time.perf_counter()-t
        rows.append({"case_id":row["case_id"],"expected_issue_type":row["expected_issue_type"],"expected_department":row["expected_department"],"expected_priority":row["expected_priority"],"pred_issue_type":issue,"pred_department":dept,"pred_priority":priority,"latency_seconds":lat})
        pd.DataFrame(rows).to_csv(path,index=False)
        print(f"[{i+1:02d}/{len(h)}] {row['case_id']} | {issue} / {priority} | {lat:.2f}s")
    out=pd.DataFrame(rows)
    print("\n=== HOLDOUT METRICS ===")
    for a,b in [("expected_issue_type","pred_issue_type"),("expected_department","pred_department"),("expected_priority","pred_priority")]:
        y1,y2=out[a].astype(str),out[b].astype(str)
        print(f"{a.replace('expected_',''):12s} | accuracy={accuracy_score(y1,y2):.3f} | macro-F1={f1_score(y1,y2,average='macro',zero_division=0):.3f}")
    print(f"\nmean_latency={out['latency_seconds'].mean():.2f}s\nMODEL={MODEL}\nSaved: {path}")

if __name__=="__main__": main()
