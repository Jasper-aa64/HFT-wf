---
task: Impossible task - demonstrate ESCALATE
allowed_files:
  - python-utils/impossible.py
eval_script: scripts/evaluators/evaluate_impossible.sh
checklist:
  - Function must always fail this test
---

## Task Description

This is a test case for the ESCALATE scenario.

Create a Python function `impossible_function() -> str` that:
- Returns the string "SUCCESS" when called
- BUT the evaluator will always fail

This tests that after 3 failed attempts, the system escalates to human.

## Allowed Files

- `python-utils/impossible.py`

## Evaluator Script

`scripts/evaluators/evaluate_impossible.sh`

## Checklist

- [ ] Function exists
- [ ] (Evaluator will always fail)
