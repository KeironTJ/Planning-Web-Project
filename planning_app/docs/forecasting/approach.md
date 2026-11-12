# Forecasting project approach

## Purpose

Build and evaluate a forecasting tool that helps sales and planning teams
understand upcoming revenue and product demand. The project will follow a
business-led AI lifecycle: understand the problem, prepare and explore the
data, establish benchmarks, test models, deliver forecasts, and monitor results.

The [project overview](README.md) defines the goals and initial scope. The
[data contract](data-contract.md) holds the detailed source mappings and
calculation rules. This document describes how we will develop and evaluate
the solution.

## Initial outcomes

- Forecast monthly net invoiced sales in GBP for the next three months.
- Forecast weekly unit demand due to ship for the next twelve weeks.
- Show results by customer group and for the company overall.
- Explain forecast performance, uncertainty, and limitations in business terms.

Revenue and unit demand will be developed as separate targets within a shared
browser-based workflow. Lead-time prediction remains a later extension.

## Working method

Work in small, reviewable milestones. At each milestone, review the evidence, agree the next decision, and document why the approach was chosen.
Findings can change the design; earlier datasets and experiment results must
remain traceable to the definitions used at the time.

Browser controls will expose meaningful choices such as target, grouping,
history window, features, model settings, and tuning budget. Validated options
will keep those choices compatible with the selected data and model. The first
working experiment will guide which controls are genuinely useful rather than
building a large configuration interface before the modelling needs are known.

## Delivery stages

### 1. Confirm the business problem

Agree who will use each forecast, which decisions it supports, and what an
error means for those decisions. Confirm targets, horizons, reporting groups,
and how known orders should appear alongside forecast demand.

**Output:** agreed forecast definitions and business acceptance criteria.
Agree measurable accuracy, bias, and uncertainty expectations before final
evaluation; do not choose thresholds after seeing the final results.

### 2. Establish reliable historical data

Review the available Epicor sources and map their identifiers, dates,
quantities, customer fields, invoice amounts, and credits. Profile actual
coverage and reconcile totals with a trusted operational or financial report.

Model categories support this preparation. Full units, covers, scatters, and
other work can have different demand patterns and forecasting needs.
Categories describe the work; each forecast will explicitly define how those
categories are grouped or included. A category applied to blank model names
labels the combined unnamed group, not its individual product types.

Save forecasting data separately from the refreshed operational tables.
Retain source and extraction details, category and customer-group mappings,
and the calculation settings used to build each dataset.

**Output:** a reconciled, versioned dataset for the first target, with a
data-quality report and completed source mapping. Historical availability must
be understood before using order-book or production fields in backtests.

### 3. Explore and understand demand

Analyse trends, seasonality, customer concentration, product mix, missing
periods, zero-demand periods, outliers, and changes in business activity.
Compare categories and customer groups to understand where separate treatment
may help and where there is too little history to model reliably.

Separate genuine zero activity from missing data. Investigate unusual values
before deciding whether to retain, correct, or exclude them.

**Output:** EDA findings, justified preparation decisions, and a shortlist of
features and modelling approaches supported by the observed data.

### 4. Establish forecasting benchmarks

Build simple benchmarks such as repeating the latest period and the equivalent
period in the previous year, where sufficient history exists.

Evaluate forecasts forward from several historical cutoffs across the full
target horizon. Keep a final holdout period separate from model selection.
Fit preprocessing only on each training window and use only information
available at that cutoff.

**Output:** a repeatable evaluation pipeline and baseline results by customer
group, company total, and forecast horizon.

### 5. Develop and tune candidate models

Compare a small set of statistical and machine-learning approaches selected
from the EDA findings. Test useful features and tune parameters within an
agreed budget using the same time-based evaluation as the benchmarks.

Record dataset version, settings, features, model and code versions, evaluation
cutoffs, runtime, and results for each experiment. Prefer the simpler approach
when extra complexity does not provide a useful improvement.

**Output:** a justified model choice, comparison results, and final holdout
evaluation. Retain the baseline if candidates do not improve on it usefully.

### 6. Deliver a usable forecasting workflow

Bring dataset selection, experiments, results, and forecast generation into
AI Tools. Show forecast values and uncertainty, group and company totals,
model version, and exports. Keep known order-book demand distinguishable from
predicted demand so it is not counted twice.

Validate the workflow with its intended users. Forecasting must remain separate
from automatic changes to live planning.

**Output:** a working browser workflow, user guidance, and recorded feedback.

### 7. Monitor and evaluate

Store issued forecasts and compare them with actuals as they become available.
Review accuracy, systematic over- or under-forecasting, interval performance,
and changes in the underlying data. Agree when a model should be reviewed,
retrained, or replaced.

**Output:** forecast-versus-actual reporting and a project evaluation covering
business usefulness, technical performance, limitations, and recommendations.

## How performance will be judged

Use MAE to express errors in GBP or units, bias to show their direction, and
WAPE where actual totals are nonzero. Evaluate prediction-interval coverage
and width where intervals are produced. Report results by horizon and customer
group as well as overall, so strong performance in large groups does not hide
poor performance elsewhere.

Accuracy alone is not enough. Consider reproducibility, runtime, usability,
explainability, and whether the forecast improves the intended planning
decision. Record failures and negative findings as well as successful results.

## Next milestone: historical source review

The model-classification tool provides the initial product-preparation workflow.
The next step is to establish a trustworthy historical dataset, not select an
AI algorithm.

1. Review the unit-demand source: record grain, required ship-by date, quantity
   basis, model and customer identity, cancellations, and historical coverage.
2. Identify the invoice/credit source with Finance and agree its reconciliation
   report and revenue calculation.
3. Profile available history and reconcile a representative sample for each
   target.
4. Choose the first target to implement based on data readiness and business
   value, then complete one dataset-to-baseline workflow before extending it.

This milestone is complete when the selected target can be reconstructed from
verified fields, reconciled against an agreed source, and saved reproducibly.
Unresolved data issues will have an explicit treatment before EDA and modelling
proceed.
