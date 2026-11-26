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

AI Tools uses one sidebar workflow: **Overview > Prepare data > Explore
intake > Compare models**. Product categories are a separate supporting tool.
Breadcrumbs show the current page; the saved-data bar shows the selected
extract and order-date period. Workflow links keep that extract in context.
These indicators show location, not sign-off or forecasting readiness.
On small screens, expand **AI Tools menu** for the same workflow links.

The overview opens the latest saved extract by default, which may be an
adjusted extract, and recommends reviewing
unreviewed findings, following up investigation/correction outcomes, or
checking coverage. Completed reviews are not a data approval. Change extract
opens the saved-extract library. Live import diagnostics, calculation rules,
provenance and future capabilities are secondary, expandable sections.
Issued forecasts and monitoring are under **Coming later**, not active steps.

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

**Available:** AI Tools overview, Prepare data with saved VA05 observations
and record-level quality review, Product categories, weekly-intake review,
unusual-week analysis, multi-year time patterns and lags, feature decisions,
and development model comparisons.

**Investigated:** existing importers, a local sales-order extract, and a bounded
live `bskyVA05v1` response on 12 November 2026. The checks found repeated release
quantities, mixed UOM, and date exceptions. Historical demand cannot be inferred
from snapshot record counts alone.

**Next:** continue data cleansing and reconcile VA05 order counts, values,
and quantities in verified UOM to the month-end intake report while developing
and evaluating the forecasting approach.
Project 2 will also need amendment/cancellation history and dated shipments.
Saved quality reviews preserve observed local values, not verified historical
intake or an approved training dataset.

Development comparisons are not approved training datasets or issued forecasts.

## Browser EDA workflow

Open **AI Tools > Explore intake** to explore saved data, or select
**Review data** on an extract card in **Prepare data** to inspect findings.
The live-source diagnostics sit separately under **Live import checks and
source limitations**. Start the extract analysis with
coverage and exclusions, then review the weekly GBP chart and product and
customer mix. **Distribution and unusual weeks** uses the same saved-extract
EDA calculations as `--eda`: it plots completed observed weeks, marks
statistical outliers separately from data-quality exceptions, and compares
summaries with each flagged-week type excluded. Partial and unavailable weeks
are not treated as zero, and the page does not change the extract. For DEV-7,
week 1 is the IQR outlier candidate and week 34 is a data-quality exception.
Select a distribution point, **Investigate week**, or a completed week number
in the coverage table to trace the total to its saved releases. The week view
shows included, voided and exception-excluded releases, the conflicting main
GBP/void observations, latest row-review evidence, and the largest captured
product/customer-group contributors. Weeks with exceptions open on excluded
releases; other weeks open on included releases, largest GBP first.

Use **Review all saved rows for this release** to record or revisit the
existing row-level decision. A return link preserves the week investigation
context; that context does not filter the record-review page by week.
Record business explanations, such as a closure delaying order entry, through
**Record an explanation**. Notes added from a week investigation are labelled
with its SOI week and saved in the existing append-only extract notes, visible
to everyone with planning view access. There is no separate week-decision
status or automatic modelling treatment.

Resolving an underlying source conflict can make a later reconciled weekly
calculation usable. Merely marking a saved row **Accepted with evidence**
cannot select a value or void status, restore an excluded release, or clear
the current extract's weekly flag. The saved observations and CLI totals remain
unchanged. An explained statistical spike likewise remains recorded; do not
automatically split or remove it.

**Time patterns and lags** shows completed weekly values by financial year on
a common scale, with gaps, quality flags and configured possible shutdowns
visible. Select a week to investigate its releases and record an explanation.
The lag table excludes partial, unavailable and quality-flagged weeks and pairs
observations by exact calendar distance, including across year boundaries.
Its outlier sensitivity uses retrospective per-year IQR candidates; neither
view changes the saved extract.

