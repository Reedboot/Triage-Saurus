/**
 * alpine-store.js — Alpine.js store for scan pipeline, modal, and status bar.
 * Loaded with `defer` *before* Alpine itself so the `alpine:init` event fires correctly.
 */

const PHASE_MAP = [
  { id: 'pp-1',  title: 'Detection',            patterns: ['phase 1', 'detection scan', 'detection'],                                             label: 'Step 1: Detection' },
  { id: 'pp-2',  title: 'OpenGrep',             patterns: ['opengrep', 'greping', 'grep scan', 'chunked opengrep', 'large repo detected'],       label: 'Step 2: OpenGrep' },
  { id: 'pp-3',  title: 'Context',              patterns: ['context extract', 'extracting context', 'phase 2', 'code context discovery'],         label: 'Step 3: Context' },
  { id: 'pp-4',  title: 'Relink',               patterns: ['relink', 'findings relink', 'phase 3b'],                                               label: 'Step 4: Relink' },
  { id: 'pp-5',  title: 'Semantic',             patterns: ['semantic', 'phase 3c', 'infer semantic'],                                               label: 'Step 5: Semantic' },
  { id: 'pp-6',  title: 'Diagrams',             patterns: ['phase 3d', 'diagram gen', 'generating diagram', 'generate architecture diagrams'],      label: 'Step 6: Diagrams' },
  { id: 'pp-7',  title: 'Complete',             patterns: ['complete', 'scan complete', 'phase 4', 'finished', 'done'],                            label: 'Step 7: Complete' },
];

document.addEventListener('alpine:init', () => {
  Alpine.store('scan', {
    // Pipeline progress
    pipelineVisible: false,
    phaseIdx: -1,
    phaseLabel: '',
    phases: PHASE_MAP.map(p => ({ id: p.id, title: p.title, state: 'idle' })), // idle | active | done

    // Modal
    modalVisible:  false,
    modalMessage:  '',
    _watchCb:  null,
    _newScanCb: null,

    // Module modal
    moduleModalVisible: false,
    detectedModules: [],
    _moduleScanCb: null,
    _moduleSkipCb: null,

    // Pipeline actions
    startPipeline() {
      this.pipelineVisible = true;
      this.phaseIdx  = -1;
      this.phaseLabel = '';
      this.phases.forEach(p => { p.state = 'idle'; });
    },

    updatePhase(text) {
      const lower = text.toLowerCase();
      for (let i = PHASE_MAP.length - 1; i >= 0; i--) {
        const phase = PHASE_MAP[i];
        const matched = phase.patterns.some(pat => lower.includes(pat));
        if (matched && i > this.phaseIdx) {
          // Mark all prior phases done
          for (let j = 0; j < i; j++) this.phases[j].state = 'done';
          this.phases[i].state = 'active';
          this.phaseIdx  = i;
          this.phaseLabel = phase.label;
          break;
        }
      }
    },

    completePipeline() {
      this.phases.forEach(p => { p.state = 'done'; });
      this.phaseLabel = 'Step 7: Complete ✓';
      setTimeout(() => { this.pipelineVisible = false; }, 3000);
    },

    // Modal actions
    showModal(message, onWatch, onNew) {
      this.modalMessage = message;
      this._watchCb     = onWatch;
      this._newScanCb   = onNew;
      this.modalVisible = true;
    },
    watchRunning() { this.modalVisible = false; if (this._watchCb)   this._watchCb(); },
    startNew()     { this.modalVisible = false; if (this._newScanCb) this._newScanCb(); },
    cancelModal()  { this.modalVisible = false; },

    // Module modal actions
    showModuleModal(modules, onScan, onSkip) {
      this.detectedModules = modules.map(m => ({ 
        ...m, 
        selected: m.found_in_repos && !m.already_scanned,  // Auto-select not-yet-scanned modules
        userProvidedPath: null,
        pathInputVisible: true,
        editingPath: false,  // For editing auto-resolved paths
        pathValidated: false
      }));
      this._moduleScanCb = onScan;
      this._moduleSkipCb = onSkip;
      this.moduleModalVisible = true;
    },
    canProceedWithModules() {
      // Can proceed if:
      // - At least one module is selected AND
      // - All selected modules have either a path or userProvidedPath
      const hasSelected = this.detectedModules.some(m => m.selected);
      if (!hasSelected) return false;
      
      return this.detectedModules.every(m => {
        if (!m.selected) return true;  // Unselected modules don't need a path
        // Selected modules need either module_repo_path or userProvidedPath
        return m.module_repo_path || (m.userProvidedPath && m.userProvidedPath.trim());
      });
    },
    proceedWithModuleScan() {
      this.moduleModalVisible = false;
      const selected = this.detectedModules.filter(m => m.selected);
      if (this._moduleScanCb) this._moduleScanCb(selected);
    },
    skipModuleScan() {
      this.moduleModalVisible = false;
      if (this._moduleSkipCb) this._moduleSkipCb();
    },
    closeModuleModal() {
      this.moduleModalVisible = false;
    },

    // CSS helper for each pipeline bar
    phaseClass(phase) {
      if (phase.state === 'done')   return 'pipeline-phase pipeline-phase--done';
      if (phase.state === 'active') return 'pipeline-phase pipeline-phase--active';
      return 'pipeline-phase pipeline-phase--idle';
    },

    phaseLabelClass() {
      const isComplete = this.phases.every(p => p.state === 'done');
      if (isComplete) return 'text-xs text-emerald-400 ml-auto';
      if (this.phaseIdx >= 0) return 'text-xs text-amber-300 ml-auto';
      return 'text-xs text-gray-400 ml-auto';
    },
  });

  // Expose triagePipeline so scan-control.js / scan-stream.js can call it
  window.triagePipeline = {
    onScanStart:    () => Alpine.store('scan').startPipeline(),
    onScanLine:     (text) => Alpine.store('scan').updatePhase(text),
    onScanComplete: () => Alpine.store('scan').completePipeline(),
    showModal:      (msg, onWatch, onNew) => Alpine.store('scan').showModal(msg, onWatch, onNew),
    showModuleModal: (modules, onScan, onSkip) => Alpine.store('scan').showModuleModal(modules, onScan, onSkip),
  };
});
