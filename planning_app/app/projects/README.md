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
The **List / Hierarchy / Timeline** switcher selects one view at a time on project and
activity lists; task lists also offer **Board**. Hierarchy remains the default
for first visits. List shows a flat table of the selected entities, while Board
groups the current page's tasks by status, with explicit page-local counts.
Switching views keeps filters, page and page size. Filtering keeps the selected
view and page size, and returns to the first page. Pagination applies to every
view. Links use `view=list|hierarchy|timeline|board`, so bookmarked/shared views work
without JavaScript; board is only valid for tasks. Invalid views return an
explicit validation error.

Project/activity detail pages show the same nested structure with the current
item expanded. A single location header links through the actual parent chain,
labelling each project/activity with its type, reference and name. The current
item is highlighted with its type, reference and the page's only main title;
standalone activities/tasks are explicitly labelled. The section navigation
highlights the current work type and exposes one link per destination, also used
by keyboard shortcuts. Dashboard, list and form pages likewise have one main
title rather than a repeated generic heading. Deleted or inaccessible work is never included.
Expansion uses native HTML disclosures and works with keyboard navigation and
without JavaScript.

Hierarchy items use one action bar: the primary workflow action (Start/resume,
Complete, Reopen or Restart), Quick edit, Logs & comments and parent-item details.
Secondary workflow actions such as Mark blocked and Cancel / close are grouped
under **More**, using a native disclosure that works without JavaScript.
Child creation is separate: **Add activity / Add task** sits beside the child
section, with the correct parent retained. Viewers see navigation only.
Status actions retain their existing permissions, validation and return context.

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

## Actionable dashboard

The work dashboard leads with an always-visible **Progress overview**: one compact
card per work type shows counts, completion and outstanding work, with links to
the full hierarchy and owned work. A separate link opens assigned tasks.
Creation buttons sit beside the heading rather than in a repeated shortcut panel.
**Needs attention** follows: Overdue, Blocked, and Due soon across visible
projects, activities and tasks. Terminal work is excluded.
Nonempty sections show the full count and links to each nonempty work type's matching quick
filter, plus at most five compact previews sorted by deadline, name, kind and ID.
An item may appear in multiple sections (for example blocked and overdue).
Missing deadlines sort last in Blocked.

Previews include reference, work type, status, priority, deadline, logs/comments
and the existing status/quick-edit controls for editors. Viewers see no mutation
controls. Status actions preserve dashboard origin and retain all permissions,
CSRF, version checks and descendant-completion rules; Quick edit refreshes it.
Empty attention groups use a compact "No … work" row rather than cards with
zero-count links. Nonempty attention panels share the available width and wrap
when there is not enough room for readable cards, rather than reserving a fixed
third of the row for each group. The work hierarchy is always visible below attention, ahead of
recent updates. Individual project/activity branches remain expandable, with
their expansion remembered by the existing workspace component. The top
navigation provides the scheduling timeline; it is not duplicated in shortcuts.

## Compact logs

Detail timelines, the logs screen and dashboard previews share a compact entry
layout: automatic-update summaries lead, with author/time metadata and a linked
work name/reference. Full before/after audit records stay collapsed under
**Audit details**. Comments up to 240 characters are shown in full; longer
comments have a preview and a native **Read full comment** disclosure, retaining
the complete text without JavaScript. Author names are shown without redundant
user IDs (unknown authors retain their ID). Permitted comment edit/delete forms
are grouped in one disclosure; permissions, CSRF and return navigation are unchanged.

## UK dates and times

Work dates are displayed and entered as **dd/mm/yyyy** throughout lists, cards,
detail pages, scheduling axes/labels, dashboard previews, reports, home work
summaries and creation/edit dialogs. Native browser date fields cannot guarantee
UK display regardless of browser locale, so reusable
`static/js/components/uk_date_input.js` provides explicit UK text entry with
calendar chooser buttons where the browser supports `showPicker()`.
Calendar choices update the UK text; impossible dates are rejected, including
non-leap-year 29 February. Blank dates remain optional. Without JavaScript the
full form still accepts and validates UK dates server-side and retains errors.

