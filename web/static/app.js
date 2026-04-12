// app.js - main application logic for Triage-Saurus UI
(function () {
  let currentEventSource = null;
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
    closeEventSource();
    clearLog();

    const form = document.getElementById('scan-form');
    if (!form) return;

    const repoPath = (document.getElementById('repo-select')?.value || '').trim();
    if (!repoPath) {
      window._triage.setStatus('Please select a repository', 'error');
      return;
    }

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
    clearLog();
    if (statusBar) statusBar.style.display = 'none';
    if (spinner) spinner.style.display = 'none';
    if (logOutput) {
      logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
    }
    window._triage.setStatus('Ready', '');
  }

  // Check if a scan is running for the selected repo
  function checkForRunningScan(repoPath) {
    if (!repoPath) return;
    
    const repoName = repoPath.split('/').pop();
    fetch(`/api/scans/${repoName}`)
      .then(response => response.json())
      .then(data => {
        if (data.running_experiment) {
          addLogLine(`[Info] Scan in progress for experiment ${data.running_experiment}`, 'info');
          addLogLine('[Info] If you were disconnected, you can reconnect by starting the scan again.', 'info');
          if (statusBar) statusBar.style.display = 'block';
          window._triage.setStatus(`Running experiment ${data.running_experiment}`, '');
        }
      })
      .catch(err => console.log('[Stream] Could not check running scan status:', err));
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
          const isHidden = tabBar.style.display === 'none';
          tabBar.style.display = isHidden ? 'block' : 'none';
          panelContent.style.display = isHidden ? 'block' : 'none';
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

    // Check for running scans on page load
    const repoSelect = document.getElementById('repo-select');
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
