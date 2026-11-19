# Order intake and shipped output forecasting

## Aim

**Project 1** forecasts future customer demand by predicting incoming orders,
order value, and volume. **Project 2** converts forecast demand and the existing
order backlog into predictions of shipped value and volume over a **12-16-week**
planning horizon.

Together, they form an end-to-end forecasting workflow from order intake to
operational output, accounting for amendments, cancellations, and shipment timing.

| Project | Outputs | Horizon |
| --- | --- | --- |
| 1. AI intake forecasting | Incoming orders, value, and volume | To be agreed to support output planning |
| 2. Intake-to-output conversion | Actual shipped value and volume | Next 12-16 weeks |

The relationship is **reported intake -> later amendments/cancellations and
shipment timing -> shipped output**. Project 1 uses Planning's imported VA05
and assigns intake to financial periods by Epicor order date. Published SOI is
a reconciliation reference. Existing outstanding orders also contribute to
future output. Shipment date, not invoice date or production completion,
determines when output occurs; the valuation basis still needs agreement.
Finished goods awaiting shipment do not count as output until they ship.

Results will be available by customer group and for the company overall.
Planning-app customer groups will be the default, with Epicor groups as an
alternative. Weekly units due to ship remain a supporting planning measure.

Shipped volume measures quantities, not labour hours. Full units, covers,
scatters, and other work can place different demands on production.

## Planned workflow

The browser will support:

1. Selecting sources, history windows, customer groups, and product categories.
2. Exploring data quality, trends, seasonality, and product mix.
3. Configuring features, models, evaluation periods, and tuning budgets.
4. Comparing models against simple forecasting benchmarks.
5. Generating forecasts, uncertainty intervals, and exports.
6. Tracking issued forecasts against actual results.

Develop and evaluate Project 1 first, then use its forecasts in Project 2.
Compare conversion models with a simple conversion baseline and a direct output
forecast. Known orders and forecast new intake must be counted once. Group
forecasts will reconcile to the company total.

The module will reuse Flask authentication, permissions, Epicor access, and
customer-group configuration. Preparation and modelling services will sit
behind validated browser controls, with background training and visible errors.
Versioned training data will remain separate from refreshed operational tables.
Forecasts will not automatically change live planning.

## Current progress

**Available:** AI Tools overview, Data Readiness with saved VA05 observations
and record-level quality review, and Model Classification.

**Investigated:** existing importers, a local sales-order extract, and a bounded
live `bskyVA05v1` response on 12 November 2026. The checks found repeated release
quantities, mixed UOM, and date exceptions. Historical demand cannot be inferred
from snapshot record counts alone.

**Next:** establish Project 1's historical intake dataset and first baseline.
Reconcile VA05 order counts, values, and quantities in verified UOM to the
month-end intake report before establishing historical coverage.
Project 2 will also need amendment/cancellation history and dated shipments.
Saved quality reviews preserve observed local values, not verified historical
intake or an approved training dataset.

No historical training dataset, EDA, or forecasting model has been produced.

## Reproduce the weekly GBP review

From `planning_app`, with the project's Python environment active:

```powershell
python -m scripts.weekly_intake_review --db .\instance\planning_dev.db --extract-id 6 --output .\data\va05-weekly-net-gbp-dev-6.csv
```

Use the local SQLite database containing the saved extract. `DEV-6` is the
development capture used for the initial benchmark; extract IDs differ by
environment. The command opens SQLite read-only, compares the Python result
with independent SQL over the saved observations, and writes a CSV only if
the totals agree. It verifies an existing identical CSV without overwriting it.
Keep the CSV in the ignored `data` directory or another approved local location.

The command reports previous-week MAE, WAPE and bias with and without
flagged weeks. Partial weeks are not exported, and missing weeks are not
replaced with zero or bridged when computing forecast errors.

## Data Readiness

Open **AI Tools > Data Readiness** at `/ai-tools/data-readiness`.
Viewing requires `view_planning`; capture and review also require
`manage_planning`.

1. Use **Save local VA05 extract and check** to preserve the currently imported
   `bskyVA05v1` observations. This does not fetch Epicor or run an importer.
2. Inspect findings and their affected records. Switch between all records,
   records with findings, and findings without review evidence. Select a
   finding type, or click an item in the visible findings summary, to focus
   on that kind of issue. To see the job and assembly rows together, select
   **View all rows for this release** on a saved row. Use **All saved rows**
   to browse releases that don't have findings.
