(function() {
  function renderOpenQuestions() {
    document.querySelectorAll('.open-questions-widget:not([data-oq-init])').forEach(function(widget) {
      widget.setAttribute('data-oq-init', '1');
      var expId = widget.dataset.experimentId;
      var repoName = widget.dataset.repoName;
      var scriptEl = widget.querySelector('script.oq-data');
      if (!scriptEl) return;
      var questions;
      try { questions = JSON.parse(scriptEl.textContent); } catch(e) { return; }
      scriptEl.remove();

      var ANSWERS = ['yes', 'no', 'dont_know'];
      var LABELS  = { yes: '✓ Yes', no: '✗ No', dont_know: '? Don\'t know' };

      var ol = document.createElement('ol');
      ol.className = 'oq-list';

      questions.forEach(function(q, idx) {
        var li = document.createElement('li');
        li.className = 'oq-item' + (q.answer ? ' oq-item--answered' : '');
        if (q.auto_answered) li.classList.add('oq-item--auto');

        var textRow = document.createElement('div');
        textRow.className = 'oq-text';
        textRow.textContent = q.question;

        if (q.asset) {
          var badge = document.createElement('span');
          badge.className = 'how-badge';
          badge.style.marginLeft = '8px';
          badge.textContent = q.asset;
          textRow.appendChild(badge);
        }
        if (q.file) {
          var fileSpan = document.createElement('span');
          fileSpan.className = 'file-link';
          fileSpan.style.marginLeft = '8px';
          fileSpan.title = q.file;
          var base = q.file.split(/[/\\]/).pop();
          fileSpan.textContent = base + (q.line ? ':' + q.line : '');
          textRow.appendChild(fileSpan);
        }
        li.appendChild(textRow);

        if (q.auto_answered && q.auto_rationale) {
          var rationale = document.createElement('div');
          rationale.className = 'oq-rationale';
          rationale.innerHTML = '<span class="oq-auto-badge">🔍 Auto-analysed</span> ' +
            q.auto_rationale.replace(/</g, '&lt;').replace(/>/g, '&gt;');
          li.appendChild(rationale);
        }

        var btnRow = document.createElement('div');
        btnRow.className = 'oq-btns';
        if (q.auto_answered) {
          var overrideLbl = document.createElement('span');
          overrideLbl.className = 'oq-override-lbl';
          overrideLbl.textContent = 'Override:';
          btnRow.appendChild(overrideLbl);
        }

        ANSWERS.forEach(function(ans) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'oq-btn oq-btn--' + ans + (q.answer === ans ? ' oq-btn--active' : '');
          btn.textContent = LABELS[ans];
          btn.addEventListener('click', function() {
            var prevAnswer = q.answer;
            q.answer = ans;
            q.auto_answered = false;
            li.classList.add('oq-item--answered');
            li.classList.remove('oq-item--auto');
            btnRow.querySelectorAll('.oq-btn').forEach(function(b) { b.classList.remove('oq-btn--active'); });
            btn.classList.add('oq-btn--active');

            var statusEl = li.querySelector('.oq-status');
            statusEl.textContent = 'Saving…';

            fetch('/api/subscription_context/' + encodeURIComponent(expId), {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                question: q.question,
                answer: ans === 'dont_know' ? "Don't know" : (ans.charAt(0).toUpperCase() + ans.slice(1)),
                scope: 'repo',
                repo_name: repoName,
                answered_by: 'user',
                confidence: ans === 'yes' ? 1.0 : ans === 'no' ? 1.0 : 0.5,
                tags: 'open_question',
              })
            })
            .then(function(r) { return r.json(); })
            .then(function(d) {
              statusEl.textContent = d.status === 'ok' ? '✓ Saved' : ('⚠ ' + (d.error || 'error'));
              setTimeout(function() { statusEl.textContent = ''; }, 3000);
            })
            .catch(function(e) {
              q.answer = prevAnswer;
              statusEl.textContent = '⚠ Failed';
              setTimeout(function() { statusEl.textContent = ''; }, 3000);
            });
          });
          btnRow.appendChild(btn);
        });

        var statusEl = document.createElement('span');
        statusEl.className = 'oq-status';
        btnRow.appendChild(statusEl);
        li.appendChild(btnRow);
        ol.appendChild(li);
      });

      widget.appendChild(ol);
    });
  }

  renderOpenQuestions();
  window._renderOpenQuestions = renderOpenQuestions;

  var observer = new MutationObserver(function(mutations) {
    for (var m of mutations) {
      if (m.addedNodes.length) { renderOpenQuestions(); break; }
    }
  });
  var body = document.querySelector('.overview-body');
  if (body) observer.observe(body, { childList: true, subtree: true });
})();
