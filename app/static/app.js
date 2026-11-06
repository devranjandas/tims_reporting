// Small helpers for the TIMS-Report pages. Everything works without JavaScript too.
(function () {
  "use strict";
  const num = /^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$/;

  // FR-IN-04: mark fields that are not numbers while typing
  document.querySelectorAll("table.entry input.num").forEach((el) => {
    const check = () => el.classList.toggle("bad", el.value.trim() !== "" && !num.test(el.value));
    el.addEventListener("input", check);
    check();
  });

  // Element choice on the new-sample page reloads the form with that element's ratios
  const elSel = document.getElementById("element-select");
  if (elSel) {
    elSel.addEventListener("change", () => {
      if (confirm("Change the element? Values typed in the table will be cleared.")) {
        window.location = elSel.dataset.url + "?element=" + encodeURIComponent(elSel.value);
      }
    });
  }

  // Show the enriched-material fields only for enriched samples
  const typeSel = document.getElementById("sample-type");
  const enr = document.querySelector(".enriched-only");
  if (typeSel && enr) {
    const sync = () => { enr.hidden = typeSel.value !== "Enriched"; };
    typeSel.addEventListener("change", sync);
    sync();
  }

  // FR-IN-09: keep an unsaved new-sample form in this browser
  const form = document.querySelector("form[data-autosave='new-sample']");
  if (form) {
    const key = "tims-new-sample";
    const fields = () => Array.from(form.querySelectorAll("input[name], select[name], textarea[name]"))
      .filter((f) => f.type !== "hidden" && f.type !== "file" && f.name !== "paste");
    const save = () => {
      try { localStorage.setItem(key, JSON.stringify(fields().map((f) => [f.name, f.value]))); } catch (e) { /* storage off */ }
    };
    form.addEventListener("input", save);
    form.addEventListener("submit", (ev) => {
      if (ev.submitter && ev.submitter.value === "save") { try { localStorage.removeItem(key); } catch (e) { /* ignore */ } }
    });
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { saved = null; }
    const empty = !form.querySelector("input[name='sample_id']").value;
    const box = document.getElementById("restore-box");
    if (saved && empty && box) {
      box.hidden = false;
      document.getElementById("restore-btn").addEventListener("click", () => {
        const counters = {};
        const all = fields();
        saved.forEach(([name, value]) => {
          const same = all.filter((f) => f.name === name);
          const i = counters[name] = (counters[name] ?? -1) + 1;
          if (same[i]) same[i].value = value;
        });
        box.hidden = true;
      });
    }
  }

  // Client-side table filters
  document.querySelectorAll("input.table-filter").forEach((inp) => {
    const table = document.getElementById(inp.dataset.table);
    inp.addEventListener("input", () => {
      const words = inp.value.toLowerCase().split(/\s+/).filter(Boolean);
      table.querySelectorAll("tr").forEach((tr, i) => {
        if (i === 0) return;
        const text = tr.textContent.toLowerCase();
        tr.hidden = !words.every((w) => text.includes(w));
      });
    });
  });
  document.querySelectorAll("input.select-all").forEach((box) => {
    box.addEventListener("change", () => {
      document.getElementById(box.dataset.table).querySelectorAll("tr:not([hidden]) input[name='sample']")
        .forEach((c) => { c.checked = box.checked; });
    });
  });

  // Report form: fill the header from a saved header, and signatories from the user list
  const hdr = document.getElementById("saved-header");
  if (hdr) {
    const data = JSON.parse(hdr.dataset.headers || "{}");
    hdr.addEventListener("change", () => {
      const h = data[hdr.value];
      if (!h) return;
      ["division_code", "organisation", "division", "section", "address", "form_number"].forEach((k) => {
        const f = document.querySelector(`input[name='${k}']`);
        if (f && !f.readOnly) f.value = h[k] || "";
      });
      const note = document.getElementById("logo-note");
      if (note) note.textContent = h.has_logo ? "The saved header's logo is used unless you choose another." : "";
    });
  }
  document.querySelectorAll("select.pick-user").forEach((sel) => {
    sel.addEventListener("change", () => {
      const opt = sel.selectedOptions[0];
      if (!opt || !opt.dataset.name) return;
      document.getElementById(sel.dataset.target + "_name").value = opt.dataset.name;
      document.getElementById(sel.dataset.target + "_designation").value = opt.dataset.designation || "";
    });
  });
})();
