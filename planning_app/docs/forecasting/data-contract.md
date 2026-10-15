# Forecasting data contract

## Purpose

This document describes the Epicor data used to build the revenue and
unit-demand forecasts. It defines the source records, fields, business
calculations, and checks that make the resulting datasets useful for analysis
and modelling.

It supports the [project overview](README.md). The overview describes the
application; this contract describes the data behind it.

The initial definitions will be refined as the BAQs and historical data are
examined. Field names below describe the forecasting dataset. Actual Epicor
aliases will be recorded in the field mappings as each source is connected.

## 1. Forecast measures

| Measure | Definition | Horizon |
| --- | --- | --- |
| Net sales | Monthly net invoiced revenue in GBP, excluding VAT and accounting for credits and returns | Three months |
| Unit demand | Weekly units due to ship, assigned to required ship-by date | Twelve weeks |
| Lead time, later phase | Time from order received date to actual shipment | Defined during lead-time analysis |

Both initial forecasts will be available by customer group and for the company
as a whole. Planning-app groups will be the default, with Epicor group codes as
an alternative.

Five-plus years of history is available for investigation. The first data
profile will establish the usable date range for each source.

### Revenue calculation

Revenue is the sum of signed, posted net sales amounts for the reporting month.
Invoice amounts contribute positively and credits reduce the total. Posted GBP
base-currency values preserve the accounting value of historical sales.

All in-scope sales contribute to revenue, including products excluded from the
unit-demand measure. Product metadata is useful for analysis but is not a
condition for including a valid financial record.

The financial mapping will establish:

- Whether the reporting month follows invoice date or posting date.
- How the source represents credit signs, reversals, and voided documents.
- Which discounts, freight, miscellaneous charges, and rebates belong in net
  sales, including amounts held at document-header level.
- The reference Epicor report used to reconcile monthly totals.

These choices will be documented alongside the field mapping so the same
definition is used in imports, charts, forecasts, and actuals.

### Unit-demand calculation

Unit demand is the sum of eligible quantities due to ship in each week. The
required ship-by date determines the week; required delivery date is retained
as a separate field for analysis.

The model inclusion rule excludes missing or blank models and Scatter models.
The proposed matching rule trims whitespace and excludes any model containing
`scatter`, case-insensitively, matching the current planning filter's substring
behaviour. Exact-name matching remains an option to confirm when reviewing the
model values. Excluded quantities will remain visible in the data-quality
summary.

Quantities will be expressed in a consistent unit of measure. Rows represent
records, not units: a release for several units contributes its quantity rather
than a count of one.

Cancellation and amendment handling will be recorded with the quantity mapping,
including whether the source release quantity is already net of changes.

This is a shipping-volume measure used as a production-demand indicator.
Completed but unshipped products still contribute to units due to ship. A
separate measure of unfinished production would require completion and
stock-allocation data.

### Periods and grouping

The proposed calendar uses calendar months and Monday-start ISO weeks, with
business dates interpreted in Europe/London and extraction timestamps in UTC.
Calendar or fiscal reporting will be confirmed during source mapping.
Week-start dates identify weekly periods across year boundaries.

Each record belongs to one customer group in the selected grouping scheme.
Customers without a mapping appear under `Unassigned`, keeping them in the
company total. Group totals and the company total use the same filters.

The planning-app customer groups and membership configuration will be reused.
Each dataset will retain the mapping used to produce its group series.
Applying current groups to historical records provides a current-group view
of past demand; historical membership analysis requires effective-dated
mappings.

## 2. Data sources

| Source | Initial role |
| --- | --- |
| Invoice and credit history | Construct monthly revenue and reconcile financial totals |
| Order-release history | Construct due-to-ship quantities and link demand to customers and models |
| COOIS job/release data | Supply or validate unit-demand fields and explain production context |

The unit-source review will determine whether COOIS contains the complete
release-level history or whether a dedicated order-release BAQ is needed.
Each quantity will have one authoritative source; joining two sources will
not cause it to be counted twice.

### A. Invoice and credit history

**Record grain:** one posted invoice or credit detail line.

**Business key:** company, document, and detail line. Document type is included
where invoice and credit identifiers share a numbering range.

#### Core fields

