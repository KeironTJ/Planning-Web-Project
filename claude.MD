Planning Web App — Project Context for Claude

What This App Is
A manufacturing execution planning system built in Flask + SQLAlchemy. It fills the gaps left by the company's ERP system (Xsuite64 — old, deprecated, limited support).

The ERP manages everything via Sales Orders only. It has no: - Works Orders - Planned date fields - Works Order status (planned, firmed, released) - Routing data

This app provides the missing planning layer on top of ERP data.

Tech Stack
Framework: Flask (not Django)
ORM: SQLAlchemy with Flask-Migrate
Auth: Flask-Login + Flask-Bcrypt + JWT
DB: SQLite (dev) — structure not yet built, empty DB
Structure: Blueprint-based (auth, admin, capacity, materials, forecasting, workload, scenarios, api/v1)

Data Sources (CSV Exports from Xsuite64 ERP)
Eight CSV files are used. Some are daily ERP exports; some are internally maintained files uploaded to the app.

IMPORTANT — Date format: All ERP date fields are Excel serial numbers (integers, e.g. 46094). Convert using: date(1899, 12, 30) + timedelta(days=n)

CSV File	Source	Contents
OpenOrderBook_HIDE.csv	ERP daily export	Sales orders, one row per SO line per work centre
StockOnHand_HIDE.csv	ERP daily export	Stock on hand per product
OpenPO_HIDE.csv	ERP daily export	Open inbound purchase orders
MainMaterialReq_HIDE.csv	ERP daily export	MRP material requirements for main line orders
ASMaterialReq_HIDE.csv	ERP daily export	MRP material requirements for after sales orders
LabourPlan_HIDE.csv	Internally maintained	Available hours/FTE per department per day (capacity buckets)
SMVTable_HIDE.csv	Internally maintained	SMV matrix — minutes per product per department (wide format)
ProductionFlowLT_HIDE.csv	Internally maintained	Production routing flows and lead times per stage

IMPORTANT — OOB structure: A single sales order line appears multiple times in the OOB, once per work centre it passes through. Example for one sofa order:

SO001 | Line 1 | Sofa XL | Customer A | Wood       | Qty 10 | Due 01-May
SO001 | Line 1 | Sofa XL | Customer A | Assembly   | Qty 10 | Due 01-May
SO001 | Line 1 | Sofa XL | Customer A | Cut        | Qty 10 | Due 01-May
SO001 | Line 1 | Sofa XL | Customer A | Sew        | Qty 10 | Due 01-May
SO001 | Line 1 | Sofa XL | Customer A | Upholstery | Qty 10 | Due 01-May
SO001 | Line 1 | Sofa XL | Customer A | Despatch   | Qty 10 | Due 01-May
The OOB rows ARE the works order operations — the ERP has already broken them down by work centre. The app enriches them with planning data (planned dates, status) that the ERP cannot store.

Confirmed CSV Column Names

OpenOrderBook_HIDE.csv
Columns: SOPNO, CUSTOMER, NAME, CUSTORDREF, CUSPRODREF, ORDERTYPE, CARAVANCODE, CARAVANDESCRIPTION, ORDITEM, ORDDATE, DUEDATE, PRODCODE, DESCRIPTION, QTY, WORKCENTRE, SELLPRICE, TOTALVALUE
- Composite key: SOPNO + ORDITEM + WORKCENTRE
- CARAVANCODE / CARAVANDESCRIPTION = product programme / collection range
- ORDERTYPE = MAINLINE (or other types)
- SELLPRICE = unit price, TOTALVALUE = line total value
- ORDDATE and DUEDATE are Excel serial numbers

StockOnHand_HIDE.csv
Columns: PRODCODE, DESCRIPTION, STKQTY

OpenPO_HIDE.csv
Columns: POPNO, ORDITEM, PRODCODE, PLDESCRIPTION, OUSTANDINGQTY, DUEDATE, SUPPLIER, NAME, Type
- Note: OUSTANDINGQTY is a typo in the ERP export — do not correct, match exactly
- DUEDATE is an Excel serial number

MainMaterialReq_HIDE.csv
Columns: CUSTID, BATCHID, WORKSORDER, LOADDATE, DUEDATE, SECTIONDESC, MATERIALCODE, MATERIALDESC, QTYREQUIREDFORSETS, QTYFORORDER, QTYISSUED, PRODGRP, PGDESCRIPTION, COMPLETE
- QTYISSUED = qty of material already issued to production
- COMPLETE = Y/N flag
- SECTIONDESC = department name

ASMaterialReq_HIDE.csv
Columns: CUSTOMER, CUSPRODREF, ORDERNUMBER, LOADDATE, DUEDATE, SECTIONDESC, PRODCODE, DESCRIPTION, QTYREQUIRED
- Separate file from MainMaterialReq — different structure, after sales orders only