The same-financial-week-last-year benchmark compares with previous week on
identical eligible targets. It matches financial week numbers, not a fixed
52-week offset; week 53 has no counterpart in a 52-week year. Both methods
require complete, observed, unflagged target and predictor weeks. Scores are
development evidence from the current capture, not historical as-of or holdout
results. The shutdown labels are not a complete historical closure calendar.
Use **Feature decisions** to record candidate inputs for the selected extract.
For each, choose test, defer or reject and record coverage and stability,
supporting evidence, availability at the forecast cutoff, cutoff details and
rationale. Candidate names are trimmed and matched case-insensitively within
the extract. Updates append a new decision and retain previous evidence; a
stale update must be reviewed again. Links lead to saved evidence, but text
references are not automatically verified. Users with `view_planning` can
read decisions; `manage_planning` is also required to record them. Do not
include customer or personal details. Decisions are manual documentation:
they do not approve data, add features to experiments or configure models.
Apply the feature-decisions migration before opening the updated workflow.
Select anywhere on a navigation card to open it.
Forecasting and monitoring are separate planned stages.

## Browser model experiments

Open **AI Tools > Model Experiments** and choose a saved extract. Users with
`view_planning` can view saved runs; starting one also requires
`manage_planning`. Apply the database migration and install the optional
forecasting requirements in the development application's environment before
starting a run. Starting experiments is disabled in production.

The comparison uses all completed financial weeks in the saved extract and
the same modelling calculations as the CLI history comparison below. Choose a
saved run, sensitivity, financial year and method to inspect scores, predictions
and the largest errors; those weeks link back to the saved release investigation.
New comparisons include two otherwise identical gradient-boosting models: four
prior weekly values alone, and those values plus the target's financial week
number (1-53). The calendar value is known before the target week. Compare the
two on the same target weeks overall and by financial year; no tuning or
seasonal transformation is performed. Earlier saved runs remain viewable but
do not contain the new comparison.
Results, extract hash, settings, package versions and hashes of the source
files observed during execution are saved. These hashes are not a code revision,
and no fitted model is saved. Earlier CLI results are not imported.

Open **Performance history** from **Compare models** to compare two saved
completed runs, including runs from different extracts. Choose a method for
each side and a shared sensitivity. The matching-target table scores only
weeks present in both runs with the same actual GBP; changed actuals and
non-overlapping weeks are counted separately. If analysis rules differ, the
matching-target scores are not shown. The full-run scores are also shown for
context but may cover different weeks. Follow the run links to inspect each
extract hash, settings, package versions and code hashes before attributing a
difference to a model change: matching actuals do not establish that the
training data or model configuration were identical. Runs are development
evidence, not a prospective holdout. The page can also compare two methods in
one run, and reports when an older run lacks a selected method.

Only one experiment can be active across application workers. A background
thread runs the comparison and may be interrupted by a worker restart; there
is no automatic retry. After six hours, starting another run marks the old
active run failed. Reload the page to check status. This is development
evidence from one capture, not a held-out evaluation or production forecast.

## Reproduce the weekly GBP review

From `planning_app`, with the project's Python environment active:

```powershell
python -m scripts.weekly_intake_review --db .\instance\planning_dev.db --extract-id 7 --output .\data\va05-weekly-net-gbp-dev-7.csv
```

Use the local SQLite database containing the saved extract. `DEV-7` is the
development capture used by the current browser EDA; the initial benchmark was
first run against `DEV-6`. Extract IDs differ by environment. The command opens
SQLite read-only, compares the Python result with independent SQL over the
saved observations, and writes a CSV only if the totals agree. It verifies an
existing identical CSV without overwriting it. Keep the CSV in the ignored
`data` directory or another approved local location.

The command reports previous-week MAE, WAPE and bias with and without
flagged weeks. Partial weeks are not exported, and missing weeks are not
replaced with zero or bridged when computing forecast errors.

Append `--compare-lookback 4` to compare last week's intake with the average
of the previous four weeks. Both methods are scored on the same target weeks,
using only observations before each target. The command lists the scored
weeks and the three largest previous-week misses. The sensitivity comparison
omits any target or lookback window containing a flagged week. This is a
development comparison, not a held-out evaluation.

