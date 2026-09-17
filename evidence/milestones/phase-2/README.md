# Phase 2: Detection engine

**Completed:** 2026-09-14

## Objective

Detect AI attribution in commit metadata accurately enough to act on, without flagging humans.

## Implementation

Four detectors (coauthor, identity, trailer, bot), rules as data (15 agents, 6 bots), a trailer parser with no regular expressions, Unicode normalisation with an explicit homoglyph map, and an evidence model with confidence levels.

## Evidence

docs/detection-engine.md

## Tests

Unit tests per detector, rule matcher tests, hostile-input tests.

## Results

Documented attribution detected; near-misses (Claude Shannon, an AI vendor's human employee) not flagged.

## Limitations

Accuracy was asserted by examples; there was no dataset and no measurement until Phase 9 - which then found three bypasses in this layer.
