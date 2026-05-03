---
task: Deterministic retry test - fails on attempt 1, passes on attempt 2
allowed_files:
  - python-utils/retry_test.py
eval_script: scripts/evaluators/evaluate_retry_deterministic.sh
checklist:
  - File exists
  - (Evaluator intentionally fails on attempt 1)
---

## Task Description

Create a simple Python file `python-utils/retry_test.py` that contains:

```python
def hello() -> str:
    return "hello"
```

This is a **deterministic retry test**. The evaluator will:
1. **Attempt 1**: Always FAIL (intentional, to test retry logic)
2. **Attempt 2+**: PASS

## Purpose

This brief tests that the retry loop correctly:
1. Handles executor failure on attempt 1
2. Resets the worktree properly between attempts
3. Includes failure evidence in the retry prompt
4. Succeeds on attempt 2

## Expected Result

After the run:
- `attempt-1/` should have REJECT (Executor failed)
- `attempt-2/` should have APPROVE
- `final_decision.md` should show APPROVE with 2 attempts used

## Allowed Files

- `python-utils/retry_test.py`

## Evaluator Script

`scripts/evaluators/evaluate_retry_deterministic.sh`
