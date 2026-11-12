# Forecasting data contract

## Purpose

Define the sources, calculations, and quality checks for the forecasts.
Source-review sections record observed findings; dataset fields and preparation
rules describe the planned design.

See the [overview](README.md) for scope and the [project approach](approach.md)
for delivery and AI Level 7 report evidence.

## 1. Forecast measures

| Project / measure | Definition | Horizon |
| --- | --- | --- |
| Project 1: incoming orders | Number of new orders received | To be agreed to support output planning |
| Project 1: intake value and volume | Value and verified quantity of new orders at receipt, before later amendments/cancellations | Same intake horizon |
| Project 2: shipped output value | Value of goods actually shipped, assigned to actual shipment date | Next 12-16 weeks |
| Project 2: shipped output volume | Verified quantity actually shipped, assigned to actual shipment date | Next 12-16 weeks |
| Supporting: unit demand | Units due to ship, assigned to required ship-by date | Weekly planning view |

Project 1 produces AI intake forecasts; Project 2 combines them with the existing
backlog to predict output. Both projects will support planning-app customer
groups, optional Epicor groups, and company totals. Five-plus years of history
has been reported as available; usable coverage remains unverified.

### Intake to shipped output

Keep original intake separate from later signed amendments and cancellations.
Link each shipment to its order/release and count its actual quantity and
attributable value, including partial shipments. Volume must use verified UOM;
do not combine furniture-unit counts with metres of fabric.
Finished goods awaiting shipment do not count as realised output.

Reconcile opening outstanding value, new intake, amendments, cancellations,
shipped value, and closing outstanding value under a consistent valuation
basis. Reconcile quantities separately under consistent UOM. Rescheduling
changes expected output timing, not raw intake.

Confirm selling-price versus cost valuation, GBP conversion, VAT/discount/charge
treatment, price changes, returns, and the reference reports/tolerances.
Invoices may support reconciliation; net invoiced revenue is not the primary
target.

### Supporting unit demand

Sum eligible quantities by current required ship-by week. This measures shipping
volume, not labour or unfinished production; completed but unshipped goods still
contribute.

One-piece job planning informed the classification screen's count review.
However, a release can contain several items and repeat across jobs and
assemblies. The proposed dataset has one record per release, retaining
job/assembly detail separately. The quantity basis and any allocation to
eligible jobs need validation before weekly totals are built.

Model categories describe work content; each forecast configuration will decide
inclusion and grouping. Names are trimmed and case-sensitive. Blank, null, and
whitespace-only names share one category assignment, which cannot identify
their individual product types.

### Calendar and grouping

Proposed output reporting uses Monday-start ISO weeks, identified by week-start
date, across the 12-16-week horizon. Interpret business dates in Europe/London and extraction
timestamps in UTC; confirm calendar versus fiscal reporting.

Use one customer group per record and identical filters for group and company
totals. Unmapped customers remain under `Unassigned`. Save the grouping used:
applying current groups to old records is a current-group view, not evidence
of historical membership.

## 2. Data sources

| Source | Role |
| --- | --- |
| Original order intake and change history, source to establish | Receipt quantities/values and dated amendments/cancellations |
| Shipment history, source to establish | Actual shipment dates, output quantities, and value |
| `bskyVA05v1`, selected sales-order source | Current release state, customers, and model links |
| `bskyCOOISv3` | Supporting job/assembly and production context |

COOIS quantities are not additional demand to add to sales releases.
The reviewed sales-order data does not yet establish original intake quantities
and values, change history, or dated shipment quantities and values.

### A. Intake, changes, and shipments

**Proposed grains:** original order-line receipt; subsequent change event;
shipment detail. Preserve company/order/line/release links where available and
a unique event or shipment-line key.

| Dataset fields | Use |
| --- | --- |
| `company_id`, `order_num`, `order_line`, `release_num` | Link intake, changes, and output |
| `intake_received_date`, `original_intake_value_gbp` | Raw intake timing and value |
| `original_intake_qty`, `amendment_qty`, `cancelled_qty`, `quantity_uom` | Raw intake and quantity changes in verified units |
| `event_id`, `event_type`, `effective_at`, `available_at` | Identify changes and when they were known |
| `amendment_value_gbp`, `cancelled_value_gbp` | Signed amendments and cancelled value under agreed rules |
| `shipment_id`, `shipment_line_id`, `actual_ship_date` | Identify output events and periods |
| `shipped_qty`, `quantity_uom`, `shipped_value_gbp` | Actual shipped volume and its attributable value |
| `customer_id`, `epicor_group_code`, `model`, `part_num` | Attribution and product mix |
| `currency`, `valuation_basis`, `conversion_rate` | Reconcile values consistently |

Release schedules must not duplicate order-line intake. A current value already
net of changes must not have those changes deducted again. Original values and
event history need a verified source; current snapshots cannot supply them by
assumption. If unavailable, agree a narrower reconstructible intake measure.
Order counts use distinct company/order keys, not line or release counts.

### B. Order-release history

**Selected BAQ:** `bskyVA05v1`.
**Proposed grain/key:** one release, identified by company, order, line, and
release number.
**Business-confirmed planned date:** `OrderRel_ReqDate`, the current required
ship-by date. It maps through `SalesOrder.req_date` to
`required_ship_by_date`. It supports expected timing and unit demand; actual
shipment date determines realised output value and volume.

