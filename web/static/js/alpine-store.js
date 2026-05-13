/**
 * alpine-store.js — Alpine.js store for scan pipeline, modal, and status bar.
 * Loaded with `defer` *before* Alpine itself so the `alpine:init` event fires correctly.
 */

const PHASE_MAP = [
  { id: 'pp-1',  title: 'Detection',           patterns: ['phase 1', 'detection scan', 'detection'],                                                             label: 'Step 1: Detection' },
  { id: 'pp-2',  title: 'Misconfig Scan',      patterns: ['phase 2 — targeted misconfigurations', 'misconfigurations', 'storing findings in db'],                label: 'Step 2: Misconfig Scan' },
  { id: 'pp-3a', title: 'Context - Patterns',  patterns: ['context phase 3.1', 'scanning patterns', 'opengrep detection'],                                       label: 'Step 3: Scanning patterns' },
  { id: 'pp-3b', title: 'Context - Manifests', patterns: ['context phase 3.2', 'parsing manifests', 'package.json', 'kubernetes manifests'],                     label: 'Step 4: Parsing manifests' },
  { id: 'pp-3c', title: 'Context - Topology',  patterns: ['context phase 3.3', 'service topology', 'extracting service', 'persisting'],                         label: 'Step 5: Service topology' },
  { id: 'pp-4',  title: 'Analysis',            patterns: ['phase 3a', 'phase 3c', 'internet exposure', 'semantic', 'extract sg rules', 'extract ci/cd artifacts'], label: 'Step 6: Analysis' },
  { id: 'pp-5',  title: 'Persistence',         patterns: ['phase 3b', 'relink findings', 'provider inheritance', 'populate resource_id'],                           label: 'Step 7: Persistence' },
  { id: 'pp-6',  title: 'Diagrams',            patterns: ['phase 3d', 'diagram gen', 'generate architecture diagrams', 'loading diagrams'],                          label: 'Step 8: Diagrams' },
  { id: 'pp-7',  title: 'Finalizing',          patterns: ['finalizing'],                                                                                           label: 'Step 9: Finalizing' },
  { id: 'pp-8',  title: 'Ready',               patterns: ['step 8: ready', 'scan ready', 'pipeline ready'],                                                        label: 'Step 10: Ready' },
];

const STAGE_TO_PHASE_INDEX = {
  'phase1-detection': 0,
  'phase1-misconfig': 1,
  'phase2-context-patterns': 2,
  'phase2-context-manifests': 3,
  'phase2-context-topology': 4,
  'phase2-context': 2, // fallback for old stage ID
  'phase3-analysis': 5,
  'phase3-persistence': 6,
  'phase3-diagrams': 7,
  finalizing: 8,
  ready: 9,
};

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

    _activatePhase(index, label) {
      if (index < 0 || index >= this.phases.length) return;
      for (let i = 0; i < index; i++) this.phases[i].state = 'done';
      this.phases[index].state = 'active';
      this.phaseIdx = index;
      this.phaseLabel = label || PHASE_MAP[index]?.label || this.phaseLabel;
    },

    updatePhase(text) {
      const lower = text.toLowerCase();
      for (let i = PHASE_MAP.length - 1; i >= 0; i--) {
        const phase = PHASE_MAP[i];
        const matched = phase.patterns.some(pat => lower.includes(pat));
        if (matched && i > this.phaseIdx) {
          this._activatePhase(i, phase.label);
          break;
        }
      }
    },

    updatePhaseFromStage(stage) {
      if (!stage || typeof stage !== 'object') return;
      this.pipelineVisible = true;

      if (stage.state === 'complete' || stage.id === 'ready') {
        this.completePipeline();
        return;
      }
      if (stage.state === 'failed') {
        this.phaseLabel = stage.label || 'Scan failed';
        return;
      }

      const index = STAGE_TO_PHASE_INDEX[stage.id];
      if (typeof index !== 'number') {
        if (stage.label) this.phaseLabel = stage.label;
        return;
      }

      if (index < this.phaseIdx) {
        if (stage.label) this.phaseLabel = stage.label;
        return;
      }

      if (index === this.phaseIdx) {
        if (stage.label) this.phaseLabel = stage.label;
        return;
      }

      this._activatePhase(index, stage.label);
    },

    completePipeline() {
      this.phases.forEach(p => { p.state = 'done'; });
      this.phaseLabel = 'Step 10: Ready ✓';
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
        source: m.source || '',
        source_file: m.source_file || '',
        source_line: Number.isFinite(Number(m.source_line)) ? Number(m.source_line) : null,
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
    onScanStage:    (stage) => Alpine.store('scan').updatePhaseFromStage(stage),
    onScanComplete: () => Alpine.store('scan').completePipeline(),
    showModal:      (msg, onWatch, onNew) => Alpine.store('scan').showModal(msg, onWatch, onNew),
    showModuleModal: (modules, onScan, onSkip) => Alpine.store('scan').showModuleModal(modules, onScan, onSkip),
  };
});
