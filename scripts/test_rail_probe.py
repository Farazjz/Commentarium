# Probe: do the bottom rail buttons (settings/logs/exit) "disappear"?
# Samples their geometry + rail mutation on dashboard & citations.
from playwright.sync_api import sync_playwright

JS = """() => {
const rail = document.querySelector('.rail');
const out = {railChildren: rail ? rail.children.length : -1,
             railH: rail ? Math.round(rail.getBoundingClientRect().height) : -1};
out.buttons = ['[data-view="settings"]', '[data-view="logs"]', '#railExit'].map(sel => {
    const b = document.querySelector(sel);
    if (!b) return 'MISSING';
    const r = b.getBoundingClientRect();
    const cs = getComputedStyle(b);
    return `y=${Math.round(r.top)} h=${Math.round(r.height)} disp=${cs.display} vis=${cs.visibility}`;
});
return JSON.stringify(out);
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    for name, url in [("dashboard", "http://127.0.0.1:8010/#/dashboard"),
                      ("citations", "http://127.0.0.1:8010/#/citations")]:
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1500)
        for i in range(6):
            print(f"[{name}] t+{i*3}s:", page.evaluate(JS))
            page.wait_for_timeout(2800)
        # also capture any inline style/variant changes on the rail itself
        print(f"[{name}] rail html tail:", page.evaluate(
            "() => document.querySelector('.rail').outerHTML.slice(-320)"))
        print("---")
    print("pageerrors:", errs or "NONE")
    b.close()
