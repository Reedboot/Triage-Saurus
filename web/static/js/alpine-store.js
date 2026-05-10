/**
 * alpine-store.js — Alpine.js store for scan pipeline, modal, and status bar.
 * Loaded with `defer` *before* Alpine itself so the `alpine:init` event fires correctly.
 */

const PHASE_MAP = [
  { id: 'pp-1',  title: 'opengrep scan',      patterns: ['opengrep', 'greping', 'grep scan', 'phase 1', 'running scan'],    label: 'Phase 1: opengrep scan' },
  { id: 'pp-2',  title: 'context extraction',  patterns: ['context extract', 'extracting context', 'phase 2'],               label: 'Phase 2: context extraction' },
  { id: 'pp-3a', title: 'findings relink',     patterns: ['relink', 'findings relink', 'phase 3a'],                          label: 'Phase 3a: findings relink' },
  { id: 'pp-3b', title: 'semantic connections', patterns: ['semantic', 'phase 3b'],                                          label: 'Phase 3b: semantic connections' },
  { id: 'pp-3c', title: 'diagram generation',  patterns: ['diagram gen', 'generating diagram', 'phase 3c'],                  label: 'Phase 3c: diagram generation' },
  { id: 'pp-4',  title: 'complete',            patterns: ['complete', 'scan complete', 'phase 4', 'finished', 'done'],       label: 'Phase 4: complete' },
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
      this.phaseLabel = 'Phase 4: complete ✓';
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

    // CSS helper for each pipeline bar
    phaseClass(phase) {
      if (phase.state === 'done')   return 'pipeline-phase flex-1 h-2 rounded-full bg-emerald-700 transition-all duration-500';
      if (phase.state === 'active') return 'pipeline-phase flex-1 h-2 rounded-full bg-emerald-500 transition-all duration-500';
      return 'pipeline-phase flex-1 h-2 rounded-full bg-gray-700 transition-all duration-500';
    },
  });

  // Expose triagePipeline so scan-control.js / scan-stream.js can call it
  window.triagePipeline = {
    onScanStart:    () => Alpine.store('scan').startPipeline(),
    onScanLine:     (text) => Alpine.store('scan').updatePhase(text),
    onScanComplete: () => Alpine.store('scan').completePipeline(),
    showModal:      (msg, onWatch, onNew) => Alpine.store('scan').showModal(msg, onWatch, onNew),
  };
});