Append `--experiment` to run a one-week-ahead development comparison of
previous week, the previous-four-week mean and a one-lag ordinary least-squares
regression. The regression is refitted at each target using adjacent prior-week
pairs available before that target, with an expanding history and a fixed
minimum of eight training pairs. The minimum is a starting rule, not a tuned
parameter. No external modelling library is required.

The report lists each target, forecast and training-pair count, and scores all
three methods on identical targets. It repeats the experiment excluding
quality-flagged weeks, IQR outlier candidates, and both. Excluded weeks cannot
appear in a target's four-week window or either side of a regression training
pair. Outlier candidates are identified from the full development series, so
this is a retrospective sensitivity check, not a real-time outlier rule. The
final section compares all scenarios on their common target weeks. Missing or
partial weeks are never filled or bridged; an insufficient or constant training
predictor is reported rather than silently replaced by another model.

All results are development evidence from a single capture, not a final
holdout or historical as-of backtest. The report does not save a model, issue
forecasts, or change the extract. Preserve the output locally if needed, along
with the code revision and verified CSV, which records the extract content hash.

For the optional multi-model comparison, install
`python -m pip install -r .\requirements-forecasting.txt` into a development
environment, then run:

```powershell
python -m scripts.weekly_model_comparison --db .\instance\planning_dev.db --extract-id 7 --output .\data\va05-weekly-net-gbp-dev-7.csv
```

This compares the previous-week and four-week benchmarks, one-lag linear
regression, ARIMA(1,1,0) without drift, and gradient boosting using four lags,
both without and with the target financial week number.
For numerical stability, ARIMA is scaled by the largest absolute available
prior value (with a minimum scale of one) and converted back to GBP for scoring.
Its fixed optimizer is Powell, with at most 500 iterations.
All methods use common target weeks, with at least 20 complete prior four-lag
training rows. Each fit uses only earlier observations. ARIMA retains gaps and
excluded weeks as missing observations, not zeroes; linear and boosting rows
touching them are omitted. The quality and retrospectively identified outlier
sensitivities apply to both training and target/lookback windows. Outputs include
the target weeks, predictions, training-row counts, package versions, MAE, WAPE
and bias. These fixed starting settings are not tuned; failed model fits stop
the report rather than silently substituting another forecast.
By default the comparison uses SOI 2026, as before. To compare all financial
years in a saved extract, supply `--history` and a separate output CSV:

```powershell
python -m scripts.weekly_model_comparison --db .\instance\planning_dev.db --extract-id 9 --output .\data\va05-weekly-net-gbp-dev-9-history.csv --history
```

The history CSV is independently checked against the saved observations and
identifies weeks by start date and financial year. Partial weeks are omitted;
unavailable completed weeks remain missing. Each financial year's IQR outliers
are identified retrospectively for sensitivity, not as a real-time rule.
Compare metrics by year as well as overall. A broader saved extract does not
establish source completeness, historical as-of accuracy, or a final holdout.

Append `--eda` for weekly coverage, distribution, outlier candidates,
early-versus-late mean, lag correlations, and the same four inclusion
sensitivities shown in the browser. The clean reference excludes partial,
unavailable, and exception weeks; comparison rows show the effect of retaining
or excluding each flagged-week type. Lag pairs use financial week numbers
rather than closing gaps by shifting rows. A possible shutdown is a label, not
a reason to discard a recorded total.

Append `--mix` for model, size within model, production size within model,
and Epicor customer-group profiles. The command checks each breakdown against
weekly GBP and release counts, then reports unresolved labels, concentration,
category activity and the main contributors to the peak week. Save a new
VA05 capture for size and customer group. Older captures report unavailable
dimensions without reading live rows. Keep detailed category results local.
The **Product and customer mix** table in **Weekly intake** shows
the same coverage, activity and concentration metrics for the saved extract.
Partial weeks are excluded; dimensions absent from the capture are marked
**Not captured**.

## Prepare data

