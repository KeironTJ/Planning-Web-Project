import { initWorkspaceState } from '../components/workspace_state.js';
import { initWorkQuickEdit } from './projects/quick_edit.js';
import { initWorkQuickAdd } from './projects/quick_add.js';
import { initPeoplePickers } from '../components/people_picker.js';
import { initUKDateInputs } from '../components/uk_date_input.js';

const workspace = document.querySelector('[data-workspace="projects"]');
if (workspace) {
    initWorkspaceState(workspace, `projects:${workspace.dataset.workUser}`);
    initWorkQuickEdit(workspace);
    initWorkQuickAdd(workspace);
    initPeoplePickers(workspace);
    initUKDateInputs(workspace);
}
