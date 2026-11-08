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
});
