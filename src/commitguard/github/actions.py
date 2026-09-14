"""GitHub Actions integration (Phase 4, not implemented).

TODO(phase-4):
* scan every commit in a pull request (``base..head``), not just the head;
* load policy from the **base** branch, so a pull request cannot relax the
  policy that evaluates it;
* emit workflow annotations and a job summary; exit non-zero on BLOCK so a
  required status check prevents merging.
"""
