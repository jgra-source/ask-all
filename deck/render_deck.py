"""Render a 16:9 branded slide deck to PDF, and refuse to render a broken one.

    python deck/render_deck.py deck/tieout-deck.html                    # measure only
    python deck/render_deck.py deck/tieout-deck.html --pdf deck/x.pdf   # measure, then render if clean
    python deck/render_deck.py deck/tieout-carousel.html --size 794x1123 --pdf deck/y.pdf

WHY THIS EXISTS SEPARATELY FROM ops/render_pdf.py: that script paginates a
generated report onto A4 — it decides where page breaks go because the number of
findings changes between runs. A deck is the opposite: seven hand-written slides
at a fixed 1280x720. Nothing needs paginating; what needs checking is that no
slide's content has quietly grown past its own frame.

THE OVERFLOW TRAP (inherited from sales-playbook/pdf/check_render.py): .slide
sets overflow:hidden, so scrollHeight clamps to clientHeight and a slide whose
text runs straight through the footer measures as clean. This compares the
bottom of every element inside .body against the top of .foot instead. Do not
replace it with a scrollHeight check.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Slide geometry. Must match the .slide rule in the deck's stylesheet — the PDF
# is printed one page per slide at exactly this size, so nothing is rescaled
# after layout and the measured pixels are the printed pixels.
#
# Two decks use this script and they are different shapes: the 16:9 deck for
# presenting, and a 794x1123 (A4 at 96dpi) portrait one for LinkedIn, whose
# document viewer is portrait and mostly read on a phone. Override with
# --size WxH; the default stays the presenting deck.
SLIDE_W = 1280
SLIDE_H = 720

# How much of a slide must stay empty below the last line. A slide filled to the
# final pixel breaks on the next copy edit; 24px is the same threshold the
# design system's own document gate warns at.
SLACK = 24

# Measure each slide: the lowest pixel any visible element in .body reaches, and
# where .foot starts. Positive slack = clean. Also reports whether the branded
# faces actually loaded — a deck silently rendered in a fallback font is the
# other way this quietly ships wrong.
MEASURE = """() => {
  const slides = [...document.querySelectorAll('.slide')];
  return {
    fonts: {
      inter: document.fonts.check("17px Inter"),
      mono:  document.fonts.check("11px 'JetBrains Mono'"),
    },
    slides: slides.map((sl, i) => {
      const body = sl.querySelector('.body');
      const foot = sl.querySelector('.foot');
      let low = 0;
      for (const el of body.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.height > 0) low = Math.max(low, r.bottom);
      }
      return {
        n: i + 1,
        slack: Math.round(foot.getBoundingClientRect().top - low),
      };
    }),
  };
}"""


def main() -> int:
    global SLIDE_W, SLIDE_H

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    html = Path(args[0]).resolve()
    out = None
    if "--pdf" in args:
        out = Path(args[args.index("--pdf") + 1]).resolve()
    if "--size" in args:
        SLIDE_W, SLIDE_H = (int(v) for v in args[args.index("--size") + 1].split("x"))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Viewport matches one slide so getBoundingClientRect returns the same
        # geometry the printer will use.
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
        page.goto(html.as_uri())
        page.wait_for_timeout(600)          # let the local font files finish loading
        m = page.evaluate(MEASURE)

        # Font check first: wrong fonts make every slack number below meaningless.
        f = m["fonts"]
        print(f"fonts   inter={f['inter']}  mono={f['mono']}")
        if not (f["inter"] and f["mono"]):
            print("FAIL — branded fonts did not load; check the @font-face paths")
            browser.close()
            return 1

        bad = []
        for s in m["slides"]:
            flag = "ok " if s["slack"] >= SLACK else "OVER"
            print(f"slide {s['n']}  slack {s['slack']:>4}px  {flag}")
            if s["slack"] < SLACK:
                bad.append(s["n"])

        if bad:
            print(f"FAIL — slides {bad} overrun their frame; cut copy, do not shrink type")
            browser.close()
            return 1

        if out:
            # One PDF page per slide, printed at the slide's own size so the
            # layout is never scaled to a paper format it was not designed for.
            # prefer_css_page_size lets the deck's own @page rule win. Without
            # it, brand.css's `@page { size: A4 }` — correct for the report —
            # is inherited and the deck prints portrait with slides sliced in
            # half. The width/height below are the fallback if that rule is
            # ever removed, not the primary control.
            page.pdf(
                path=str(out),
                width=f"{SLIDE_W}px",
                height=f"{SLIDE_H}px",
                prefer_css_page_size=True,
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            print(f"wrote {out}")
            browser.close()
            return check_pdf(out, len(m["slides"]))

        browser.close()
    return 0


def check_pdf(path: Path, want_pages: int) -> int:
    """Prove the PRINTED file is what we asked for, not just the laid-out page.

    WHY THIS EXISTS: the measure gate above runs in the browser, so it passes on
    a deck that then prints portrait and slices every slide in half — which is
    exactly what an inherited `@page { size: A4 }` did here. A gate that shares
    the blind spot of the thing it guards proves nothing, so this reads the
    bytes that were actually written.
    """
    import re

    data = path.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    box = re.search(rb"/MediaBox\s*\[([^\]]+)\]", data)
    dims = [round(float(v)) for v in box.group(1).split()] if box else []

    # px -> pt at the 96dpi CSS reference: 1280x720px is 960x540pt.
    want = [0, 0, round(SLIDE_W * 0.75), round(SLIDE_H * 0.75)]
    print(f"pdf     pages={pages} (want {want_pages})  mediabox={dims} (want {want})")

    if pages != want_pages or dims != want:
        print("FAIL — printed geometry is wrong; check @page and prefer_css_page_size")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
