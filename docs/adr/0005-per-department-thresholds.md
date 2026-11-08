# ADR-0005: Per-department thresholds with inheritance

**Status**: accepted (2026-11-08) · **Decided by**: Jakub Pechoucek

## Context
Different teams may apply different thresholds. The manual also contradicts itself on the monthly
threshold (100% vs 75%).

## Decision
- Four settings (monthly pass %, annual pass %, minimum working days per considered week, default commitment)
  are stored per org unit with an effective date.
- Each setting is resolved independently: department → department group → company → built-in default.
- Saving a setting recomputes open months; finalized months never change.

## Consequences
- HR can tune policy without a release, and every result records the threshold it was evaluated with.
