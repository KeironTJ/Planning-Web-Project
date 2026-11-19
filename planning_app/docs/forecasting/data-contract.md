# Forecasting data contract

## Purpose

Defines forecast measures, source mappings, record grain, and data quality
requirements. Proposed mappings and unresolved rules are not approved for use.
The [project approach](approach.md) records investigation and delivery steps.

## 1. Forecast measures

| Project / measure | Definition | Horizon |
| --- | --- | --- |
| Project 1: incoming orders | Distinct new orders assigned to the SOI reporting period by Epicor order date | To be agreed to support output planning |
| Project 1: intake value and volume | GBP release value and verified quantity from imported VA05, assigned to the SOI reporting period by Epicor order date | Same intake horizon |
| Project 2: shipped output value | Value of goods actually shipped, assigned to actual shipment date | Next 12-16 weeks |
| Project 2: shipped output volume | Verified quantity actually shipped, assigned to actual shipment date | Next 12-16 weeks |
| Supporting: unit demand | Units due to ship, assigned to required ship-by date | Weekly planning view |

Project 1 produces AI intake forecasts; Project 2 combines them with the existing
backlog to predict output. Both projects will support planning-app customer
groups, optional Epicor groups, and company totals. Five-plus years of history
has been reported as available; usable coverage remains unverified.

### Intake to shipped output

The Project 1 source is Planning's local VA05 data, refreshed by the Epicor
importer. Published SOI is a reconciliation reference, not the forecast
target. Open-month figures are provisional. A ship-date-only change affects
expected output, not the intake month or amount.

Published SOI GBP includes the weekly VA05-based pivots, a Shop subtotal,
and any manual reporting adjustments. A refreshed VA05 extract alone is not
equivalent to published SOI, even when the reporting dates and financial
calendar match.

The first weekly GBP baseline excludes releases whose VA05 line is marked
void at extraction. The source rows are retained and their excluded value
is reported separately. Missing or conflicting void flags are exceptions,
not assumed to be non-void. This rule describes the current extract, not
the order's status at the time it was booked.

For Project 2, keep later amendments and cancellations separate from the
intake basis. Do not apply a change again if it is already reflected in that basis.
Link each shipment to its order/release and count its actual quantity and
attributable value, including partial shipments. Volume must use verified UOM;
do not combine furniture-unit counts with metres of fabric.
Finished goods awaiting shipment do not count as realised output.

Reconcile opening outstanding value, new intake, amendments, cancellations,
shipped value, and closing outstanding value under a consistent valuation
basis. Reconcile quantities separately by UOM. Net invoiced revenue is not a
forecast measure.

### Supporting unit demand

Sum eligible quantities by current required ship-by week. This measures shipping
volume, not labour or unfinished production; completed but unshipped goods still
contribute.

A release can contain multiple units and appear on multiple job or assembly
rows. The proposed grain for unit demand is one record per release.

Model categories describe work content; each forecast configuration will decide
inclusion and grouping. Names are trimmed and case-sensitive. Blank, null, and
whitespace-only names share one category assignment, which cannot identify
their individual product types.

Job and assembly rows are production detail, not additional customer demand.
Where VA05 supplies release quantities or values, count each consistent
main/no-assembly observation once per release. Missing or conflicting main
observations are exceptions; subassembly values cannot substitute for them.

### Calendar and grouping

Project 1 uses the SOI financial calendar. Epicor order date assigns each order
to a Monday-start financial week, and the SOI calendar assigns that week to a
financial month. Financial months consist of whole weeks and may cross
calendar-month boundaries. The verified 2026 calendar follows a 4-4-5
pattern in each quarter. Use the reporting calendar for the relevant year
rather than deriving month from the order date or assuming the same pattern
in other years.

The proposed Project 2 output calendar uses Monday-start ISO weeks, identified
by week-start date, across the 12-16-week horizon. Interpret business dates in
Europe/London and extraction timestamps in UTC.

Use one customer group per record and identical filters for group and company
totals. Unmapped customers remain under `Unassigned`. Save the grouping used:
applying current groups to old records is a current-group view, not evidence
of historical membership.

### Project 1 EDA fields and feature availability

