---
task: Create safe_divide utility function
allowed_files:
  - python-utils/safe_math.py
  - python-utils/test_safe_math.py
eval_script: scripts/evaluators/evaluate_safe_divide.sh
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
- `python-utils/test_safe_math.py` — the test file (must print PASS/FAIL for each test case)

## Evaluator Script

`scripts/evaluators/evaluate_safe_divide.sh`

## Checklist

- [ ] Function returns correct result for normal division
- [ ] Function returns 0.0 for division by zero
- [ ] Type hints present
- [ ] Test file exists and passes
- [ ] No exceptions raised during test

## Test Cases Required

```python
# Test 1: Normal division
assert safe_divide(10.0, 2.0) == 5.0

# Test 2: Division by zero
assert safe_divide(10.0, 0.0) == 0.0

# Test 3: Negative numbers
assert safe_divide(-10.0, 2.0) == -5.0

# Test 4: Float division
assert abs(safe_divide(1.0, 3.0) - 0.333...) < 0.001
```

Each test should print PASS or FAIL.
