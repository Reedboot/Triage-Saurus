// table-resize.js - Shared utility for making table columns resizable
// Usage: initTableColumnResize(table, storageKey)
(function(){
  function initTableColumnResize(table, storageKey) {
    if (!table) return;
    if (!storageKey) storageKey = 'table_col_widths';
    
    const thead = table.querySelector('thead');
    if (!thead) return;
    
    const headers = Array.from(thead.querySelectorAll('th'));
    
    // Load saved widths
    let savedWidths = {};
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) savedWidths = JSON.parse(stored);
    } catch (e) {}
    
    // Apply saved widths and add resize handles
    headers.forEach((th, idx) => {
      const key = th.getAttribute('data-key') || `col_${idx}`;
      
      // Apply saved width
      if (savedWidths[key]) {
        th.style.width = savedWidths[key] + 'px';
      }
      
      // Skip if resize handle already exists
      if (th.querySelector('.resize-handle')) return;
      
      // Add resize handle
      const handle = document.createElement('div');
      handle.className = 'resize-handle';
      th.appendChild(handle);
      
      let startX = 0;
      let startWidth = 0;
      
      handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        startX = e.pageX;
        startWidth = th.offsetWidth;
        
        th.classList.add('resizing');
        table.classList.add('col-resizing');
        
        const onMouseMove = (e) => {
          const diff = e.pageX - startX;
          const newWidth = Math.max(50, startWidth + diff); // Min 50px
          th.style.width = newWidth + 'px';
        };
        
        const onMouseUp = () => {
          th.classList.remove('resizing');
          table.classList.remove('col-resizing');
          
          // Save width
          const widths = {};
          headers.forEach((h, i) => {
            const k = h.getAttribute('data-key') || `col_${i}`;
            if (h.style.width) {
              widths[k] = parseInt(h.style.width);
            }
          });
          try {
            localStorage.setItem(storageKey, JSON.stringify(widths));
          } catch (e) {}
          
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);
        };
        
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
      });
    });
  }
  
  // Expose globally
  window.initTableColumnResize = initTableColumnResize;
})();