| Dataset field | Use |
| --- | --- |
| `company_id` | Identify company scope and distinguish source keys |
| `document_id`, `document_line_id` | Identify each financial line |
| `document_type`, `posted_status` | Select posted sales and credit records |
| `invoice_date`, `posting_date` | Establish the monthly reporting basis |
| `customer_id` | Link revenue to a stable customer identity |
| `net_amount_gbp` | Signed net revenue excluding VAT, after the agreed accounting treatment |
| `epicor_group_code` | Support Epicor grouping when selected |

#### Supporting fields

| Dataset field | Use |
| --- | --- |
| `document_currency`, `net_amount_document_currency` | Currency reconciliation |
| `original_document_id` | Trace credits and reversals |
| `part_num`, `model`, `product_family` | Product-mix analysis |
| `source_modified_at`, `available_at` | Track corrections and when records became available, where supplied |

Header-level charges may need a separate extract or explicit allocation,
depending on the chosen revenue definition. Monetary totals are reconciled
before and after any allocation.

Financial lines will be aggregated at their own grain. Job, assembly, shipment,
and release links will use separate aggregates or allocation tables where
needed, preserving the original financial total.

### B. Order-release history

**Record grain:** one order release.

**Business key:** company, order number, order line, and release number.

The historical extract includes fulfilled and cancelled releases as well as
open releases, so it represents the full history rather than only today's
outstanding demand.

#### Core fields

| Dataset field | Use |
| --- | --- |
| `company_id`, `order_num`, `order_line`, `release_num` | Identify the release |
| `customer_id`, `epicor_group_code` | Customer attribution and grouping |
| `required_ship_by_date` | Assign demand to the due-to-ship week |
| `release_qty` | Quantity at the verified release grain |
| `selling_uom` | Interpret the quantity consistently |
| `model` | Apply the unit inclusion rule |
| `open_status`, `void_status` | Interpret release state |
| `cancelled_qty` | Apply cancellation treatment where separately represented |

#### Supporting fields

| Dataset field | Use |
| --- | --- |
| `order_received_date` | Analyse order timing and support the later lead-time target |
| `required_delivery_date`, `original_required_ship_by_date` | Examine delivery expectations and schedule changes |
| `part_num`, `product_family` | Product-mix analysis and unit conversion |
| `production_uom`, `uom_conversion` | Convert quantities where selling and production units differ |
| `fulfilled_qty`, `hold_status` | Explain outstanding demand and release status |
| `created_at`, `source_modified_at`, `available_at` | Support historical availability analysis where supplied |

Part-specific conversion factors will be used where units differ. The mapping
will identify whether fulfilment quantities describe shipment or production
completion, since these support different measures.

### C. COOIS job/release data

**Existing BAQ:** `bskyCOOISv3`.

The application currently imports one row per job/assembly and refreshes the
live works-order table on each sync. A separate historical extract will supply
the forecasting dataset.

Relevant existing mappings include:

| Existing BAQ field | Current use |
| --- | --- |
| `Calculated_Model` | Model |
| `JobHead_ProdPlnWk_c` | Production plan week |
| `JobHead_ReqDueDate` | Job required due date |
| `JobHead_StartDate` | Job start date |
| `OrderRel_ReqDate` | Release requested date |
| `OrderHed_OrderReceivedDate_c` | Order received date |

The reported ReqShipbyDate and ReqDel Date fields will be mapped separately
after reviewing the BAQ. Their relationship to the existing date mappings
will be recorded.

The historical source review will cover:

- Company, job, assembly, and linked order/line/release identifiers.
- Customer, model, product family, and quantity/UOM.
- Required ship-by and delivery dates.
- Planned start, due date, and production plan week.
- Released, completed, closed, and cancelled status.
- Production and completed quantities.
- Allocated quantities where a job serves multiple releases.

Finished-unit quantities will be calculated at the appropriate grain before
joining jobs, releases, or operations. This keeps multiple operations and
subassemblies from multiplying the same demand.

## 3. Dataset preparation and quality

### Mapping and import

Each source configuration will record its BAQ ID, field aliases, types, date
parameters, company scope, and business-key definition. Imports will use
bounded date windows and verified pagination, with documented date boundaries.
Stable ordering and source consistency will be checked for multi-page extracts.

Import results will show the extracted period, record counts, validation
results, and reconciliation totals. A dataset with errors affecting its keys,
dates, values, or quantities will remain unavailable for training until those
errors are resolved.

### Quality checks

