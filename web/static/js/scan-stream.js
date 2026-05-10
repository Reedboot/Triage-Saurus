/**
 * scan-stream.js — EventSource management and log output rendering.
 */
import { state } from './state.js';

// ── EventSource ───────────────────────────────────────────────────────────────

export function closeEventSource() {
  if (state.currentEventSource) {
    state.currentEventSource.close();
    state.currentEventSource = null;
  }
}

// ── Log output ────────────────────────────────────────────────────────────────

export function clearLog() {
  if (state.logOutput) {
    state.logOutput.innerHTML = '';
    state.logOutput.scrollTop = 0;
  }
  setLogAutoScrollEnabled(true, false);
  const placeholder = document.getElementById('section-placeholder');
  if (placeholder) placeholder.style.display = 'none';
}

export function updateLogAutoScrollButton() {
  if (!state.logAutoScrollBtn) return;
  if (state.logAutoScrollEnabled) {
    state.logAutoScrollBtn.textContent = '⏸ Auto-scroll';
    state.logAutoScrollBtn.title       = 'Pause auto-scroll';
  } else {
    state.logAutoScrollBtn.textContent = '▶ Resume auto-scroll';
    state.logAutoScrollBtn.title       = 'Resume auto-scroll';
  }
}

export function setLogAutoScrollEnabled(enabled, scrollToBottom) {
  state.logAutoScrollEnabled = !!enabled;
  if (state.logAutoScrollEnabled && scrollToBottom && state.logOutput) {
    requestAnimationFrame(() => {
      if (state.logOutput) state.logOutput.scrollTop = state.logOutput.scrollHeight;
    });
  }
  updateLogAutoScrollButton();
}

// ── Log line classification ───────────────────────────────────────────────────

export function detectPhaseClass(text) {
  if (typeof text !== 'string') return null;
  const m = text.match(/(?:▶\s*)?(?:PHASE|Phase)\s*(\d+[a-z]?)/i);
  return m ? `phase-${m[1].toLowerCase()}` : null;
}

export function detectLogSourceClass(text) {
  if (typeof text !== 'string') return null;
  const m = text.match(
    /^\[(Info|Web|Pipeline|Detection|Misconfigurations|Store|AzureGoat|Error|Warning|Warn|Success)\]/i
  );
  if (!m) return null;
  const tag = m[1].toLowerCase();
  const map = {
    error: 'error', warning: 'warn', warn: 'warn', success: 'success',
    info: 'info', web: 'line-web', pipeline: 'line-pipeline',
    detection: 'line-detection', misconfigurations: 'line-misconfigurations',
    store: 'line-store', azuregoat: 'line-repo',
  };
  return map[tag] || `line-${tag}`;
}

export function detectSeparatorClass(text) {
  if (typeof text !== 'string') return null;
  return /^(?:=|─|—|-){10,}$/.test(text.trim()) ? 'line-sep' : null;
}

export function addLogLine(text, className) {
  if (!state.logOutput) return;

  // Notify Alpine pipeline store for phase tracking
  window.triagePipeline?.onScanLine?.(text);

  const line    = document.createElement('div');
  const classes = [];

  const phaseClass     = detectPhaseClass(text);
  const sourceClass    = detectLogSourceClass(text);
  const separatorClass = detectSeparatorClass(text);

  if (className) classes.push(className);
  if (phaseClass) classes.push(phaseClass);
  if (sourceClass) {
    if (
      (sourceClass === 'error' || sourceClass === 'warn' || sourceClass === 'success') &&
      classes.includes('info')
    ) {
      classes.splice(classes.indexOf('info'), 1);
    }
    if (!classes.includes(sourceClass)) classes.push(sourceClass);
  }
  if (separatorClass) classes.push(separatorClass);
  if (classes.length) line.className = classes.join(' ');

  line.textContent = text;
  state.logOutput.appendChild(line);

  if (state.logAutoScrollEnabled) {
    requestAnimationFrame(() => {
      if (state.logOutput && state.logAutoScrollEnabled) {
        state.logOutput.scrollTop = state.logOutput.scrollHeight;
      }
    });
  }
}
