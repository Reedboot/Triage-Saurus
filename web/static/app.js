// app.js - main application logic for Triage-Saurus UI
(function () {
  let currentEventSource = null;
  let currentPollInterval = null;  // Track polling to prevent duplicates
  let statusBar = null;
  let statusText = null;
  let spinner = null;
  let logOutput = null;
  let scanBtn = null;
  let resetBtn = null;

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

  // Clear the log output
  function clearLog() {
    if (logOutput) {
      logOutput.innerHTML = '';
    }
    // Hide section placeholder when starting a new scan
    const placeholder = document.getElementById('section-placeholder');
    if (placeholder) {
      placeholder.style.display = 'none';
    }
  }

  // Add a line to the log
  function addLogLine(text, className) {
    if (!logOutput) return;
    const line = document.createElement('div');
    if (className) {
      line.className = className;
    }
    // Preserve whitespace but escape HTML
    line.textContent = text;
    logOutput.appendChild(line);
    // Auto-scroll to bottom
    logOutput.parentElement?.scrollTo(0, logOutput.parentElement.scrollHeight);
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
    const repoName = repoPath.split('/').pop();
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

          addLogLine('[Connected to scan stream]', 'info');

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
                addLogLine('[Stream closed]', 'info');
                window._triage.setStatus('Scan complete', 'success');
                if (scanBtn) scanBtn.disabled = false;
                if (spinner) spinner.style.display = 'none';
                return;
              }

              chunkCount++;
              if (chunkCount === 1) {
                addLogLine('[Receiving data...]', 'info');
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
                      }
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
    closeEventSource();
    // Cancel any polling
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    clearLog();
    if (statusBar) statusBar.style.display = 'none';
    if (spinner) spinner.style.display = 'none';
    if (logOutput) {
      logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
    }
    // Show section placeholder when resetting
    const placeholder = document.getElementById('section-placeholder');
    if (placeholder) {
      placeholder.style.display = 'flex';
    }
    window._triage.setStatus('Ready', '');
  }

  // Check if a scan is running for the selected repo and auto-reconnect
  function checkForRunningScan(repoPath) {
    if (!repoPath) return;
    
    const repoName = repoPath.split('/').pop();
    fetch(`/api/scans/${repoName}`)
      .then(response => response.json())
      .then(data => {
        if (data.running_experiment) {
          // Auto-reconnect to running scan
          closeEventSource();
          // Clear log placeholder text and preserve any past progress
          if (logOutput && logOutput.innerHTML.includes('Scan output will appear here')) {
            logOutput.innerHTML = '';
          }
          // Hide section placeholder when reconnecting
          const sectionPlaceholder = document.getElementById('section-placeholder');
          if (sectionPlaceholder) {
            sectionPlaceholder.style.display = 'none';
          }
          addLogLine(`[Info] Reconnecting to running experiment ${data.running_experiment}...`, 'info');
          if (statusBar) statusBar.style.display = 'block';
          if (spinner) spinner.style.display = 'block';
          window._triage.setStatus(`Reconnecting to experiment ${data.running_experiment}...`, '');
          
          // Start receiving streaming output for the running experiment
          // Since the scan is already running on the server, we just need to get the output
          // by making a request to /scan which will detect the running experiment via lock file
          reconnectToRunningExperiment(repoPath, data.running_experiment);
        }
      })
      .catch(err => console.log('[Stream] Could not check running scan status:', err));
  }

  // Reconnect to a running experiment by polling for its status
  function reconnectToRunningExperiment(repoPath, experimentId) {
    const repoName = repoPath.split('/').pop();
    
    // Cancel any existing polling to prevent duplicates
    if (currentPollInterval) {
      clearInterval(currentPollInterval);
      currentPollInterval = null;
    }
    
    addLogLine('[Info] Scan already in progress on server', 'info');
    addLogLine(`[Info] Experiment ID: ${experimentId}`, 'info');
    addLogLine('[Info] Waiting for scan to complete...', 'info');
    addLogLine('[Info] Polling server every 5 seconds...', 'info');
    
    window._triage.setStatus('Scan in progress…', '');
    if (statusBar) statusBar.style.display = 'block';
    
    // Poll for scan completion with timeout detection
    let pollCount = 0;
    const maxPollCount = 84; // 7 minutes = 420 seconds / 5 sec interval = 84 polls
    currentPollInterval = setInterval(() => {
      pollCount++;
      const elapsedMin = Math.floor(pollCount * 5 / 60);
      const elapsedSec = (pollCount * 5) % 60;
      
      // Safety check: if polling for more than 7 min without completion, assume process crashed
      if (pollCount > maxPollCount) {
        clearInterval(currentPollInterval);
        currentPollInterval = null;
        addLogLine('[Error] Scan appears to have stalled or crashed (no completion after 7+ minutes)', 'error');
        addLogLine('[Error] Lock file may be stale. You can try starting a new scan.', 'error');
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
            addLogLine('[Info] Scan complete!', 'info');
            window._triage.setStatus('Scan complete', 'success');
            if (spinner) spinner.style.display = 'none';
            if (scanBtn) scanBtn.disabled = false;
            
            // Show completed message with duration
            if (elapsedMin > 0) {
              addLogLine(`[Info] Total time: ${elapsedMin}m ${elapsedSec}s`, 'info');
            } else {
              addLogLine(`[Info] Total time: ${elapsedSec}s`, 'info');
            }
          } else if (pollCount % 6 === 0) {
            // Show progress update every 30 seconds (6 * 5 sec intervals)
            if (elapsedMin > 0) {
              addLogLine(`[Web] Scan in progress — elapsed ${elapsedMin}m ${elapsedSec}s (est. 4-5 min remaining)`, 'info');
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

  // Initialize on DOM ready
  function init() {
    statusBar = document.getElementById('status-bar');
    statusText = document.getElementById('status-text');
    spinner = document.getElementById('spinner');
    logOutput = document.getElementById('log-output');
    scanBtn = document.getElementById('scan-btn');
    resetBtn = document.getElementById('reset-btn');

    const scanForm = document.getElementById('scan-form');

    if (scanForm) {
      scanForm.addEventListener('submit', handleScanSubmit);
    }

    if (resetBtn) {
      resetBtn.addEventListener('click', handleReset);
    }

    // Handle sections toggle button
    const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
    if (toggleSectionsBtn) {
      toggleSectionsBtn.addEventListener('click', function() {
        const tabBar = document.getElementById('section-tab-bar');
        const panelContent = document.getElementById('section-panel-content');
        if (tabBar && panelContent) {
          // Use getComputedStyle to check actual display value from CSS
          const computedDisplay = window.getComputedStyle(tabBar).display;
          const isHidden = computedDisplay === 'none';
          // Set inline style to override CSS
          tabBar.style.display = isHidden ? 'flex' : 'none';
          panelContent.style.display = isHidden ? 'flex' : 'none';
          toggleSectionsBtn.title = isHidden ? 'Hide sections' : 'Show sections';
        }
      });
    }

    // Handle page visibility changes (mobile tab switch)
    document.addEventListener('visibilitychange', function() {
      if (document.hidden) {
        console.log('[Stream] Page hidden');
      } else {
        console.log('[Stream] Page visible - stream should resume if active');
      }
    });

    // Handle repo selection change - auto-reconnect to running scans
    const repoSelect = document.getElementById('repo-select');
    if (repoSelect) {
      repoSelect.addEventListener('change', function() {
        if (this.value) {
          checkForRunningScan(this.value);
        }
      });
    }

    // Check for running scans on page load
    if (repoSelect && repoSelect.value) {
      checkForRunningScan(repoSelect.value);
    }

    // Set initial status
    if (statusText) {
      statusText.textContent = 'Ready';
    }
    if (statusBar) {
      statusBar.style.display = 'none';
    }
  }

  // Run init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
