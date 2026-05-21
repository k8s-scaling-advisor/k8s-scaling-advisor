---
name: Feature request
about: Propose an enhancement to K8s Scaling Advisor
title: "[feature] "
labels: enhancement
assignees: ''
---

## Problem

<!-- What problem does this solve? Who's affected? -->

## Proposed solution

<!-- How should it work from the user's perspective? -->

## Alternatives considered

<!-- Other approaches you thought about and why you didn't pick them -->

## Scope

This project is a **scaling advisor**, not a full workload analyzer. Proposed
features should fit within:

- ✅ Right-sizing recommendations from observed usage
- ✅ HPA/VPA suitability classification
- ✅ Pattern detection across fleets
- ✅ Surfacing existing K8s/Prometheus signals (OOM, throttle, restart cause)
- ❌ Full APM, distributed tracing, log search
- ❌ Replacing Datadog / New Relic / Splunk
- ❌ Live cluster mutation (we recommend, the user applies)

If the request crosses those lines, propose it as a separate companion tool.
