(() => {
  // Label every cell of a .table-stack table with its column heading, so on
  // phones and tablets each row can be shown as a card of "Label  value" lines.
  document.querySelectorAll("table.table-stack").forEach((table) => {
    const headings = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      let column = 0;
      [...row.children].forEach((cell) => {
        if (!cell.hasAttribute("data-label")) {
          cell.setAttribute("data-label", headings[column] || "");
        }
        column += Number(cell.getAttribute("colspan") || 1);
      });
    });
  });
})();
