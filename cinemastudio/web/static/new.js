(() => {
  const btn = document.getElementById("ideas-btn");
  const panel = document.getElementById("ideas-panel");
  const seed = document.getElementById("ideas-seed");
  const go = document.getElementById("ideas-go");
  const close = document.getElementById("ideas-close");
  const list = document.getElementById("ideas-list");
  const logline = document.getElementById("logline");

  if (!btn) return;

  btn.addEventListener("click", () => {
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) seed.focus();
  });
  close.addEventListener("click", () => panel.classList.add("hidden"));

  async function fetchIdeas() {
    const value = seed.value.trim();
    if (!value) {
      seed.focus();
      return;
    }
    list.innerHTML = '<p class="muted small">Generating loglines... (Gemini is thinking)</p>';
    go.disabled = true;
    try {
      const fd = new FormData();
      fd.append("seed", value);
      fd.append("count", "10");
      const r = await fetch("/api/loglines", { method: "POST", body: fd });
      if (!r.ok) {
        const text = await r.text();
        list.innerHTML = `<p class="banner err">${text}</p>`;
        return;
      }
      const data = await r.json();
      list.innerHTML = "";
      (data.loglines || []).forEach((line) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "idea-item";
        item.textContent = line;
        item.addEventListener("click", () => {
          logline.value = line;
          panel.classList.add("hidden");
          logline.focus();
        });
        list.appendChild(item);
      });
    } catch (err) {
      list.innerHTML = `<p class="banner err">${err}</p>`;
    } finally {
      go.disabled = false;
    }
  }

  go.addEventListener("click", fetchIdeas);
  seed.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      fetchIdeas();
    }
  });
})();
