/**
 * sections.js — section tab system, log/sections view switching.
 */
import { state }                         from './state.js';
import {
  refetchDiagramsWithApiOpsMode,
  updateApiOpsButtonText,
} from './diagram-actions.js';

// ── View switching ────────────────────────────────────────────────────────────

export function showSectionsView() {
  const tabBar          = document.getElementById('section-tab-bar');
  const panelContent    = document.getElementById('section-panel-content');
  const logOut          = document.getElementById('log-output');
  const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
  if (tabBar)        tabBar.style.display = 'flex';
  if (panelContent)  panelContent.style.display = '';
  if (logOut)        logOut.style.display = 'none';
  if (toggleSectionsBtn) {
    toggleSectionsBtn.title       = 'Show log';
    toggleSectionsBtn.textContent = '📜 Log';
  }
}

export function showLogView() {
  const tabBar          = document.getElementById('section-tab-bar');
  const panelContent    = document.getElementById('section-panel-content');
  const logOut          = document.getElementById('log-output');
  const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
  if (tabBar)       tabBar.style.display = 'none';
  if (panelContent) panelContent.style.display = 'none';
  if (logOut)       logOut.style.display = '';
  if (toggleSectionsBtn) {
    toggleSectionsBtn.title       = 'Show sections';
    toggleSectionsBtn.textContent = '📑 Sections';
  }
}

// ── Repo name helper (also used by scan-control) ──────────────────────────────

export function getCurrentRepoName() {
  const sel = document.getElementById('repo-select');
  if (!sel?.value) return '';
  const repoOpt = sel.querySelector('option:checked');
  return (repoOpt?.dataset?.name) || sel.value.split('/').pop();
}

// ── Tab builder ───────────────────────────────────────────────────────────────

export function buildSectionTabs(experimentId, repoName) {
  if (!experimentId || !repoName) return;
  state.currentExperimentId = experimentId;
  state.currentRepoName     = repoName;
  state.apiOpsMode          = 'auto';
  updateApiOpsButtonText();

  fetch(`/api/view/tabs/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`)
    .then(r => r.json())
    .then(data => {
      const tabs = Array.isArray(data.tabs) ? data.tabs : [];
      if (!tabs.length) return;

      const tabBar = document.getElementById('section-tab-bar');
      if (!tabBar) return;

      tabBar.innerHTML = '';
      tabBar.setAttribute('role', 'tablist');
      tabBar.setAttribute('aria-label', 'Scan result sections');

      tabs.forEach((tab, idx) => {
        const btn = document.createElement('button');
        btn.className            = 'section-tab-btn' + (idx === 0 ? ' active' : '');
        btn.dataset.key          = tab.key;
        btn.dataset.experimentId = experimentId;
        btn.dataset.repoName     = repoName;
        btn.textContent          = tab.label;
        btn.setAttribute('role',         'tab');
        btn.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
        btn.setAttribute('aria-controls', 'section-panel-content');
        btn.addEventListener('click', function () {
          tabBar.querySelectorAll('.section-tab-btn').forEach(b => {
            b.classList.remove('active');
            b.setAttribute('aria-selected', 'false');
          });
          btn.classList.add('active');
          btn.setAttribute('aria-selected', 'true');
          loadSectionContent(tab.key, experimentId, repoName);
        });
        tabBar.appendChild(btn);
      });

      tabBar.style.removeProperty('display');
      showSectionsView();
      if (tabs.length > 0) loadSectionContent(tabs[0].key, experimentId, repoName);
    })
    .catch(() => {});

  refetchDiagramsWithApiOpsMode(true);
}

// ── Section content loader ────────────────────────────────────────────────────

export function loadSectionContent(key, experimentId, repoName) {
  if (!key || !experimentId || !repoName) return Promise.resolve();
  const panel = document.getElementById('section-panel-content');
  if (!panel) return Promise.resolve();

  panel.innerHTML = '<div class="section-loading"><span>⏳ Loading…</span></div>';
  panel.style.removeProperty('display');

  const url = `/api/view/${encodeURIComponent(key)}/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`;

  return fetch(url)
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
    .then(html => {
      panel.innerHTML = html;
      const initMap = {
        overview:   () => window.initOverview   && window.initOverview(panel),
        tldr:       () => window.initOverview   && window.initOverview(panel),
        findings:   () => window.initFindings   && window.initFindings(panel),
        containers: () => window.initContainers && window.initContainers(panel),
        assets:     () => window.initAssets     && window.initAssets(panel, repoName, experimentId),
        roles:      () => window.initRoles      && window.initRoles(panel),
      };
      const initFn = initMap[key];
      if (initFn) try { initFn(); } catch (e) { console.warn('[Sections] init error for', key, e); }
      panel.querySelectorAll('script').forEach(oldScript => {
        const s = document.createElement('script');
        s.textContent = oldScript.textContent;
        oldScript.replaceWith(s);
      });
    })
    .catch(err => {
      panel.innerHTML = `<div class="empty-state"><p>⚠ Failed to load section: ${err.message}</p></div>`;
    });
}

// ── Key-based activation ──────────────────────────────────────────────────────

export function activateSectionKey(key, experimentId, repoName) {
  const tabBar = document.getElementById('section-tab-bar');
  if (tabBar) {
    const btn = tabBar.querySelector(`.section-tab-btn[data-key="${key}"]`);
    if (btn) { btn.click(); return; }
  }
  if (experimentId && repoName) buildSectionTabs(experimentId, repoName);
}
