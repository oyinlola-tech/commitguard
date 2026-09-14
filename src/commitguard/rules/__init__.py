"""Detection rules as data.

Detectors never hard-code agent names. The pipeline is::

    rules/*.yaml --(loader: safe YAML + schema)--> RuleSet
    RuleSet --> IdentityMatcher (normalisation, deliberate matching)
    Detector + IdentityMatcher --> Finding

:mod:`commitguard.rules.models` and :mod:`commitguard.rules.matcher` are pure;
only :mod:`commitguard.rules.loader` reads files.
"""
