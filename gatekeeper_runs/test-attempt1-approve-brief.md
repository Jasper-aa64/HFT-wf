---
task: Create safe_add utility (simple case)
allowed_files:
  - python-utils/safe_add.py
  - python-utils/test_safe_add.py
eval_script: scripts/evaluators/evaluate_safe_add.sh
checklist:
  - Function returns correct result for normal addition
  - Function handles None gracefully (returns 0)
  - Type hints present
  - Test file passes
---

## Task Description

Create a Python utility function `safe_add(a: float | None, b: float | None) -> float` that:
- Returns `a + b` when both are not None
- Returns `0.0` when either is None
- Includes type hints

This is a simple case that should pass on attempt 1.

## Allowed Files

- `python-utils/safe_add.py` — the utility function
- `python-utils/test_safe_add.py` — test file

## Evaluator Script

`scripts/evaluators/evaluate_safe_add.sh`

## Checklist

- [ ] Function returns correct result for normal addition
- [ ] Function returns 0.0 when either input is None
- [ ] Type hints present
- [ ] Test file exists and passes

## Test Cases Required

```python
# Test 1: Normal addition
assert safe_add(10.0, 2.0) == 12.0

# Test 2: First is None
assert safe_add(None, 2.0) == 0.0

# Test 3: Second is None
assert safe_add(10.0, None) == 0.0

# Test 4: Both None
assert safe_add(None, None) == 0.0

# Test 5: Negative numbers
assert safe_add(-5.0, 3.0) == -2.0
```
