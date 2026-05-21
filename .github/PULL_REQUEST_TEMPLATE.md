<!-- One-line summary of what this PR does -->

## Summary

<!-- 1-3 bullet points covering motivation + change shape -->

-

## Type

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing usage to change)
- [ ] Docs / chore / refactor (no behavior change)

## Test plan

<!-- How did you verify this? Include the commands you ran. -->

- [ ] `pytest tests -q` passes locally
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] Manual end-to-end run on a real cluster (paste a snippet of output)

## Reviewer checklist

- [ ] Behavior change is reflected in the README / report template
- [ ] New thresholds added to `k8s_advisor/constants.py` (not hardcoded)
- [ ] No Salesforce-internal or other private cluster names leaked
- [ ] No real-cluster CSV / report files committed (kept under `reports/`)