AI Tools displays dates as `DD/MM/YYYY` and timestamps in the same format
with a local 24-hour time. Saved extracts and CSV exports keep their ISO dates.

Open **AI Tools > Prepare data** at `/ai-tools/data-readiness`.
Viewing requires `view_planning`; capture and review also require
`manage_planning`.

Before deploying the adjustment and exclusion workflow, back up the configured
database and run `flask db upgrade` from `planning_app` using the target
environment. Apply all migrations before serving the updated application.

The current-import readiness section uses **Epicor order date** for Project 1.
It counts releases with missing or inconsistent order dates separately from
releases with consistent dates inside or outside the verified SOI calendar.
This checks date coverage, not complete weeks or usable GBP. Customer received
date remains a timing diagnostic and is never substituted for order date.
These checks do not change saved extracts or their findings.

1. Expand **Save a new extract**, choose inclusive Epicor order dates and
   select **Save extract and check**. This saves observations from the current `bskyVA05v1` import and
   does not fetch Epicor. Releases with missing or conflicting order dates
   are saved separately for review, not counted in the period.
2. In **Review data**, open **Check coverage**
   to check the whole saved period and drill into releases. Partial boundary
   weeks can be inspected but are excluded from completed-week summaries;
   gaps are unavailable, not zero. Older captures have no selected range
   and show coverage inferred from the saved dates and capture time.
   Inspect findings and their affected records. Switch between all records,
   records with findings, and findings without review evidence. Select a
   finding type, or click an item in **Findings by type**, to focus
   on that kind of issue. To see the job and assembly rows together, select
   **View all rows for this release** on a saved row. Use **All saved rows**
   to browse releases that don't have findings.
   The active filters are shown above the rows. Remove one at a time or use
   **Clear all filters** to show all saved rows. A GBP group depends on the
   nonpositive GBP finding and is cleared with it. Week investigation context
   and the selected page size are retained.
   The findings summary shows how many affected rows have a saved review
   outcome for each finding. Select **not reviewed** to open the remaining
   rows. The row highlights show red for rows still needing review, amber when
   all affected rows are reviewed, and green when there are no affected rows.
   Reviewed does not mean accepted or corrected; these colours indicate review
   progress, not the severity or resolution of a finding.
   On the original capture, **Planned rows** counts distinct affected rows
   covered by active corrections or exclusions, including order-wide date
   corrections. A planned adjustment may not address that finding, and only
   takes effect in a separately created adjusted extract. Counts can overlap
   between finding types; cleared or merely suggested adjustments do not count.
   Selecting the nonpositive GBP finding shows a breakdown directly above the
   saved rows. It splits
   flagged rows into consistently void zeroes, consistently non-void zeroes,
   negative GBP, and conflicting or incomplete main observations. Select
   a group to filter the saved review list, or open a release to compare
   saved GBP and void observations. Bulk review and worklist exports apply
   to the selected group across all pages. Choose **All groups** for the
   whole finding. The breakdown also shows the latest review outcome
   counts for each group across all pages; select a count to filter the
   rows by that outcome. The saved-records table has one set of column
   headings; scroll horizontally using the bars above or below the table.
   The order key remains visible while scrolling sideways. Expand a row to
   see its record context and full findings alongside review evidence.
   Planning adjustments are grouped separately below them.
   Choose 25, 50 or 100 rows per page and use the numbered page links to
   navigate; changing filters or page size returns to the first page.
   Managers can download CSV worklists for **Not reviewed**, **Needs
   investigation**, and **Source correction required**. The links at the
   top under **Correction worklists and new capture** cover the whole extract; the finding links cover that finding or
   selected GBP group. Worklists use the current outcome, not the history.
   Once source corrections are available
   in Planning, prefill the same dates and explicitly save a new extract.
   The previous extract and its review evidence are preserved.
   To set a review outcome across all pages, select one finding without a
   release filter, expand **Set review outcome for … matching rows**, select
   an outcome, enter a shared reason and confirm the count. To leave prior
   outcomes untouched, filter to **Not reviewed** first. Otherwise the new
   outcome becomes current, and earlier decisions remain in the history.

