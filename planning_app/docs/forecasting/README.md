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

The relationship is **raw intake -> amendments/cancellations and shipment
timing -> shipped output**. Existing outstanding orders also contribute to
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

**Available:** AI Tools navigation and the Model Classification screen.

**Investigated:** existing importers, a local sales-order extract, and a bounded
live `bskyVA05v1` response on 12 November 2026. The checks found repeated release
quantities, mixed UOM, and date exceptions. Historical demand cannot be inferred
from snapshot record counts alone.

**Next:** establish Project 1's historical intake dataset and first baseline.
Confirm original receipt dates, order counts, quantities/values, UOM, and coverage.
Project 2 will also need amendment/cancellation history and dated shipments.

No historical training dataset, EDA, or forecasting model has been produced.

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

The migration creates editable categories and preserves existing assignments.

## Documentation

- [Project approach](approach.md): lifecycle, evaluation, and AI Level 7 report
  evidence.
- [Data contract](data-contract.md): definitions, source mappings, findings, and
  outstanding data decisions.

These documents will support the project report with actual findings and the
reasoning behind decisions.
