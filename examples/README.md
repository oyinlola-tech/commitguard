# CommitGuard examples

Each directory is a self-contained example: what it shows, the configuration,
a sample violation and the result you should see.

| Example | Shows | Needs |
|---|---|---|
| [basic/](basic/) | Local enforcement with Git hooks on one repository | Git and CommitGuard |
| [github-actions/](github-actions/) | The server-side check on pull requests and pushes | A GitHub repository |
| [github-app/](github-app/) | The webhook-driven App service and its dashboard | A GitHub App you create and host |
| [organization-policy/](organization-policy/) | A central policy repositories cannot weaken | The App service |
| [exceptions/](exceptions/) | A scoped, expiring exception to a policy | The App service |
| [policy-rollout/](policy-rollout/) | Staged rollout of a policy change | The App service |

Every example that can be checked automatically is: `tests/integration/test_examples.py`
parses each configuration, runs the sample commits through the real engine and
asserts the documented decision, so an example cannot drift from the code.

## Installing CommitGuard first

CommitGuard is **not on PyPI**. The name `commitguard` there belongs to an
unrelated project, so `pip install commitguard` installs someone else's code.
Install from source, pinned to a commit:

```bash
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
# or, in a virtual environment:
python -m pip install "commitguard @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
```

See [docs/getting-started/quickstart.md](../docs/getting-started/quickstart.md).
