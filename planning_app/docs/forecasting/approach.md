# Forecasting project approach

## Purpose

Develop two linked projects:

1. **AI intake forecasting:** predict incoming orders, order value, and volume.
2. **Intake-to-output conversion:** use Project 1's forecasts and the existing
   backlog to predict shipped value and volume over the next 12-16 weeks,
   accounting for amendments, cancellations, and shipment timing.

Complete the modelling lifecycle for Project 1 first, then Project 2. Evaluate
each separately and the combined workflow end to end.
The [overview](README.md) defines the scope; the [data contract](data-contract.md)
records source definitions and findings.

## Delivery stages

### 1. Define the business problem

Agree who will use the forecasts, which decisions they support, and the cost
of over- or under-forecasting. Confirm horizons, groups, and how known orders
will contribute alongside forecast new intake. Output is measured at shipment,
not invoice or production-completion date.

**Output:** forecast definitions and measurable acceptance criteria agreed
before final evaluation.

### 2. Prepare reliable history

For Project 1, use Planning's imported VA05 data and assign intake to
financial periods by Epicor order date. Use published SOI as a reconciliation
reference, with Shop and manual adjustments shown separately. Treat the open
month as provisional.
For Project 2, link subsequent changes, cancellations, and actual shipments
without counting changes already included in the intake figures. Reconcile
movement from opening order book to closing order book.

Save versioned datasets separately from operational tables, retaining source
parameters, extraction details, mappings, quality results, and calculation
settings.

Choose the inclusive order-date window when saving a VA05 extract. Review
its coverage and investigate discrepancies in Data Readiness before using
the saved observations for EDA. Send source corrections to authorised owners
as a worklist; save a separate extract for the same dates once corrections are
available in Planning. Choose training and evaluation windows later, not
when reviewing coverage.

**Output:** a reconciled dataset and data-quality report for the first target.

### 3. Explore demand

For weekly GBP, start with coverage, gaps, zeros, trend, lag relationships
and outliers. Then profile model, size within model and Epicor customer
group on the same eligible releases. Reconcile each breakdown to weekly
GBP. Use concentration, category activity, missing or conflicting labels,
and contributors to peak weeks to decide which groups merit further study.
Distinguish missing data from genuine zero demand; do not remove a spike
solely because it is unusual.

Before fitting, record each candidate feature's coverage, stability and
availability at the forecast cutoff. Keep, defer or reject it with a reason.
Check whether earlier history supports seasonal tests; fewer than 52
observations cannot establish a full annual pattern. Freeze the target
definition, feature list and chronological evaluation plan before fitting.

For Project 2,
Examine how much intake eventually ships, how long it takes, and how amendments
and cancellations vary by customer and work content. Recent unshipped intake
must not be treated as lost or cancelled.

**Output:** EDA findings that justify preparation, features, and candidate
models, including whether groups need separate treatment.

### 4. Establish benchmarks

Test simple intake/output forecasts such as the latest period and the same
period last year, where history permits. Establish an intake-to-shipment
baseline using observed conversion and timing. Evaluate forward from several
historical cutoffs across the full 12-16-week output horizon.

Fit preprocessing on training data only and use information available at each
cutoff. Reserve a final holdout for evaluation after model selection.

**Output:** repeatable baseline results by group and horizon.

### 5. Develop and tune models

Compare a small set of statistical and machine-learning approaches supported
by the EDA. Test features and tune within an agreed budget using the same
time-based evaluation as the benchmarks.

First develop and evaluate AI intake forecasts against simple intake baselines.
Then compare intake-to-output models with a simple conversion baseline and a
direct output forecast. Evaluate shipped value and volume as well as stage
errors, including the effect of uncertain intake.
Do not assume volume converts to value at a fixed average price.

Record dataset, code and model versions, settings, features, cutoffs, runtime,
and results. Use rolling cutoffs with the same target weeks for every
candidate and baseline. Fit transformations and tune only inside the
development period. Apply the selected settings to an untouched or
prospective holdout, review errors by period and relevant group, and retain
the simpler approach unless complexity adds useful value.

**Output:** a justified model choice and final holdout results.

### 6. Deliver and monitor

Bring dataset selection, evaluated model selection, forecasts, uncertainty,
and exports into AI Tools. Show the data, settings and scores behind each
selectable model. Review usability with intended users.

Store issued forecasts and compare them with actuals. Agree review, retraining,
and replacement criteria; keep forecasting separate from live planning changes.

**Output:** a usable workflow, feedback, monitoring, and project evaluation.

## Evaluation

| Measure | Purpose |
| --- | --- |
| MAE | Report value errors in GBP, volume errors in verified units, and order-count errors for the relevant project |
| Bias | Identify systematic over- or under-forecasting |
| WAPE | Express relative error where actual totals are nonzero |
| Interval coverage and width | Assess uncertainty where intervals are produced |

