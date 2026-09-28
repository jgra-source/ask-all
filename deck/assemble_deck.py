"""
Assemble Ask All's 16:9 deck from the shared slide styles and the hand-written slides.

    python deck/assemble_deck.py        (run from ask-all/)

Why: the slide CSS (_deck_head.html) is the same for every G Anastacio product deck
and was copied from Tandem's deck unchanged; only the seven slides in
_askall_deck_body.html are written by hand. Re-run this after editing the slides,
then render with deck/render_deck.py. Mirrors docs/assemble_carousel.py for decks.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Block: the per-product note that heads the file: what must never be said, and where numbers came from
NOTE = """
  Ask All — the seven-slide product deck. 16:9, for presenting.

  THE CLAIM THAT MUST NOT BE SOFTENED: the reviewers answer, they never act. The Claude
  reviewer lists "None" when asked for its tools; Gemini's file reads and page opens are
  refused automatically. Gemini's web search can still run, and is flagged when it does.

  ALSO TRUE AND MUST BE SAID: three defects were found while building (connected apps left
  loaded; Gemini CLI refused by Google; Antigravity's self-updater flashing a console). No
  test suite: the proof is saved runs in runs/. One user, no paying client.

  NUMBERS FROM THE SOURCE (counted 2026-09-28): 3 page tasks, 2 of 4 models enabled.
  Run ids on the proof slide are runs/ folder names (date 0928 + time).
  Slides live in deck/_askall_deck_body.html; re-run deck/assemble_deck.py after editing.

  Renders with:  python deck/render_deck.py deck/askall-deck.html --pdf deck/askall-deck.pdf
"""


def main():
    # Block: head (fonts + slide CSS) + slides, wrapped into one standalone page
    head = (HERE / "_deck_head.html").read_text(encoding="utf-8")
    body = (HERE / "_askall_deck_body.html").read_text(encoding="utf-8")
    doc = ("<!doctype html>\n<!--" + NOTE.rstrip() + "\n-->\n<html lang=\"en\">\n<head>\n"
           "<meta charset=\"utf-8\">\n<title>Ask All — paste once, every AI answers, see where they disagree</title>\n\n"
           + head.rstrip() + "\n<body>\n\n" + body.rstrip() + "\n\n</body>\n</html>\n")
    out = HERE / "askall-deck.html"
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out}  ({len(doc.splitlines())} lines)")


if __name__ == "__main__":
    main()