| Dataset fields | Use |
| --- | --- |
| `company_id`, `order_num`, `order_line`, `release_num` | Release identity |
| `customer_id`, `epicor_group_code` | Customer attribution |
| `required_ship_by_date`, `release_qty` | Planned timing and supporting weekly demand |
| `selling_uom`, `production_uom`, `uom_conversion` | Interpret and convert quantities |
| `model`, `part_num`, `product_family` | Category allocation and product mix |
| `open_status`, `void_status`, `cancelled_qty` | Cancellation treatment, where represented |
| `order_received_date`, `required_delivery_date`, `original_required_ship_by_date` | Timing and schedule analysis |
| `fulfilled_qty`, `hold_status` | Fulfilment and release context |
| `created_at`, `source_modified_at`, `available_at` | Historical availability, where supplied |

Map UOM to each quantity explicitly; the imported `OrderDtl_IUM` alone does not
establish selling or release-quantity units. Confirm part-specific conversions
and whether fulfilment fields describe shipment or production.

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
| `Calculated_SellingQty` returns `SellingReqQty` on main/no-assembly rows | Check UOM before equating it with release quantity |
| `Calculated_ShippedQty` adds `OurJobShippedQty` and `OurStockShippedQty` when job-shipped quantity is non-null, on main/no-assembly rows | Shipment is a separate measure |
| Releases join `JobProd`, jobs, and assemblies | Full release quantity can repeat across jobs |
| `PartWip` and customer/channel joins may multiply rows | Raw join uniqueness needs checking |
| Customer joins through `OrderHed.BTCustNum` | Confirm bill-to is the intended customer perspective |
| Date filters use `OrderHed.OrderDate` | An order-date window does not prove complete ship-by-week coverage |
| Open/closed filters use `OrderHed.OpenOrder`; release-open and line-void flags are selected, not filtered | Header status alone cannot determine release eligibility |
| Company is used in joins but omitted from the selection | Retain verified extraction company scope in the dataset key |

At release grain, retain one consistent quantity, flag conflicts rather than
picking the first row, and keep model/assembly detail separately. Mixed-model
releases need explicit category allocation. Include fulfilled history and
identify cancellations so the agreed rule can be applied.

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

Verify historical coverage, company scope, status filters, UOM, delivery-date
mapping, and allocations where jobs serve multiple releases. Aggregate before
joining operations or assemblies to avoid multiplying demand.

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
Those cases need targeted fresh checks. This sample does not establish full
history, cancellation rules, quantity units, or reconciled totals.

## 3. Dataset preparation and quality

Planned imports will record BAQ, aliases, types, company scope, keys, parameters,
and extraction timestamps. Use bounded windows, verify date boundaries and
pagination, and check ordering/consistency across pages.

| Check | Required treatment |
| --- | --- |
| Missing keys, dates, values, or parse failures | Report explicitly; do not convert missing quantities or money to zero |
| Duplicate keys | Separate consistent repeats from conflicts |
| Quantity grain, UOM, and model allocation | Preserve eligible unit totals across joins |
| Intake/change/shipment links and values | Preserve original intake, count changes once, and allocate partial shipments |
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

## 4. Historical evaluation

Use time-based evaluation, fitting preprocessing on training data only.
Keep amendments, cancellations, and shipments after a forecast cutoff out of
its input features. They are later outcomes, not information available at
prediction time. Record business dates and availability separately.

Features such as "units already booked at the forecast date" require historical
snapshots or change events. Today's order book cannot reconstruct those states.
Simple target-series baselines can proceed without order-book features.
Evaluate Project 1's intake counts, value, and volume before using its forecasts
in Project 2.
Use actual observed intake to study conversion, but forecast intake when testing
the end-to-end future workflow; report those two evaluations separately.

## 5. Later data extensions

- **Richer production features:** completion and capacity data may help explain
  shipment timing once the intake-to-output baseline is established.
- **Detailed lead-time prediction:** shipment timing is part of the core flow;
  a separate job/customer-level lead-time tool remains an extension. Treat open
  orders as incomplete observations, not zero lead time.
- **Historical customer groups:** effective-dated membership would support
  historical grouping; the initial saved current mapping provides today's
  grouping of past demand.

## 6. Outstanding source decisions

1. Explain zero versus null subassembly quantities in the deployed response.
2. Obtain fresh multi-job, partial-shipment, and conflicting-quantity examples.
3. Confirm quantity UOM/conversions, category allocation, and cancellation rules.
4. Confirm bill-to attribution, delivery/original-date meanings, and company
   scope.
5. Verify usable coverage and reconcile totals; check COOIS links without
   multiplying release demand.
6. Identify original intake and dated change/shipment sources; agree valuation
   and how cancellations, returns, and price changes affect the flow.
7. Agree calendar, reference reports, tolerances, storage, and retention.

Record each resolution with its evidence and date or dataset version. Version
changes to calculations, inclusion rules, and group mappings with affected
datasets.

## Current implementation

Model Classification uses the current COOIS snapshot and persistent categories,
not historical training data. It reports record counts and quantity exceptions;
categories do not yet filter or aggregate forecast targets. Saves and removals
are audited; existing planning and revenue calculations are unchanged.

See the [usage and setup guide](README.md#model-classification) for permissions,
assignment behaviour, and migration instructions.
