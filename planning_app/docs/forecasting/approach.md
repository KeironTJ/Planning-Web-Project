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

**Output:** a reconciled dataset and data-quality report for the first target.

### 3. Explore demand

Examine trends, seasonality, product mix, customer concentration, gaps, zeros,
outliers, and business changes. Distinguish missing data from genuine zero
demand and investigate exceptions before correcting or excluding them.
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
and results. Retain the simpler approach unless complexity adds useful value.

**Output:** a justified model choice and final holdout results.

### 6. Deliver and monitor

Bring dataset selection, experiments, forecasts, uncertainty, and exports into
AI Tools. Develop browser controls around the choices demonstrated by working
experiments. Review usability with intended users.

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

## Next milestone

The first milestone is a weekly GBP baseline from Planning's imported VA05.
Aggregate one value per release by financial week, check historical coverage,
and compare representative periods with SOI. Separate known reporting
adjustments from unexplained differences. The resulting history reflects
VA05 values at extraction, not what the order book showed at each close.

Use the net VA05 view for the GBP baseline: exclude lines marked void at
extraction and report their value separately. In the 45 completed 2026
weeks checked, voided lines accounted for 4.54% of stored release value,
with a maximum weekly impact of 15.08%.

The weekly intake view was checked against independent read-only SQL for
financial week 40 (5-11 October) using development capture `DEV-6`
(19 November 2026, `va05-local-v3`, `weekly-net-gbp-v1`). The rendered chart
and table matched the SQL check on release counts, net GBP and voided GBP:
616 included releases, 40 excluded as void, and no exceptions. This checks
one completed week against the local source, not historical completeness.

The Christmas shutdown is part of the trading calendar. Mark the confirmed
shutdown weeks in the history and retain any orders recorded during them.
Check year-end gaps against each year's shutdown dates before treating a
week with no records as zero intake. The three year-end gaps in local VA05
contain receipt-dated orders but no orders dated in those weeks. Nearly all
of those receipts have January order dates. This is consistent with the
shutdown affecting order entry, but the exact historical closure dates
are not verified. Keep those weeks separate from confirmed zero-intake
weeks.

Build the first dataset once coverage and valuation are understood. Explore
the resulting series and test a simple baseline. Add volume once the quantity
basis and UOM are verified. Project 2 needs dated change and shipment
records; these are separate from the first intake baseline.
