"""
A pretend AI for the tests. It meets the same contract as a real model (prompt in on
stdin, answer out on stdout), so the whole pipeline can be tested with no AI account,
no network and no cost. It answers in the fixed shape the page reads, and reports
back what it saw in the prompt so the tests can prove what reached the model.

    --name a|b        makes two fakes' answers differ
    --verdict TEXT    the VERDICT line it gives
    --fail            exits with an error, to test how a failed model is shown
"""
import argparse
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="a")
ap.add_argument("--verdict", default="Bid")
ap.add_argument("--fail", action="store_true")
args = ap.parse_args()
prompt = sys.stdin.buffer.read().decode("utf-8", "replace")

# Block: a broken model: no answer, non-zero exit
if args.fail:
    sys.stderr.write("fake model: deliberate failure\n")
    sys.exit(3)

# Block: a follow-up (says whether it was told which earlier answer was its own).
# Checked FIRST: a follow-up prompt also contains the earlier comparison's headings.
if "## The user's new follow-up question" in prompt:
    print(f"Follow-up answer from {args.name}. saw-own-mark={'(you)' in prompt}")

# Block: the comparison step (recognised by its fixed headings)
elif "AGREEMENT:" in prompt and "## Only one reviewer caught" in prompt:
    print("AGREEMENT: Agree\n\n## Bottom line\nBoth say bid; check the budget first.\n\n"
          "## Agree on\n- The post is fresh.\n\n"
          "## Disagree on\n- Budget: A says it is fine; B says it is tight. Settled by: the scope.\n\n"
          "## Only one reviewer caught\n- A: the deadline is unrealistic.")

# Block: a first answer, in the fixed shape; flags a planted instruction like a real model should
else:
    profile = "Sam Rivera" if "Sam Rivera" in prompt else "no-profile"
    out = [f"VERDICT: {args.verdict}", f"WHY: fake reason from {args.name} ({profile}).", "",
           "## Diagnostics", f"- checked by {args.name}", "", "## Pain points", "- slow intake"]
    if "ignore all previous instructions" in prompt.lower():
        out += ["", "## Suspicious instructions in the input", '> "ignore all previous instructions"']
    print("\n".join(out))