| Field family | Captured source and use | Model eligibility |
| --- | --- | --- |
| Order and receipt dates; financial calendar | Epicor order date assigns the reporting week. Receipt date supports timing comparisons. The verified financial calendar defines period boundaries. | Published calendar fields may be known in advance. Receipt and order activity may be used only from periods available at the forecast cutoff. |
| GBP, quantity, UOM and void status | Stored VA05 release value, release quantity, inventory UOM and line-void flag. GBP uses the confirmed net rule. | Prior-period GBP is a candidate predictor. Quantity-derived features require a verified quantity basis and comparable UOM. Current void flags are not proof of status at past cutoffs. |
| Product model and size | `model`, `size_desc` and `prod_size` as captured from VA05. Size is profiled within model; the two size fields are evaluated separately. | Candidate historical mix features only. Check coverage, cardinality, definition and stability before use. |
| Epicor customer group | `customer_group` as captured from VA05, not a historically effective group assignment. | Candidate historical mix feature only. Named accounts and customer identifiers are not captured for this review. |

The capture field list is authoritative. Older extracts without a dimension
cannot be profiled for it and are not backfilled from live data. Mix analysis
uses the same eligible release set as net GBP. Missing or conflicting
dimension labels remain unassigned and their GBP stays in the overall total.
Realised target-week orders, customer mix, model mix and adjustments are not
forecast inputs.

For the first one-week-ahead development comparison, the target is total
weekly net GBP and the only predictor family is net GBP from completed prior
financial weeks. Product, size and customer group remain descriptive in this
comparison. Prior-week values reconstructed from one saved capture reflect
what VA05 showed at capture, not necessarily what was known at each historical
forecast cutoff. Record the capture used and retain inputs at issue time for
prospective evaluation.

## 2. Data sources

| Source | Role |
| --- | --- |
| Published SOI intake report | Reconciliation reference; known differences include the Shop subtotal and manual reporting adjustments |
| Order change history, source to establish if needed | Dated amendments/cancellations for Project 2 or unexplained intake differences |
| Shipment history, source to establish | Actual shipment dates, output quantities, and value |
| `bskyVA05v1`, imported into Planning's `sales_orders` table | Project 1 source for order dates, release values, quantities, customers, and model links; historical coverage remains unverified |
| `bskyCOOISv3` | Supporting job/assembly and production context |

COOIS quantities are not additional demand to add to sales releases.
VA05 does not supply a verified month-end snapshot, change history, or dated
shipment detail.

### A. Intake, changes, and shipments

**Default Project 1 reporting date:** `OrderHed_OrderDate` maps through
`SalesOrder.order_date` to `intake_reporting_date`. The August-October 2026 SOI
preparation workbooks use `Order Date` to look up the financial week and that
week to look up the financial month. A manual SOI adjustment may assign a
different period in published SOI, but does not change the Project 1 period.

**Customer receipt date:** `OrderHed_OrderReceivedDate_c` maps through
`SalesOrder.order_received_date` to `intake_received_date`. It records when
the customer order arrived and does not assign the Project 1 reporting period.

**Proposed grains:** reported intake at order-line or release grain, as supported
by the source; subsequent change event; shipment detail. Preserve
order/line/release links where available and a unique event or shipment-line key.

| Dataset fields | Use |
| --- | --- |
| `order_num`, `order_line`, `release_num` | Link intake, changes, and output |
| `intake_reporting_date`, `intake_value_gbp` | Financial period assignment and VA05 release value in GBP |
| `intake_received_date` | Customer receipt date, retained separately |
| `intake_qty`, `amendment_qty`, `cancelled_qty`, `quantity_uom` | Reported intake and quantity changes in verified units |
| `event_id`, `event_type`, `effective_at`, `available_at` | Identify changes and when they were known |
| `amendment_value_gbp`, `cancelled_value_gbp` | Signed amendments and cancelled value under agreed rules |
| `shipment_id`, `shipment_line_id`, `actual_ship_date` | Identify output events and periods |
| `shipped_qty`, `quantity_uom`, `shipped_value_gbp` | Actual shipped volume and its attributable value |
| `customer_id`, `epicor_group_code`, `model`, `part_num` | Attribution and product mix |
| `currency`, `valuation_basis`, `conversion_rate` | Reconcile values consistently |

Release schedules must not duplicate order-line intake. A current value already
net of changes must not have those changes deducted again. Historical periods
derived from current VA05 rows reflect the state at extraction, not what was
known or recorded at month-end. Record the extraction date with the series.
Order counts use distinct order numbers, not line or release counts.

Current candidate mappings:

