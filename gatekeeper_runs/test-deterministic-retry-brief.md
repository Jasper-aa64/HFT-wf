---
task: Create hello function
allowed_files:
  - python-utils/retry_test.py
eval_script: scripts/evaluators/evaluate_retry_deterministic.sh
checklist:
  - File exists
  - Function returns "hello"
  - Type hints present
---

## Task Description

Create a Python file `python-utils/retry_test.py` that contains:

```python
def hello() -> str:
    return "hello"
```

## Allowed Files

- `python-utils/retry_test.py`

## Evaluator Script

`scripts/evaluators/evaluate_retry_deterministic.sh`

## Checklist

- [ ] File `python-utils/retry_test.py` exists
- [ ] Function `hello()` returns exactly `"hello"`
- [ ] Type hints present (`-> str`)
