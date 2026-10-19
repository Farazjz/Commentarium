import time
import fitz
import httpx

BASE = "http://127.0.0.1:8010"


def make_pdf(name):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Commentarium async ingest test - " + name, fontsize=14)
    page.insert_text((72, 120), "Retrieval augmented generation for a thesis on dental caries.", fontsize=11)
    return doc.tobytes()


with httpx.Client(base_url=BASE, timeout=120) as c:
    ok = True
    proj = c.get("/projects").json()
    pid = proj[0]["id"]
    pname = proj[0]["name"]
    print("project:", pid, pname)

    # 1. upload + async ingest (auto mode)
    r = c.post("/projects/" + pid + "/ingest-async",
               files={"file": ("ingest-live-a.pdf", make_pdf("A"), "application/pdf")},
               data={"ocr_mode": "auto"})
    print("upload-a:", r.status_code, list(r.json()))
    jid, doc_id = r.json()["job_id"], r.json()["document"]["id"]
    # poll job
    deadline = time.time() + 90
    job = None
    while time.time() < deadline:
        job = c.get("/jobs/" + jid).json()
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(1.0)
    print("job-a:", job["status"], "err:", job["error"])
    if job["status"] != "completed":
        ok = False

    # document status morphed to indexed with page_count
    docs = c.get("/documents").json()
    mine = [d for d in docs if d["id"] == doc_id][0]
    print("doc-a:", mine["status"], "pages:", mine["page_count"], "mode:", mine["index_mode"])
    if mine["status"] != "indexed" or not mine["page_count"]:
        ok = False
    # 2. re-index via async endpoint (direct mode)
    r2 = c.post("/documents/" + doc_id + "/ingest-async?ocr_mode=direct")
    print("reindex:", r2.status_code)
    jid2 = r2.json()["job_id"]
    deadline = time.time() + 90
    job2 = None
    while time.time() < deadline:
        job2 = c.get("/jobs/" + jid2).json()
        if job2["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(1.0)
    print("job-b:", job2["status"], "err:", job2["error"])
    if job2["status"] != "completed":
        ok = False

    # 3. invalid ocr_mode -> 400
    r3 = c.post("/documents/" + doc_id + "/ingest-async?ocr_mode=bogus")
    print("bad mode:", r3.status_code, r3.json())
    if r3.status_code != 400:
        ok = False

    # cleanup both docs
    r4 = c.delete("/documents/" + doc_id)
    print("cleanup-a:", r4.status_code, r4.json()["deleted"])
    left = [d for d in c.get("/documents").json() if d["filename"].startswith("ingest-live")]
    print("leftover:", left)
    if left:
        ok = False

    print("ALL PASS" if ok else "SOME CHECKS FAILED")
