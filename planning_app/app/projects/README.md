# Projects, activities, tasks and logs

This module extends the existing Flask application and user accounts. Open
**Projects & Tasks** in the sidebar, or `/projects/`. The main home page highlights
the five earliest overdue and upcoming work items, with total counts.

## Expandable work hierarchy

The dashboard groups all visible work into projects, standalone activities and
standalone tasks. Expand a project to see its activities and direct project
tasks; expand an activity to see its tasks. Empty branches are explicitly shown.
Each branch shows task/activity counts, completion, overdue counts and financial
totals for the displayed work.

Projects and activities lists use the same expandable view. Filters and
pagination select the listed entities, then include their live descendants to
preserve context: a planned project may contain completed or blocked tasks.
Activity lists group selected activities beneath their projects. Task lists
group the matching page of tasks beneath their activity/project, without
bringing in unrelated sibling tasks. Ancestors included solely for location
are labelled **Parent context**; their own costs are excluded from displayed
totals. Counts describe the displayed tree, not hidden results on other pages.
The **List / Hierarchy** switcher selects one view at a time on project and
activity lists; task lists also offer **Board**. Hierarchy remains the default
for first visits. List shows a flat table of the selected entities, while Board
groups the current page's tasks by status, with explicit page-local counts.
Switching views keeps filters, page and page size. Filtering keeps the selected
view and page size, and returns to the first page. Pagination applies to every
view. Links use `view=list|hierarchy|board`, so bookmarked/shared views work
without JavaScript; board is only valid for tasks. Invalid views return an
explicit validation error.

Project/activity detail pages show the same nested structure with the current
item expanded. Location breadcrumbs on activity/task details link back through
the actual parent chain. Deleted or inaccessible work is never included.
Expansion uses native HTML disclosures and works with keyboard navigation and
without JavaScript.

### Remembering your workspace

Modular JavaScript in `static/js/modules/projects.js` uses the reusable
`static/js/components/workspace_state.js` component to remember expanded
branches, page scroll and task-board horizontal scroll.
Preferences live in session storage, separately for each user and browser tab, with a maximum of
40 remembered pages. Filtered pages have separate expansion/scroll state.
Task, activity, project and log lists restore the last filters and page when
revisited without an explicit query; explicit links take precedence. **Reset**
clears that list's filters and remembered page state while retaining its view.
View preferences are remembered separately per list, including when arriving
with new filters but no explicit view. An explicit `view` always takes precedence.
Each view has separate expansion/scroll state. URL anchors such as
`#logs` take precedence over remembered scroll positions.

Opening a work item provides **Back to previous view**. New/edit forms preserve
their origin for both Save and Cancel; validation errors retain it too. Status
actions return to the screen where they were submitted, including filtered
hierarchies. Comment actions return to the current item's logs. Sharing changes
also retain the current detail-page context. Return URLs are validated before
mutations and must name a local, read-only work screen; external destinations,
API routes and edit forms are rejected. Permissions, CSRF, workflow validation
and version checks are unchanged.

The forms and return links also work without JavaScript. If browser storage is
unavailable or malformed, a visible warning explains that remembering the
workspace is unavailable; ordinary navigation remains usable. No work-item
contents or permissions are stored in browser preferences.

## Completing and closing work

Status actions are visible on each detail page and inside the expandable work
cards, including task cards. They use the same editor permissions, CSRF,
optimistic version checks and automatic auditing as the edit form/API.

- **Start** planned work, then **Complete** when it is done.
- **Mark blocked**, then **Start / resume** before completing blocked work.
- **Cancel / close** stops work without counting it as successfully completed.
- **Reopen** completed work or **Restart** cancelled work to make it outstanding.
- Closing a project/activity requires **every live descendant** (including empty
  activities and direct project tasks) to be completed or cancelled. The server
  returns an explicit conflict with outstanding activity/task counts otherwise.
  It does not automatically complete children.
- Reopen closed ancestors before reopening children or adding outstanding child
  work underneath them. Closed records and their history remain visible.

Count-based completion cards show completed, outstanding and cancelled projects,
activities and tasks on the dashboard/report. Project detail/summary includes
separate child activity and descendant task counts; activity detail includes task
counts. Hierarchy cards show counts for the displayed work (filtered context
does not imply full-project totals). Spending is a separate metric: an expensive
task and a free task each count as one task.

## Workflow and logs UI

Status chips use consistent text, icons and theme-aware colours throughout the
hierarchy, detail views, tables and task board: planned (not started), active
(in progress), blocked (waiting), completed (finished), cancelled (stopped).
Project, activity and task branches have distinct icons and border treatments;
labels distinguish direct project tasks from activity tasks and standalone work.
The navigation highlights the current section. Standalone tasks remain accessible
through the Tasks context filter rather than a duplicate navigation tab.