3. Use the model name and its current Product categories assignment as context
   where captured. Verify whether the VA05 model identifies the individual
   assembly; a category does not decide which work to count.
4. Record an outcome and supporting evidence. Later reviews append to the
   history; they do not overwrite earlier decisions. Use the status counts
   on **Review data** to filter by the latest outcome. Accepted with
   evidence is a review decision, not approval for training. On this page,
   expand only the rows you need. Draft different outcomes and evidence for
   multiple rows on the current page, then choose **Save drafted reviews**.
   The batch saves together without reloading. Save drafts before changing
   filters or moving to another page.
   To give selected rows the same outcome, use **Review selected rows on this
   page**, tick rows with findings, enter shared evidence and confirm. **Select
   all rows on this page** also selects rows without findings; deselect those
   before submitting a review. Selection is limited to the current page; existing outcomes
   remain in review history. Save individual drafts on those rows first. Use
   **Back to review controls** below the rows to return to the selection and
   draft controls without losing the current page's selections.
   To reuse a decision, tick **Remember this decision for matching future
   extracts** when saving it (also available for a shared finding review).
   Existing decisions can be saved again with this option. On a later extract,
   **Previously reviewed** highlights unreviewed rows with matching evidence.
   Open the rows, check the earlier outcome and reason, select the suggestions,
   and confirm them together on the current page. Each confirmation appends a
   new review linked to its source; it also remains eligible for future
   suggestions. To stop remembering a review in that extract, save a new review
   for that row without the checkbox. More recent matching reviews in other
   extracts remain independent.

   Matching is deliberately strict: the source and rule version must match,
   and capture environment must match, as must every captured row and its findings for the whole order, apart from
   local row IDs and import timestamps. Incomplete release keys and indistinguishable
   duplicate rows are not matched. Changed values, additional findings, different
   capture fields or a different selection of order rows require a fresh review.
   A suggestion is not a review until confirmed; a row's outcome still covers
   all its findings. No source data or forecast inclusion is changed.

   For historical values that cannot be corrected in Epicor, open **Planning
   adjustment for this release**. Managers can exclude the whole release or
   specify replacement quantity, GBP value, void status, order date and/or
   required ship-by date, with supporting evidence. Quantity and GBP replace
   main-row values; void and ship-by apply to all rows in the release. An
   order-date correction requires an explicit choice between this release
   and all captured releases on the order. The latest adjustment replaces the
   entire previous adjustment for that release; include every correction still
   needed. The form shows captured values from the selected row for reference,
   not as corrections; other rows can differ. An active adjustment is prefilled
   for editing, including its evidence, which should be reviewed and updated.
   Clear an adjustment to stop applying it. Previous decisions remain
   in the adjustment history.

   To exclude individual saved rows instead of a whole release, select rows
   on the current page and use **Exclude or restore selected rows** with shared
   evidence. Only the selected observations are excluded; other rows in the
   release remain. The same control can restore previously excluded rows.
   Row exclusions are specific to this capture and never apply automatically
   to later captures. A later capture can suggest an earlier exclusion when
   the complete saved row values and findings match uniquely within the order
   in both captures (ignoring local row ID and import timestamp), with the same
   source, rules and environment. Use **Find suggested row exclusions** to
   find matches across the extract, including rows without findings. Open
   the marked rows, inspect the earlier evidence and select suggestions on
   the current page to confirm together.
   A confirmed exclusion keeps a link to the earlier evidence. A newer
   matching restore suppresses older suggestions. Identical ambiguous rows,
   changed rows and removed rows are not suggested. Review the resulting
   findings and release totals carefully.

   Under **Planning adjustments**, review the active decisions and confirm
   **Create adjusted extract**. This creates a separate, retrospective saved
   extract with recalculated findings and links to the original and the exact
   decisions used. The latest review outcome for a surviving row carries
   forward with its original reviewer, date, evidence and a link to the
   original review only when every captured row and its findings on the order
   remain unchanged. The original remember setting is preserved; it is not
   required for this carry-over. Changed orders need fresh reviews, and
   excluded rows have no review in the adjusted extract. Existing reviews
   in an adjusted extract are never overwritten by a carry-over.
   Whole-release exclusions remove all job and assembly rows for the release;
   individual row exclusions remove only the selected observations.
   The original observations, reviews and Epicor data are unchanged. Analysis
   and model comparisons can select the adjusted extract; record which extract
   and hash were used. Later discoveries may not have been known at historical
   forecast cutoffs, so adjusted results are not an as-of backtest. A changed
   order date can move or exclude a captured release from a period; it cannot
   recover rows omitted by the original capture.

   Later captures suggest a prior adjustment only when the captured order,
   findings, source, rules and environment match. Suggestions are never
   applied automatically: inspect the evidence and confirm each one. A newer
   matching cleared decision suppresses older suggestions.
