// app.js - main application logic for Triage-Saurus UI
(function () {
  let currentEventSource = null;
  let currentPollInterval = null;  // Track polling to prevent duplicates
  let statusBar = null;
  let statusText = null;
  let spinner = null;
  let logOutput = null;
  let scanBtn = null;
 
  let currentExperimentId = null;  // Track active experiment for section tabs
  let currentRepoName = null;  // Track current repo for API ops refetching
  let architectureAiStream = null;
  let architectureAiBtn = null;
  let architectureAiStopInFlight = false;

  // API ops visibility mode state
  let apiOpsMode = 'auto'; // 'auto' | 'all' | 'hide'
  let storedDiagrams = []; // Cache original diagrams for API ops filtering

  // Zoom state for mermaid diagrams - now per-diagram
  const zoomState = {
    scale: 2.0,
    minScale: 0.5,
    maxScale: 8.0,
    panX: 0,
    panY: 0
  };

  // Per-diagram zoom/pan state store (keyed by diagram index)
  const diagramStates = {};

  // Pan interaction state
  let isPanning = false;
  let activePanPointerId = null;
  let panStartX = 0;
  let panStartY = 0;
  let panOriginX = 0;
  let panOriginY = 0;
  let currentDiagramIndex = 0;

  // Track whether live scan output should stay pinned to the bottom.
  let logAutoScrollEnabled = true;
  const logAutoScrollThreshold = 24;
  let logAutoScrollBtn = null;

  // Global status setter used by other modules
  window._triage = window._triage || {};
  window._triage.setStatus = function (message, type) {
    if (statusText) {
      statusText.textContent = message;
      statusText.className = 'status-text';
      if (type === 'error') {
        statusText.classList.add('error');
      } else if (type === 'warn') {
        statusText.classList.add('warn');
      } else if (type === 'success') {
        statusText.classList.add('success');
      }
    }
  };

  // Close any open event source
  function closeEventSource() {
    if (currentEventSource) {
      currentEventSource.close();
      currentEventSource = null;
    }
  }

  function updateArchitectureAiButton(isRunning) {
    if (!architectureAiBtn) return;
    architectureAiBtn.disabled = !!isRunning;
    architectureAiBtn.textContent = isRunning ? '⏳ Architecture AI…' : '🤖 Run AI (Architecture)';
    architectureAiBtn.title = isRunning
      ? 'Architecture AI is running'
      : 'Run AI against the architecture diagram and suggest code/rule changes';
  }

  function closeArchitectureAiStream() {
    if (architectureAiStream) {
      try {
        architectureAiStream.close();
      } catch (err) {}
      architectureAiStream = null;
    }
    architectureAiStopInFlight = false;
    updateArchitectureAiButton(false);
    if (window._triage && typeof window._triage.setToolbarStopState === 'function') {
      window._triage.setToolbarStopState({
        enabled: false,
        visible: false,
        label: '⏹ Stop AI Scan',
      });
    }
    if (window._triage && typeof window._triage.setStatusBusy === 'function') {
      window._triage.setStatusBusy(false);
    }
  }

  // Apply zoom and pan to diagram container
  function applyTransform() {
    const zoomInner = document.getElementById('diagram-zoom-inner');
    if (zoomInner) {
      requestAnimationFrame(() => {
        zoomInner.style.transition = isPanning ? 'none' : '';
        zoomInner.style.transform = `scale(${zoomState.scale}) translate(${zoomState.panX}px, ${zoomState.panY}px)`;
        zoomInner.style.transformOrigin = 'top left';
      });
    }
    updateZoomDisplay();
  }

  // Update zoom level display
  function updateZoomDisplay() {
    const zoomLevel = Math.round(zoomState.scale * 100);
    const display = document.getElementById('zoom-level-display');
    if (display) {
      display.textContent = `${zoomLevel}%`;
    }
  }

  // Zoom in by 10%
  function zoomIn() {
    const newScale = zoomState.scale * 1.1;
    if (newScale <= zoomState.maxScale) {
      zoomState.scale = newScale;
      applyTransform();
      saveDiagramState(currentDiagramIndex);
    }
  }

  // Zoom out by 10%
  function zoomOut() {
    const newScale = zoomState.scale / 1.1;
    if (newScale >= zoomState.minScale) {
      zoomState.scale = newScale;
      applyTransform();
      saveDiagramState(currentDiagramIndex);
    }
  }

  // Reset zoom and pan to default
  function zoomReset() {
    fitActiveDiagram('contain');
  }

  function getDiagramContentBounds(svg) {
    if (!svg) return null;

    try {
      const box = svg.getBBox();
      if (box && box.width > 0 && box.height > 0) {
        return { x: box.x, y: box.y, width: box.width, height: box.height };
      }
    } catch (e) {}

    const vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width > 0 && vb.height > 0) {
      return { x: vb.x || 0, y: vb.y || 0, width: vb.width, height: vb.height };
    }

    const rect = svg.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      return { x: rect.x || 0, y: rect.y || 0, width: rect.width, height: rect.height };
    }

    return null;
  }

  function fitActiveDiagram(mode = 'contain') {
    const activeDiagram = getActiveDiagramView();
    const svg = activeDiagram ? activeDiagram.querySelector('svg') : null;
    const zoomWrap = document.getElementById('diagram-zoom-wrap');

    if (!svg || !zoomWrap) {
      zoomState.scale = 1.0;
      zoomState.panX = 0;
      zoomState.panY = 0;
      applyTransform();
      return;
    }

    const bounds = getDiagramContentBounds(svg);
    const wrapWidth = zoomWrap.clientWidth || zoomWrap.offsetWidth || 0;
    const wrapHeight = zoomWrap.clientHeight || zoomWrap.offsetHeight || 0;

    if (!bounds || !wrapWidth || !wrapHeight) {
      zoomState.scale = 1.0;
      zoomState.panX = 0;
      zoomState.panY = 0;
      applyTransform();
      return;
    }

    const padding = 32;
    const availableWidth = Math.max(1, wrapWidth - padding);
    const availableHeight = Math.max(1, wrapHeight - padding);
    const containScale = Math.max(
      zoomState.minScale,
      Math.min(availableWidth / bounds.width, availableHeight / bounds.height)
    );
    const widthScale = Math.max(zoomState.minScale, availableWidth / bounds.width);
    const fitScale = Math.min(
      zoomState.maxScale,
      mode === 'width' ? widthScale : containScale
    );

    zoomState.scale = fitScale;
    zoomState.panX = (wrapWidth * (1 - fitScale)) / (2 * fitScale);
    zoomState.panY = mode === 'width'
      ? 0
      : (wrapHeight * (1 - fitScale)) / (2 * fitScale);
    applyTransform();
    saveDiagramState(currentDiagramIndex);
  }

  function scheduleDiagramFit(attempt = 0) {
    const activeDiagram = getActiveDiagramView();
    const svg = activeDiagram ? activeDiagram.querySelector('svg') : null;
    const zoomWrap = document.getElementById('diagram-zoom-wrap');
    const bounds = svg ? getDiagramContentBounds(svg) : null;

    if (svg && zoomWrap && bounds && bounds.width > 0 && bounds.height > 0 &&
      (zoomWrap.clientWidth || zoomWrap.offsetWidth) &&
      (zoomWrap.clientHeight || zoomWrap.offsetHeight)) {
      fitActiveDiagram('width');
      return;
    }

    if (attempt >= 30) {
      fitActiveDiagram('width');
      return;
    }

    requestAnimationFrame(() => scheduleDiagramFit(attempt + 1));
  }

  // Save current zoom state for a diagram index
  function saveDiagramState(diagramIndex) {
    diagramStates[diagramIndex] = {
      scale: zoomState.scale,
      panX: zoomState.panX,
      panY: zoomState.panY
    };
  }

  // Load zoom state for a diagram index
  function loadDiagramState(diagramIndex) {
    if (diagramStates[diagramIndex]) {
      const state = diagramStates[diagramIndex];
      zoomState.scale = state.scale;
      zoomState.panX = state.panX;
      zoomState.panY = state.panY;
    } else {
      scheduleDiagramFit();
      return;
    }
    applyTransform();
  }

  // Initialize pan/zoom interactivity on diagram SVG
  function initPanZoom() {
    const zoomWrap = document.getElementById('diagram-zoom-wrap');
    if (!zoomWrap) return;

    const diagramViews = document.getElementById('diagram-views');
    if (!diagramViews) return;

    // Get all mermaid SVG elements
    const mermaidElements = diagramViews.querySelectorAll('.mermaid svg');
    
    mermaidElements.forEach(svg => {
      if (svg.__panZoomInitialized) return;
      svg.__panZoomInitialized = true;

      // Enable dragging for pan with pointer capture where available, and keep a
      // mouse fallback for browsers/tests that do not emit pointer events here.
      let panInputType = null;
      const onMouseWindowMove = (e) => updatePan('mouse', e.clientX, e.clientY);
      const onMouseWindowUp = () => finishPan('mouse');

      const beginPan = (inputType, clientX, clientY, pointerId = null) => {
        if (isPanning) return;
        isPanning = true;
        panInputType = inputType;
        activePanPointerId = pointerId;
        panStartX = clientX;
        panStartY = clientY;
        panOriginX = zoomState.panX;
        panOriginY = zoomState.panY;
        svg.style.cursor = 'grabbing';
        if (inputType === 'mouse') {
          window.addEventListener('mousemove', onMouseWindowMove);
          window.addEventListener('mouseup', onMouseWindowUp);
        }
      };

      const updatePan = (inputType, clientX, clientY, pointerId = null) => {
        if (!isPanning || panInputType !== inputType) return;
        if (inputType === 'pointer' && activePanPointerId !== pointerId) return;
        const scale = zoomState.scale || 1;
        zoomState.panX = panOriginX + ((clientX - panStartX) / scale);
        zoomState.panY = panOriginY + ((clientY - panStartY) / scale);
        applyTransform();
      };

      const finishPan = (inputType, pointerId = null) => {
        if (!isPanning || panInputType !== inputType) return;
        if (inputType === 'pointer' && activePanPointerId !== null && pointerId !== activePanPointerId) return;
        if (inputType === 'pointer' && svg.releasePointerCapture && activePanPointerId !== null) {
          try {
            if (svg.hasPointerCapture && svg.hasPointerCapture(activePanPointerId)) {
              svg.releasePointerCapture(activePanPointerId);
            }
          } catch (err) {}
        }
        isPanning = false;
        panInputType = null;
        activePanPointerId = null;
        if (inputType === 'mouse') {
          window.removeEventListener('mousemove', onMouseWindowMove);
          window.removeEventListener('mouseup', onMouseWindowUp);
        }
        svg.style.cursor = 'grab';
        saveDiagramState(currentDiagramIndex);
        applyTransform();
      };

      svg.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        beginPan('pointer', e.clientX, e.clientY, e.pointerId);
        if (svg.setPointerCapture) {
          try {
            svg.setPointerCapture(e.pointerId);
          } catch (err) {}
        }
        e.preventDefault();
      });

      svg.addEventListener('pointermove', (e) => {
        updatePan('pointer', e.clientX, e.clientY, e.pointerId);
      });

      svg.addEventListener('pointerup', (e) => finishPan('pointer', e.pointerId));
      svg.addEventListener('pointercancel', (e) => finishPan('pointer', e.pointerId));
      svg.addEventListener('lostpointercapture', (e) => finishPan('pointer', e.pointerId));

      svg.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        beginPan('mouse', e.clientX, e.clientY);
        e.preventDefault();
      });

      svg.addEventListener('mousemove', (e) => {
        updatePan('mouse', e.clientX, e.clientY);
      });

      svg.addEventListener('mouseup', () => finishPan('mouse'));
      svg.addEventListener('mouseleave', () => finishPan('mouse'));

      // Mouse wheel zoom
      svg.addEventListener('wheel', (e) => {
        e.preventDefault();
        const currentScale = zoomState.scale || 1;
        const wheelStep = Math.max(0.004, Math.min(0.015, 0.02 / currentScale));
        const delta = e.deltaY > 0 ? (1 - wheelStep) : (1 + wheelStep);
        const newScale = currentScale * delta;
        
        if (newScale >= zoomState.minScale && newScale <= zoomState.maxScale) {
          // Calculate zoom center point
          const rect = svg.getBoundingClientRect();
          const useCenterAnchor = currentScale >= 2;
          const x = useCenterAnchor ? rect.width / 2 : (e.clientX - rect.left);
          const y = useCenterAnchor ? rect.height / 2 : (e.clientY - rect.top);
          
          // Adjust pan to zoom towards cursor
          zoomState.panX += (x * (1 - delta)) / (currentScale * delta);
          zoomState.panY += (y * (1 - delta)) / (currentScale * delta);
          zoomState.scale = newScale;
          applyTransform();
          saveDiagramState(currentDiagramIndex);
        }
      });

      svg.style.cursor = 'grab';
    });
  }


  // Clear the log output
  function clearLog() {
    if (logOutput) {
      logOutput.innerHTML = '';
      logOutput.scrollTop = 0;
    }
    setLogAutoScrollEnabled(true, false);
    // Hide section placeholder when starting a new scan
    const placeholder = document.getElementById('section-placeholder');
    if (placeholder) {
      placeholder.style.display = 'none';
    }
  }

  function updateLogAutoScrollButton() {
    if (!logAutoScrollBtn) return;
    if (logAutoScrollEnabled) {
      logAutoScrollBtn.textContent = '⏸ Auto-scroll';
      logAutoScrollBtn.title = 'Pause auto-scroll';
    } else {
      logAutoScrollBtn.textContent = '▶ Resume auto-scroll';
      logAutoScrollBtn.title = 'Resume auto-scroll';
    }
  }

  function setLogAutoScrollEnabled(enabled, scrollToBottom) {
    logAutoScrollEnabled = !!enabled;
    if (logAutoScrollEnabled && scrollToBottom && logOutput) {
      requestAnimationFrame(() => {
        if (logOutput) {
          logOutput.scrollTop = logOutput.scrollHeight;
        }
      });
    }
    updateLogAutoScrollButton();
  }

  // Detect phase in message and return CSS class
  function detectPhaseClass(text) {
    if (typeof text !== 'string') return null;
    const phaseMatch = text.match(/(?:▶\s*)?(?:PHASE|Phase)\s*(\d+[a-z]?)/i);
    if (phaseMatch) {
      return `phase-${phaseMatch[1].toLowerCase()}`;
    }
    return null;
  }

  function detectLogSourceClass(text) {
    if (typeof text !== 'string') return null;
    const sourceMatch = text.match(/^\[(Info|Web|Pipeline|Detection|Misconfigurations|Store|AzureGoat|Error|Warning|Warn|Success)\]/i);
    if (!sourceMatch) return null;

    const tag = sourceMatch[1].toLowerCase();
    switch (tag) {
      case 'error':
        return 'error';
      case 'warning':
      case 'warn':
        return 'warn';
      case 'success':
        return 'success';
      case 'info':
        return 'info';
      case 'web':
        return 'line-web';
      case 'pipeline':
        return 'line-pipeline';
      case 'detection':
        return 'line-detection';
      case 'misconfigurations':
        return 'line-misconfigurations';
      case 'store':
        return 'line-store';
      case 'azuregoat':
        return 'line-repo';
      default:
        return `line-${tag}`;
    }
  }

  function detectSeparatorClass(text) {
    if (typeof text !== 'string') return null;
    const trimmed = text.trim();
    if (/^(?:=|─|—|-){10,}$/.test(trimmed)) {
      return 'line-sep';
    }
    return null;
  }

  // Add a line to the log
  function addLogLine(text, className) {
    if (!logOutput) return;
    const line = document.createElement('div');

    const classes = [];
    const phaseClass = detectPhaseClass(text);
    const sourceClass = detectLogSourceClass(text);
    const separatorClass = detectSeparatorClass(text);

    if (className && className !== 'info') {
      classes.push(className);
    } else if (className) {
      classes.push(className);
    }

    if (phaseClass) {
      classes.push(phaseClass);
    }

    if (sourceClass) {
      if ((sourceClass === 'error' || sourceClass === 'warn' || sourceClass === 'success') && classes.includes('info')) {
        classes.splice(classes.indexOf('info'), 1);
      }
      if (!classes.includes(sourceClass)) {
        classes.push(sourceClass);
      }
    }

    if (separatorClass) {
      classes.push(separatorClass);
    }

    if (classes.length > 0) {
      line.className = classes.join(' ');
    }

    // Preserve whitespace but escape HTML
    line.textContent = text;
    logOutput.appendChild(line);
    if (logAutoScrollEnabled) {
      requestAnimationFrame(() => {
        if (logOutput && logAutoScrollEnabled) {
          logOutput.scrollTop = logOutput.scrollHeight;
        }
      });
    }
  }

  // Handle scan form submission
  function handleScanSubmit(e) {
    e.preventDefault();
    
    const form = document.getElementById('scan-form');
    if (!form) return;

    const repoPath = (document.getElementById('repo-select')?.value || '').trim();
    if (!repoPath) {
      window._triage.setStatus('Please select a repository', 'error');
      return;
    }

    // Check if a scan is already running for this repo
    // Use canonical repo name from data-name attribute if available
    const repoSelect = document.getElementById('repo-select');
    const repoOpt = repoSelect?.querySelector('option:checked');
    const repoName = (repoOpt && repoOpt.dataset && repoOpt.dataset.name) 
      ? repoOpt.dataset.name 
      : repoPath.split('/').pop();
    fetch(`/api/scans/${repoName}`)
      .then(response => response.json())
      .then(data => {
        if (data.running_experiment) {
          // A scan is already running - show modal
          showScanModal(repoName, data.running_experiment, repoPath);
        } else {
          // No scan running - proceed normally
          startScan(repoPath);
        }
      })
      .catch(err => {
        console.log('[Stream] Could not check running scan status, proceeding:', err);
        startScan(repoPath);
      });
  }
  
  // Show modal for running experiment
  function showScanModal(repoName, experimentId, repoPath) {
    const modal = document.getElementById('scan-modal');
    const modalMsg = document.getElementById('modal-message');
    const watchBtn = document.getElementById('modal-watch');
    const newBtn = document.getElementById('modal-new');
    const cancelBtn = document.getElementById('modal-cancel');
    
    if (!modal) return;
    
    // Set message
    modalMsg.textContent = `Experiment ${experimentId} is currently running for ${repoName}.`;
    
    // Remove old event listeners by cloning
    watchBtn.replaceWith(watchBtn.cloneNode(true));
    newBtn.replaceWith(newBtn.cloneNode(true));
    cancelBtn.replaceWith(cancelBtn.cloneNode(true));
    
    // Get fresh references
    const watchBtnNew = document.getElementById('modal-watch');
    const newBtnNew = document.getElementById('modal-new');
    const cancelBtnNew = document.getElementById('modal-cancel');
    
    // Attach handlers
    watchBtnNew.onclick = () => {
      modal.style.display = 'none';
      closeEventSource();
      // Clear placeholder text but preserve past progress
      if (logOutput && logOutput.innerHTML.includes('Scan output will appear here')) {
        logOutput.innerHTML = '';
      }
      // checkForRunningScan will add appropriate messages
      checkForRunningScan(repoPath);
    };
    
    newBtnNew.onclick = () => {
      modal.style.display = 'none';
      startScan(repoPath);
    };
    
    cancelBtnNew.onclick = () => {
      modal.style.display = 'none';
    };
    
    // Show modal
    modal.style.display = 'flex';
  }

  // Start a new scan (helper function)
  function startScan(repoPath) {
    closeArchitectureAiStream();
    closeEventSource();
    clearLog();

    // Show status
    if (statusBar) statusBar.style.display = 'block';
    if (spinner) spinner.style.display = 'block';
    window._triage.setStatus('Connecting to scan stream…', '');

    // Disable submit button
    if (scanBtn) scanBtn.disabled = true;

    // Create form data
    const formData = new FormData();
    formData.append('repo_path', repoPath);

    // Start the scan with EventSource for streaming
    try {
      // Use fetch to POST the form data, then read streaming response
      fetch('/scan', {
        method: 'POST',
        body: formData,
      })
        .then((response) => {
          if (!response.ok) {
            addLogLine(`Error: HTTP ${response.status}`, 'error');
            window._triage.setStatus('Scan failed', 'error');
            if (scanBtn) scanBtn.disabled = false;
            if (statusBar) statusBar.style.display = 'none';
            if (spinner) spinner.style.display = 'none';
            return;
          }

          addLogLine('[Info] 🔌 Connected to scan stream', 'info');

          // Read the streaming response
          if (!response.body) {
            addLogLine('Error: Response body not available', 'error');
            if (scanBtn) scanBtn.disabled = false;
            return;
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let currentEvent = null;
          let chunkCount = 0;

          function processChunk() {
            reader.read().then(({ done, value }) => {
              if (done) {
                // Stream ended
                addLogLine('[Info] 🔒 Stream closed', 'info');
                window._triage.setStatus('Scan complete', 'success');
                if (scanBtn) scanBtn.disabled = false;
                if (spinner) spinner.style.display = 'none';
                return;
              }

              chunkCount++;
              if (chunkCount === 1) {
                addLogLine('[Info] 📡 Receiving data...', 'info');
              }

              // Append to buffer and process lines
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop(); // Keep incomplete line in buffer

              lines.forEach((line) => {
                // Skip empty lines that are just part of SSE framing
                if (line === '') {
                  currentEvent = null;
                  return;
                }

                // Handle SSE event type
                if (line.startsWith('event: ')) {
                  currentEvent = line.slice(7).trim();
                  return;
                }

                // Handle SSE data
                if (line.startsWith('data: ')) {
                  const dataStr = line.slice(6);
                  try {
                    // Parse the JSON string value
                    const message = JSON.parse(dataStr);
                    if (typeof message === 'string') {
                      addLogLine(message, currentEvent || '');
                    } else if (message && typeof message === 'object') {
                      if (message.message) {
                        addLogLine(message.message, message.level || currentEvent || '');
                      }
                      // Handle special event types
                      if (currentEvent === 'done') {
                        window._triage.setStatus('Scan complete', 'success');
                        const expId = message.experiment_id || currentExperimentId;
                        if (expId) {
                          const repoName = getCurrentRepoName();
                          buildSectionTabs(expId, repoName);
                        }
                      }
                    }
                    // Handle experiment id event
                    if (currentEvent === 'experiment' && typeof message === 'string' && message) {
                      currentExperimentId = message;
                    }
                    // Handle diagrams event — render mermaid in diagram panel
                    if (currentEvent === 'diagrams' && Array.isArray(message)) {
                      renderDiagrams(message);
                    }
                    if (message && message.status) {
                      window._triage.setStatus(message.status, '');
                    }
                  } catch (e) {
                    // If not JSON, just add it as text
                    addLogLine(dataStr, currentEvent || '');
                  }
                  currentEvent = null;
                  return;
                }
              });

              processChunk();
            });
          }

          window._triage.setStatus('Scan running…', '');
          processChunk();
        })
        .catch((error) => {
          addLogLine(`Connection error: ${error.message}`, 'error');
          window._triage.setStatus('Connection failed', 'error');
          if (scanBtn) scanBtn.disabled = false;
          if (statusBar) statusBar.style.display = 'none';
          if (spinner) spinner.style.display = 'none';
        });
    } catch (error) {
      addLogLine(`Error: ${error.message}`, 'error');
      window._triage.setStatus('Error starting scan', 'error');
      if (scanBtn) scanBtn.disabled = false;
      if (statusBar) statusBar.style.display = 'none';
      if (spinner) spinner.style.display = 'none';
    }
  }

  // Handle reset button
  function handleReset() {
    closeArchitectureAiStream();
    closeEventSource();
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    clearLog();
    currentExperimentId = null;
    if (statusBar) statusBar.style.display = 'none';
    if (spinner) spinner.style.display = 'none';
    if (logOutput) {
      logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
      logOutput.scrollTop = 0;
    }
    setLogAutoScrollEnabled(true, false);
    // Reset section tabs to placeholder and switch back to log view
    const tabBar = document.getElementById('section-tab-bar');
    if (tabBar) {
      tabBar.innerHTML = '<span id="tab-bar-placeholder" style="padding:8px 14px;font-size:0.75rem;color:var(--text-faint)">Run or load a scan to see sections</span>';
    }
    const panelContent = document.getElementById('section-panel-content');
    if (panelContent) {
      panelContent.innerHTML = '<div class="empty-state" id="section-placeholder"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7h18M3 12h18M3 17h18"/></svg><p>Run or load a scan to view<br/>section details.</p></div>';
    }
    showLogView();
    window._triage.setStatus('Ready', '');
  }

  // Check if a scan is running for the selected repo and auto-reconnect
  function checkForRunningScan(repoPath) {
    if (!repoPath) return;
    
    // Use canonical repo name from data-name attribute if available
    const repoSelect = document.getElementById('repo-select');
    const repoOpt = repoSelect?.querySelector('option:checked');
    const repoName = (repoOpt && repoOpt.dataset && repoOpt.dataset.name) 
      ? repoOpt.dataset.name 
      : repoPath.split('/').pop();
    fetch(`/api/scans/${repoName}`)
      .then(response => response.json())
      .then(data => {
        if (data.running_experiment) {
          // Auto-reconnect to running scan
          closeEventSource();
          // Clear log placeholder text and preserve any past progress
          if (logOutput && logOutput.innerHTML.includes('Scan output will appear here')) {
            logOutput.innerHTML = '';
            logOutput.scrollTop = 0;
          }
          setLogAutoScrollEnabled(true, false);
          // Hide section placeholder when reconnecting
          const sectionPlaceholder = document.getElementById('section-placeholder');
          if (sectionPlaceholder) {
            sectionPlaceholder.style.display = 'none';
          }
          addLogLine(`[Info] 🔄 Reconnecting to running experiment ${data.running_experiment}...`, 'info');
          if (statusBar) statusBar.style.display = 'block';
          if (spinner) spinner.style.display = 'block';
          window._triage.setStatus(`Reconnecting to experiment ${data.running_experiment}...`, '');
          
          // Start receiving streaming output for the running experiment
          // Since the scan is already running on the server, we just need to get the output
          // by making a request to /scan which will detect the running experiment via lock file
          reconnectToRunningExperiment(repoPath, data.running_experiment, data.running_experiment_created_at);
        }
      })
      .catch(err => console.log('[Stream] Could not check running scan status:', err));
  }

  // Reconnect to a running experiment by polling for its status
  function reconnectToRunningExperiment(repoPath, experimentId, createdAt) {
    // Use canonical repo name from data-name attribute if available
    const repoSelect = document.getElementById('repo-select');
    const repoOpt = repoSelect?.querySelector('option:checked');
    const repoName = (repoOpt && repoOpt.dataset && repoOpt.dataset.name) 
      ? repoOpt.dataset.name 
      : repoPath.split('/').pop();
    
    // Cancel any existing polling to prevent duplicates
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    
    addLogLine('[Info] ⏳ Scan already in progress on server', 'info');
    addLogLine(`[Info] 🧪 Experiment ID: ${experimentId}`, 'info');

    // Fetch and display historical log from disk before starting poll
    fetch(`/api/scan_log/${encodeURIComponent(repoName)}`)
      .then(r => r.json())
      .then(data => {
        const lines = Array.isArray(data.lines) ? data.lines : [];
        if (lines.length > 0) {
          addLogLine('[Info] 📜 --- Log from scan start ---', 'info');
          lines.forEach(line => addLogLine(line, ''));
          addLogLine('[Info] 📜 --- End of historical log ---', 'info');
        }
      })
      .catch(() => {})
      .finally(() => {
        addLogLine('[Info] ⏳ Waiting for scan to complete...', 'info');
        addLogLine('[Info] 📡 Polling server every 5 seconds...', 'info');
        _startReconnectPoll(repoName, experimentId, createdAt);
      });

    window._triage.setStatus('Scan in progress…', '');
    if (statusBar) statusBar.style.display = 'block';
  }

  function _startReconnectPoll(repoName, experimentId, createdAt) {
    // Poll for scan completion with timeout detection
    let pollCount = 0;
    const startTime = createdAt ? new Date(createdAt) : new Date();  // Use actual start time if available
    
    // Show initial elapsed time immediately on reconnect
    if (createdAt) {
      const now = new Date();
      const elapsedMs = now - startTime;
      const elapsedSec = Math.floor(elapsedMs / 1000);
      const elapsedMin = Math.floor(elapsedSec / 60);
      const remainingSec = elapsedSec % 60;
      if (elapsedMin > 0) {
        addLogLine(`[Web] Scan in progress — elapsed ${elapsedMin}m ${remainingSec}s (est. 4-5 min remaining)`, 'info');
      } else {
        addLogLine(`[Web] Scan in progress — elapsed ${elapsedSec}s (est. 4-5 min remaining)`, 'info');
      }
    }
    
    currentPollInterval = setInterval(() => {
      pollCount++;
      
      // Calculate actual elapsed time from scan start
      const now = new Date();
      const elapsedMs = now - startTime;
      const elapsedSec = Math.floor(elapsedMs / 1000);
      const elapsedMin = Math.floor(elapsedSec / 60);
      const remainingSec = elapsedSec % 60;
      
      // Safety check: if polling for more than 7 min without completion, assume process crashed
      if (elapsedSec > 420) {  // 7 minutes in seconds
        clearInterval(currentPollInterval);
        currentPollInterval = null;
        addLogLine('[Error] ❌ Scan appears to have stalled or crashed (no completion after 7+ minutes)', 'error');
        addLogLine('[Error] ⚠️ Lock file may be stale. You can try starting a new scan.', 'error');
        window._triage.setStatus('Scan timeout', 'error');
        if (spinner) spinner.style.display = 'none';
        if (scanBtn) scanBtn.disabled = false;
        return;
      }
      
      fetch(`/api/scans/${repoName}`)
        .then(response => response.json())
        .then(data => {
          // Check if this experiment is still running
          if (!data.running_experiment) {
            // Scan completed!
            clearInterval(currentPollInterval);
            currentPollInterval = null;
            addLogLine('[Info] ✅ Scan complete!', 'info');
            window._triage.setStatus('Scan complete', 'success');
            if (spinner) spinner.style.display = 'none';
            if (scanBtn) scanBtn.disabled = false;
            
            // Show completed message with duration
            if (elapsedMin > 0) {
              addLogLine(`[Info] ⏱️ Total time: ${elapsedMin}m ${remainingSec}s`, 'info');
            } else {
              addLogLine(`[Info] ⏱️ Total time: ${elapsedSec}s`, 'info');
            }

            // Load section tabs now that scan is done
            const expId = currentExperimentId || experimentId;
            if (expId) buildSectionTabs(expId, repoName);
          }else if (pollCount % 6 === 0) {
            // Show progress update every 30 seconds (6 * 5 sec intervals)
            if (elapsedMin > 0) {
              addLogLine(`[Web] Scan in progress — elapsed ${elapsedMin}m ${remainingSec}s (est. 4-5 min remaining)`, 'info');
            } else {
              addLogLine(`[Web] Scan in progress — elapsed ${elapsedSec}s (est. 4-5 min remaining)`, 'info');
            }
          }
        })
        .catch(err => {
          // Log errors but don't stop polling
          if (pollCount === 1) {
            console.log('[Reconnect] Error checking scan status:', err);
          }
        });
    }, 5000); // Poll every 5 seconds
    
    if (spinner) spinner.style.display = 'none';
  }

  // Switch the log panel to show sections (hide raw log)
  function showSectionsView() {
    const tabBar = document.getElementById('section-tab-bar');
    const panelContent = document.getElementById('section-panel-content');
    const logOut = document.getElementById('log-output');
    const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
    if (tabBar) tabBar.style.display = 'flex';
    if (panelContent) panelContent.style.display = '';
    if (logOut) logOut.style.display = 'none';
    if (toggleSectionsBtn) { toggleSectionsBtn.title = 'Show log'; toggleSectionsBtn.textContent = '📜 Log'; }
  }

  // Switch the log panel to show raw log (hide sections)
  function showLogView() {
    const tabBar = document.getElementById('section-tab-bar');
    const panelContent = document.getElementById('section-panel-content');
    const logOut = document.getElementById('log-output');
    const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
    if (tabBar) tabBar.style.display = 'none';
    if (panelContent) panelContent.style.display = 'none';
    if (logOut) logOut.style.display = '';
    if (toggleSectionsBtn) { toggleSectionsBtn.title = 'Show sections'; toggleSectionsBtn.textContent = '📑 Sections'; }
  }

  function setDiagramPlaceholderVisible(visible) {
    const diagramViews = document.getElementById('diagram-views');
    if (!diagramViews) return;

    diagramViews.classList.toggle('has-diagrams', !visible);
    const placeholder = diagramViews.querySelector('#diagram-placeholder, .diagram-placeholder, .empty-state');
    if (placeholder) {
      placeholder.hidden = !visible;
      if (visible) {
        placeholder.removeAttribute('aria-hidden');
      } else {
        placeholder.setAttribute('aria-hidden', 'true');
      }
    }
  }

  // ── Diagram rendering ────────────────────────────────────────────────────

  function renderDiagrams(diagrams) {
    console.log('[renderDiagrams] Called with:', diagrams);
    if (!Array.isArray(diagrams) || !diagrams.length) {
      console.log('[renderDiagrams] No diagrams or not an array');
      return;
    }

    const diagramViews = document.getElementById('diagram-views');
    const diagramTabs = document.getElementById('diagram-tabs');
    
    if (!diagramViews || !diagramTabs) {
      console.error('[renderDiagrams] Missing required elements!');
      
      // Diagnostic: log the entire page structure
      console.log('[renderDiagrams] DIAGNOSTICS:');
      console.log('  - diagram-views element:', diagramViews ? '✓ FOUND' : '✗ NOT FOUND');
      console.log('  - diagram-tabs element:', diagramTabs ? '✓ FOUND' : '✗ NOT FOUND');
      console.log('  - diagram-panel:', document.getElementById('diagram-panel') ? '✓ FOUND' : '✗ NOT FOUND');
      console.log('  - diagram-zoom-wrap:', document.getElementById('diagram-zoom-wrap') ? '✓ FOUND' : '✗ NOT FOUND');
      console.log('  - split-right:', document.querySelector('.split-right') ? '✓ FOUND' : '✗ NOT FOUND');
      
      // Log all elements with "diagram" in the id
      const diagramElements = document.querySelectorAll('[id*="diagram"]');
      console.log('  - All diagram-related elements:', diagramElements.length);
      diagramElements.forEach(el => {
        console.log('    - ID:', el.id, 'Tag:', el.tagName, 'Classes:', el.className);
      });
      
      return;
    }

    Object.keys(diagramStates).forEach((key) => delete diagramStates[key]);
    currentDiagramIndex = 0;

    // Hide the empty-state message as soon as real diagrams are being rendered.
    setDiagramPlaceholderVisible(false);
    const placeholder = diagramViews.querySelector('#diagram-placeholder, .diagram-placeholder, .empty-state');
    if (placeholder) placeholder.remove();

    // Clear existing dynamic content
    diagramTabs.innerHTML = '';
    diagramViews.querySelectorAll('.diagram-view').forEach(el => el.remove());

    let addedCount = 0;
    diagrams.forEach((diag, idx) => {
      const title = diag.title || `Diagram ${idx + 1}`;
      const code = (diag.code || '').trim();
      if (!code) {
        console.warn('[renderDiagrams] Skipping diagram', idx, 'no code');
        return;
      }

      addedCount++;
      // Create tab button
      const tabBtn = document.createElement('button');
      tabBtn.className = 'btn-small' + (idx === 0 ? ' active' : '');
      tabBtn.dataset.idx = idx;
      tabBtn.textContent = title;
      tabBtn.style.marginRight = '4px';
      diagramTabs.appendChild(tabBtn);

      // Create diagram view
      const viewDiv = document.createElement('div');
      viewDiv.className = 'diagram-view' + (idx === 0 ? ' active' : '');
      viewDiv.dataset.idx = idx;

      const pre = document.createElement('pre');
      pre.className = 'mermaid';
      pre.style.background = 'transparent';
      pre.dataset.source = code;
      pre.textContent = code;
      viewDiv.appendChild(pre);
      diagramViews.appendChild(viewDiv);

      tabBtn.addEventListener('click', () => {
        // Save current diagram state before switching
        saveDiagramState(currentDiagramIndex);
        
        diagramTabs.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        tabBtn.classList.add('active');
        diagramViews.querySelectorAll('.diagram-view').forEach(v => {
          v.classList.toggle('active', v.dataset.idx === String(idx));
        });
        
        // Load new diagram state
        currentDiagramIndex = idx;
        loadDiagramState(idx);

        // Re-inject icons for the newly visible diagram tab (in case it wasn't
        // visible during initial injection and getBBox returned zeros).
        if (window.MermaidIconInjector) {
          setTimeout(() => MermaidIconInjector.processAllDiagrams(), 100);
        }
      });
    });

    console.log('[renderDiagrams] Added', addedCount, 'diagrams to UI');

    // Render mermaid
    if (window.mermaid) {
      try {
        window.mermaid.initialize({ 
          startOnLoad: false, 
          theme: 'dark',
          securityLevel: 'loose',
          onError: (err) => {
            console.error('[Mermaid] Rendering error:', err.message);
          }
        });
        
        // Log each diagram before rendering
        diagramViews.querySelectorAll('.mermaid').forEach((elem, idx) => {
          const codeLen = elem.textContent.length;
          const first50 = elem.textContent.substring(0, 50).replace(/\n/g, ' ');
          console.log(`[renderDiagrams] Diagram ${idx}: ${codeLen} bytes, starts with: "${first50}..."`);
        });
        
        window.mermaid.init(undefined, diagramViews.querySelectorAll('.mermaid'));
        // Initialize pan/zoom after rendering, then inject icons
        setTimeout(() => {
          initPanZoom();
          // Reset zoom and center for first diagram once layout has settled
          currentDiagramIndex = 0;
          scheduleDiagramFit();
        }, 150);
        setTimeout(() => { if (window.MermaidIconInjector) MermaidIconInjector.processAllDiagrams(); }, 400);
      } catch (e) {
        console.warn('[Diagrams] Mermaid render error:', e);
        console.error('[Diagrams] Full error stack:', e.stack);
      }
    } else {
      // Wait for mermaid to load then re-render
      const waitForMermaid = setInterval(() => {
        if (window.mermaid) {
          clearInterval(waitForMermaid);
          try {
            window.mermaid.initialize({ 
              startOnLoad: false, 
              theme: 'dark',
              securityLevel: 'loose',
              onError: (err) => {
                console.error('[Mermaid] Rendering error:', err.message);
              }
            });
            window.mermaid.init(undefined, diagramViews.querySelectorAll('.mermaid'));
            // Initialize pan/zoom after rendering, then inject icons
            setTimeout(() => {
              initPanZoom();
              // Reset zoom and center for first diagram once layout has settled
              currentDiagramIndex = 0;
              scheduleDiagramFit();
            }, 150);
            setTimeout(() => { if (window.MermaidIconInjector) MermaidIconInjector.processAllDiagrams(); }, 400);
          } catch (e) {
            console.error('[Diagrams] Mermaid render error:', e);
          }
        }
      }, 300);
      setTimeout(() => clearInterval(waitForMermaid), 10000);
    }
  }

  // Expose so past scan loading can also trigger diagrams
  window._triage.renderDiagrams = renderDiagrams;

  // Also wire up renderDiff (used by compare feature)
  window._triage.renderDiff = window._triage.renderDiff || function(data) {
    if (data && data.diagrams) renderDiagrams(data.diagrams);
  };
  window.renderDiff = window._triage.renderDiff;

  // ── Diagram button handlers ──────────────────────────────────────────────

  function showToast(message) {
    const existingToast = document.querySelector('.diagram-toast');
    if (existingToast) {
      existingToast.remove();
    }

    const toast = document.createElement('div');
    toast.className = 'diagram-toast';
    toast.textContent = message;
    toast.style.cssText = `
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: #1e293b;
      color: #e2e8f0;
      padding: 12px 16px;
      border-radius: 6px;
      border: 1px solid #475569;
      z-index: 1000;
      font-size: 14px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      animation: slideIn 0.3s ease-out;
    `;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
      toast.style.animation = 'fadeOut 0.3s ease-out';
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  function getActiveDiagramView() {
    const diagramViews = document.getElementById('diagram-views');
    if (!diagramViews) return null;
    return diagramViews.querySelector('.diagram-view.active');
  }

  function getDiagramSource(diagramView) {
    if (!diagramView) return null;
    const preEl = diagramView.querySelector('pre.mermaid');
    if (!preEl) return null;
    return preEl.dataset.source || preEl.textContent || '';
  }

  function refreshDiagram() {
    const activeDiagram = getActiveDiagramView();
    if (!activeDiagram) {
      showToast('No active diagram');
      return;
    }

    const source = getDiagramSource(activeDiagram);
    if (!source) {
      showToast('No diagram source found');
      return;
    }

    try {
      const preEl = activeDiagram.querySelector('pre.mermaid');
      if (!preEl) return;

      // Store zoom/pan state if it exists
      const zoomWrap = document.getElementById('diagram-zoom-wrap');
      const savedZoomState = {
        scale: zoomState.scale,
        panX: zoomState.panX,
        panY: zoomState.panY
      };
      const savedScrollState = zoomWrap ? {
        scrollLeft: zoomWrap.scrollLeft,
        scrollTop: zoomWrap.scrollTop
      } : null;

      // Clear rendered diagram by removing SVG
      const svg = preEl.nextElementSibling;
      if (svg && svg.tagName === 'svg') {
        svg.remove();
      }

      // Mermaid mutates the <pre>; restore raw source before re-rendering.
      preEl.removeAttribute('data-processed');
      preEl.textContent = source;

      // Re-initialize mermaid on this element
      if (window.mermaid) {
        window.mermaid.init(undefined, preEl);
        
        // Restore zoom/pan state
        if (savedScrollState && zoomWrap) {
          setTimeout(() => {
            zoomWrap.scrollLeft = savedScrollState.scrollLeft;
            zoomWrap.scrollTop = savedScrollState.scrollTop;
            zoomState.scale = savedZoomState.scale;
            zoomState.panX = savedZoomState.panX;
            zoomState.panY = savedZoomState.panY;
            initPanZoom();
            applyTransform();
          }, 100);
        }
        
        showToast('Diagram refreshed');
      }
    } catch (err) {
      console.error('[Diagram] Refresh error:', err);
      showToast('Error refreshing diagram');
    }
  }

  function copyDiagramSource() {
    const activeDiagram = getActiveDiagramView();
    if (!activeDiagram) {
      showToast('No active diagram');
      return;
    }

    const source = getDiagramSource(activeDiagram);
    if (!source) {
      showToast('No diagram source found');
      return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(source)
        .then(() => {
          showToast('Copied to clipboard');
        })
        .catch(err => {
          console.error('[Diagram] Copy error:', err);
          showToast('Copy failed');
        });
    } else {
      // Fallback for older browsers
      try {
        const textarea = document.createElement('textarea');
        textarea.value = source;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('Copied to clipboard');
      } catch (err) {
        console.error('[Diagram] Fallback copy error:', err);
        showToast('Copy failed');
      }
    }
  }

  function _showDiagramNotReady(message) {
    const placeholder = document.getElementById('diagram-placeholder');
    const diagramViews = document.getElementById('diagram-views');
    if (placeholder) {
      placeholder.innerHTML = `
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <p style="color:var(--text-muted)">${message}</p>
        <button class="btn-small retry-diagram-btn" style="margin-top:8px">↺ Retry</button>`;
      placeholder.style.removeProperty('display');
    }
    if (diagramViews) {
      Array.from(diagramViews.querySelectorAll('.diagram-view')).forEach(v => v.remove());
    }
    const tabsEl = document.getElementById('diagram-tabs');
    if (tabsEl) tabsEl.innerHTML = '';
  }

  function refetchDiagramsWithApiOpsMode(forceRefresh = false) {
    // Fetch and render diagrams for the current experiment with API ops filtering.
    const experimentId = currentExperimentId;
    const repoName = currentRepoName || getCurrentRepoName();
    
    if (!experimentId || !repoName) {
      console.log('[Diagrams] No experiment or repo to load');
      return;
    }

    // Determine include_api_operations parameter based on apiOpsMode
    let includeApiOps = undefined;
    if (apiOpsMode === 'all') {
      includeApiOps = '1';
    } else if (apiOpsMode === 'hide') {
      includeApiOps = '0';
    }
    // For 'auto', don't override (let server decide)

    // Build URL with parameters
    const url = new URL(`/api/diagrams/${encodeURIComponent(experimentId)}`, window.location.origin);
    url.searchParams.set('repo_name', repoName);
    if (includeApiOps !== undefined) {
      url.searchParams.set('include_api_operations', includeApiOps);
    }

    fetch(url.toString())
      .then(r => {
        if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
        return r.json();
      })
      .then(data => {
        console.log('[Diagrams] Response:', data);
        if (data && data.diagrams && data.diagrams.length > 0) {
          console.log('[Diagrams] Found', data.diagrams.length, 'diagrams');
          storedDiagrams = data.diagrams;
          renderDiagrams(data.diagrams);
        } else {
          console.log('[Diagrams] No diagrams in response:', data);
          _showDiagramNotReady('No diagram data was returned.');
        }
      })
      .catch(err => {
        console.warn('[Diagrams] Failed to fetch:', err);
        const is404 = err.status === 404;
        _showDiagramNotReady(
          is404
            ? 'Diagram not available yet — the scan may still be in progress.'
            : `Could not load diagram (${err.message}).`
        );
      });
  }

  function updateApiOpsButtonText() {
    const btn = document.getElementById('toggle-api-ops-btn');
    if (!btn) return;
    
    let buttonText = '🧩 API ops: ';
    if (apiOpsMode === 'auto') {
      buttonText += 'Auto (<10)';
    } else if (apiOpsMode === 'all') {
      buttonText += 'All';
    } else if (apiOpsMode === 'hide') {
      buttonText += 'Hidden';
    }
    btn.textContent = buttonText;
  }

  function handleToggleApiOps() {
    // Cycle: auto -> all -> hide -> auto
    if (apiOpsMode === 'auto') {
      apiOpsMode = 'all';
    } else if (apiOpsMode === 'all') {
      apiOpsMode = 'hide';
    } else {
      apiOpsMode = 'auto';
    }

    updateApiOpsButtonText();
    refetchDiagramsWithApiOpsMode();
  }

  function runArchitectureAiReview() {
    const experimentId = currentExperimentId;
    const repoName = currentRepoName || getCurrentRepoName();
    if (!experimentId || !repoName) {
      showToast('Load a completed scan first');
      return;
    }

    if (!document.querySelector('#diagram-views svg')) {
      showToast('Load a completed scan first');
      return;
    }

    closeArchitectureAiStream();
    architectureAiStopInFlight = false;
    updateArchitectureAiButton(true);

    if (window._triage && typeof window._triage.showLog === 'function') {
      window._triage.showLog();
    }
    if (window._triage && typeof window._triage.setStatus === 'function') {
      window._triage.setStatus('Running architecture AI…', 'info');
    }
    if (window._triage && typeof window._triage.setStatusBusy === 'function') {
      window._triage.setStatusBusy(true);
    }
    if (window._triage && typeof window._triage.appendLog === 'function') {
      window._triage.appendLog(`[Architecture] Starting analysis for ${repoName} (experiment ${experimentId})`);
    }

    if (window._triage && typeof window._triage.setToolbarStopState === 'function') {
      window._triage.setToolbarStopState({
        enabled: true,
        visible: true,
        label: '⏹ Stop Architecture AI',
      });
    }

    if (window._triage && typeof window._triage.registerToolbarStop === 'function') {
      window._triage.registerToolbarStop(async () => {
        if (architectureAiStopInFlight) return;
        architectureAiStopInFlight = true;

        if (window._triage && typeof window._triage.setToolbarStopState === 'function') {
          window._triage.setToolbarStopState({
            enabled: false,
            visible: true,
            label: '⏳ Stopping Architecture AI…',
          });
        }

        try {
          const resp = await fetch(
            `/api/analysis/stop/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`,
            { method: 'POST' }
          );
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) {
            throw new Error(data.error || `HTTP ${resp.status}`);
          }
          if (window._triage && typeof window._triage.appendLog === 'function') {
            window._triage.appendLog('[Architecture] Stop requested');
          }
          streamDone = true;
          clearPolling();
          closeArchitectureAiStream();
          if (window._triage && typeof window._triage.setStatus === 'function') {
            window._triage.setStatus('Stopped', 'warn');
          }
        } catch (err) {
          architectureAiStopInFlight = false;
          if (window._triage && typeof window._triage.setToolbarStopState === 'function') {
            window._triage.setToolbarStopState({
              enabled: true,
              visible: true,
              label: '⏹ Stop Architecture AI',
            });
          }
          if (window._triage && typeof window._triage.setStatus === 'function') {
            window._triage.setStatus('Stop failed', 'error');
          }
          if (window._triage && typeof window._triage.appendLog === 'function') {
            window._triage.appendLog(`[Architecture] Stop failed: ${err.message}`);
          }
        }
      });
    }

    const streamUrl = `/api/analysis/copilot/stream/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}?mode=architecture`;
    if (window._triage && typeof window._triage.appendLog === 'function') {
      window._triage.appendLog('[Architecture] Connecting stream...');
    }

    let streamDone = false;
    let pollTimer = null;

    const clearPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const finishSuccess = () => {
      streamDone = true;
      clearPolling();
      closeArchitectureAiStream();
      if (window._triage && typeof window._triage.setStatus === 'function') {
        window._triage.setStatus('Completed', 'success');
      }
      if (window._triage && typeof window._triage.appendLog === 'function') {
        window._triage.appendLog('[Architecture] Architecture review completed');
      }
      refetchDiagramsWithApiOpsMode(true);
    };

    const finishFailure = (detail) => {
      streamDone = true;
      clearPolling();
      closeArchitectureAiStream();
      if (window._triage && typeof window._triage.setStatus === 'function') {
        window._triage.setStatus(detail || 'Architecture AI failed', 'error');
      }
      if (window._triage && typeof window._triage.appendLog === 'function' && detail) {
        window._triage.appendLog(`[Architecture] ${detail}`);
      }
    };

    const pollStatus = async () => {
      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const data = await resp.json().catch(() => ({}));
        if (data.status === 'running') {
          const label = (data.active_agent_label || data.active_agent || 'Architecture validation').toString();
          if (window._triage && typeof window._triage.setStatus === 'function') {
            window._triage.setStatus(`Running: ${label}…`, 'info');
          }
          if (window._triage && typeof window._triage.setStatusBusy === 'function') {
            window._triage.setStatusBusy(true);
          }
          if (window._triage && typeof window._triage.setToolbarStopState === 'function') {
            window._triage.setToolbarStopState({
              enabled: true,
              visible: true,
              label: '⏹ Stop Architecture AI',
            });
          }
          return;
        }
        if (data.status === 'completed') {
          finishSuccess();
          return;
        }
        if (data.status === 'failed' || data.status === 'stopped') {
          finishFailure((data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString());
          return;
        }
      } catch (err) {
        if (window._triage && typeof window._triage.setStatus === 'function') {
          window._triage.setStatus('Architecture status check failed', 'warn');
        }
      }
    };

    architectureAiStream = new EventSource(streamUrl);

    architectureAiStream.addEventListener('log', (evt) => {
      try {
        const line = JSON.parse(evt.data);
        if (window._triage && typeof window._triage.appendLog === 'function') {
          window._triage.appendLog(`[Architecture] ${line}`);
        }
      } catch (err) {}
    });

    architectureAiStream.addEventListener('done', () => {
      finishSuccess();
    });

    architectureAiStream.addEventListener('error', async () => {
      if (streamDone) {
        return;
      }

      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const data = await resp.json().catch(() => ({}));
        if (data.status === 'completed') {
          finishSuccess();
          return;
        }
        if (data.status === 'failed' || data.status === 'stopped') {
          finishFailure((data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString());
          return;
        }
      } catch (err) {}

      if (architectureAiStream) {
        try {
          architectureAiStream.close();
        } catch (closeErr) {}
        architectureAiStream = null;
      }
      clearPolling();
      pollTimer = setInterval(pollStatus, 2000);
      if (window._triage && typeof window._triage.setStatus === 'function') {
        window._triage.setStatus('Streaming disconnected (polling...)', 'warn');
      }
      if (window._triage && typeof window._triage.setStatusBusy === 'function') {
        window._triage.setStatusBusy(true);
      }
    });

    clearPolling();
    pollTimer = setInterval(pollStatus, 2000);
  }

  function exportDiagramSvg() {
    const activeDiagram = getActiveDiagramView();
    if (!activeDiagram) {
      showToast('No active diagram');
      return;
    }

    const svgElement = activeDiagram.querySelector('svg');
    if (!svgElement) {
      showToast('No diagram SVG found');
      return;
    }

    try {
      let svgString = new XMLSerializer().serializeToString(svgElement);
      
      // Add dark background rect as first element after <svg tag
      const darkBg = '#0d1111';
      svgString = svgString.replace(
        '<svg',
        `<svg><rect width="100%" height="100%" fill="${darkBg}"/>`
      );
      
      const blob = new Blob([svgString], { type: 'image/svg+xml' });
      
      const experimentId = currentExperimentId || 'unknown';
      const repoName = getCurrentRepoName() || 'diagram';
      const timestamp = Date.now();
      const filename = `diagram_${experimentId}_${repoName}_${timestamp}.svg`;
      
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      
      showToast('SVG downloaded');
    } catch (err) {
      console.error('[Diagram] SVG export error:', err);
      showToast('Error exporting SVG');
    }
  }

  function exportDiagramPNG() {
    const activeDiagram = getActiveDiagramView();
    if (!activeDiagram) {
      showToast('No active diagram');
      return;
    }

    const svg = activeDiagram.querySelector('svg');
    if (!svg) {
      showToast('No diagram SVG found');
      return;
    }

    showToast('Converting to PNG...');

    try {
      // Create a temporary div to hold the cloned SVG
      const tempDiv = document.createElement('div');
      tempDiv.style.position = 'fixed';
      tempDiv.style.left = '-9999px';
      tempDiv.style.top = '-9999px';
      tempDiv.style.backgroundColor = '#0d1111';
      tempDiv.style.padding = '20px';
      
      // Clone the SVG
      const clonedSvg = svg.cloneNode(true);
      tempDiv.appendChild(clonedSvg);
      document.body.appendChild(tempDiv);

      // Use html2canvas to render the SVG to canvas
      if (!window.html2canvas) {
        document.body.removeChild(tempDiv);
        showToast('html2canvas library not loaded');
        console.error('[Diagram] html2canvas not available');
        return;
      }

      window.html2canvas(tempDiv, {
        backgroundColor: '#0d1111',
        scale: 2,
        allowTaint: true,
        useCORS: true
      }).then(canvas => {
        // Convert canvas to PNG blob and trigger download
        canvas.toBlob((blob) => {
          if (!blob) {
            showToast('Failed to create PNG');
            document.body.removeChild(tempDiv);
            return;
          }

          // Create filename with experiment ID, repo name, and timestamp
          const experimentId = currentExperimentId || 'unknown';
          const repoName = getCurrentRepoName() || 'repo';
          const timestamp = Date.now();
          const filename = `diagram_${experimentId}_${repoName}_${timestamp}.png`;

          // Create download link and trigger
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(url);

          // Clean up temporary div
          document.body.removeChild(tempDiv);
          showToast('PNG downloaded: ' + filename);
        }, 'image/png');
      }).catch(err => {
        document.body.removeChild(tempDiv);
        console.error('[Diagram] PNG export error:', err);
        showToast('Failed to export PNG');
      });
    } catch (err) {
      console.error('[Diagram] PNG export error:', err);
      showToast('PNG export failed');
    }
  }

  // ── Section tabs system ───────────────────────────────────────────────────

  function getCurrentRepoName() {
    const sel = document.getElementById('repo-select');
    if (!sel || !sel.value) return '';
    // Prefer data-name attribute (canonical repo name from DB), fallback to path extraction
    const repoOpt = sel.querySelector('option:checked');
    if (repoOpt && repoOpt.dataset && repoOpt.dataset.name) {
      return repoOpt.dataset.name;
    }
    return sel.value.split('/').pop();
  }

  // Build tab buttons for a completed/loaded experiment
  function buildSectionTabs(experimentId, repoName) {
    if (!experimentId || !repoName) return;
    currentExperimentId = experimentId;
    currentRepoName = repoName;
    apiOpsMode = 'auto'; // Reset API ops mode when loading new experiment
    updateApiOpsButtonText(); // Update button text to reflect reset state

    fetch(`/api/view/tabs/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`)
      .then(r => r.json())
      .then(data => {
        const tabs = Array.isArray(data.tabs) ? data.tabs : [];
        if (!tabs.length) return;

        const tabBar = document.getElementById('section-tab-bar');
        if (!tabBar) return;

        // Build tab buttons
        tabBar.innerHTML = '';
        tabs.forEach((tab, idx) => {
          const btn = document.createElement('button');
          btn.className = 'section-tab-btn' + (idx === 0 ? ' active' : '');
          btn.dataset.key = tab.key;
          btn.dataset.experimentId = experimentId;
          btn.dataset.repoName = repoName;
          btn.textContent = tab.label;
          btn.addEventListener('click', function () {
            tabBar.querySelectorAll('.section-tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadSectionContent(tab.key, experimentId, repoName);
          });
          tabBar.appendChild(btn);
        });

        // Make tabs visible and switch panel to sections view
        tabBar.style.removeProperty('display');
        showSectionsView();

        // Auto-load the first tab
        if (tabs.length > 0) {
          loadSectionContent(tabs[0].key, experimentId, repoName);
        }
      })
      .catch(err => console.log('[Sections] Could not load tabs:', err));

    // Load diagrams for this experiment (needed when loading past scans)
    refetchDiagramsWithApiOpsMode(true);
  }

  // Load section content into the panel
  function loadSectionContent(key, experimentId, repoName) {
    if (!key || !experimentId || !repoName) return Promise.resolve();

    const panel = document.getElementById('section-panel-content');
    if (!panel) return Promise.resolve();

    // Show loading state
    panel.innerHTML = '<div class="section-loading"><span>⏳ Loading…</span></div>';
    panel.style.removeProperty('display');

    const url = `/api/view/${encodeURIComponent(key)}/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`;

    return fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then(html => {
        panel.innerHTML = html;
        // Run section-specific init functions on the injected content
        const initMap = {
          overview:   () => window.initOverview && window.initOverview(panel),
          tldr:       () => window.initOverview && window.initOverview(panel),
          findings:   () => window.initFindings && window.initFindings(panel),
          containers: () => window.initContainers && window.initContainers(panel),
          assets:     () => window.initAssets && window.initAssets(panel, repoName, experimentId),
          roles:      () => window.initRoles && window.initRoles(panel),
        };
        const initFn = initMap[key];
        if (initFn) {
          try { initFn(); } catch (e) { console.warn('[Sections] init error for', key, e); }
        }
        // Re-execute inline <script> blocks injected via innerHTML
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

  // Activate a section by key (find tab button and click it, or load directly)
  function activateSectionKey(key, experimentId, repoName) {
    const tabBar = document.getElementById('section-tab-bar');
    if (tabBar) {
      const btn = tabBar.querySelector(`.section-tab-btn[data-key="${key}"]`);
      if (btn) { btn.click(); return; }
    }
    if (experimentId && repoName) buildSectionTabs(experimentId, repoName);
  }

  // ── window._triage helpers used by overview.js and other modules ─────────

  window._triage.appendLog = function (text) {
    if (logOutput) addLogLine(text, 'info');
  };

  window._triage.showLog = function () {
    showLogView();
    if (logOutput) logOutput.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  window._triage.showRawLog = function () {
    window._triage.showLog();
  };

  window._triage.loadSectionContent = function (key, experimentId, repoName) {
    const tabBar = document.getElementById('section-tab-bar');
    if (tabBar) {
      tabBar.querySelectorAll('.section-tab-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.key === key);
      });
    }
    return loadSectionContent(key, experimentId, repoName);
  };

  window._triage.activateSectionKey = function (key, experimentId, repoName) {
    activateSectionKey(key, experimentId, repoName);
  };

  window._triage.setStatusBusy = function (isBusy) {
    if (spinner) spinner.style.display = isBusy ? 'block' : 'none';
  };

  window._triage.setToolbarStopState = function (opts) {
    const btn = document.getElementById('stop-ai-toolbar-btn');
    if (!btn) return;
    if (opts && opts.label) btn.textContent = opts.label;
    btn.disabled = !(opts && opts.enabled);
    btn.hidden = !(opts && opts.visible);
  };

  let _toolbarStopCallback = null;
  window._triage.registerToolbarStop = function (callback) {
    _toolbarStopCallback = callback;
    const btn = document.getElementById('stop-ai-toolbar-btn');
    if (btn && !btn.__stopBound) {
      btn.__stopBound = true;
      btn.addEventListener('click', () => { if (_toolbarStopCallback) _toolbarStopCallback(); });
    }
  };

  // ── End section tabs system ───────────────────────────────────────────────

  // Initialize on DOM ready
  function init() {
    statusBar = document.getElementById('status-bar');
    statusText = document.getElementById('status-text');
    spinner = document.getElementById('spinner');
    logOutput = document.getElementById('log-output');
    scanBtn = document.getElementById('scan-btn');

    logAutoScrollBtn = document.getElementById('toggle-log-autoscroll-btn');
    architectureAiBtn = document.getElementById('architecture-run-ai-btn');
    updateLogAutoScrollButton();
    updateArchitectureAiButton(false);

    const scanForm = document.getElementById('scan-form');

    if (scanForm) {
      scanForm.addEventListener('submit', handleScanSubmit);
    }

    if (logAutoScrollBtn) {
      logAutoScrollBtn.addEventListener('click', function () {
        const nextEnabled = !logAutoScrollEnabled;
        setLogAutoScrollEnabled(nextEnabled, nextEnabled);
      });
    }

    // Handle sections toggle button — switches between sections view and raw log view
    const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
    if (toggleSectionsBtn) {
      toggleSectionsBtn.addEventListener('click', function() {
        const tabBar = document.getElementById('section-tab-bar');
        const panelContent = document.getElementById('section-panel-content');
        const logOut = document.getElementById('log-output');
        if (!tabBar || !panelContent || !logOut) return;
        const sectionsVisible = tabBar.style.display !== 'none' &&
          window.getComputedStyle(tabBar).display !== 'none';
        if (sectionsVisible) {
          // Switch to log view
          tabBar.style.display = 'none';
          panelContent.style.display = 'none';
          logOut.style.display = '';
          toggleSectionsBtn.title = 'Show sections';
          toggleSectionsBtn.textContent = '📑 Sections';
        } else {
          // Switch to sections view
          tabBar.style.display = 'flex';
          panelContent.style.display = '';
          logOut.style.display = 'none';
          toggleSectionsBtn.title = 'Show log';
          toggleSectionsBtn.textContent = '📜 Log';
        }
      });
    }

    // Handle hide/show log panel from diagram panel toolbar
    const toggleLogBtn = document.getElementById('toggle-log-btn');
    if (toggleLogBtn) {
      toggleLogBtn.addEventListener('click', function() {
        const logPanel = document.getElementById('log-panel');
        const workspace = document.querySelector('.workspace');
        if (!logPanel) return;
        const isHidden = logPanel.style.display === 'none';
        logPanel.style.display = isHidden ? '' : 'none';
        if (workspace) {
          workspace.classList.toggle('collapsed', !isHidden);
        }
        toggleLogBtn.textContent = isHidden ? '📜 Hide scan' : '📜 Show scan';
        toggleLogBtn.title = isHidden ? 'Hide scan output' : 'Show scan output';
      });
    }

    // Handle diagram panel hide/show toggle
    const toggleDiagramBtn = document.getElementById('toggle-diagram-btn-persistent');
    if (toggleDiagramBtn) {
      toggleDiagramBtn.addEventListener('click', function () {
        const diagramPanel = document.getElementById('diagram-panel');
        const workspace = document.querySelector('.workspace');
        if (!diagramPanel || !workspace) return;

        const isHidden = diagramPanel.style.display === 'none';
        diagramPanel.style.display = isHidden ? '' : 'none';
        workspace.classList.toggle('diagram-hidden', !isHidden);
        toggleDiagramBtn.textContent = isHidden ? 'Hide diagram' : 'Show diagram';
        toggleDiagramBtn.title = isHidden ? 'Hide/show architecture diagram' : 'Show architecture diagram';
      });
    }

    // Handle zoom buttons for diagrams
    const zoomInBtn = document.getElementById('zoom-in-btn');
    if (zoomInBtn) {
      zoomInBtn.addEventListener('click', zoomIn);
    }

    const zoomOutBtn = document.getElementById('zoom-out-btn');
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener('click', zoomOut);
    }

    const zoomResetBtn = document.getElementById('zoom-reset-btn');
    if (zoomResetBtn) {
      zoomResetBtn.addEventListener('click', zoomReset);
    }

    // Handle diagram refresh button
    const refreshDiagramBtn = document.getElementById('refresh-diagram-btn');
    if (refreshDiagramBtn) {
      refreshDiagramBtn.addEventListener('click', refreshDiagram);
    }

    // Delegated handler for the "Retry" button injected by _showDiagramNotReady
    const diagramPanel = document.getElementById('diagram-panel');
    if (diagramPanel) {
      diagramPanel.addEventListener('click', e => {
        if (e.target.closest('.retry-diagram-btn')) {
          refetchDiagramsWithApiOpsMode(true);
        }
      });
    }

    const architectureAiBtnEl = document.getElementById('architecture-run-ai-btn');
    if (architectureAiBtnEl) {
      architectureAiBtnEl.addEventListener('click', runArchitectureAiReview);
    }

    // Handle diagram copy source button
    const copyDiagramBtn = document.getElementById('copy-diagram-btn');
    if (copyDiagramBtn) {
      copyDiagramBtn.addEventListener('click', copyDiagramSource);
    }

    // Handle diagram export SVG button
    const exportSvgBtn = document.getElementById('export-diagram-svg-btn');
    if (exportSvgBtn) {
      exportSvgBtn.addEventListener('click', exportDiagramSvg);
    }

    // Handle diagram PNG export button
    const exportPngBtn = document.getElementById('export-diagram-png-btn');
    if (exportPngBtn) {
      exportPngBtn.addEventListener('click', exportDiagramPNG);
    }

    // Handle API ops visibility toggle button
    const toggleApiOpsBtn = document.getElementById('toggle-api-ops-btn');
    if (toggleApiOpsBtn) {
      toggleApiOpsBtn.addEventListener('click', handleToggleApiOps);
    }

    updateApiOpsButtonText();

    // Reset zoom when switching diagram tabs
    const diagramTabs = document.getElementById('diagram-tabs');
    if (diagramTabs) {
      const observer = new MutationObserver(() => {
        const tabBtns = diagramTabs.querySelectorAll('button');
        tabBtns.forEach(btn => {
          if (!btn.__zoomListenerAttached) {
            btn.__zoomListenerAttached = true;
            btn.addEventListener('click', zoomReset);
          }
        });
      });
      observer.observe(diagramTabs, { childList: true });
    }

    // Handle page visibility changes (mobile tab switch)
    document.addEventListener('visibilitychange', function() {
      if (document.hidden) {
        console.log('[Stream] Page hidden');
      } else {
        console.log('[Stream] Page visible - stream should resume if active');
      }
    });

    // Handle repo selection change - reset UI and auto-reconnect to running scans
    const repoSelect = document.getElementById('repo-select');
    if (repoSelect) {
      repoSelect.addEventListener('change', function() {
        if (this.value) {
          // Reset log output
          closeArchitectureAiStream();
          closeEventSource();
          if (currentPollInterval) {
            clearInterval(currentPollInterval);
            currentPollInterval = null;
          }
          if (logOutput) {
            logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
            logOutput.scrollTop = 0;
          }
          setLogAutoScrollEnabled(true, false);
          if (statusBar) statusBar.style.display = 'none';
          if (spinner) spinner.style.display = 'none';
          window._triage.setStatus('Ready', '');

          // Reset section tabs to placeholder state
          const tabBar = document.getElementById('section-tab-bar');
          if (tabBar) {
            tabBar.innerHTML = '<span id="tab-bar-placeholder" style="padding:8px 14px;font-size:0.75rem;color:var(--text-faint)">Run or load a scan to see sections</span>';
          }
          const panelContent = document.getElementById('section-panel-content');
          if (panelContent) {
            panelContent.innerHTML = '<div class="empty-state" id="section-placeholder"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7h18M3 12h18M3 17h18"/></svg><p>Run or load a scan to view<br/>section details.</p></div>';
          }

          // Clear architecture diagrams and tabs
          const diagramViews = document.getElementById('diagram-views');
          if (diagramViews) {
            diagramViews.innerHTML = '<div class="empty-state"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="8"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg><p>Run or load a scan to view<br/>architecture diagrams.</p></div>';
            setDiagramPlaceholderVisible(true);
          }
          const diagramTabs = document.getElementById('diagram-tabs');
          if (diagramTabs) {
            diagramTabs.innerHTML = '';
          }
           const diagramZoomWrap = document.getElementById('diagram-zoom-wrap');
           if (diagramZoomWrap) {
             // Note: Don't clear diagram-zoom-wrap entirely as it contains nested elements
             // (diagram-zoom-inner, diagram-views) needed by renderDiagrams().
           }

          // Populate past-scan and compare dropdowns from API
          // Use canonical repo name from data-name attribute if available
          const repoOpt = this.querySelector('option:checked');
          const repoName = (repoOpt && repoOpt.dataset && repoOpt.dataset.name) 
            ? repoOpt.dataset.name 
            : this.value.split('/').pop();
          fetch(`/api/scans/${encodeURIComponent(repoName)}`)
            .then(r => r.json())
            .then(data => {
              const scans = Array.isArray(data.scans) ? data.scans : [];

              // Populate past-scan-select
              const pastSelect = document.getElementById('past-scan-select');
              if (pastSelect) {
                pastSelect.innerHTML = '<option value="" disabled selected>— select a past scan —</option>';
                scans.forEach(s => {
                  const opt = document.createElement('option');
                  opt.value = s.experiment_id;
                  const dt = s.scanned_at ? new Date(s.scanned_at).toLocaleString() : s.experiment_id;
                  opt.textContent = `#${s.experiment_id} — ${dt}`;
                  pastSelect.appendChild(opt);
                });
              }

              // Populate compare dropdowns
              ['compare-from-select', 'compare-to-select'].forEach(id => {
                const sel = document.getElementById(id);
                if (!sel) return;
                sel.innerHTML = '<option value="" disabled selected>— select a scan —</option>';
                scans.forEach(s => {
                  const opt = document.createElement('option');
                  opt.value = s.experiment_id;
                  const dt = s.scanned_at ? new Date(s.scanned_at).toLocaleString() : s.experiment_id;
                  opt.textContent = `#${s.experiment_id} — ${dt}`;
                  sel.appendChild(opt);
                });
              });

              // Show past-scans-row if there are past scans
              const pastRow = document.getElementById('past-scans-row');
              if (pastRow) {
                pastRow.classList.toggle('visible', scans.length > 0);
              }

              // Auto-select the most recent scan (by scanned_at timestamp) and load its data
              if (pastSelect && scans.length > 0) {
                const mostRecentScan = scans.reduce((latest, current) => {
                  const latestTime = new Date(latest.scanned_at).getTime();
                  const currentTime = new Date(current.scanned_at).getTime();
                  return currentTime > latestTime ? current : latest;
                });
                pastSelect.value = mostRecentScan.experiment_id;
                // Trigger change event to load section tabs and relevant data
                pastSelect.dispatchEvent(new Event('change'));
              }
            })
            .catch(err => console.log('[Repo] Could not load past scans:', err));

          checkForRunningScan(this.value);
        }
      });
    }

    // Check for running scans on page load
    if (repoSelect && repoSelect.value) {
      checkForRunningScan(repoSelect.value);
    }

    // Load section tabs when a past scan is selected
    const pastScanSelect = document.getElementById('past-scan-select');
    if (pastScanSelect) {
      pastScanSelect.addEventListener('change', function () {
        const experimentId = this.value;
        const repoName = getCurrentRepoName();
        if (experimentId && repoName) {
          currentExperimentId = experimentId;
          buildSectionTabs(experimentId, repoName);
        }
      });
    }

    // Set initial status and ensure log view on startup
    if (statusText) {
      statusText.textContent = 'Ready';
    }
    if (statusBar) {
      statusBar.style.display = 'none';
    }
    // Start in log view (sections hidden until a scan completes or past scan is loaded)
    showLogView();

    if (logOutput && !logOutput.__autoScrollBound) {
      logOutput.__autoScrollBound = true;
      const updateLogAutoScroll = () => {
        const distanceFromBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight;
        const atBottom = distanceFromBottom <= logAutoScrollThreshold;
        if (atBottom !== logAutoScrollEnabled) {
          setLogAutoScrollEnabled(atBottom, false);
        }
      };
      logOutput.addEventListener('scroll', updateLogAutoScroll);
      updateLogAutoScroll();
    }
  }

  // Run init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
