import httpx
BASE = "http://127.0.0.1:8010"
with httpx.Client(base_url=BASE, timeout=60) as c:
    ok = True
    docs = c.get("/documents").json()
    d = docs[0]
    print("doc:", d["filename"], "title:", d["title"], "verified:", d["citation_verified"])

    # 1. crossref auto (title search, network to crossref.org)
    a = c.get("/documents/" + d["id"] + "/citations/auto").json()
    auto = a.get("auto", [])
    print("auto:", a["source"], len(auto), "candidates; top:",
          (auto[0].get("title", "") if auto else "(none)")[:60],
          "conf:", auto[0].get("confidence") if auto else "-")

    # 2. confirm (with verified metadata) -> export shows it
    meta = {"title": "Verification flow title", "authors": ["Doe, J.", "Roe, R."],
            "year": 2024, "journal": "Journal of Testing", "doi": "10.9999/test"}
    r = c.post("/documents/" + d["id"] + "/citations/confirm", json={"metadata": meta}).json()
    print("confirm:", r["saved"], "verified now:", r["doc"]["citation_verified"], "title:", r["doc"]["title"])
    if not r["saved"]:
        ok = False

    # 3. export apa + vancouver
    e1 = c.get("/citations/export?style=apa").json()
    e2 = c.get("/citations/export?style=vancouver").json()
    print("apa:", e1["count"], "| first:", (e1["references"][0] if e1["references"] else "none"))
    print("van:", e2["count"], "| first:", (e2["references"][0] if e2["references"] else "none"))

    # 4. resolve raw markers
    res = c.post("/citations/resolve", json={
        "answer_text": "Answer text [SRC:" + d["id"] + "|p4] end.",
        "sources": [{"document_id": d["id"], "page": 4}],
        "style": "vancouver"}).json()
    print("resolved:", res["answer"])
    print("refs:", [x["key"] + " " + x["text"][:40] for x in res["references"]])

    # 5. restore the doc's original (empty) metadata via direct sqlite —
    #    there is no 'unverify' endpoint by design (confirm is intentional).
    import sqlite3
    conn = sqlite3.connect(r"E:\code\14\V2\data\app.db")
    conn.execute("UPDATE documents SET title=NULL, authors=NULL, year=NULL, journal=NULL, doi=NULL, citation_verified=0 WHERE id=?", (d["id"],))
    conn.commit()
    back = conn.execute("SELECT title, citation_verified FROM documents WHERE id=?", (d["id"],)).fetchone()
    conn.close()
    print("restored via sqlite:", back)

    print("ALL PASS" if ok else "SOME CHECKS FAILED")