| Source field | Intake use / remaining check |
| --- | --- |
| `OrderHed_OrderDate` | Selected date for Project 1 SOI reporting periods |
| `OrderHed_OrderReceivedDate_c` | Confirmed customer order receipt date; date exceptions still need review |
| `Calculated_ReleasePrice` | Supplied query allocates `(DocExtPriceDtl - DocDiscount) / OrderQty * OurReqQty` to main/no-assembly rows, returning zero for zero `OrderQty`: a release value, not a unit price; discount and quantity bases need verification |
| `Calculated_ReleasePriceGBP` | Supplied query divides release value by header exchange rate; verify conversion and reconcile allocated release values to the month-end report |

### B. Order-release observations

**Selected BAQ:** `bskyVA05v1`.
**Proposed grain/key:** one release, identified by order, line, and release number.
**Business-confirmed planned date:** `OrderRel_ReqDate`, the current required
ship-by date. It maps through `SalesOrder.req_date` to
`required_ship_by_date`. It supports expected timing and unit demand; actual
shipment date determines realised output value and volume.

| Dataset fields | Use |
| --- | --- |
| `order_num`, `order_line`, `release_num` | Release identity |
| `customer_id`, `epicor_group_code` | Customer attribution |
| `required_ship_by_date`, `release_qty` | Planned timing and supporting weekly demand |
| `selling_uom`, `production_uom`, `uom_conversion` | Interpret and convert quantities |
| `model`, `part_num`, `product_family` | Category allocation and product mix |
| `open_status`, `void_status`, `cancelled_qty` | Cancellation treatment, where represented |
| `order_received_date`, `required_delivery_date`, `original_required_ship_by_date` | Timing and schedule analysis |
| `fulfilled_qty`, `hold_status` | Fulfilment and release context |
| `created_at`, `source_modified_at`, `available_at` | Historical availability, where supplied |

Each quantity requires a verified UOM. `OrderDtl_IUM` is inventory UOM and does
not establish selling or release-quantity units.

#### BAQ expression and join review

The supplied simplified query represents `Calculated_ReleaseQty` as:

```sql
case
    when JobAsmbl.AssemblySeq is null or JobAsmbl.AssemblySeq = 0
    then OrderRel.OurReqQty
    else null
end
```

This expression neither subtracts shipments nor allocates quantity between
jobs. The live response differs on subassemblies, as recorded below.

| Query finding | Consequence |
| --- | --- |
| `Calculated_SellingQty` returns `SellingReqQty` on main/no-assembly rows | Selling and release quantities are not established as equivalent |
| `Calculated_ShippedQty` adds `OurJobShippedQty` and `OurStockShippedQty` when job-shipped quantity is non-null, on main/no-assembly rows | Shipment is a separate measure |
| Releases join `JobProd`, jobs, and assemblies | Full release quantity can repeat across jobs |
| `PartWip` and customer/channel joins may multiply rows | BAQ rows are not guaranteed unique per release |
| Customer joins through `OrderHed.BTCustNum` | VA05 customer attribution is bill-to |
| Date filters use `OrderHed.OrderDate` | An order-date window does not prove complete ship-by-week coverage |
| Open/closed filters use `OrderHed.OpenOrder`; release-open and line-void flags are selected, not filtered | Header status alone cannot determine release eligibility |

### C. COOIS job/release data

`bskyCOOISv3` refreshes the live works-order table at job/assembly grain.

| Existing BAQ fields | Current use |
| --- | --- |
| `Calculated_Model` | Model classification |
| `JobHead_JobNum`, `JobAsmbl_AssemblySeq` | Job/assembly identity |
| `JobProd_OrderNum`, `JobProd_OrderLine`, `JobProd_OrderRelNum` | Release links |
| `Customer_CustID` | Customer |
| `JobAsmbl_RequiredQty`, `Calculated_SellingQty` | Required and selling quantities |
| `JobHead_QtyCompleted`, `Calculated_ShippedQty01` | Completion and shipment |
| `JobHead_ProdPlnWk_c`, `JobHead_ReqDueDate`, `JobHead_StartDate` | Production scheduling |
| `OrderRel_ReqDate`, `OrderHed_OrderReceivedDate_c` | Release requested and order-received dates |

### Source review: findings from the application code

