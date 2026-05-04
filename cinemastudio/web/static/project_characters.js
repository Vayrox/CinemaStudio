(() => {
  const { showError, promptForm, busy, jsonOrErr } = window.CinemaCharacters;
  const slug = window.SLUG;
  const grid = document.getElementById("char-grid");
  const newBtn = document.getElementById("char-new-btn");
  const importBtn = document.getElementById("char-import-btn");
  if (!grid) return;

  newBtn.addEventListener("click", async () => {
    const data = await promptForm("New character");
    if (!data) return;
    const fd = new FormData();
    fd.append("name", data.name);
    fd.append("description", data.description);
    try {
      await jsonOrErr(await fetch(`/api/projects/${slug}/characters`, { method: "POST", body: fd }));
      window.location.reload();
    } catch (e) {
      showError(`Failed: ${e.message}`);
    }
  });

  importBtn.addEventListener("click", async () => {
    let data;
    try {
      data = await jsonOrErr(await fetch("/api/library"));
    } catch (e) {
      showError(`Failed to load library: ${e.message}`);
      return;
    }
    const characters = data.characters || [];
    if (!characters.length) {
      showError("Library is empty. Add characters at /library first.");
      return;
    }
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    const cards = characters
      .map(
        (c) => `
      <button type="button" class="lib-pick" data-pick-slug="${c.slug}">
        ${c.has_portrait ? `<img src="/library-files/${c.slug}/portrait" alt="${c.name}" />` : '<div class="char-portrait-placeholder">no portrait</div>'}
        <div class="lib-pick-meta">
          <strong>${c.name}</strong>
          <span class="muted small">${(c.description || "").slice(0, 80)}</span>
        </div>
      </button>`
      )
      .join("");
    overlay.innerHTML = `
      <div class="modal-card wide">
        <h3>Import from library</h3>
        <div class="lib-pick-grid">${cards}</div>
        <div class="actions"><button class="btn" data-action="cancel">Close</button></div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
    overlay.querySelectorAll("[data-pick-slug]").forEach((b) =>
      b.addEventListener("click", async () => {
        const libSlug = b.dataset.pickSlug;
        try {
          await jsonOrErr(
            await fetch(`/api/projects/${slug}/characters/import/${libSlug}`, { method: "POST" })
          );
          window.location.reload();
        } catch (err) {
          showError(`Import failed: ${err.message}`);
        }
      })
    );
  });

  grid.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-char-action]");
    if (!btn) return;
    const card = btn.closest("[data-char-slug]");
    const charSlug = card.dataset.charSlug;
    const action = btn.dataset.charAction;

    if (action === "regen") {
      try {
        await busy(card, "regen", () =>
          jsonOrErr(fetch(`/api/projects/${slug}/characters/${charSlug}/portrait`, { method: "POST" }))
        );
        window.location.reload();
      } catch (e) {
        showError(`Generation failed: ${e.message}`);
      }
    } else if (action === "edit") {
      const data = await promptForm("Edit character", {
        name: card.querySelector("h4").textContent,
        description: card.querySelector(".muted.small").textContent.trim(),
      });
      if (!data) return;
      const fd = new FormData();
      fd.append("name", data.name);
      fd.append("description", data.description);
      try {
        await jsonOrErr(
          await fetch(`/api/projects/${slug}/characters/${charSlug}/update`, { method: "POST", body: fd })
        );
        window.location.reload();
      } catch (err) {
        showError(`Update failed: ${err.message}`);
      }
    } else if (action === "delete") {
      if (!confirm("Delete this character from the project?")) return;
      try {
        await jsonOrErr(
          await fetch(`/api/projects/${slug}/characters/${charSlug}`, { method: "DELETE" })
        );
        window.location.reload();
      } catch (err) {
        showError(`Delete failed: ${err.message}`);
      }
    } else if (action === "save-to-library") {
      const name = card.querySelector("h4").textContent;
      const description = card.querySelector(".muted.small").textContent.trim();
      const fd = new FormData();
      fd.append("name", name);
      fd.append("description", description);
      try {
        const data = await jsonOrErr(await fetch("/api/library", { method: "POST", body: fd }));
        // If a portrait exists in the project, also push it to library.
        const img = card.querySelector("img");
        if (img && data.slug) {
          const blob = await fetch(img.src).then((r) => r.blob());
          const upFd = new FormData();
          upFd.append("file", blob, "portrait.jpg");
          await jsonOrErr(
            await fetch(`/api/library/${data.slug}/upload`, { method: "POST", body: upFd })
          );
        }
        showError(`Saved "${name}" to your library.`);
      } catch (err) {
        showError(`Save failed: ${err.message}`);
      }
    }
  });

  grid.addEventListener("change", async (e) => {
    const input = e.target.closest('input[type="file"][data-char-action="upload"]');
    if (!input || !input.files[0]) return;
    const card = input.closest("[data-char-slug]");
    const charSlug = card.dataset.charSlug;
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      await busy(card, "upload", () =>
        jsonOrErr(fetch(`/api/projects/${slug}/characters/${charSlug}/upload`, { method: "POST", body: fd }))
      );
      window.location.reload();
    } catch (err) {
      showError(`Upload failed: ${err.message}`);
    }
  });
})();
