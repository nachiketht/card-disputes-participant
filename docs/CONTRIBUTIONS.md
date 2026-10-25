# Contributions

Taken from `git log` on `mvp-v1` (41 commits). The author string and email are what git stored. Commit counts are for that string, not a merged identity. Roles, review duty, and who should be asked next are not in the history.

To be confirmed by the team: whether the three Nachiketh author strings are one person, and what each person is responsible for outside these commits.

## Author strings

| Author string | Email in git | Commits |
| --- | --- | --- |
| nachiketht | nachiketh.tibrewal@genspark.net | 21 |
| Nachiketh Tibrewal | nachiketh.tibrewal@genspark.net | 9 |
| Nachiketh Tibrewal | Nachiketh.Tibrewal@genspark.net | 2 |
| not-ricksanchez | tanishq.sharma@genspark.net | 6 |
| dhyeydesai166 | dhyeyv.desai@genspark.net | 3 |

## What those commits touch

### nachiketht (21)

- `8130824` Initial provided files: development corpus, `tools/runtime.py`, `tools/evaluate.py`, `tools/validate_package.py`, README, and the early docs.
- Plan revisions `754e2e1`, `e187bd5`, `cec03c5`.
- `653a55f` and `0a2813e`: package layout, rules, workflow, command entry, and the first tests.
- Tracing, retry, breaker, metrics, and security tests: `4c36fbd`, `9ffd696`, `2696637`, `7728f7a`, `d258a74`, `e38bbad`.
- Demos and week 10 evidence: `0f5d52a`, `cd346f5`, `763bf95`, `941c128`, `987233d`, `2b9675b`.
- Docs and the decision log: `932c94f`, `242da93`, `71f2687`.

### Nachiketh Tibrewal `<nachiketh.tibrewal@genspark.net>` (9)

- Plan notes `18736f0`, `a08569e`.
- `87ffd91` Bind D01 record creation to the server draft and its hash (`scripts/normal_case.py`).
- Command guide and doc fixes: `1daf1c6`, `c9a5773`.
- Restricted quotes, leak patterns, adversarial cases, and instruction flags: `ea875d7`, `ff5985d`, `882f251`, `8b937ca`.

### Nachiketh Tibrewal `<Nachiketh.Tibrewal@genspark.net>` (2)

- `84ab608` Merge plan Version 3.7 and D01 record creation fix.
- `a83a4a2` Merge plan v3.8 risk fixes into initial-build.

### not-ricksanchez `<tanishq.sharma@genspark.net>` (6)

- `7c013c2` thin end to end demo (`scripts/normal_case.py` and the first saved D01 submission).
- `bbc82ef` removed old plan.
- `57463bc` Updated duplicate_case handling.
- `0fb94be` Updated adapters for prompts.
- `2e1c7a9` version 1: model client, prompts, review flow, and gate.
- `4d7195a` updated gitignore.

### dhyeydesai166 `<dhyeyv.desai@genspark.net>` (3)

- `0872241` Record the pod's finalized design decisions in the copilot plan.
- `08e157d` Add the version 3.9 copilot plan.
- `75c16e4` Add the Nova Converse judge and the Monday model and cloud notes.

The subjects above are the git subjects. What "Nova Converse", "Monday model", and "the pod" refer to is to be confirmed by the team.

## Not in this history

To be confirmed by the team:

- Who reviewed these commits.
- Who operates the mock or an environment outside this repository.
- Credit for work that never landed on `mvp-v1`.
