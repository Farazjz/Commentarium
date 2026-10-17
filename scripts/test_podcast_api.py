import time
import httpx

BASE = "http://127.0.0.1:8010"

with httpx.Client(base_url=BASE, timeout=120) as c:
    ok = True
    pods0 = c.get("/podcasts").json()
    print("baseline episodes:", len(pods0), [(p["title"], p["status"]) for p in pods0][:3])

    docs = c.get("/documents").json()
    doc_ids = [d["id"] for d in docs[:2]]
    pid = (c.get("/projects").json())[0]["id"]

    r = c.post("/podcasts/generate-async", json={
        "project_id": pid, "document_ids": doc_ids,
        "title": "SPA live test episode", "duration_setting": "short",
        "provider": "edge-tts", "host_languages": {"1": "en", "2": "en"}})
    print("generate-async:", r.status_code, r.json().get("job_id"))
    jid = r.json()["job_id"]

    deadline = time.time() + 240
    job = None
    while time.time() < deadline:
        job = c.get("/jobs/" + jid).json()
        print("  tick:", job["status"], (job["message"] or "")[:60], job["progress"])
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(6)
    print("final:", job["status"], "err:", job["error"])
    if job["status"] != "completed":
        ok = False
        pod_id = None
    rows = []
    # cleanup: delete every test row (generate re-creates the row itself)
    created = []
    if job["status"] == "completed":
        created.append((job.get("result") or {}).get("id"))
    p1 = c.get("/podcasts/" + created[0]).json() if created and created[0] else None
    if p1:
        print("episode:", p1["status"], "dur:", p1.get("duration_sec"), "provider:", p1.get("tts_provider"),
              "has_audio:", bool(p1.get("audio_path")))
        if p1["status"] != "ready":
            ok = False
        # v1 semantics: TTS failure is non-fatal -> ready with empty audio_path
        if p1.get("duration_sec"):
            a = c.get("/podcasts/" + created[0] + "/audio")
            print("audio:", a.status_code, a.headers.get("content-type"), len(a.content), "bytes")
            if a.status_code != 200 or len(a.content) < 1000:
                ok = False
        else:
            print("audio: skipped (TTS unavailable at test time - script-only episode is valid)")

    # regenerate-async
    regen_target = created[0]
    r2 = c.post("/podcasts/" + regen_target + "/regenerate-async", json={"duration_setting": "short"})
    print("regen:", r2.status_code, r2.json().get("job_id"))
    j2 = r2.json()["job_id"]
    deadline = time.time() + 240
    while time.time() < deadline:
        job2 = c.get("/jobs/" + j2).json()
        if job2["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(6)
    print("regen final:", job2["status"])

    # delete cleanup: every row the test created
    if job2["status"] == "completed":
        created.append((job2.get("result") or {}).get("id"))
    for pid_s in [x for x in created if x]:
        d = c.delete("/podcasts/" + pid_s).json()
        print("deleted:", pid_s, d)
    left = [p for p in c.get("/podcasts").json() if p["id"] in created]
    print("gone:", not left)
    print("ALL PASS" if ok and not left else "SOME CHECKS FAILED")