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

Link original intake, subsequent value/quantity changes, cancellations, and
actual shipment events. Map keys, dates, UOM, values, and customer/category
fields. Profile coverage and reconcile the movement from opening order book
through intake and changes to shipments and closing order book.

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

### Review feedback

On 18 November 2026, review of the VA05 findings highlighted that the number of
records makes a single mixed list difficult to work through. Filtering by
finding type is the next usability improvement to assess. This is a review
workflow issue, not evidence that every flagged record is an error or needs
individual correction.

## Next milestone

Establish Project 1's intake dataset and first baseline:

1. Use the existing VA05 source to investigate receipt dates, counts, values,
   and quantities. Saved local observations and review evidence are available;
   verify which values preserve original receipt rather than later changes.
2. Resolve UOM, valuation, duplicates, and usable historical coverage, recording
   decisions against saved findings before implementing preparation rules.
3. Agree intake frequency/horizon and reconcile representative totals.
4. Save the intake dataset, complete EDA, and evaluate the first intake baseline.

Record amendment/cancellation and shipment-source requirements for Project 2
alongside this work.
