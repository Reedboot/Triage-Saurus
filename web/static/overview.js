// overview.js - handles AI analysis controls in Overview and TLDR tabs
(function () {
  function initModuleMapping(container) {
    var section = (container || document).querySelector('.module-mapping-section');
    if (!section) return;

    var list = section.querySelector('#module-mapping-list');
    if (!list || list.children.length) return; // already populated

    var depsEl  = section.querySelector('script.module-mapping-data');
    var reposEl = section.querySelector('script.module-mapping-repos');
    if (!depsEl) return;

    var moduleDeps, availableRepos;
    try {
      moduleDeps     = JSON.parse(depsEl.textContent);
      availableRepos = reposEl ? JSON.parse(reposEl.textContent) : [];
    } catch (e) {
      console.warn('Module mapping data parse error:', e);
      return;
    }

    var experimentId = (section.dataset.experimentId || '').trim();
    var repoName     = (section.dataset.repoName || '').trim();

    function escHtml(s) {
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function baseName(p) {
      return String(p || '').split(/[/\\]/).filter(Boolean).pop() || String(p || '');
    }
    function inferRepoFromSource(source) {
      if (!source) return null;
      var m = source.match(/_git\/([^\/\?#]+)/);
      if (m) return decodeURIComponent(m[1]);
      m = source.match(/(?:github|gitlab)\.com\/[^\/]+\/([^\/\?#.]+)/);
      if (m) return m[1];
      return null;
    }
    function repoMatchScore(rName, suggested) {
      if (!rName || !suggested) return 0;
      var r = rName.toLowerCase(), s = suggested.toLowerCase();
      if (r === s) return 3;
      if (r.includes(s) || s.includes(r)) return 2;
      var rWords = r.split(/[-_]+/), sWords = s.split(/[-_]+/);
      var overlap = rWords.filter(function(w) { return sWords.includes(w) && w.length > 2; }).length;
      return overlap > 0 ? 1 : 0;
    }

    moduleDeps.forEach(function(sourceGroup) {
      var modules  = (sourceGroup.modules && Array.isArray(sourceGroup.modules)) ? sourceGroup.modules : [sourceGroup];
      var source   = sourceGroup.source || (modules[0] && modules[0].source) || '';
      var suggested = inferRepoFromSource(source);

      // Determine current mapping from the first module that has one
      // (all modules in a source group share the same target repo)
      var currentRepo = null, isExternal = false;
      for (var mi = 0; mi < modules.length; mi++) {
        var cm = modules[mi].current_mapping;
        if (cm && typeof cm === 'object') {
          if (cm.external) { isExternal = true; currentRepo = null; break; }
          if (cm.repo)     { currentRepo = cm.repo; break; }
        } else if (typeof cm === 'string' && cm) {
          currentRepo = cm; break;
        }
      }

      // ── Card ──
      var li = document.createElement('li');
      li.className = 'module-dep-item' + (currentRepo || isExternal ? ' module-dep-item--mapped' : '');

      // 1. "Module Source" label + URL
      var srcLabel = document.createElement('div');
      srcLabel.className = 'module-dep-label';
      srcLabel.textContent = 'Module Source';
      li.appendChild(srcLabel);

      var srcPre = document.createElement('pre');
      srcPre.className = 'md-code';
      srcPre.style.margin = '4px 0 10px';
      srcPre.innerHTML = '<code>' + escHtml(source || '(unknown source)') + '</code>';
      li.appendChild(srcPre);

      // 2. "Maps to" label + dropdown
      var mapsLabel = document.createElement('div');
      mapsLabel.className = 'module-dep-label';
      mapsLabel.textContent = 'Maps to';
      li.appendChild(mapsLabel);

      var sel = document.createElement('select');
      sel.className = 'module-repo-select';
      // Store all module names comma-separated so save picks them all up
      sel.dataset.moduleName   = modules.map(function(m) { return m.name || ''; }).join(',');
      sel.dataset.moduleSource = source;
      sel.style.margin = '4px 0 12px';
      sel.appendChild(new Option('— Please Select —', ''));

      var ranked = availableRepos
        .map(function(r) { return { name: r, score: repoMatchScore(r, suggested) }; })
        .filter(function(r) { return r.score > 0; })
        .sort(function(a, b) { return b.score - a.score; });

      if (ranked.length) {
        var sugGroup = document.createElement('optgroup');
        sugGroup.label = 'Suggested';
        ranked.forEach(function(r) { sugGroup.appendChild(new Option(r.name, r.name)); });
        sel.appendChild(sugGroup);
      }
      var sugNames = new Set(ranked.map(function(r) { return r.name; }));
      var remaining = availableRepos.filter(function(r) { return !sugNames.has(r); });
      if (remaining.length) {
        var allGroup = document.createElement('optgroup');
        allGroup.label = 'All repos';
        remaining.forEach(function(r) { allGroup.appendChild(new Option(r, r)); });
        sel.appendChild(allGroup);
      }
      sel.appendChild(new Option('↗ External / Third-party', '__external__'));

      var valueToSet = isExternal ? '__external__' : (currentRepo || (ranked.length === 1 ? ranked[0].name : ''));
      if (valueToSet) {
        for (var oi = 0; oi < sel.options.length; oi++) {
          if (sel.options[oi].value === valueToSet) { sel.options[oi].selected = true; break; }
        }
      }
      li.appendChild(sel);

      // 3. References list
      var refsLabel = document.createElement('div');
      refsLabel.className = 'module-dep-label';
      refsLabel.textContent = 'References';
      li.appendChild(refsLabel);

      var refUl = document.createElement('ul');
      refUl.style.cssText = 'margin:4px 0 0; padding-left:16px; list-style:disc;';
      modules.forEach(function(mod) {
        var refLi = document.createElement('li');
        refLi.style.cssText = 'font-size:0.82rem; color:var(--text); line-height:1.6;';
        var html = '<strong>' + escHtml(mod.name || '') + '</strong>';
        if (mod.file) {
          html += ' — <span class="file-link" style="color:var(--text-muted)" title="' + escHtml(mod.file) + '">' + escHtml(baseName(mod.file)) + '</span>';
          if (mod.line) html += '<span class="line-num">:' + escHtml(String(mod.line)) + '</span>';
        }
        refLi.innerHTML = html;
        refUl.appendChild(refLi);
      });
      li.appendChild(refUl);

      list.appendChild(li);
    });
    });

    var saveBtn  = section.querySelector('#save-module-mappings-btn');
    var statusEl = section.querySelector('#module-mapping-status');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        var selects = list.querySelectorAll('select.module-repo-select');
        var mappings = {};
        selects.forEach(function(s) {
          if (!s.value) return;
          var val = s.value === '__external__' ? { repo: null, external: true } : { repo: s.value };
          // data-module-name may be comma-separated (one select per source group)
          var names = (s.dataset.moduleName || '').split(',').map(function(n) { return n.trim(); }).filter(Boolean);
          names.forEach(function(n) { mappings[n] = val; });
        });
        if (!Object.keys(mappings).length) {
          if (statusEl) statusEl.textContent = 'Nothing to save — select at least one mapping.';
          setTimeout(function() { if (statusEl) statusEl.textContent = ''; }, 3000);
          return;
        }
        saveBtn.disabled = true;
        if (statusEl) statusEl.textContent = 'Saving…';
        fetch('/api/module/mappings/' + encodeURIComponent(experimentId) + '/' + encodeURIComponent(repoName), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mappings: mappings })
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          saveBtn.disabled = false;
          if (d.ok) {
            if (statusEl) statusEl.textContent = '✓ Saved ' + (d.saved || Object.keys(mappings).length) + ' mapping(s)';
            // Add a mapped badge beside each select on success
            selects.forEach(function(s) {
              if (!s.value) return;
              var item = s.closest('.module-dep-item');
              if (item) item.classList.add('module-dep-item--mapped');
            });
          } else {
            if (statusEl) statusEl.textContent = 'Error: ' + (d.error || 'unknown');
          }
          setTimeout(function() { if (statusEl) statusEl.textContent = ''; }, 4000);
        })
        .catch(function(e) {
          saveBtn.disabled = false;
          if (statusEl) statusEl.textContent = 'Error: ' + e.message;
        });
      });
    }
  }

  function initOverview(container) {
    if (!container) container = document;

    const meta = container.querySelector('.overview-actions');
    const runBtn = container.querySelector('#overview-run-ai-btn, #tldr-run-ai-btn');
    const statusEl = container.querySelector('#overview-ai-status, #tldr-ai-status');
    const genRulesBtn = container.querySelector('#overview-gen-rules-btn');
    if (!meta || !statusEl) return;

    const experimentId = (meta.dataset.experimentId || '').trim();
    const repoName = (meta.dataset.repoName || '').trim();
    const refreshSection = (meta.dataset.refreshSection || 'overview').trim() || 'overview';
    if (!experimentId || !repoName) {
      statusEl.textContent = 'Missing experiment context';
      if (runBtn) runBtn.disabled = true;
      return;
    }
    if (!runBtn) return;

    const triage = window._triage || {};
    let pollTimer = null;
    let seenAiLogCount = 0;
    let stopInFlight = false;
    let activeStream = null;
    let observedActiveRun = false;
    let completionRefreshTriggered = false;

    function setStatus(text, isError) {
      statusEl.textContent = text;
      statusEl.classList.toggle('error', !!isError);
    }

    function updateGlobalStatus(message, tone) {
      if (triage && typeof triage.setStatus === 'function' && message) {
        triage.setStatus(message, tone || 'info');
      }
    }

    function updateGlobalBusy(isBusy) {
      if (triage && typeof triage.setStatusBusy === 'function') {
        triage.setStatusBusy(!!isBusy);
      }
    }

    function updateToolbarStop(enabled) {
      if (triage && typeof triage.setToolbarStopState === 'function') {
        triage.setToolbarStopState({
          enabled: !!enabled,
          visible: !!enabled,
          label: '⏹ Stop AI Scan',
        });
      }
    }
    updateToolbarStop(false);

    function closeActiveStream(reason) {
      if (!activeStream) return;
      try {
        activeStream.close();
      } catch (err) {
        // ignore
      }
      if (triage && typeof triage.appendLog === 'function' && reason) {
        triage.appendLog(`[Copilot] Stream closed (${reason})`);
      }
      activeStream = null;
    }

    async function requestStop(source) {
      if (stopInFlight) return;
      stopInFlight = true;
      if (triage && typeof triage.setToolbarStopState === 'function') {
        triage.setToolbarStopState({
          enabled: false,
          visible: true,
          label: '⏹ Stopping AI Scan...',
        });
      }
      try {
        const resp = await fetch(`/api/analysis/stop/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`, {
          method: 'POST',
        });
        if (!resp.ok) {
          throw new Error('stop_failed');
        }
        const payload = await resp.json().catch(() => ({}));
        if (triage && typeof triage.appendLog === 'function') {
          triage.appendLog(source === 'toolbar' ? '[Copilot] Stop requested from toolbar' : '[Copilot] Stop requested');
        }
        if (payload && payload.error) {
          throw new Error(payload.error || 'stop_failed');
        }
        setStatus('Stopping…', false);
        if (runBtn) runBtn.disabled = false;
        closeActiveStream('stopped by user');
        updateGlobalStatus('Stopping AI analysis…', 'warn');
        updateGlobalBusy(true);
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(pollStatus, 2000);
      } catch (err) {
        setStatus('Stop failed', true);
        if (triage && typeof triage.appendLog === 'function') {
          triage.appendLog('[Copilot] Stop threw error');
        }
        updateGlobalStatus('AI analysis stop failed', 'error');
        updateGlobalBusy(false);
      } finally {
        stopInFlight = false;
      }
    }

    if (triage && typeof triage.registerToolbarStop === 'function') {
      triage.registerToolbarStop(() => requestStop('toolbar'));
    }

    function clearPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function maybeRefreshSectionAfterCompletion() {
      if (completionRefreshTriggered) return;
      if (!observedActiveRun && !activeStream) return;
      completionRefreshTriggered = true;
      observedActiveRun = false;

      // Refresh the section that initiated the run (overview/tldr)
      if (window._triage && typeof window._triage.loadSectionContent === 'function') {
        window._triage.loadSectionContent(refreshSection, experimentId, repoName).catch(() => {});
      }

      // If there are open questions, take the user to Global Knowledge Q&A; else to Overview.
      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const status = await resp.json().catch(() => ({}));
        const openQ = Array.isArray(status.open_questions) ? status.open_questions : [];
        const unanswered = openQ.filter((q) => (typeof q === 'string' && q.trim()) || (q && typeof q === 'object' && String(q.question || '').trim())).length;
        if (window._triage && typeof window._triage.activateSectionKey === 'function') {
          window._triage.activateSectionKey(unanswered > 0 ? 'subscription' : 'overview', experimentId, repoName);
        }
      } catch (e) {
        if (window._triage && typeof window._triage.activateSectionKey === 'function') {
          window._triage.activateSectionKey('overview', experimentId, repoName);
        }
      }
    }

    async function pollStatus() {
      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const data = await resp.json();

        const logs = Array.isArray(data.logs) ? data.logs : [];
        if (logs.length > seenAiLogCount) {
          const newLines = logs.slice(seenAiLogCount);
          seenAiLogCount = logs.length;
          if (window._triage && typeof window._triage.appendLog === 'function') {
            newLines.forEach((line) => window._triage.appendLog(`[AI] ${line}`));
          }
        }

        if (data.status === 'running') {
          observedActiveRun = true;
          completionRefreshTriggered = false;
          const label = (data.active_step_label || data.active_step || '').toString();
          const skepticsRunning = !!data.skeptics_running;
          const statusText = label ? `Running: ${label}${skepticsRunning ? ' (skeptics)' : ''}…` : 'Running...';
          setStatus(statusText, false);
          if (runBtn) runBtn.disabled = true;
          updateGlobalBusy(true);
          if (!stopInFlight) {
            if (triage && typeof triage.setToolbarStopState === 'function') {
              triage.setToolbarStopState({
                enabled: true,
                visible: true,
                label: '⏹ Stop AI Scan',
              });
            }
          }
          updateGlobalStatus(statusText, skepticsRunning ? 'warn' : 'info');
          return;
        }
        if (data.status === 'completed') {
          closeActiveStream('completed via status');
          setStatus('Completed', false);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          maybeRefreshSectionAfterCompletion();
          clearPolling();
          updateGlobalStatus('AI analysis completed', 'success');
          return;
        }
        if (data.status === 'failed' || data.status === 'stopped') {
          observedActiveRun = false;
          closeActiveStream(`${data.status} via status`);
          const detail = (data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString();
          setStatus(detail, true);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          clearPolling();
          if (data.status === 'failed' && triage && typeof triage.appendLog === 'function' && detail) {
            triage.appendLog(`[Copilot] ${detail}`);
          }
          updateGlobalStatus(data.status === 'stopped' ? 'AI analysis stopped' : 'AI analysis failed', data.status === 'stopped' ? 'warn' : 'error');
          return;
        }

        if (data.status === 'idle' || !data.status) {
          observedActiveRun = false;
          closeActiveStream('idle via status');
          setStatus('Idle', false);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          clearPolling();
          updateGlobalStatus('AI analysis idle', 'info');
        }
      } catch (err) {
        setStatus('Status check failed', true);
        clearPolling();
        if (runBtn) runBtn.disabled = false;
        updateToolbarStop(false);
        updateGlobalBusy(false);
        updateGlobalStatus('AI status check failed', 'error');
      }
    }

    if (runBtn && !runBtn.__overview_ai_bound) {
      runBtn.__overview_ai_bound = true;
      runBtn.addEventListener('click', async () => {
        runBtn.disabled = true;
        observedActiveRun = true;
        completionRefreshTriggered = false;
        if (triage && typeof triage.setToolbarStopState === 'function') {
          triage.setToolbarStopState({
            enabled: true,
            visible: true,
            label: '⏹ Stop AI Scan',
          });
        }
        setStatus('Starting...', false);
        seenAiLogCount = 0;

        if (window._triage && typeof window._triage.showRawLog === 'function') {
          window._triage.showRawLog();
        }
        if (window._triage && typeof window._triage.appendLog === 'function') {
          window._triage.appendLog(`[AI] Starting analysis for ${repoName} (experiment ${experimentId})`);
        }
        if (window._triage && typeof window._triage.setStatus === 'function') {
          window._triage.setStatus('Running AI analysis…', 'info');
        }
        updateGlobalBusy(true);

        try {
          // Start a Copilot streaming job; stream output into the Log panel via SSE.
          const streamUrl = `/api/analysis/copilot/stream/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`;

          if (window._triage && typeof window._triage.appendLog === 'function') {
            window._triage.appendLog('[Copilot] Connecting stream...');
          }

          closeActiveStream('restarting');
          const es = new EventSource(streamUrl);
          activeStream = es;

           es.addEventListener('log', (evt) => {
            try {
              const line = JSON.parse(evt.data);
              if (window._triage && typeof window._triage.appendLog === 'function') {
                window._triage.appendLog(`[Copilot] ${line}`);
              }
            } catch (e) {
              // ignore
            }
          });

           let streamDone = false;

           es.addEventListener('done', () => {
              streamDone = true;
              closeActiveStream('completed');
              setStatus('Completed', false);
               runBtn.disabled = false;
               updateToolbarStop(false);
               updateGlobalBusy(false);
             clearPolling();
            maybeRefreshSectionAfterCompletion();
          });

           es.addEventListener('error', async () => {
              // EventSource fires 'error' for disconnects, and may also fire when the server closes the stream.
              // If we've already seen a 'done' event, ignore this.
              if (streamDone) {
                closeActiveStream('completed');
                return;
              }

              // Check status first; only treat as a real error if the backend reports failed.
              try {
                const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
                const st = await resp.json().catch(() => ({}));
                if (st && st.status === 'completed') {
                  streamDone = true;
                  closeActiveStream('completed');
                  setStatus('Completed', false);
                  runBtn.disabled = false;
                  updateToolbarStop(false);
                  updateGlobalBusy(false);
                  clearPolling();
                  maybeRefreshSectionAfterCompletion();
                  return;
                }
                if (st && st.status === 'failed') {
                  const detail = (st.error || 'Failed').toString();
                  if (triage && typeof triage.appendLog === 'function' && detail) {
                    triage.appendLog(`[Copilot] ${detail}`);
                  }
                  closeActiveStream('failed');
                  setStatus(detail, true);
                  runBtn.disabled = false;
                  updateToolbarStop(false);
                  updateGlobalBusy(false);
                  clearPolling();
                  return;
                }
              } catch (e) {}

              // Otherwise, fall back to polling and let the status endpoint decide whether we actually finished.
              closeActiveStream('disconnected');
              clearPolling();
              pollTimer = setInterval(pollStatus, 2000);

              setStatus('Streaming disconnected (polling...)', true);
              updateGlobalBusy(true);
            });

           setStatus('Running Copilot...', false);
           if (pollTimer) clearInterval(pollTimer);
           pollTimer = setInterval(pollStatus, 2000);
        } catch (err) {
           setStatus('Start failed', true);
           if (window._triage && typeof window._triage.appendLog === 'function') {
             window._triage.appendLog('[Copilot] Start failed due to network/runtime error');
           }
            runBtn.disabled = false;
            updateToolbarStop(false);
            updateGlobalBusy(false);
        }
      });
    }

    if (genRulesBtn) {
      genRulesBtn.disabled = false;
    }

    pollStatus();
    initModuleMapping(container);
  }

  window.initOverview = initOverview;
})();
