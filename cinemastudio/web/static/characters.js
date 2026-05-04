// Shared helpers for character cards (project tab + library page).
window.CinemaCharacters = (() => {
  function showError(msg) {
    const banner = document.createElement("div");
    banner.className = "banner err";
    banner.textContent = msg;
    document.querySelector("main").prepend(banner);
    setTimeout(() => banner.remove(), 5000);
  }

  function promptForm(title, defaults = {}) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML = `
        <div class="modal-card">
          <h3>${title}</h3>
          <label><span>Name</span><input type="text" data-field="name" value="${(defaults.name || "").replace(/"/g, "&quot;")}" /></label>
          <label><span>Description</span><textarea data-field="description" rows="4">${defaults.description || ""}</textarea></label>
          <p class="muted small">Lock physical traits: age, build, hair, eyes, distinctive marks, wardrobe.</p>
          <div class="actions">
            <button class="btn primary" data-action="ok">Save</button>
            <button class="btn" data-action="cancel">Cancel</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const finish = (val) => {
        overlay.remove();
        resolve(val);
      };
      overlay.querySelector('[data-action="ok"]').addEventListener("click", () => {
        const name = overlay.querySelector('[data-field="name"]').value.trim();
        const description = overlay.querySelector('[data-field="description"]').value.trim();
        if (!name) return;
        finish({ name, description });
      });
      overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => finish(null));
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) finish(null);
      });
    });
  }

  async function busy(card, action, fn) {
    const btn = card.querySelector(`[data-${action}]`) || card;
    btn.classList.add("busy");
    try {
      return await fn();
    } finally {
      btn.classList.remove("busy");
    }
  }

  async function jsonOrErr(respOrPromise) {
    const resp = await respOrPromise;
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  return { showError, promptForm, busy, jsonOrErr };
})();
