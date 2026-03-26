// containers.js - initializes the containers partial when inserted into the DOM
(function () {
  function norm(s) {
    return (s || '').toString().toLowerCase();
  }

  function initContainers(container) {
    if (!container) container = document;

    const searchInput = container.querySelector('#containers-search');
    const cards = Array.from(container.querySelectorAll('.container-card'));
    const emptyState = container.querySelector('#containers-empty');

    if (!cards.length) return;

    function applyFilter() {
      const q = norm(searchInput && searchInput.value).trim();
      const effective = q.length >= 2 ? q : '';
      let visible = 0;

      for (const card of cards) {
        const hay = norm(card.dataset.search || card.textContent || '');
        const show = !effective || hay.includes(effective);
        card.style.display = show ? '' : 'none';
        if (show) visible++;
      }

      if (emptyState) {
        emptyState.style.display = visible ? 'none' : '';
      }
    }

    let timer = null;
    function scheduleFilter() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        applyFilter();
        timer = null;
      }, 120);
    }

    if (searchInput && !searchInput.__containers_bound) {
      searchInput.__containers_bound = true;
      searchInput.addEventListener('input', scheduleFilter);
    }

    applyFilter();
  }

  window.initContainers = initContainers;
})();