Report each project's outputs by customer group and horizon as well as overall.
Assess runtime, reproducibility, explainability, usability, and planning value alongside
accuracy. Record unsuccessful experiments too.

## Project report and evidence

This documentation will support my AI Level 7 project report. Each stage will
record the findings, decisions, alternatives, and outcomes needed to explain
the work. Mapping to the exact assessment criteria remains to be done.

Keep review evidence from both environments. Development notes show why
checks or workflows changed; production notes show what happened after
deployment and what still needed attention. When citing evidence in the
report, record the environment-qualified extract reference (for example
`DEV-12` or `LIVE-12`), capture date, and rules version. An extract number
on its own is not unique across environments. Link related decisions in
the report rather than copying notes between databases.

| Report area | Project evidence |
| --- | --- |
| Business and system understanding | Planning needs, forecast definitions, Epicor mappings, and release/job/assembly relationships |
| Data preparation and governance | Profiles, UOM and cancellation rules, reconciliation, access controls, and dataset versions |
| EDA and method selection | Observed patterns, preparation decisions, technical references, and baseline/candidate rationale |
| Experiments and evaluation | Time-based splits, leakage controls, tuning, errors, uncertainty, and holdout results |
| Delivery and reflection | Tests, user feedback, business usefulness, trade-offs, limitations, and revised decisions |