LabourPlan_HIDE.csv
Columns: Date, Week, Day, WorkDay?, Day Complete, Hours, [one column per department], Total FTE
- One row per calendar day
- Each department column = available hours/FTE for that day
- Week format: YYYY-WW (e.g. 2026-01)
- Date is an Excel serial number
- This is the primary source for capacity_buckets — import it, do not enter manually

SMVTable_HIDE.csv
Columns: COMPONENT ID, TIMING CODE, DESCRIPTION, [one column per department], OPS, Date Updated
- Wide/pivoted format: one row per product, one column per department = SMV in minutes
- TIMING CODE = product category (e.g. "2 Seater", "Arm Filled", "Armchair")
- OPS = number of operations in the routing

ProductionFlowLT_HIDE.csv
Columns: UNIQUE FLOW, Production Flow, Ops, Lead Time Q, [one column per department], Firmed?
- UNIQUE FLOW = binary-encoded string identifying which departments a product passes through
- Each department column = lead time in days at that stage
- Production Flow = human-readable sequence (e.g. "BLINDS (CTN SECTION) -> DESPATCH")
- Lead Time Q = total lead time in working days
- Firmed? = Y/N — whether the routing is confirmed
- Enables backward scheduling: calculate planned dates from due date minus lead times

Confirmed Departments (Work Centres)
18 departments — these are the same concept (each department = a work centre):
WOODMILL, FURNITURE TIMBER, CUTTING, MACHINING, FILLING, UPHOLSTERY, MATTRESS, TACKING, CURTAINS, CURTAIN POLES, BEDDING, BLINDS (CTN SECTION), DESPATCH, AFTER SALES, BELFIELD TEXTILES, TEK SEATING (CAB SEATS), DIVAN, ENCAPSULATED SPRINGS, GENERAL

Data Model
From ERP (imported via CSV)
sales_order_lines — parent record, one per SO+line combination
- so_number (SOPNO), line_number (ORDITEM), customer_code (CUSTOMER), customer_name (NAME)
- customer_order_ref (CUSTORDREF), customer_product_ref (CUSPRODREF)
- order_type (ORDERTYPE), caravan_code (CARAVANCODE), caravan_description (CARAVANDESCRIPTION)
- product_code (PRODCODE), product_description (DESCRIPTION)
- qty_ordered (QTY), order_date (ORDDATE), due_date (DUEDATE)
- unit_price (SELLPRICE), total_value (TOTALVALUE)
- imported_at (timestamp of last CSV import)

works_order_operations — child record, one per work centre per SO line
- sales_order_line_id (FK), department_id (FK)
- work_centre_name (WORKCENTRE), qty (QTY)
- ERP fields updated on each import: qty, due_date, total_value
- Planner fields preserved on re-import: planned_date, status, completed_date, notes

stock — from StockOnHand CSV (full replace daily)
- product_code (PRODCODE), description (DESCRIPTION), qty_on_hand (STKQTY)

purchase_orders — from OpenPO CSV (full replace daily)
- po_number (POPNO), line_number (ORDITEM), product_code (PRODCODE)
- description (PLDESCRIPTION), outstanding_qty (OUSTANDINGQTY — match typo)
- due_date (DUEDATE), supplier_code (SUPPLIER), supplier_name (NAME), po_type (Type)

material_requirements_main — from MainMaterialReq CSV (full replace daily)
- customer_id (CUSTID), batch_id (BATCHID), works_order (WORKSORDER)
- load_date (LOADDATE), due_date (DUEDATE), department (SECTIONDESC)
- material_code (MATERIALCODE), material_description (MATERIALDESC)
- qty_required_per_set (QTYREQUIREDFORSETS), qty_for_order (QTYFORORDER), qty_issued (QTYISSUED)
- product_group (PRODGRP), product_group_description (PGDESCRIPTION), complete (COMPLETE)

material_requirements_aftersales — from ASMaterialReq CSV (full replace daily)
- customer (CUSTOMER), customer_product_ref (CUSPRODREF), order_number (ORDERNUMBER)
- load_date (LOADDATE), due_date (DUEDATE), department (SECTIONDESC)
- product_code (PRODCODE), description (DESCRIPTION), qty_required (QTYREQUIRED)

Maintained in the App (imported or manually managed)
departments — 18 work centres — code, name, target_hours_per_day — seeded from known list

smv_matrix — Standard Minute Values per product per department
- component_id (COMPONENT ID), timing_code (TIMING CODE), description (DESCRIPTION)
- department_id (FK), smv_minutes (value from dept column)
- ops (OPS), date_updated (Date Updated)
- confidence: estimated | timed_study | most_study (low/medium/high)
- MOST studies ongoing — always show confidence level in UI
- Importable from SMVTable_HIDE.csv (wide format must be unpivoted on import)