| Area | Import behaviour | Forecasting consequence |
| --- | --- | --- |
| COOIS retention | Deletes works orders before reloading | Current snapshot, not historical snapshots |
| COOIS key | Keeps the first job/assembly row | Discarded raw duplicates cannot be assessed locally |
| COOIS filters | Production plan bounds are `0` to `9999999` | Historical inclusion still needs verification |
| Sales-order retention | Replaces open rows; closed imports replace orders returned in their batch | Retained history is mutable, not an as-of order book |
| Closed import window | Starts at current-year January on first default run; later uses rolling overlap and capped open-order lookback; manual dates supported | Local coverage differs from available Epicor history |
| Sales-order key | Order, line, release, assembly, and job | Multiple stored rows per release |
| Sales-order mapping | Imports requested, need-by, original ship-by, release/selling/shipped quantities, and inventory UOM | Validate meanings and units before aggregation |
| Parsing | Malformed dates/decimals become missing | Forecasting validation must report conversion failures |
| Pagination | Client uses `$top`/`$skip`; importer comments say these BAQs ignore paging | Actual extraction completeness needs checking |

Implementation references:

- [COOIS importer](../../app/core/epicor_importers.py#L432)
- [Sales-order mapping](../../app/core/epicor_importers.py#L608)
- [Open-order importer](../../app/core/epicor_importers.py#L706)
- [Closed-order importer](../../app/core/epicor_importers.py#L774)
- [BAQ client](../../app/core/epicor_client.py#L172)

#### Local data checks

Read-only checks on 12 November 2026 used the default development SQLite file,
not a confirmed active application database. Import timestamps span July to
October 2026.

| Check | Result |
| --- | --- |
| Stored rows / distinct order-line-release keys | 97,272 / 69,422 |
| Releases with multiple rows | 17,002 |
| Subassembly rows with populated release quantity | All 27,848 |
| Releases where subassembly quantity differs from a consistent main quantity | 17,000 |
| Releases without a main/no-assembly row | 0 |
| Missing main-row release quantities | 0 |
| Releases with nonpositive main quantity | 180 |
| Releases with conflicting main quantities | 1, with values zero and one |
| Order-date range | 2024-01-02 to 2026-10-19 |
| Required ship-by range | 1899-12-31 to 2173-04-10 |
| Rows with ship-by earlier than order date | 82 |

One multi-job release repeats its full quantity on two main rows. Partial
shipment examples retain the full release requirement. Reviewed examples
contain both `EA` and `M`.

These findings require release-level deduplication, UOM checks, and review of
quantity/date exceptions rather than automatic correction. The local date range
does not establish the reported five-plus years of history.

Rows have no batch identifier. Import timestamps cannot establish which query
definition produced them, and previously discarded duplicates cannot be
reconstructed from this file.

**Project 1 intake check:** all 33,666 local orders have one received date,
but 10,968 differ from order date. Received dates range from 2002-03-31 to
2045-10-21, so presence alone does not establish a usable intake date.
Main-row release/GBP values are populated, but 8,874 rows have nonpositive
values and one release has conflicting main-row GBP values. Main rows include
67,585 GBP and 1,839 EUR currency records. No comparison to month-end report
totals was made.

#### Fresh BAQ response check

On 12 November 2026, the existing Epicor client fetched separate `Open` and
`Closed` responses for order dates 13 October to 12 November 2026. No importer
ran or operational tables changed. Raw responses remained in memory, so these
counts are diagnostic evidence, not a saved reproducible dataset.

| Check | Open orders | Closed orders |
| --- | --- | --- |
| Returned rows | 3,789 | 257 |
| Distinct order/line/release keys | 2,787 | 209 |
| Main/no-assembly rows | 2,787 | 209 |
| Subassembly rows | 1,002 | 48 |
| Subassembly rows with populated release quantity | 1,002 | 48 |
| Repeated job/assembly import keys | 0 | 0 |
| Releases with multiple nonblank jobs | 0 | 0 |
| Conflicting non-null main-row release quantities | 0 | 0 |
| Missing required ship-by dates | 0 | 0 |
| Required ship-by range | 2026-01-18 to 2027-02-05 | 2026-10-23 to 2027-01-22 |

Both responses include inventory UOM `EA` and `M`. The reviewed main/subassembly
pairs return main quantity one and subassembly quantity zero, rather than null.
The difference from the supplied expression therefore occurs in the current
response too; its cause remains unexplained.

No multi-job or numerically partially shipped release appeared in this window.
The sample does not establish full history, cancellation rules, quantity units,
or reconciled totals.

#### Development capture comparison (19 November 2026)

A read-only comparison of four saved development captures from 18 November
(latest `DEV-4`, `va05-local-v2`) found the same 97,272 observations across all
four for the compared keys, quantities, values, inventory UOM, currency, rate,
and import time. The default development sales-order table and the 2 October
local backup also matched on the compared fields, including selling quantity.
The newest sales-order import timestamp in that file was 2 October. These
copies did not yield a before/after amendment or a receipt baseline; this does
not show that the underlying Epicor orders were unchanged.

Original PO values and amendment history are not required for the Project 1
month-end measure.

## 3. Dataset preparation and quality

Planned imports will record BAQ, aliases, types, keys, parameters,
and extraction timestamps. Use bounded windows, verify date boundaries and
pagination, and check ordering/consistency across pages.

| Check | Required treatment |
| --- | --- |
| Missing keys, dates, values, or parse failures | Report explicitly; do not convert missing quantities or money to zero |
| Duplicate keys | Separate consistent repeats from conflicts |
| Quantity grain, UOM, and model allocation | Preserve eligible unit totals across joins |
| Intake/change/shipment links and values | Reconcile intake to month-end totals, count changes once, and allocate partial shipments |
| Currency and valuation | Keep intake, outstanding orders, and output comparable |
| Customer mapping | Retain unassigned customers in totals |
| Coverage and partial periods | Distinguish gaps from genuine zero activity |
| Reconciliation | Match agreed Finance/planning reports under equivalent filters and tolerances |

Blocking errors must be resolved or given an agreed, recorded treatment before
training. Report counts and totals before and after preparation.

### Versions and access

Save each dataset's source settings, category/customer mappings, measure rules,
coverage, quality results, content hash, and historical-availability assumptions.
Experiments will reference the dataset version alongside code, settings, and
evaluation dates.

Use read-only Epicor access and application permissions. Exclude personal
details and free-text comments from the forecasting extract. Credentials and
raw company data stay outside source control; storage and retention must follow
company policy.

## 4. Historical availability and unresolved mappings

Business dates and availability timestamps are separate fields. An observation
can be used as a forecast input only when it was available by the forecast
cutoff. Current order-book rows do not establish prior open-order balances.

| Area | Unresolved |
| --- | --- |
| Project 1 reference | Month-end report/export, historical coverage, orders open at close, cancellations, restatements, and reconciliation tolerances |
| Quantity | Selling/release UOM and conversion, fulfilment field definitions, and mixed-model allocation |
| Valuation | Selling-price versus cost basis, FX, VAT, discounts, charges, and returns |
| Attribution and dates | Bill-to attribution, delivery and original-date meanings, calendar versus fiscal periods |
| Project 2 sources | Dated amendment, cancellation and shipment data |
| Governance | Forecast dataset storage and retention |

## Current implementation

Model Classification uses the current COOIS snapshot and persistent categories,
not historical training data. It reports record counts and quantity exceptions;
categories do not yet filter or aggregate forecast targets. Saves and removals
are audited; existing planning and revenue calculations are unchanged.

Data Readiness summarizes local sales-order rows, multi-row releases for
context, date and quantity exceptions, observed units/currencies, and import
timestamps. Saved VA05 reviews additionally retain whitelisted local
observations, source limitations, a content hash, and versioned quality results.
Capture does not call Epicor and cannot recover discarded raw duplicates,
parse failures or extraction parameters.

Saved checks use main/no-assembly rows for missing/nonpositive quantities and
GBP values. From `va05-local-v2`, sharing a release key is not itself a
finding; conflicting main-row quantities/values still are. Reviewers can
open all saved rows for a release to compare jobs and assemblies without
aggregating them. Earlier extracts retain their original checks and evidence.
Order-level received-date conflicts span releases.
Review outcomes append evidence without altering findings or source values.
New saved reviews include the observed VA05 model and display its current
classification for context; older reviews without a captured model are not
backfilled from mutable operational data. These current labels are not
historical assignments or forecast inclusion rules.
These records support source investigation, not training approval or a
reconciled month-end intake series. Dated changes and actual shipment detail
remain unverified.

See the [usage and setup guide](README.md#data-readiness) for permissions,
capture/review behaviour, storage limitations, and migration instructions.