**Logs & updates** is a dedicated, paginated timeline at `/projects/logs`.
Filter by entry type, work type/item ID or author. Each entry names and links its
source, separates comments from automatic updates, and gives audit events a
readable summary (including before/after statuses). Full audit snapshots remain
available inside the entry's disclosure. The dashboard highlights recent entries,
and every work card links to its detail-page comments/timeline.
The same log filters are supported by `GET /projects/api/logs`:
`log_kind=comment|audit`, `target_kind=projects|activities|tasks`, `target_id`
(requires work type), and `author_id`. Item-specific filters match the direct log
target, while a project/activity detail timeline includes descendant work.
All these surfaces enforce inherited access and exclude deleted live entries.

## Deployment

Back up the database, then apply the migration using the application's configured
environment and interpreter:

```powershell
Set-Location planning_app
& '..\.venv\Scripts\python.exe' -m flask --app wsgi db upgrade
```

Use your deployment interpreter instead of the example local virtual environment
where appropriate. The new revision is `e2a9417c630b`. No dependency changes or
database reseeding are required. Apply the migration before starting the updated
application. Downgrading removes the module's data; it is not an archive operation.
Production deployment still requires the existing application's HTTPS, secure
session cookies, secrets, database backups and operational monitoring.

## Models and hierarchy

- `Project`: a root work item.
- `Activity`: optional `project_id`; no project means standalone.
- `Task`: optional `project_id` **or** `activity_id`, or neither for standalone.
  A task inside an activity derives its project through that activity. Sending
  both IDs is rejected, preventing contradictory hierarchy data.
- `LogEntry`: exactly one project, activity or task target. Manual comments and
  automatic audit entries share a timeline.
- `Share`: exactly one root target and one user or department recipient, with
  `viewer` or `editor` access.
- Task assignments use a relational many-to-many association with existing users.

Work items include descriptions, owner IDs, departments, status, priority,
start/end/deadline dates, decimal budgets/actuals, creator/timestamps and an
optimistic concurrency version. Variance is calculated as **budget - actuals**.

This is a user/department collaboration module, not ERP site-scoped operational
data. Department sharing uses the existing `User.department` value and reflects
current department membership. Setting an item's department alone does **not**
make it visible to that department.

## Visibility and permissions

New root items are private to their owner, creator and administrators. Explicit
viewer/editor user or department shares widen visibility. There is no anonymous
or public sharing. Children inherit their root's access; a child owner or
assignee does not bypass root access. Assignment requires access first.

| Operation | Viewer | Editor | Root owner/creator/admin |
|---|---|---|---|
| Read items, logs and reports | Yes | Yes | Yes |
| Create child work, edit work, add comments | No | Yes | Yes |
| Edit/delete own comments | No | Yes | Yes |
| Change sharing, hierarchy or ownership | No | No | Yes |
| Delete work or inspect archived history | No | No | Yes |

User and department grants are additive: revoking a user grant does not remove
an independent department grant. Revocation immediately removes inherited
access; assignments do not themselves grant access. Ownership transfer does not
remove the original creator's management rights.

Moving work requires management rights on its old root and editor access to its
new parent. Move live children first before reparenting an activity. Moves carry
the item's history and assignments; its current root governs visibility.
Existing standalone shares remain stored but inactive while the item is nested.

## Validation and reporting semantics

- Name and department are required; owner and assignees must be active users.
- Start date cannot follow end date or deadline.
- Money must be finite, non-negative, within `Numeric(14, 2)`, and have at most
  two decimal places. Actuals may exceed budget; overspend is explicitly flagged
  rather than preventing users from recording real factory costs.
- New work begins `planned`. Valid transitions:
  - `planned` -> `active`, `cancelled`
  - `active` -> `blocked`, `completed`, `cancelled`
  - `blocked` -> `active`, `cancelled`
  - `completed` -> `active` (reopen)
  - `cancelled` -> `planned` (restart)
- Parent status is manually managed, not automatically derived. Parents cannot
  be closed with outstanding child work. Progress measures completed
  descendant tasks, excluding cancelled tasks from the denominator. No tasks
  means zero progress, not automatic completion.
- Budgets/actuals are **direct incremental costs on each item**, not duplicated
  child totals. The overall report sums each visible item once. Project/activity
  reports show their own budgets and a `rollup` including child costs, alongside
  descendant-task progress. Do not sum overlapping project/activity rollups.
- Overdue means deadline before today; upcoming is today through 14 days ahead,
  inclusive. Completed/cancelled items are excluded. Dates use the server's
  business calendar; audit timestamps are stored and serialized in UTC with
  an explicit offset, including when using SQLite.
- Workload counts each open task once per assignee. Unassigned tasks count
  against their owner. All reports enforce the same visibility rules as CRUD.
- Timelines in the dashboard/report show the latest 100 updates. Root managers
  can inspect complete retained history, including deleted child work/comments,
  in **Archive** or the item's **Retained history** page.

