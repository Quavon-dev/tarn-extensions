"use strict";

(function () {
  const script = document.currentScript;
  const searchUrl = new URL("search.json", script.src);

  function normalize(text) {
    return text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function start() {
    const form = document.querySelector("form.search");
    const input = document.getElementById("q");
    const results = document.getElementById("results");
    const status = document.getElementById("status");
    if (!form || !input || !results || !status) {
      return;
    }

    const params = new URLSearchParams(location.search);
    const fixed = form.querySelector('input[name="category"]');
    const chips = document.querySelectorAll("a.chip[data-category]");
    const labels = new Map(Array.from(chips, (chip) => [chip.dataset.category, chip.textContent]));
    const requested = params.get("category");
    const category = fixed ? fixed.value : labels.has(requested) ? requested : "";
    const cards = Array.from(results.querySelectorAll("li[data-id]"));
    let haystacks = null;

    if (!fixed && category) {
      document.querySelectorAll('a.chip[aria-current="page"]').forEach((chip) => chip.removeAttribute("aria-current"));
      chips.forEach((chip) => {
        if (chip.dataset.category === category) {
          chip.setAttribute("aria-current", "page");
        }
      });
    }

    function remember(query) {
      const next = new URLSearchParams();
      if (query) {
        next.set("q", query);
      }
      if (!fixed && category) {
        next.set("category", category);
      }
      const search = next.toString();
      history.replaceState(null, "", search ? "?" + search : location.pathname);
    }

    function apply() {
      const query = input.value.trim();
      const terms = normalize(query).split(/\s+/).filter(Boolean);
      let shown = 0;
      for (const card of cards) {
        const haystack = haystacks.get(card.dataset.id) || "";
        const visible = (!category || card.dataset.category === category) && terms.every((term) => haystack.includes(term));
        card.hidden = !visible;
        shown += visible ? 1 : 0;
      }
      const scope = category && !fixed ? " in " + labels.get(category) : "";
      if (!query && !scope) {
        status.textContent = "";
      } else if (shown === 0) {
        status.textContent = "No extensions match" + (query ? " \u201c" + query + "\u201d" : "") + scope + ".";
      } else {
        status.textContent = shown + (shown === 1 ? " extension" : " extensions") + scope + ".";
      }
      remember(query);
    }

    fetch(searchUrl, { credentials: "omit" })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(response.statusText))))
      .then((entries) => {
        haystacks = new Map(
          entries.map((entry) => [
            entry.id,
            normalize([entry.name, entry.description, entry.author, entry.category, labels.get(entry.category) || ""].join(" ")),
          ])
        );
        if (!input.value) {
          input.value = params.get("q") || "";
        }
        input.addEventListener("input", apply);
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          apply();
        });
        document.addEventListener("keydown", (event) => {
          const target = event.target;
          const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable;
          if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
            event.preventDefault();
            input.focus();
          }
        });
        apply();
      })
      .catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