| Check | Purpose |
| --- | --- |
| Required keys, dates, and values | Ensure records can be attributed and aggregated |
| Duplicate business keys | Detect repeated records or joins that multiply values; distinguish exact repeats from conflicting versions |
| Credit and currency treatment | Preserve the correct signed GBP revenue |
| Quantity grain and UOM | Preserve finished-unit totals across source joins |
| Model inclusion | Explain included and excluded unit demand |
| Customer mapping | Show unassigned customers without losing their values |
| Date coverage | Identify gaps and incomplete months or weeks |
| Financial and unit reconciliation | Match trusted Epicor reports under equivalent filters |

Missing quantities and financial values will be reported as data errors rather
than converted to zero. Empty periods will be treated as zero activity only
when source coverage supports that interpretation. Partial periods will be
identified separately from completed training periods.

Revenue reconciliation will compare document counts and monthly GBP totals.
Unit reconciliation will compare release and eligible COOIS quantities at their
declared grain. Finance and planning will provide the reference reports and
appropriate numerical tolerances.

### Dataset versions

Each saved dataset will record:

- Source BAQs, mappings, filters, and extraction timestamps.
- Customer-group mapping and measure definitions.
- Date coverage, record counts, and quality results.
- A content hash and source-availability assumptions.

Experiments will reference these versions along with their configuration, code
version, evaluation dates, and random seed where applicable. This makes results
traceable and allows earlier experiments to be repeated after new data arrives.

### Access and storage

Forecasting imports will use read-only Epicor access. Customer identifiers
provide the required linkage; personal details and free-text comments are
outside the initial dataset scope.

Dataset access and exports will use application permissions. Credentials and
raw company data will remain outside source control. Storage location and
retention will follow the agreed company policy.

## 4. Historical evaluation

Initial models will use the historical revenue and unit-demand series with
time-based training and evaluation periods. Data preparation within each test
will use the training period, preserving a fair comparison with later actuals.

The source review will establish how late postings and historical corrections
affect the series. An event's business date and the date it became visible can
differ; this distinction matters when reconstructing a historical forecast.
Where the dataset reflects final corrected values, that basis will be recorded
in the evaluation.

Historical order-book snapshots are needed when testing features such as
"units already booked at the forecast date." They are not a prerequisite for
exploring the historical target series or starting simple baseline models.

## 5. Later data extensions

### Order-book-aware forecasting

Dated snapshots or audit events would allow the model to use the order book as
it stood at each forecast date. Each snapshot needs the release business key
and capture timestamp; change events need an event identifier and effective
and availability timestamps.

Historical change tracking is still to be checked in Epicor. If it is
unavailable, prospective snapshots can build this history from deployment.

Two forecast designs can then be compared:

1. Forecast total units due to ship and show the known order book alongside it.
2. Forecast additional demand and combine it with an estimate of how known
   orders will be fulfilled, cancelled, or rescheduled.

The first design does not add the known order book to the total forecast.
The second requires historical order-book states to evaluate its components.

### Lead-time prediction

The lead-time extension will link order received dates to actual shipment
events. Its dataset will include release keys, shipment identifiers, shipment
dates and quantities, and corrections or returns.

Analysis will determine whether first shipment, final shipment, or
quantity-weighted shipment time best answers the business question, and whether
to measure calendar or working days.

Open orders will be represented as incomplete observations rather than
zero-duration orders. The modelling approach will account for that distinction
or define a completed-order cohort and explain its limitations.

### Historical customer membership

Effective-dated customer-group membership would support analysis using the
groups that existed at each historical date. The initial dataset can instead
use a saved current-group mapping to compare historical demand under today's
planning structure.

## 6. Next source-mapping work

The next step is to review the invoice/credit and COOIS BAQ definitions and
record:

1. Source aliases, types, keys, date filters, and available historical range.
2. Revenue date basis, credit signs, charge treatment, and GBP amounts.
3. Required ship-by date, quantity/UOM, and model inclusion.
4. Whether COOIS supplies the complete unit history or needs an order-release
   source alongside it.
5. Company scope, reporting calendar, reference reports, and storage arrangements.

Historical snapshots, lead-time shipment definitions, and model acceptance
criteria will be addressed in their respective development stages.

## Maintaining this document

This contract will be updated as source mapping, EDA, and model development
clarify the data. Actual mappings and agreed calculations will replace the
initial descriptions, and analysis findings will explain changes to the dataset.

Changes to targets, inclusion rules, or group definitions will be versioned
with the affected datasets. The project overview will be updated when those
changes affect the application's scope or behaviour. Both documents will
describe the current design while saved dataset and experiment versions
preserve the definitions used for earlier results.