5. On **Review data**, expand **Extract details and calculation rules** below
   the review list for assembly guidance and provenance. **Business notes**
   follow these details. Use notes for observations about the extract or a named
   financial week. Notes added from a week investigation receive its week label. Notes
   are timestamped, attributed to the author, and append-only. They do not
   change the saved observations, hash, findings, or row decisions. Do not
   include customer or personal details.
6. Open **Explore intake > Weekly intake** for the selected extract. The chart and table
   use the verified 2026 SOI calendar, show one consistent main value per
   release, and report void GBP separately from net GBP. Partial weeks,
   possible Christmas shutdown weeks, and unavailable values are labelled.
   Extracts without the captured void field cannot run this analysis.
   The view does not fetch Epicor, read live order values, or save a dataset.

EDA uses the immutable extract and does not select training dates. The
existing browser charts and CLI review still use SOI 2026 within the saved
scope; the coverage drilldown works across financial years. Training and
evaluation windows will be chosen at the modelling stage.

Checks distinguish main/no-assembly quantities and values from subassemblies
and report conflicts, missing fields, and date exceptions. New captures
do not flag a release simply because it has multiple rows. Earlier extracts
retain their original findings and evidence; save a new extract to use the
updated checks. The findings summary shows affected rows across the full
extract, not units, money, or review progress. Rows can appear under multiple
findings. The matching-record count reflects the selected filters.

New extracts retain selected keys, customer ID and group, job/assembly links, model names, dates,
quantities, inventory UOM, release values, currency fields, and row import
timestamps. Older extracts without model names remain unchanged and show
that the model was not captured.
Customer names, other personal fields, and operational free-text comments are
not copied. Customer IDs may still identify individuals; use the existing
planning permissions and follow company access and retention policy. Earlier
extracts without customer ID show **Not captured** and are not backfilled.
The saved-row review shows customer ID and group alongside model and both
size fields. Extraction parameters cannot be recovered from the current local table
and are recorded as unverified, not guessed.

Each extract records its capture time/user, selection, fields, rule version,
findings, and SHA-256 hash of source metadata and ordered observations.
It survives operational refreshes. Stored decimal values retain their precision;
missing values remain missing. Review evidence is audited and must not contain
personal details.

The category shown beside a saved model is the **current** assignment and can
change after capture. It is not evidence of historical membership. Models
are matched using the same trimmed, case-sensitive names and blank-name
assignment as Product categories. Category labels do not change checks,
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
extract, regardless of filters. Extracts with notes, row reviews, model
experiments or feature decisions are protected.
Deletion is audited and does not change Epicor or operational data. There is
no automatic expiry; agree a retention policy before production capture.

## Product categories

Open **AI Tools > Product categories** at `/ai-tools/models`.
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
row review, extract note, experiment, feature-decision and reusable-review changes. Apply them to the intended environment
before opening Prepare data. They do not capture operational records
automatically.

## Documentation

- [Project approach](approach.md): lifecycle, evaluation, and AI Level 7 report
  evidence.
- [Data contract](data-contract.md): definitions, source mappings, findings, and
  outstanding data decisions.

These documents will support the project report with actual findings and the
reasoning behind decisions.
