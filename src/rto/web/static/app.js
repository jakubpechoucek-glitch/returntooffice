// Minimal progressive enhancement (CSP: script-src 'self', no inline scripts).
document.addEventListener("DOMContentLoaded", () => {
  // Confirmation for destructive / important actions: <button data-confirm="...">
  document.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (!window.confirm(el.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
  // Auto-submit selects: <select data-autosubmit>
  document.querySelectorAll("select[data-autosubmit]").forEach((el) => {
    el.addEventListener("change", () => el.form && el.form.submit());
  });
  // Request form: show the fields relevant for the selected type
  const type = document.querySelector("#request-type");
  if (type) {
    const sync = () => {
      document.querySelectorAll("[data-for-types]").forEach((block) => {
        const show = block.getAttribute("data-for-types").split(",").includes(type.value);
        block.hidden = !show;
      });
      const hint = document.querySelector("#type-hint");
      if (hint) hint.textContent = type.selectedOptions[0].getAttribute("data-hint") || "";
    };
    type.addEventListener("change", sync);
    sync();
  }

  // Org chart: expand / collapse all, and search that opens the path to every match
  document.querySelectorAll("[data-org-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("data-org-action") === "expand";
      document.querySelectorAll(".org-tree details").forEach((d) => { d.open = open; });
    });
  });
  const search = document.querySelector("#org-search");
  if (search) {
    const result = document.querySelector("#org-search-result");
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      let hits = 0;
      document.querySelectorAll(".org-node").forEach((li) => {
        const match = q.length >= 2 && li.getAttribute("data-name").includes(q);
        li.classList.toggle("match", match);
        if (match) {
          hits += 1;
          let el = li.parentElement;
          while (el && !el.classList.contains("org-tree")) {
            if (el.tagName === "DETAILS") el.open = true;
            el = el.parentElement;
          }
        }
      });
      result.textContent = q.length >= 2 ? `${hits} match(es)` : "";
      const first = document.querySelector(".org-node.match");
      if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }
});
