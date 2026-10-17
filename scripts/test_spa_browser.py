# Browser E2E for the v2 SPA: loads every view, clicks nav buttons,
# opens modals, asserts no console/page errors. Catches ReferenceErrors
# that node --check cannot see.
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)

    def snap(name):
        view = page.eval_on_selector("#view", "n => n.textContent.slice(0, 80)")
        print(f"[{name}] view: {view!r}")

    # dashboard rendered?
    h2 = page.eval_on_selector_all(".content h1", "els => els.map(e => e.textContent)")
    print("dashboard h1:", [t[:60] for t in h2])
    assert h2, "dashboard did not render"

    # click every rail button; each must switch the view without page errors
    for vid in ["library", "chat", "podcast", "citations", "ocr", "settings", "logs", "dashboard"]:
        page.click(f'.railbtn[data-view="{vid}"]')
        page.wait_for_timeout(900)
        active = page.eval_on_selector(".railbtn.active", "b => b.dataset.view")
        crumb = page.eval_on_selector("#crumbView", "e => e.textContent")
        snap(vid)
        assert active == vid, f"rail click {vid}: active={active}"
        print(f"  crumb: {crumb}")

    # theme toggle button
    page.click("#themeBtn")
    page.wait_for_timeout(200)
    theme = page.eval_on_selector("html", "e => e.dataset.theme")
    assert theme == "light", f"theme toggle failed: {theme}"
    page.click("#themeBtn")
    print("theme toggle: OK")

    # Ctrl+K palette
    page.keyboard.press("Control+k")
    page.wait_for_timeout(300)
    opened = page.eval_on_selector("#paletteOverlay", "e => e.classList.contains('open')")
    assert opened, "palette did not open"
    page.keyboard.type("pod")
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    page.wait_for_timeout(700)
    active = page.eval_on_selector(".railbtn.active", "b => b.dataset.view")
    print("palette go:", active)
    assert active == "podcast", f"palette nav failed: {active}"

    # podcast: New episode modal opens
    page.click('button:has-text("New episode")')
    page.wait_for_timeout(400)
    modal = page.query_selector(".modal h3")
    print("podcast modal:", modal.text_content() if modal else None)
    assert modal and "New podcast" in modal.text_content(), "generate modal missing"
    page.click('.modal .btn:not(.primary):has-text("Cancel")')
    page.wait_for_timeout(200)

    # OCR view: Extract modal
    page.click('.railbtn[data-view="ocr"]')
    page.wait_for_timeout(800)
    page.click('button:has-text("Extract from file")')
    page.wait_for_timeout(400)
    modal = page.query_selector(".modal h3")
    print("ocr modal:", modal.text_content() if modal else None)
    assert modal and "OCR" in modal.text_content(), "ocr modal missing"
    page.click('.modal .btn:not(.primary):has-text("Cancel")')

    # settings view renders grouped cards
    page.click('.railbtn[data-view="settings"]')
    page.wait_for_timeout(1000)
    cards = page.eval_on_selector_all(".content h3", "els => els.map(e => e.textContent)")
    print("settings cards:", cards[:5])
    assert any("Gateway" in c for c in cards), "groups missing"

    # logs view renders log lines
    page.click('.railbtn[data-view="logs"]')
    page.wait_for_timeout(1200)
    logs_head = page.eval_on_selector(".logbox", "e => e.textContent.slice(0, 90)")
    print("logs head:", logs_head)
    assert logs_head and "(no log lines)" not in logs_head, "logbox empty"

    print("errors:", errors if errors else "NONE")
    print("ALL PASS")
    browser.close()

if errors:
    print("PAGE ERRORS DETECTED", file=sys.stderr)
    sys.exit(2)
