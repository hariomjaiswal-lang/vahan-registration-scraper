"""Open the live VAHAN report page in a visible browser and dump everything we
need to fill in `config.yaml -> selectors`:

  * a full-page screenshot
  * page.html
  * a list of every <select>, form <input>, <button>, and PrimeFaces widget
    with its id / name / classes / visible label

Usage:
  python -m tools.calibrate                 # visible browser, waits for Enter
  python -m tools.calibrate --headless      # no window
  python -m tools.calibrate --no-wait       # don't pause before closing

Then match the printed controls to the filter rows and paste concrete `css:`
values into config.yaml. Re-run app after.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252 and choke on glyphs like the captcha arrow.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import sync_playwright

from backend import config

OUT = Path(__file__).resolve().parent.parent / "calibration"

JS_DUMP = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const near = el => {
    let row = el.closest('tr,div,li');
    for (let i = 0; i < 5 && row; i++) {
      const t = (row.innerText || '').trim().split('\n')[0];
      if (t && t.length < 45) return t;
      row = row.parentElement;
    }
    return '';
  };
  const rec = (el, type) => ({
    type, tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.getAttribute('name') || null,
    formcontrolname: el.getAttribute('formcontrolname') || null,
    role: el.getAttribute('role') || null,
    cls: el.className || null,
    placeholder: el.getAttribute('placeholder') || null,
    aria: el.getAttribute('aria-label') || null,
    text: (el.innerText || el.value || '').trim().slice(0, 50) || null,
    label: near(el),
  });
  const grab = (sel, type) => [...document.querySelectorAll(sel)].filter(vis).map(e => rec(e, type));
  return [].concat(
    grab('select', 'select'),
    grab('input:not([type=hidden])', 'input'),
    grab('button, a[role=button]', 'button'),
    grab('[formcontrolname]', 'ng-control'),
    grab('[role=combobox], [role=listbox]', 'combobox'),
    grab('ng-select, .ng-select, .multiselect, .dropdown-toggle, [class*=multiselect], [class*=multi-select]', 'widget'),
    grab('label', 'label'),
    grab('img', 'img'),
  );
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="run without a visible window")
    ap.add_argument("--no-wait", action="store_true", help="close immediately after dumping")
    args = ap.parse_args()

    cfg = config.load()
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless, slow_mo=0 if args.headless else 150)
        page = browser.new_context().new_page()
        url = cfg["portal"]["url"]
        print(f"Opening {url} ...")
        page.goto(url, wait_until="domcontentloaded", timeout=cfg["portal"]["load_timeout"] * 1000)
        page.wait_for_timeout(6000)  # let Angular/PrimeFaces settle

        (OUT / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT / "page.png"), full_page=True)

        controls = page.evaluate(JS_DUMP)
        (OUT / "controls.json").write_text(json.dumps(controls, indent=2), encoding="utf-8")

        print(f"\nWrote {OUT/'page.png'}, page.html, controls.json")
        print(f"{len(controls)} visible controls:\n")
        for c in controls:
            print(
                f"  [{c['type']:>17}] label={c['label']!r:28} "
                f"id={c['id']!r} name={c['name']!r} placeholder={c['placeholder']!r} "
                f"text={c['text']!r}"
            )
        if not args.no_wait and sys.stdin.isatty():
            input("\nInspect the browser, then press Enter to close...")
        browser.close()


if __name__ == "__main__":
    main()