Work timestamps (created, updated, archived and log times) use the shared
`utcfmt('uk-datetime')` formatter with **dd/mm/yyyy hh:mm:ss**, in 24-hour time.
It preserves the app's existing UTC-to-browser-local timezone convention;
the server-rendered no-JavaScript fallback uses UTC. Calendar dates never undergo
timezone conversion. Other existing shared formatter modes remain unchanged.
API dates, timestamp payloads, raw audit snapshots and machine-readable time
attributes remain ISO. Legacy ISO full-form date submissions remain supported.

## Search and quick filters

Every project/activity/task list has **Search work**, matching a case-insensitive
substring in the item's name or description, or an exact complete hierarchical
reference (for example `01.03.07`, `A03.07` or `T07`). Leading/trailing whitespace
is ignored; `%` and `_` are literal search characters, not SQL wildcards.
Queries are limited to 200 characters. Reference lookup follows current location,
so old references stop matching after moves unless present in an item's text.

Quick filters are **Owned by me**, **Open**, **Blocked**, **Overdue**, and
**Due soon (14 days)**. Tasks additionally offer **Assigned to me**.
Open excludes completed/cancelled items; overdue/due-soon also exclude these
terminal states. Due soon includes today and the next 14 calendar days.
Owned by me means ownership, not creation, assignment or shared access.
Assigned to me includes closed tasks unless narrowed by another filter, and
never grants access to work whose root sharing has been revoked.

One quick filter is selected at a time; selecting it again removes it.
It narrows the current search and ordinary filters, rather than clearing them
(contradictory combinations legitimately produce no matches). Priority now has
its own ordinary filter alongside status, department, owner, deadline and context.
The total matching count is shown above the selected view. Ordinary filters are
grouped under a native **More filters** disclosure, collapsed by default and
expanded with an active-filter count when any are applied. Search and quick
filters stay easy to reach without a full filter panel taking up the screen.

Search/quick filters are server-side and apply consistently to List, Hierarchy,
Board, Timeline and `GET /projects/api/<kind>?q=...&quick=...`.
Filters select the listed work type, not text on ancestors or descendants;
hierarchy/timeline retain their documented parent/child context.
Changing quick filters resets pagination to page one while keeping the view,
page size, search and other filters. Submitting the form also preserves explicit
project/activity scope. Switching views and paging retain all filters.
Reset clears them; the existing tab-local workspace component remembers them.
Ordinary GET forms and links work without JavaScript.

## Work quick editing

**Quick edit** is available to editors on project, activity and task rows and
hierarchy cards, and on task board cards, including dashboard/report tables and
detail summaries. One shared dialog edits deadline and priority for every work
type without navigating to the full form. Tasks additionally offer assigned
users; projects and activities do not support task assignments.
In hierarchy/detail status action rows, Quick edit follows the first workflow
action (Start, resume, reopen or the first available action) and precedes the
red-outlined **Cancel / close** action. List and board views retain their
standalone Quick edit links.
Assignees use searchable checkboxes; filtering people never clears selections.
Only active users with inherited viewer/editor access are offered. Existing
assignees who have become inactive or lost access require explicit confirmation
of removal before saving. Assignment never grants access.

Opening the editor fetches current values and eligible users from
`GET /projects/api/<kind>/<id>/edit-options`. Saving uses the existing work PATCH
endpoint with only deadline, priority and version (plus assignees for tasks); unrelated fields
are unchanged. A successful save refreshes the current workspace so all copies,
filters, counts and overdue totals are reconciled. Validation/network failures
keep entered values in the dialog. Requests time out after 30 seconds; an
unconfirmed save asks users to reload latest values before retrying, since the
server may have received the write. Stale versions offer **Reload latest values
(discards edits)** rather than overwriting someone else's changes.

The editor is implemented in `static/js/modules/projects/quick_edit.js` and uses
the shared CSRF-aware fetch helper. Native dialogs provide keyboard focus
containment and Escape/Cancel dismissal; dismissal is disabled during saves to
avoid unconfirmed writes. **Edit all fields** opens the existing form. Without
JavaScript/native dialog support, Quick edit is a normal link to that form.
Viewer-only users see no quick-edit links and cannot access edit options or save.

## Faster creation

