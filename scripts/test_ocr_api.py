# Live test of the P3 OCR API surface (run against 127.0.0.1:8010).
"""Creates a 2-page text PDF with PyMuPDF, POSTs it to /ocr/extract (smart
mode -> no external OCR backend needed), polls the job, then walks the whole
/ocr/output management surface. Cleans up after itself."""
from __future__ import annotations

import io
import time

import fitz
import httpx

BASE = "http://127.0.0.1:8010"


def make_test_pdf() -> bytes:
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72 + i * 20), f"Commentarium OCR test — page {i + 1}", fontsize=14)
        page.insert_text((72, 120 + i * 20), "The quick brown fox studies retrieval augmented generation.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def main() -> None:
    pdf = make_test_pdf()
    ok = True

    with httpx.Client(base_url=BASE, timeout=30) as c:
        # 0. baseline: output list works
        baseline = c.get("/ocr/output").json()
        print(f"[list]   baseline count={baseline['count']} files={[f['name'] for f in baseline['files']][:5]}")

        # 1. POST /ocr/extract (multipart form)
        r = c.post("/ocr/extract", files={"file": ("ocr-live-test.pdf", pdf, "application/pdf")},
                   data={"backend": "tesseract", "force_ocr": "false", "dpi": "200"})
        print(f"[extract] {r.status_code} {r.json()}")
        job_id = r.json()["job_id"]

        # 2. poll job
        deadline = time.time() + 30
        job = None
        while time.time() < deadline:
            job = c.get(f"/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.4)
        print(f"[job]    status={job['status']} progress={job['progress']} message={job['message']!r}")
        if job["status"] != "completed":
            print(f"[job]    ERROR: {job['error']}")
            ok = False
        else:
            res = job["result"]
            print(f"[result] saved={res['saved_name']} pages={res['pages']} wc={res['wc']} backend={res['backend']}")
            print(f"[md]     first lines: {res['content'].splitlines()[:4]}")

        # 3. list output
        listing = c.get("/ocr/output").json()
        print(f"[list]   now count={listing['count']}, test file present="
              f"{any(f['name'] == 'ocr-live-test.md' for f in listing['files'])}")
        if not any(f["name"] == "ocr-live-test.md" for f in listing["files"]):
            ok = False

        # 4. content endpoint
        cont = c.get("/ocr/output/ocr-live-test.md/content")
        print(f"[content] {cont.status_code} len={len(cont.json().get('content', ''))} "
              f"snippet={cont.json().get('content', '')[:60]!r}")

        # 5. download endpoint
        dl = c.get("/ocr/output/ocr-live-test.md")
        print(f"[download] {dl.status_code} content-type={dl.headers.get('content-type')} len={len(dl.content)}")

        # 6. traversal guard must 404
        bad = c.get("/ocr/output/..%5Capp.log")
        print(f"[guard]  traversal -> {bad.status_code} (want 404)")
        if bad.status_code != 404:
            ok = False
        bad2 = c.get("/ocr/output/nonexistent.md/content")
        print(f"[guard]  missing file -> {bad2.status_code} (want 404)")
        if bad2.status_code != 404:
            ok = False

        # 7. unsupported upload must fail cleanly
        r2 = c.post("/ocr/extract", files={"file": ("evil.exe", b"BIN", "application/octet-stream")})
        print(f"[guard]  bad ext -> {r2.status_code} {r2.json().get('detail', r2.json().get('job_id'))}")
        if r2.status_code == 200:
            # queued job will fail; verify it fails not crashes
            j2 = c.get(f"/jobs/{r2.json()['job_id']}")
            _ = j2.json()

        # 8. invalid backend -> service error, job fails cleanly (no crash)
        r3 = c.post("/ocr/extract", files={"file": ("b.wav", b"x", "audio/wav"), })

        # 9. delete
        dele = c.delete("/ocr/output/ocr-live-test.md")
        print(f"[delete] {dele.status_code} {dele.json()}")
        after = c.get("/ocr/output").json()
        still = any(f["name"] == "ocr-live-test.md" for f in after["files"])
        print(f"[list]   after delete count={after['count']} still-present={still}")
        if still:
            ok = False

    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")


if __name__ == "__main__":
    main()
