"""Validate the dashboard UI in a real browser: layout, tabs, no errors.

Run (server must be up):  .venv/bin/python tools/ui_check.py
Exit non-zero if a hard failure is found, so it can gate a release.
"""

from __future__ import annotations

import sys
from collections import Counter

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8787"


def main() -> int:
    errors: list[str] = []
    fails: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        # --- Monitor ------------------------------------------------------
        cards = page.query_selector_all("#theses .card")
        syms = [c.query_selector(".sym").inner_text().strip() for c in cards]
        dupes = {s: c for s, c in Counter(syms).items() if c > 1}
        print(f"[monitor] cards={len(cards)} unique={len(set(syms))} dupes={len(dupes)}")
        if dupes:
            fails.append(f"monitor duplicate cards: {list(dupes)[:5]}")
        page.screenshot(path="/tmp/ui-monitor.png", full_page=False)

        # --- All tickers --------------------------------------------------
        page.click("#tab-tickers")
        page.wait_for_timeout(2500)

        # filter bar must sit on the same line band as the title (no float-out)
        geom = page.eval_on_selector("#view-tickers h2", """h => {
          const hr = h.getBoundingClientRect();
          const se = document.querySelector('#tk-search').getBoundingClientRect();
          return {h2h: Math.round(hr.height), titleTop: Math.round(hr.top),
                  searchTop: Math.round(se.top),
                  overlaps: se.top < hr.bottom && se.top >= hr.top};
        }""")
        print(f"[tickers] header h={geom['h2h']} searchTop={geom['searchTop']} "
              f"titleTop={geom['titleTop']} aligned={geom['overlaps']}")
        if geom["h2h"] < 30 and not geom["overlaps"]:
            fails.append("filter bar floated out of the header row")

        rows = page.query_selector_all("#tk-table tbody tr")
        tsyms = [r.query_selector("td").inner_text().strip() for r in rows
                 if r.query_selector("td")]
        tdupes = {s: c for s, c in Counter(tsyms).items() if c > 1}
        print(f"[tickers] rows={len(tsyms)} unique={len(set(tsyms))} dupes={len(tdupes)}")
        if tdupes:
            fails.append(f"ticker duplicate rows: {list(tdupes)[:5]}")
        page.screenshot(path="/tmp/ui-tickers.png", full_page=False)

        # search works
        page.fill("#tk-search", "MS")
        page.wait_for_timeout(700)
        got = [r.query_selector("td").inner_text().strip()
               for r in page.query_selector_all("#tk-table tbody tr")
               if r.query_selector("td")]
        print(f"[tickers] search 'MS' -> {got}")
        if not all("MS" in s for s in got):
            fails.append(f"search returned non-matching rows: {got}")
        page.fill("#tk-search", "")
        page.wait_for_timeout(600)

        # --- Plugins ------------------------------------------------------
        page.click("#tab-plugins")
        page.wait_for_timeout(1200)
        tabs = page.query_selector_all("#kindtab button")
        heads = page.query_selector_all(".kindhead h3")
        plug = page.query_selector_all(".plugin")
        print(f"[plugins] kind tabs={len(tabs)} kindhead={len(heads)} cards={len(plug)}")
        if len(tabs) < 5:
            fails.append("plugin kind tabs missing")
        if not heads:
            fails.append("plugin kind explanation missing")
        tab_text = [t.inner_text().strip() for t in tabs]
        print(f"[plugins] tabs: {tab_text}")
        page.screenshot(path="/tmp/ui-plugins.png", full_page=False)

        # --- Deep dive ----------------------------------------------------
        page.click("#tab-tickers")
        page.wait_for_timeout(2000)
        first = page.query_selector("#tk-table tbody tr.tk-row")
        if first:
            first.click()
            page.wait_for_timeout(2500)
            canvas = page.query_selector("#detail-chart")
            ai = page.query_selector("#ai-box")
            print(f"[detail] chart={canvas is not None} ai_box={ai is not None}")
            if canvas is None:
                fails.append("detail view has no chart canvas")
            page.screenshot(path="/tmp/ui-detail.png", full_page=False)

        browser.close()

    print(f"\nconsole errors: {len(errors)}")
    for e in errors[:8]:
        print("  " + e)
    if fails:
        print(f"\nFAILURES ({len(fails)}):")
        for f in fails:
            print("  - " + f)
        return 1
    if errors:
        return 1
    print("\nUI OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
