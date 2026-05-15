/**
 * Searchable Repository Selector
 * Converts a standard select element into a searchable dropdown
 */

function initializeRepoSelector() {
  const selectEl = document.getElementById('repo-select');
  if (!selectEl) return;

  // Extract repo options from the select
  const repos = [];
  selectEl.querySelectorAll('option').forEach(option => {
    if (option.value && option.dataset.name) {
      repos.push({
        name: option.dataset.name,
        path: option.value,
        element: option
      });
    }
  });

  if (repos.length === 0) return;

  // Create custom dropdown UI
  const container = document.createElement('div');
  container.className = 'repo-selector-container';
  
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'repo-selector-input';
  input.placeholder = '— choose a repo —';
  input.setAttribute('aria-label', 'Search repositories');

  const dropdown = document.createElement('div');
  dropdown.className = 'repo-selector-dropdown';

  const listContainer = document.createElement('div');
  listContainer.className = 'repo-selector-list';

  // Initially show all repos
  function renderOptions(filterText = '') {
    listContainer.innerHTML = '';
    const filtered = repos.filter(repo =>
      repo.name.toLowerCase().includes(filterText.toLowerCase())
    );

    if (filtered.length === 0) {
      const noResults = document.createElement('div');
      noResults.className = 'repo-selector-no-results';
      noResults.textContent = 'No repositories found';
      listContainer.appendChild(noResults);
      return;
    }

    filtered.forEach(repo => {
      const item = document.createElement('div');
      item.className = 'repo-selector-item';
      item.textContent = repo.name;
      item.dataset.value = repo.path;

      item.addEventListener('click', () => {
        selectRepo(repo);
      });

      item.addEventListener('mouseenter', () => {
        document.querySelectorAll('.repo-selector-item').forEach(el => {
          el.classList.remove('active');
        });
        item.classList.add('active');
      });

      listContainer.appendChild(item);
    });
  }

  function selectRepo(repo) {
    input.value = repo.name;
    selectEl.value = repo.path;
    dropdown.classList.remove('open');
    // Trigger change event for any listeners
    selectEl.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Input event listener
  input.addEventListener('input', (e) => {
    const filterText = e.target.value;
    renderOptions(filterText);
    dropdown.classList.add('open');
  });

  // Focus event - show dropdown with all options
  input.addEventListener('focus', () => {
    renderOptions('');
    dropdown.classList.add('open');
  });

  // Keyboard navigation
  input.addEventListener('keydown', (e) => {
    const items = listContainer.querySelectorAll('.repo-selector-item');
    const activeItem = listContainer.querySelector('.repo-selector-item.active');

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (items.length === 0) return;
        if (!activeItem) {
          items[0].classList.add('active');
        } else {
          const nextItem = activeItem.nextElementSibling;
          if (nextItem?.classList.contains('repo-selector-item')) {
            activeItem.classList.remove('active');
            nextItem.classList.add('active');
            nextItem.scrollIntoView({ block: 'nearest' });
          }
        }
        break;

      case 'ArrowUp':
        e.preventDefault();
        if (items.length === 0) return;
        if (!activeItem) {
          items[items.length - 1].classList.add('active');
        } else {
          const prevItem = activeItem.previousElementSibling;
          if (prevItem?.classList.contains('repo-selector-item')) {
            activeItem.classList.remove('active');
            prevItem.classList.add('active');
            prevItem.scrollIntoView({ block: 'nearest' });
          }
        }
        break;

      case 'Enter':
        e.preventDefault();
        if (activeItem) {
          const selectedRepo = repos.find(r => r.path === activeItem.dataset.value);
          if (selectedRepo) selectRepo(selectedRepo);
        }
        break;

      case 'Escape':
        dropdown.classList.remove('open');
        break;
    }
  });

  // Close dropdown when clicking outside
  document.addEventListener('click', (e) => {
    if (!container.contains(e.target)) {
      dropdown.classList.remove('open');
    }
  });

  // Restore previously selected repo on page load
  const selectedIndex = selectEl.selectedIndex;
  if (selectedIndex > 0) {
    const selectedOption = selectEl.options[selectedIndex];
    input.value = selectedOption.dataset.name || '';
  }

  // Build the dropdown structure
  dropdown.appendChild(listContainer);
  container.appendChild(input);
  container.appendChild(dropdown);

  // Replace the original select with our custom dropdown
  selectEl.style.display = 'none';
  selectEl.parentNode.insertBefore(container, selectEl);

  // Initial render
  renderOptions('');
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeRepoSelector);
} else {
  initializeRepoSelector();
}

