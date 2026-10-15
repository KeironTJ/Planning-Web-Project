# Sales and production demand forecasting

## Project aim

This project will use historical Epicor data to forecast monthly net sales and
weekly unit demand within the planning application. It will give sales and
planning teams a forward view of expected revenue and volume, showing where
demand is likely to increase, fall, or change between customer groups.

The application will support the full modelling process through the browser:
exploring data, configuring experiments, comparing models, generating
forecasts, and tracking results against actual performance.

## Business questions

- What net sales can be expected over the next three months?
- How many units are likely to be due to ship over the next twelve weeks?
- Which customer groups are driving changes in revenue and volume?
- How do seasonal patterns affect demand?
- How accurate are the forecasts, and where do they consistently overestimate
  or underestimate demand?

A later extension will examine how the time from order receipt to shipment
changes throughout the year.

## Forecasts

| Forecast | Measure | Horizon |
| --- | --- | --- |
| Net sales | Monthly net invoiced revenue in GBP, excluding VAT and accounting for credits and returns | Three months |
| Unit demand | Weekly units due to ship, grouped by required ship-by date | Twelve weeks |
| Lead time, later phase | Time from order received date to actual shipment | To be defined during lead-time analysis |

Results will be available by customer group and for the company as a whole.
The existing planning groups will be the default, with Epicor customer group
codes available as an alternative.

The revenue forecast will include all in-scope sales. The unit forecast will
follow the planning rules for model inclusion, excluding blank models and
Scatter models.

Units due to ship provide a practical indicator of upcoming production demand.
They measure volume rather than labour hours; product mix will help explain
why similar unit totals can place different demands on production.

## Using the application

### Explore the data

Select a historical period and examine sales and unit demand by customer group.
Charts and summaries will show trends, seasonal patterns, product mix, customer
concentration, and periods of unusually high or low activity. A data-quality
view will show missing values, duplicates, and gaps that affect the analysis.

### Configure and run experiments

Choose the forecast target, customer grouping, history window, and forecast
horizon. Select a model and its features, set the evaluation period, and
optionally run a parameter search within a chosen tuning budget.

Each experiment will save its settings, dataset version, and results so it can
be repeated or compared with another approach. Training will run in the
background, with progress and errors shown in the browser.

### Compare models

Compare forecasts with actual results across historical periods. The comparison
will show overall accuracy, performance by customer group and forecast horizon,
and whether a model tends to forecast too high or too low.

Simple forecasts, such as repeating the previous period or the same period last
year, will provide the starting benchmark. More complex models will be judged
on whether they offer a useful improvement.

### Produce and review forecasts

Use the selected model to generate forecasts, inspect expected values and
prediction intervals, and export results. Group forecasts will reconcile to the
company total.

Known order-book demand will be shown alongside the forecast. The calculation
will distinguish a forecast of total demand from additional demand so that
existing orders are counted once.

As new actuals arrive, the application will track accuracy, bias, and changes in
the data, supporting decisions about when to retrain or replace a model.

## Technical approach

### Integration with the planning application

The forecasting module will use the existing Flask application, authentication,
permissions, Epicor connection, and customer-group configuration. This keeps
forecasting alongside the sales and planning information it is intended to
support.

Data preparation, training, evaluation, and prediction will sit in separate
services behind the browser interface. This allows the modelling logic to be
tested independently and keeps experiments separate from live planning changes.
Credentials will remain server-side, and browser settings will use validated
options rather than executable code.

### Historical datasets

Historical invoice and credit data will provide the revenue series. Order-release
and COOIS data will provide the unit-demand series and supporting production
context. Five-plus years of history is available for investigation.

Training datasets will be versioned separately from the live order-book and
works-order tables, which are refreshed for operational use. Saving the data
used by each experiment makes model comparisons repeatable and allows results
to be traced back to their source.

The [historical data contract](data-contract.md) defines the BAQ requirements,
field mappings, inclusion rules, and reconciliation checks.

### Model development

Revenue and unit demand will have separate models because they measure different
outcomes and operate at different time frequencies. They will share the same
experiment and reporting tools.

Exploratory analysis will guide model selection. The comparison will start with
simple baselines, then examine a small set of statistical and machine-learning
approaches suited to the observed trends, seasonality, and demand patterns.
Feature engineering and parameter tuning will be evaluated as part of that
comparison.

### Evaluation

Models will be tested by forecasting forward from several points in the
historical data. Each test will use information available at that point,
including fitting data transformations on the training period only. A final
holdout period will be kept separate from model selection and tuning.

Evaluation will cover the full three-month revenue and twelve-week unit
horizons. Measures will include:

- **MAE:** average error in GBP or units.
- **Bias:** whether forecasts tend to be too high or too low.
- **WAPE:** error relative to actual volume, where actual totals are nonzero.
- **Interval coverage and width:** how often actuals fall within prediction
  intervals, and how informative those intervals are.

Results will be broken down by customer group and horizon, alongside company
totals. Business acceptance criteria will be agreed before the final evaluation
so the chosen model is assessed against a practical planning need.

## Development stages

| Stage | Main outcome |
| --- | --- |
| 1. Business and data definition | Forecast targets, business rules, and historical BAQ requirements |
| 2. Data ingestion | Browser source configuration, validated imports, and versioned datasets |
| 3. Exploratory analysis | Data-quality reports and analysis of trends, seasonality, and demand patterns |
| 4. Baseline forecasting | Simple forecasts with repeatable time-based evaluation |
| 5. Modelling and tuning | Feature engineering, model comparison, and parameter optimisation |
| 6. Forecast delivery | Saved model versions, browser forecasts, prediction intervals, and exports |
| 7. Monitoring | Forecast-versus-actual reporting and retraining controls |
| 8. Project evaluation | Business findings, technical evaluation, and recommendations |

Development will proceed in small stages, with a review of the approach and a
walkthrough of the implementation at each stage.

## Documentation

The documentation will explain the data, modelling choices, experiment results,
and reasons for the final approach. Code documentation will describe module
responsibilities, public functions, and the reasoning behind data preparation
and modelling logic.

Together, these will provide a technical reference for maintaining the
application and material for explaining the project during the AI Level 7
apprenticeship assessment.

The overview and data contract will be updated throughout source mapping,
analysis, and development. They will describe the current design and explain
changes arising from the findings. Dataset and experiment versions will retain
the definitions used for earlier results.

## Current progress

The initial forecast scope and business definitions are documented. The next
step is to map the historical Epicor BAQs and build the ingestion layer.
