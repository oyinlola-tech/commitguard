# Detection evaluation

**Measured 2026-09-17** on Linux 7.1.5 (x86_64), Intel i5-8350U, 8 CPUs, 16 GB,
Python 3.13.15, Git 2.53.0, CommitGuard 0.1.0.dev0.

## Headline result

Dataset **1.3.0**, 9,174 labelled cases (3,709 that must not be allowed, 5,465
that must be):

| Metric | Value |
|---|---|
| False negatives | **0** |
| False positives | **0** |
| Precision | 100.000% |
| Recall | 100.000% |
| F1 | 1.000 |
| False positive rate | 0.000% |
| False negative rate | 0.000% |
| Exact decision match | 9,174 / 9,174 |
| Latency per commit | p50 0.2215 ms, p95 0.4383 ms, p99 0.6728 ms |

**Read that with the next section.** These numbers are measured *after* fixing
three bypasses that these same benchmarks found. They say the implementation now
handles every case in a dataset written to attack it - not that no other bypass
exists.

## Every recorded run, including the failures

Thirteen runs, kept in `benchmarks/results/raw/detection/`:

| # | Time (UTC) | Dataset | Cases | FN | FP | What it shows |
|---|---|---|---|---|---|---|
| 1 | 13:24:25 | 1.0.0 | 9,115 | **1** | 0 | first run: a replacement character before the key hid attribution |
| 2 | 14:16:32 | 1.1.0 | 9,142 | 0 | **2** | the first fix flagged two *human* bulleted trailers |
| 3 | 14:17:16 | 1.0.0 | 9,115 | 0 | 0 | after the second fix |
| 4 | 14:17:24 | 1.1.0 | 9,142 | 0 | 0 | after the second fix |
| 5 | 14:58:10 | 1.2.0 | 9,157 | **11** | 0 | fuzzing found Unicode letters/numbers before the key |
| 6 | 15:03:03 | 1.0.0 | 9,115 | 0 | 0 | after the third fix |
| 7 | 15:03:07 | 1.1.0 | 9,142 | 0 | 0 | after the third fix |
| 8 | 15:03:12 | 1.2.0 | 9,157 | 0 | 0 | after the third fix |
| 9 | 15:12:44 | 1.3.0 | 9,174 | **15** | 0 | fuzzing found default-ignorable characters |
| 10 | 15:15:09 | 1.0.0 | 9,115 | 0 | 0 | after the fourth fix |
| 11 | 15:15:14 | 1.1.0 | 9,142 | 0 | 0 | after the fourth fix |
| 12 | 15:15:19 | 1.2.0 | 9,157 | 0 | 0 | after the fourth fix |
| 13 | 15:15:23 | 1.3.0 | 9,174 | 0 | 0 | current state |

Runs 1, 2, 5 and 9 are the useful ones: they are the measurements that changed
the software.

## The three bypasses, and the false positives

### 1. A replacement character hid the attribution (run 1)

```text
feat: add payment service

\ufffdCo-authored-by: Claude <noreply@anthropic.com>
```

The parser required a trailer line to begin with an alphanumeric character, so a
symbol in front made the line invisible to it - while a human reader, and GitHub's
rendering, still sees the attribution. **Fix:** skip up to 16 leading
non-alphanumeric characters and record `LEADING_CHARACTERS`.

### 2. The fix caused two false positives (run 2)

```text
Reviewers:
- Reviewed-by: Grace Hopper <grace.hopper@example.org>
```

Skipping the `- ` made the rest parse as a trailer whose key was then treated as
malformed, so ordinary bulleted human trailers produced warnings. **Fix:** the
skipped prefix is not part of the key and is not itself a defect.

This is the value of keeping false-positive probes in the dataset: the fix for a
security bug introduced a usability bug, and the next run caught it.

### 3. Unicode letters and numbers before the key (run 5)

```text
\u32acCo-authored-by: Claude <noreply@anthropic.com>     (CIRCLED IDEOGRAPH METAL)
\u2460Co-authored-by: Claude <noreply@anthropic.com>     (CIRCLED DIGIT ONE)
\u24deCo-authored-by: Claude <noreply@anthropic.com>     (CIRCLED LATIN SMALL LETTER O)
```

Found by property-based fuzzing, not by hand: 387 of 4,000 generated variants were
allowed. The skip rule used Python's Unicode-aware `isalnum()`, and it ran *after*
NFKC normalisation, so `\u2460` became `1` and `\u24de` became `o` and joined the
key. **Fix:** trailer keys are ASCII, so the prefix is whatever precedes the first
ASCII letter or digit - judged both before and after NFKC, with the shortest
reading winning. Look-alike letters (a Cyrillic `\u0421`) stay part of the key,
because there they are a disguised key rather than a prefix.

### 4. Characters that render as nothing (run 9)

```text
Co-authored\ufe0f-by: Claude <noreply@anthropic.com>     (VARIATION SELECTOR-16)
Co-authored-by: Clau\u034fde Code <dev@example.com>      (COMBINING GRAPHEME JOINER)
```

Normalisation removed control (Cc) and format (Cf) characters, which covers
zero-width spaces - but Unicode marks 4,174 code points as
`Default_Ignorable_Code_Point`, and many are neither. A variation selector inside
the key or inside an agent alias defeated detection completely. **Fix:**
normalisation now removes every default-ignorable code point; the ranges are
checked against the Unicode 18.0.0 data file.

## Accuracy by case class (dataset 1.3.0, current code)

| Class | Cases | Exact decision | FN | FP |
|---|---|---|---|---|
| clean | 43 | 43 | 0 | 0 |
| violations | 27 | 27 | 0 | 0 |
| variations | 21 | 21 | 0 | 0 |
| malformed | 9 | 9 | 0 | 0 |
| adversarial | 74 | 74 | 0 | 0 |
| generated | 9,000 | 9,000 | 0 | 0 |

The 9,000 generated cases exist to give the aggregate metrics volume and to catch
systematic errors; the hand-written 174 are where the difficulty is. Precision and
recall on the generated majority would look excellent even with all three
bypasses present - which is exactly why the adversarial class, not the headline
number, is the interesting part.

## What these numbers do not mean

- They are not a false-positive rate for **real** repositories. The dataset is
  synthetic, and 5,465 of its cases are clean by construction.
- They cannot measure attribution that was never written down: a commit with the
  trailer removed is indistinguishable from a human commit, by design.
- They are single-machine, single-run figures. The latency percentiles vary with
  machine load; the accuracy figures do not.
