import { initWorkspaceState } from '../components/workspace_state.js';

const workspace = document.querySelector('[data-workspace="projects"]');
if (workspace) {
    initWorkspaceState(workspace, `projects:${workspace.dataset.workUser}`);
}