The source review already shows why one-piece job planning does not make every
imported row one item, why classification is separate from forecast inclusion,
and why refreshed operational tables cannot reconstruct an earlier order book.
The [data contract](data-contract.md#local-data-checks) holds the supporting
findings rather than duplicating them here.

Keep the question, method, source/date or dataset version, result, and decision
for each substantial check. Use approved aggregates or anonymised examples in
the report. Personal reflection will accompany the technical evidence as the
work develops; development assistance should follow the course's assessment
rules.

### Why Project 1 uses Epicor order date

The first reconciliation used the customer order-received date. The
August-October SOI preparation workbooks showed that Finance uses Epicor
order date to look up the financial week, then assigns that week to a
financial month. The 2026 calendar follows a 4-4-5 pattern. Using order date
reduced the sum of absolute weekly differences by 78% in September and 25%
in October, although receipt date gave closer monthly totals in both months.
The project uses order date for period assignment and keeps receipt date
as a separate field.

The pre-final-week and final pivots, plus Shop, reproduced the published
weekly totals for August-October, apart from a seven-pence difference in
week 39. In week 40, Finance had moved a customer stock order from week 37
to week 40 for reporting. A fresh Epicor export matched the local import
for all three orders checked; the stock order's source date and GBP value
had not changed. That is why published SOI is a cross-check rather than
the forecast source: it includes Shop and manual adjustments that a
refreshed VA05 extract does not reproduce.

### Review feedback

On 18 November 2026, review of the VA05 findings highlighted that the number of
records makes a single mixed list difficult to work through. Filtering by
finding type is the next usability improvement to assess. This is a review
workflow issue, not evidence that every flagged record is an error or needs
individual correction.

## Project 1: baseline and EDA evidence

The first milestone was a weekly GBP baseline from Planning's imported VA05.
Aggregate one value per release by financial week, check historical coverage,
and compare representative periods with SOI. Separate known reporting
adjustments from unexplained differences. The resulting history reflects
VA05 values at extraction, not what the order book showed at each close.

Use the net VA05 view for the GBP baseline: exclude lines marked void at
extraction and report their value separately. In the 45 completed 2026
weeks checked, voided lines accounted for 4.54% of stored release value,
with a maximum weekly impact of 15.08%.

The weekly intake chart and table matched independent read-only SQL for all
45 completed financial weeks in development capture `DEV-6` (19 November
2026, `va05-local-v3`, `weekly-net-gbp-v1`). The SQL comparison uses the
capture's four-decimal GBP precision. The first local dataset excludes
partial week 46. Week 34 is marked incomplete: its one release with
conflicting GBP and void status is excluded, and the remaining observed
total is retained.

The first benchmark predicts each week from the preceding week (44
one-week-ahead comparisons). A sensitivity check skips comparisons where
week 34 supplies either the forecast input or the actual. Both checks use
one VA05 capture, so they do not recreate historical order-book snapshots.
The [review command](../../scripts/weekly_intake_review.py) reruns capture
validation, CSV generation and the benchmark. The
[independent SQL](../../scripts/verify_weekly_intake.sql) verifies release
counts and GBP totals against the saved rows. See the
[run instructions](README.md#reproduce-the-weekly-gbp-review).

On the same 41 forecast weeks, the previous-four-week mean reduced WAPE
from 19.00% to 16.85% relative to the previous-week forecast. With week 34
excluded from both targets and lookbacks, the mean still had lower WAPE
(16.99% versus 19.23%) but higher overforecast bias. It reduced two of the
three largest previous-week misses substantially but barely changed the
third. These are development results; reserve future weeks for a held-out
comparison before choosing a default.

The repeatable EDA (`--eda`) found 44 unflagged observations, no missing
or zero weeks in the verified period, and one outlier candidate (week 1).
Lag-one and lag-two correlations remained positive after removing week 1
for sensitivity; lag four weakened. The spike remains in the source.
This supports testing a small lag-based regression, not assuming annual
seasonality from less than a full year.

Saved capture `DEV-7` (19 November 2026) includes all four mix dimensions.
All 45 completed weeks matched independent SQL, and each mix breakdown
reconciled to the weekly included release counts and net GBP. No conflicting
labels were found among included releases; missing labels remained unassigned.

| Candidate | Saved-extract evidence | Decision for the first learned model |
| --- | --- | --- |
| Prior weekly net GBP | Lag-one and lag-two relationships persisted when week 1 was removed in sensitivity analysis. | Use a small set of lagged totals or summaries of completed weeks. |
| Product model | 237 categories; median active in 11 of 45 weeks; 20 active only once; 2,077 releases unassigned. | Defer per-model inputs. Test broader lagged product shares separately if justified. |
| Size within model | 835 combinations; median active in six weeks; 5,651 releases unassigned. | Defer: sparse combinations and unverified size definitions. |
| Production size within model | 942 combinations; median active in five weeks; 2,100 releases unassigned. | Defer for the same reasons; do not treat as interchangeable with size. |
| Epicor customer group | 16 groups; median active in 27 weeks; top five accounted for 90.1% of net GBP; two releases unassigned. | Best mix candidate for a separate lagged-feature comparison, not a first-model requirement. Group concentration does not establish account concentration. |
| Calendar, receipt activity and quantity | One verified calendar year, incomplete historical shutdown dates, and unverified quantity basis/UOM. | Use the calendar to assign weeks; defer these predictor families for the first experiment. |

The Christmas shutdown is part of the trading calendar. Mark the confirmed
shutdown weeks in the history and retain any orders recorded during them.
Check year-end gaps against each year's shutdown dates before treating a
week with no records as zero intake. The three year-end gaps in local VA05
contain receipt-dated orders but no orders dated in those weeks. Nearly all
of those receipts have January order dates. This is consistent with the
shutdown affecting order entry, but the exact historical closure dates
are not verified. Keep those weeks separate from confirmed zero-intake
weeks.

## Current milestone: make the EDA evidence visible

The initial analytical review is complete for the one-week-ahead GBP
prototype. The browser now shows the `DEV-7` weekly-net distribution and
summaries using the same calculation as the `--eda` command. It identifies
week 1 as an IQR outlier candidate while keeping it in the extract, and
separately marks week 34 as a data-quality exception. Its sensitivity table
compares all observed completed weeks, outlier-excluded weeks, quality-flag
excluded weeks, and both exclusions. Partial weeks and unavailable values are
not filled with zero; no saved data is changed.

Completed weeks now link to a saved-release investigation. This shows the
same inclusion/exclusion decisions as the weekly aggregation, conflicting
main observations, latest record reviews and the largest captured mix
contributors. Review outcomes stay at record level; business explanations
use week-labelled extract notes. Neither clears a calculation exception or
changes the frozen values. Any reconciled calculation needs a separate,
evidenced preparation step. An explained shutdown-related spike is not
automatically removed or redistributed.

Browser checks against `DEV-7` confirmed that the retained totals for weeks 1
and 34 were unchanged. Week 34's single excluded release showed both conflicting
main observations and existing review evidence without clearing the weekly flag.
The CLI still matched all 45 completed weeks to independent SQL and the
existing CSV. Focused tests cover release grain, filters and pagination,
record-review return links, note permissions and persistence, and unchanged
weekly exclusions after acceptance.

The next two EDA steps are **Time patterns and lags**, followed by
**Feature decisions**. Continue using `DEV-7`; no new capture is needed.

### Following milestone: first learned forecast

Use the frozen `DEV-7` weekly series to compare a small lag-based regression
with the previous-week and four-week-average benchmarks on common rolling
forecast targets. Report MAE, WAPE and bias and repeat the comparison without
windows touching the flagged week 34. The
[review command](../../scripts/weekly_intake_review.py) reproduces the EDA
and benchmarks with `--eda --mix --compare-lookback 4`.

Treat the inspected 2026 weeks as development data. Use prospective forecasts
for an untouched check before recommending a default in AI Tools. No further
extract is needed for the development comparison; this does not establish
historical as-of accuracy or annual seasonality.
