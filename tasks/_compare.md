<!--
Task: _compare — the summary step, not a task you run directly.
One model reads all the answers (labelled A, B, C, never by model name, so it
can't favour its own) and lists where they agree and disagree.
{{TASK}} is the task name; {{ANSWERS}} is the labelled answers.
The exact first line and headings below are what the web page reads to draw the
agreement badge and the Agree / Disagree / Only-one lists, so keep them fixed.
-->
Several reviewers answered the same "{{TASK}}" request independently. Compare
their answers. Don't add a new opinion of your own on the original request; your
job is to show the user where the reviewers line up and where they don't.

Start with exactly this line:
AGREEMENT: Agree | Partly agree | Split   (pick one: do their overall verdicts match?)

Then use exactly these headings, in this order, with short bullets in plain words:

## Bottom line
One or two sentences: what the user should decide or check next.

## Agree on
At most 5 bullets.

## Disagree on
One bullet per point: "Point: A says ...; B says ... Settled by: ...".

## Only one reviewer caught
At most 5 bullets, each starting with the reviewer letter, like "A: ...".

{{ANSWERS}}
