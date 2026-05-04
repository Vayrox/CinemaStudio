(() => {
  const { showError, promptForm, busy, jsonOrErr } = window.CinemaCharacters;
  const slug = window.SLUG;
  const grid = document.getElementById("char-grid");
  const newBtn = document.getElementById("char-new-btn");
  const importBtn = document.getElementById("char-import-btn");
  const resetBtn = document.getElementById("char-reset-btn");
  if (!grid) return;

  resetBtn.addEventListener("click", async (e) => {
    const wipeSheets = e.shiftKey;
    const promptText = wipeSheets
      ? "Shift-click detected: this will WIPE the cast AND delete every character sheet file on disk. Irreversible. Continue?"
      : "Reset cast?\n\nThis wipes the cast list and rebuilds it from your screenplay. " +
        "Generated/uploaded sheet files on disk are KEPT (so you don't lose them).\n\n" +
        "Tip: hold Shift while clicking Reset cast to also delete the sheet files.";
    if (!confirm(promptText)) return;
    const fd = new FormData();
    fd.append("keep_sheets", wipeSheets ? "0" : "1");
    try {
      await jsonOrErr(await fetch(`/api/projects/${slug}/cast/reset`, { method: "POST", body: fd }));
      window.location.reload();
    } catch (err) {
      showError(`Reset failed: ${err.message}`);
    }
  });

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

  // Inline rename: commit on blur or Enter when value differs from original.
  grid.addEventListener("blur", async (e) => {
    const input = e.target.closest("[data-char-rename]");
    if (!input) return;
    const newName = input.value.trim();
    const oldName = input.dataset.original;
    if (!newName || newName === oldName) {
      input.value = oldName;
      return;
    }
    const card = input.closest("[data-char-slug]");
    const fd = new FormData();
    fd.append("old_name", oldName);
    fd.append("new_name", newName);
    try {
      await jsonOrErr(await fetch(`/api/projects/${slug}/cast/update`, { method: "POST", body: fd }));
      window.location.reload();
    } catch (err) {
      showError(`Rename failed: ${err.message}`);
      input.value = oldName;
    }
  }, true);
  grid.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest("[data-char-rename]")) {
      e.preventDefault();
      e.target.blur();
    }
  });

  async function pickFromLibrary() {
    let data;
    try {
      data = await jsonOrErr(await fetch("/api/library"));
    } catch (e) {
      showError(`Failed to load library: ${e.message}`);
      return null;
    }
    const characters = data.characters || [];
    if (!characters.length) {
      showError("Library is empty. Add characters at /library first.");
      return null;
    }
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      const cards = characters
        .map(
          (c) => `
        <button type="button" class="lib-pick" data-pick-slug="${c.slug}">
          ${c.has_portrait ? `<img src="/library-files/${c.slug}/portrait" alt="${c.name}" />` : '<div class="char-portrait-placeholder">no sheet</div>'}
          <div class="lib-pick-meta">
            <strong>${c.name}</strong>
            <span class="muted small">${(c.description || "").slice(0, 80)}</span>
          </div>
        </button>`
        )
        .join("");
      overlay.innerHTML = `
        <div class="modal-card wide">
          <h3>Pick a character</h3>
          <div class="lib-pick-grid">${cards}</div>
          <div class="actions"><button class="btn" data-action="cancel">Close</button></div>
        </div>
      `;
      document.body.appendChild(overlay);
      const close = (val) => { overlay.remove(); resolve(val); };
      overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => close(null));
      overlay.addEventListener("click", (e) => { if (e.target === overlay) close(null); });
      overlay.querySelectorAll("[data-pick-slug]").forEach((b) =>
        b.addEventListener("click", () => close(b.dataset.pickSlug))
      );
    });
  }

  importBtn.addEventListener("click", async () => {
    const libSlug = await pickFromLibrary();
    if (!libSlug) return;
    try {
      await jsonOrErr(
        await fetch(`/api/projects/${slug}/characters/import/${libSlug}`, { method: "POST" })
      );
      window.location.reload();
    } catch (err) {
      showError(`Import failed: ${err.message}`);
    }
  });

  grid.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-char-action]");
    if (!btn) return;
    const card = btn.closest("[data-char-slug]");
    const charSlug = card.dataset.charSlug;
    const action = btn.dataset.charAction;

    if (action === "regen") {
      // Server runs this through JobRunner so we get live-log streaming.
      // Show an overlay on the card, kick off the job, switch to the log
      // tab, and let the existing SSE flow reload the page when it ends.
      card.classList.add("generating");
      card.dataset.busyLabel = "Generating sheet...";
      try {
        await jsonOrErr(
          fetch(`/api/projects/${slug}/characters/${charSlug}/portrait`, { method: "POST" })
        );
      } catch (err) {
        card.classList.remove("generating");
        showError(`Generation failed: ${err.message}`);
        return;
      }
      if (window.CinemaLog) {
        window.CinemaLog.clearLog();
        window.CinemaLog.setStatus("running");
        window.CinemaLog.switchToLogs();
        window.CinemaLog.listen({ reloadOnEnd: true });
      }
    } else if (action === "edit") {
      const data = await promptForm("Edit character", {
        name: card.dataset.charName,
        description: card.querySelector(".char-desc").textContent.trim(),
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
    } else if (action === "replace") {
      const libSlug = await pickFromLibrary();
      if (!libSlug) return;
      const oldName = card.dataset.charName;
      const fd = new FormData();
      fd.append("old_name", oldName);
      fd.append("lib_slug", libSlug);
      try {
        await jsonOrErr(
          await fetch(`/api/projects/${slug}/cast/update`, { method: "POST", body: fd })
        );
        window.location.reload();
      } catch (err) {
        showError(`Replace failed: ${err.message}`);
      }
    } else if (action === "save-to-library") {
      const name = card.dataset.charName;
      const description = card.querySelector(".char-desc").textContent.trim();
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
    card.classList.add("generating");
    card.dataset.busyLabel = "Uploading...";
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      await jsonOrErr(
        fetch(`/api/projects/${slug}/characters/${charSlug}/upload`, { method: "POST", body: fd })
      );
      window.location.reload();
    } catch (err) {
      card.classList.remove("generating");
      showError(`Upload failed: ${err.message}`);
    }
  });
})();