## API

The JSON API lives under `/projects/api` and uses the existing authenticated
Flask session. It deliberately does not accept an arbitrary user ID as identity.
All mutations require a valid Flask-WTF CSRF token in `X-CSRFToken` (or a standard
form token for HTML). Tokens are present in the module's HTML forms.

| Method | Path | Purpose |
|---|---|---|
| GET, POST | `/{projects,activities,tasks}` | List/create |
| GET, PATCH, DELETE | `/{kind}/{id}` | Read/update/soft-delete |
| GET | `/{kind}/{id}/summary` | Progress, child work, timeline |
| GET | `/{kind}/{id}/history` | Owner-only retained history |
| GET, POST | `/{kind}/{id}/shares` | Root-owner-only list/upsert grants |
| DELETE | `/{kind}/{id}/shares/{share_id}` | Revoke grant |
| GET, POST | `/logs` | Visible timeline/create comment |
| GET, PATCH, DELETE | `/logs/{id}` | Read/update/soft-delete comment |
| GET | `/reports` | Progress, finances, overdue, workload, updates |

Collection endpoints return `{items, page, per_page, total, pages}`. Default
page size is 50; maximum is 100. Pass `page` and `per_page` for pagination.
Work collections accept `status`, `priority`, `department`, `owner_id`,
`project_id`, `activity_id`, `deadline=overdue|upcoming` and
`scope=mine|standalone|project|activity`. `project_id` includes activity tasks
through their effective project. The task board reflects the current filtered
page. Summaries/reports aggregate all visible work, not just one list page.
Reports expose a `completion` object keyed by `projects`, `activities`, `tasks`,
each containing `completed`, `total` (excluding cancelled), `outstanding`,
`cancelled` and `percent`. Per-project/activity reports and summaries expose the
same object for child work. The existing task-based `progress` shape is unchanged.
The HTML status action endpoint is `POST /projects/{kind}/{id}/status` with
`status`, `version` and `csrf_token`; API integrations continue to PATCH the
item's status.

PATCH and DELETE requests require the current `version`. A stale version returns
409; invalid data returns 400; invisible/missing items return 404; insufficient
editing rights return 403. JSON errors have the shape `{"error": "message"}`.

### Example usage from an authenticated browser page

Use a CSRF token from the page's hidden form field:

```javascript
const csrf = document.querySelector('input[name="csrf_token"]').value;
async function send(path, method, body) {
  const response = await fetch(`/projects/api${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

const project = await send("/projects", "POST", {
  name: "Packaging line improvement", department: "Operations", budget: "5000.00",
});
const activity = await send("/activities", "POST", {
  name: "Trial new process", department: "Operations", project_id: project.id,
});
const task = await send("/tasks", "POST", {
  name: "Measure trial output", department: "Operations", activity_id: activity.id,
});
await send("/tasks", "POST", {
  name: "Independent safety review", department: "Operations",
});
await send(`/projects/${project.id}/shares`, "POST", {
  department: "Operations", role: "viewer",
});
const active = await send(`/tasks/${task.id}`, "PATCH", {
  version: task.version, status: "active",
});
await send("/logs", "POST", { task_id: active.id, body: "Trial started." });
```

## Audit and extension points

Every successful mutation atomically appends an existing `AuditLog` record and an
immutable automatic `LogEntry`, including before/after snapshots, actor and
timestamp. Comment changes retain previous text in those snapshots. Work and
manual comments are soft-deleted; parents with live children cannot be deleted.
Automatic audit entries cannot be changed through either UI or API.

This is application-level auditability, not cryptographic tamper evidence:
database administrators retain direct database access. Use restricted database
roles, backups and external immutable log retention where compliance requires it.

`models.py` owns the schema, `services.py` owns access/validation/reporting,
`routes.py` handles HTTP/transactions, and `templates/projects` supplies the UI.
Future integration should call the services and commit the transaction only
after all mutations succeed; do not bypass access checks or write models
directly from new endpoints.

Run the focused tests:

```powershell
Set-Location planning_app
& '..\.venv\Scripts\python.exe' -m pytest tests\test_projects.py tests\test_projects_hierarchy.py tests\test_projects_completion.py tests\test_projects_ui.py tests\test_auth.py -q
```

Tests cover standalone/nested hierarchies, access isolation and revocation,
viewer/editor sharing, dates/money/status validation, assignment/workload,
CSRF, optimistic concurrency, soft deletion, audit snapshots, rendered forms,
and a migration upgrade/downgrade round trip compared against model metadata.
Hierarchy tests additionally verify exact parent/child placement, all standalone
variants, per-page/filter semantics, contextual totals, breadcrumbs and access
revocation.
Completion tests cover visible status actions, parent close validation through
HTML and JSON, safe reopening, audit history and money-independent counts.
