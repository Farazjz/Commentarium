import json
import time

import httpx

BASE = "http://127.0.0.1:8010"

with httpx.Client(base_url=BASE, timeout=120) as c:
    ok = True
    # 1. fresh session
    s = c.post("/chat/sessions?project_id=&title=SPA+live+test").json()
    sid = s["id"]
    print("session:", sid, repr(s.get("title")))

    # 2. ask a grounded question
    r = c.post("/chat/ask", json={"question": "In one sentence, what do these papers say about sugar/food frequency and dental caries risk?",
                                  "session_id": sid, "distributed": False, "style": "vancouver"})
    print("ask:", r.status_code)
    res = r.json()
    if "error" in res:
        print("ERROR:", res["error"])
        ok = False
    else:
        print("answer:", res["answer"][:180].replace("\n", " "))
        print("sources:", [(x["document_id"], x["page"]) for x in res["sources"]])
        print("refs:", [x["key"] for x in res["references"]])
        print("retrieved:", res["retrieved"], "style:", res["citation_style"])
        if not res["sources"] or not res["references"]:
            ok = False

    # 3. history: stored assistant content equals live answer (no raw markers
    #    in this build — inline resolution happens at ask time in the UI)
    hist = c.get("/chat/sessions/" + sid + "/messages").json()
    print("history roles:", [m["role"] for m in hist])
    amsg = [m for m in hist if m["role"] == "assistant"][-1]
    has_src = "[SRC:" in amsg["content"]
    stored_eq = amsg["content"] == res["answer"]
    print("stored has SRC markers:", has_src, "| stored==live:", stored_eq)
    print("sources json:", amsg["sources"][:80])
    if not stored_eq:
        ok = False

    # 4. rename + list + delete
    c.patch("/chat/sessions/" + sid, json={"title": "renamed live test"})
    listed = c.get("/chat/sessions").json()
    titles = [x["title"] for x in listed]
    print("sessions now:", titles[:6])
    print("delete:", c.delete("/chat/sessions/" + sid).json())
    after = [x["id"] for x in c.get("/chat/sessions").json()]
    print("gone:", sid not in after)
    if sid in after:
        ok = False

    print("ALL PASS" if ok else "SOME CHECKS FAILED")