3. Use the model name and its current Model Classification category as context
   where captured. Verify whether the VA05 model identifies the individual
   assembly; a category does not decide which work to count.
4. Record an outcome and supporting evidence. Later reviews append to the
   history; they do not overwrite earlier decisions.
5. Use **Comments / Notes** for observations about the whole extract. Notes
   are timestamped, attributed to the author, and append-only. They do not
   change the saved observations, hash, findings, or row decisions. Do not
   include customer or personal details.
6. Open **Explore weekly intake** from a saved extract. The chart and table
   use the verified 2026 SOI calendar, show one consistent main value per
   release, and report void GBP separately from net GBP. Partial weeks,
   possible Christmas shutdown weeks, and unavailable values are labelled.
   Extracts without the captured void field cannot run this analysis.
   The view does not fetch Epicor, read live order values, or save a dataset.

Checks distinguish main/no-assembly quantities and values from subassemblies
and report conflicts, missing fields, and date exceptions. New captures
do not flag a release simply because it has multiple rows. Earlier extracts
retain their original findings and evidence; save a new extract to use the
updated checks. The findings summary shows affected rows across the full
extract, not units, money, or review progress. Rows can appear under multiple
findings. The matching-record count reflects the selected filters.

New extracts retain selected keys, job/assembly links, model names, dates,
quantities, inventory UOM, release values, currency fields, and row import
timestamps. Older extracts without model names remain unchanged and show
that the model was not captured.
Customer names, personal fields, and operational free-text comments are not
copied. Extraction parameters cannot be recovered from the current local table
and are recorded as unverified, not guessed.

Each extract records its capture time/user, selection, fields, rule version,
findings, and SHA-256 hash of source metadata and ordered observations.
It survives operational refreshes. Stored decimal values retain their precision;
missing values remain missing. Review evidence is audited and must not contain
personal details.

The category shown beside a saved model is the **current** assignment and can
change after capture. It is not evidence of historical membership. Models
are matched using the same trimmed, case-sensitive names and blank-name
assignment as Model Classification. Category labels do not change checks,
quantities, or forecast inclusion.

Acceptance means a recorded judgement, **not training approval**. No review
automatically corrects, excludes, deduplicates, or changes operational records.
Controlled preparation, reconciliation, and dataset approval remain later steps.
The configured application database holds saved extracts and evidence. Use
separate development and production databases; a deployment does not carry
development extracts or notes into production. Live imports update the local
operational table, not existing saved extracts or their evidence.
Extract references such as `DEV-12` and `LIVE-12` use the app environment
recorded when the extract was saved. Earlier extracts with no recorded
environment appear as `UNLABELLED`; their provenance is not inferred.

Users with `manage_planning` can use **Delete entire saved extract** at the
bottom of the review page. Confirmation deletes all saved rows for that
extract, regardless of filters. Extracts with notes or row reviews are protected.
Deletion is audited and does not change Epicor or operational data. There is
no automatic expiry; agree a retention policy before production capture.

## Model Classification

Open **AI Tools > Model Classification** at `/ai-tools/models`.
Viewing requires `view_planning`; editing also requires `manage_planning`.

Create categories around work content, then assign exact model names using
individual dropdowns or the checkbox selection action. Click **Save all
changes** to persist pending assignments together.

- Model names are trimmed and case-sensitive.
- Unclassified models can remain unassigned.
- Blank names form one group; its category does not distinguish individual
  parts, replacements, fabrics, or other unnamed work.
- Assigned models must be moved before deleting a category.
- Assignments survive COOIS refreshes.

The summary shows current COOIS job/assembly records and quantity exceptions,
not historical unit demand. Saved-assignment counts can include models absent
from the current snapshot.

Categories describe products. Future forecast configurations will decide
inclusion and grouping independently, allowing different treatment of work
content without changing its labels. Classification does not change existing
capacity-board calculations.

### Setup

Apply the migration using the application's configured environment:

```text
flask --app wsgi db upgrade
```

Migrations preserve model assignments and add separate extract, observation,
row review, and extract note tables. Apply them to the intended environment
before opening Data Readiness. They do not capture operational records
automatically.

## Documentation

- [Project approach](approach.md): lifecycle, evaluation, and AI Level 7 report
  evidence.
- [Data contract](data-contract.md): definitions, source mappings, findings, and
  outstanding data decisions.

These documents will support the project report with actual findings and the
reasoning behind decisions.
