# What happens to your feedback

The loop this project tries to run:

```text
External user ─▶ Feedback ─▶ Issue ─▶ Investigation ─▶ Implementation ─▶ Test ─▶ Release ─▶ Documentation ─▶ Measure
```

## Categories

| Category | Where it goes |
|---|---|
| **Security issue** | [Private reporting](../../SECURITY.md), never a public issue |
| **Bug** | Issue, reproduced, then a failing test before a fix |
| **Usability** | Issue; often a message or default, not a feature |
| **Documentation** | Issue; a doc that is wrong is treated as a bug |
| **Performance** | Issue with numbers; reproduced as a benchmark where possible |
| **Compatibility** | Issue with OS, Git and Python versions |
| **Integration** | Issue about GitHub behaviour, often needing a real installation |
| **Feature request** | Issue; may be declined, and will be told so |
| **Question** | Issue; a question that keeps recurring becomes documentation |

## Lifecycle

| State | Meaning |
|---|---|
| `reported` | open, not yet looked at |
| `triaged` | category and severity assigned, reproduction attempted |
| `accepted` | it will be worked on |
| `rejected` | it will not, with the reason written down |
| `duplicate` | linked to the original |
| `fixed` | merged into `main` |
| `released` | in a release, and in the changelog |

## What will not happen

- Every feature request being accepted. A security tool grows by saying no; a
  request that does not solve a clearly identified security, reliability,
  usability or research problem will be declined with a reason.
- Silent closure. If something is rejected, the issue says why.
- A promised date. There is one maintainer and no service commitment.

## Bugs that become tests

A reproduced bug gets a test **before** the fix, so it can never come back
quietly. For security bugs this is required, and the test carries the `security`
marker so it runs in `commitguard reproduce security`.
