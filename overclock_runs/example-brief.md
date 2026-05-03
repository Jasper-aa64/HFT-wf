---
task: Create safe_divide utility function
allowed_files:
  - python-utils/safe_math.py
  - python-utils/test_safe_math.py
eval_command: python3 python-utils/test_safe_math.py
checklist:
  - Function returns correct result for normal division
  - Function returns 0.0 for division by zero
  - Type hints present
  - Test file passes
  - No exceptions raised
---

## Task Description

Create a Python utility function `safe_divide(a: float, b: float) -> float` that:
- Returns `a / b` when `b != 0`
- Returns `0.0` when `b == 0` (instead of raising ZeroDivisionError)
- Includes type hints

## Allowed Files

- `python-utils/safe_math.py` — the utility function
- `python-utils/test_safe_math.py` — the test file

## Evaluator Command

```bash
python3 python-utils/test_safe_math.py
```

## Checklist

- [ ] Function returns correct result for normal division
- [ ] Function returns 0.0 for division by zero
- [ ] Type hints present
- [ ] Test file exists and passes
- [ ] No exceptions raised during test
