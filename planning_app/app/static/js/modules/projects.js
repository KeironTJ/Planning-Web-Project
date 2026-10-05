import { initWorkspaceState } from '../components/workspace_state.js';
import { initWorkQuickEdit } from './projects/quick_edit.js';

const workspace = document.querySelector('[data-workspace="projects"]');
if (workspace) {
    initWorkspaceState(workspace, `projects:${workspace.dataset.workUser}`);
    initWorkQuickEdit(workspace);
}