capacity_buckets — from LabourPlan CSV (imported, not manually entered)
- department_id (FK), date, week, available_hours
- Can be manually overridden after import (e.g. for holidays)

production_flows — from ProductionFlowLT CSV
- unique_flow (UNIQUE FLOW), flow_description (Production Flow)
- ops (Ops), total_lead_time_days (Lead Time Q), firmed (Firmed?)
- Per-department lead time days (one column per dept)
- Used for backward scheduling: planned_date = due_date minus cumulative lead time

import_batches — audit log of all CSV uploads
- import_type, filename, uploaded_by, uploaded_at, row_count, status, notes

Import Strategy
CSV	Strategy	Key
Open Order Book	UPSERT — preserve planner fields	SOPNO + ORDITEM + WORKCENTRE
Stock	Full replace (truncate + reload)	—
Open PO	Full replace (truncate + reload)	—
Main Material Req	Full replace (truncate + reload)	—
AS Material Req	Full replace (truncate + reload)	—
Labour Plan	Full replace (truncate + reload)	—
SMV Table	UPSERT	COMPONENT ID + department
Production Flow LT	Full replace (truncate + reload)	—

OOB upsert logic:
- If record exists → update ERP fields (qty, due_date, value), PRESERVE planner fields (planned_date, status)
- If record is new → insert with status = not_started
- If old record not in today's CSV → mark as closed (do NOT delete — preserve history)

Works Order Status Flow
not_started → in_progress → completed
                           → closed (left OOB — shipped/cancelled in ERP)
Status is set manually by planners for now. Long-term: status derived from barcode/scan events (hardware not yet available).

Key Features to Build
Phase 1 — Foundation
[ ] Department / Work Centre setup screen (seed from known list of 18)
[ ] CSV import screens for all 8 files
[ ] OOB importer with upsert logic
[ ] Labour Plan importer (populates capacity_buckets)
[ ] SMV Table importer (unpivot wide format → row per product per dept)
Phase 2 — Planning Views
[ ] WIP Tracker — pivoted view, one row per sales order line, columns per work centre
    Status indicators: not_started | in_progress | completed
    Planners mark off each work centre as orders progress
[ ] Capacity Dashboard — load vs available per dept per day/week/month
    Load = SUM(operation qty × SMV minutes / 60) grouped by dept + week
[ ] Works Order list per department (for production teams — they currently have nothing)
[ ] SMV Matrix editor (CRUD screen)
[ ] Backward scheduling — use ProductionFlowLT lead times to suggest planned dates per operation
Phase 3 — Reporting
[ ] Material shortage report (MRP requirements vs stock + open POs)
[ ] Sales value reports (by customer, by week, by department)
[ ] WIP valuation
Phase 4 — Long Term
[ ] Works order printing / PDF generation (redesign from ERP format)
[ ] Barcode scanning for active WIP tracking (hardware pending)
    wip_scans table: operation_id, scan_type (start|complete|partial), qty_scanned, scanned_at, scanned_by
    Status on operations would be derived from scans rather than manually set

Capacity Calculation
dept_load_hours = SUM(operation.qty × smv.smv_minutes / 60)
  WHERE smv.product_code = operation.product_code
  AND smv.department_id = target_dept
  AND operation.planned_date BETWEEN period_start AND period_end

headroom = capacity_bucket.available_hours - dept_load_hours
Views needed: daily, weekly, monthly — all by department.

Material Shortage Calculation
shortage = material_requirement.qty_required
         - stock.qty_on_hand
         - SUM(open PO qty due before requirement date)
WHERE shortage > 0

Existing Models — What to Keep / Drop
Keep (with modifications)
WorkCentre → replace with Department model (18 confirmed departments)
CapacityBucket → keep, link to Department, populate from LabourPlan CSV
Material → keep for reference data
PurchaseOrder → replace with CSV-imported version
All auth models (User, Role, Permission)

Remove / Not Needed Yet
Routing — replaced by ProductionFlowLT + SMV matrix
Operation — replaced by works_order_operations from OOB
BOM — defer, not needed for initial phases
ForecastRun / ForecastItem — defer, not needed for initial phases

Important Notes for Development
CSV column names are now confirmed — use exact names from the Confirmed CSV Column Names section above.
Planner-added data must survive daily CSV re-imports. Never truncate the OOB/operations table.
SMV values are estimates until MOST studies complete — always show confidence level in UI.
DB is currently empty — schema can be freely redesigned.
Work centres and departments are the same concept — each department is also a work centre.
LabourPlan CSV drives capacity_buckets — do not ask planners to enter hours manually.
ProductionFlowLT enables backward scheduling from due date — use it in Phase 2.
The OUSTANDINGQTY typo in OpenPO is an ERP quirk — match it exactly in the importer.