Dashboard/list creation links and contextual **Add activity / Add task** links
open one shared quick-add dialog. Essential fields are name, department, owner,
priority, deadline and one parent selector. Contextual links preselect the parent
and inherit its department; new work starts planned with zero direct costs.
**Create and add another** retains these settings and clears just the name.
Closing after creation refreshes the workspace to reconcile all displayed work.

`GET /projects/api/<kind>/create-options` supplies current defaults, active owners
and editable, non-terminal parents. The existing collection POST performs all
validation and automatic auditing. Errors preserve the entered draft; uncertain
network/timeout responses advise checking the list before retrying to avoid
duplicates. Controls prevent double submission and dismissal during saves.
`static/js/modules/projects/quick_add.js` uses the shared CSRF-aware fetch helper.
Without JavaScript/native dialogs, creation links still open the full form.

Full forms put essentials first and group description, start/end dates and costs
under **More details** (expanded on edits and failed submissions). Tasks have one
grouped project/activity parent selector and searchable assignee checkboxes using
the reusable `static/js/components/people_picker.js`. Filtering never deselects
people; assignment still requires inherited access and never grants it.
**Save and add another** on new full forms retains the parent and return location.
Legacy API and form `project_id` / `activity_id` inputs remain supported.

## Hierarchical work references

References use the existing record IDs, padded to at least two digits:

- Project `01`; its activity `01.03`; that activity's task `01.03.07`.
- A direct project task uses `01.00.07` (`00` means no activity).
- Standalone activity `A03`, its task `A03.07`, and standalone task `T07`.

These are location references, not sibling sequence numbers. Gaps are normal;
deletion, filtering and pagination never renumber work. Moving an activity/task
changes its reference to match the current hierarchy. The existing move workflow
still requires moving child items first before changing their parent's location;
references follow each move and reattachment.
Database IDs, URLs and permissions do not change. References appear across work
lists, hierarchy/board cards, detail screens, parent selectors, reports, logs and
archives. APIs expose `reference`; automatic audit snapshots capture it.

## Scheduling timeline

**Timeline** is available on all three lists, as a collapsed section on detail
pages, and at `/projects/timeline` for all visible live work. Project/activity
list timelines include the selected page's descendants; task timelines contain
only the matching page's tasks. The whole-workspace view groups parents with
children. No inaccessible or archived work is included.

Bars span inclusive start/end calendar dates. A single date is a milestone
rather than an invented duration; diamonds mark deadlines. The dashed today
marker appears only within the displayed range. Exact dates accompany every row,
and work without any dates is listed separately under **Unscheduled**.
The range automatically fits the displayed dates with at most five tick labels;
there are no zoom/range controls, dependencies or drag-to-reschedule actions yet.
Quick edit changes deadlines and refreshes the view; full editing sets start/end
dates. Rendering works without JavaScript, and horizontal scroll is remembered
by the existing workspace-state component.

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
  returns an explicit conflict with outstanding activity/task counts through the
  API; the detail-page action returns to the item and shows the same warning.
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

Detail pages lead with the work reference, status and key facts, with secondary
metadata, hierarchy, progress and sharing available in disclosures. The delete
disclosure explains archive/child restrictions before offering a confirmation
action. A child-work warning explains why a project or activity cannot yet be
closed. The **Keyboard shortcuts** guide opens with `?`, without a navigation button:
`/` focuses list search, `g` then `d/p/a/t` navigates, `n` uses a single
contextual Add action, and `?` opens the guide. Shortcuts avoid text-entry fields
and open dialogs; keyboard shortcuts require JavaScript.

**Logs & updates** is a dedicated, paginated timeline at `/projects/logs`.
Filter by entry type, work type/item ID, author, or a work name/reference search.
Name search matches accessible projects, activities and tasks; reference search
uses the same complete hierarchical references as the work lists. Suggestions
show up to 100 visible items, while typed searches cover all accessible work.
Each entry names and links its
source, separates comments from automatic updates, and gives audit events a
readable summary (including before/after statuses). Full audit snapshots remain
available inside the entry's disclosure. The dashboard highlights recent entries,
and every work card links to its detail-page comments/timeline.
The same log filters are supported by `GET /projects/api/logs`:
`log_kind=comment|audit`, `target_kind=projects|activities|tasks`, `target_id`
(requires work type), `target_search` (up to 200 characters), and `author_id`.
Item-specific filters match the direct log
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
