(() => {
  const { showError, promptForm, busy, jsonOrErr } = window.CinemaCharacters;
  const grid = document.getElementById("lib-grid");
  const newBtn = document.getElementById("lib-new-btn");

  newBtn.addEventListener("click", async () => {
    const data = await promptForm("New character");
    if (!data) return;
    const fd = new FormData();
    fd.append("name", data.name);
    fd.append("description", data.description);
    try {
      await jsonOrErr(await fetch("/api/library", { method: "POST", body: fd }));
      window.location.reload();
    } catch (e) {
      showError(`Failed: ${e.message}`);
    }
  });

  const urlBtn = document.getElementById("lib-from-url-btn");
  urlBtn.addEventListener("click", () => promptFromUrl());

  function promptFromUrl() {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card">
        <h3>Character from image URL</h3>
        <label><span>Name</span><input type="text" data-field="name" placeholder="Maya Reyes" /></label>
        <label><span>Description (optional)</span><textarea data-field="description" rows="3" placeholder="age, build, hair, eyes, distinguishing marks, wardrobe"></textarea></label>
        <label><span>Image URL</span><input type="url" data-field="url" placeholder="https://www.pinterest.com/pin/... or direct image URL" /></label>
        <fieldset class="radio-group">
          <legend>What to do with the image</legend>
          <label class="radio">
            <input type="radio" name="lib-url-mode" value="as-is" checked />
            <span><strong>Use as-is.</strong> Save the photo directly as the character sheet. Free.</span>
          </label>
          <label class="radio">
            <input type="radio" name="lib-url-mode" value="generate" />
            <span><strong>Generate 3-panel sheet from this reference.</strong> Sends the image to Nano Banana Pro and produces a front · back · close-up sheet locked to it. Costs one image generation.</span>
          </label>
        </fieldset>
        <div class="actions">
          <button class="btn primary" data-action="ok">Add character</button>
          <button class="btn" data-action="cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('[data-action="cancel"]').addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('[data-action="ok"]').addEventListener("click", async () => {
      const name = overlay.querySelector('[data-field="name"]').value.trim();
      const url = overlay.querySelector('[data-field="url"]').value.trim();
      const description = overlay.querySelector('[data-field="description"]').value.trim();
      const mode = overlay.querySelector('input[name="lib-url-mode"]:checked').value;
      if (!name) { showError("Name required"); return; }
      if (!url) { showError("URL required"); return; }
      const ok = overlay.querySelector('[data-action="ok"]');
      ok.disabled = true;
      ok.textContent = mode === "generate" ? "Generating sheet..." : "Fetching...";
      const fd = new FormData();
      fd.append("name", name);
      fd.append("description", description);
      fd.append("url", url);
      fd.append("mode", mode);
      try {
        await jsonOrErr(await fetch("/api/library/from-url", { method: "POST", body: fd }));
        window.location.reload();
      } catch (err) {
        showError(`Failed: ${err.message}`);
        ok.disabled = false;
        ok.textContent = "Add character";
      }
    });
  }

  grid.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-lib-action]");
    if (!btn) return;
    const card = btn.closest("[data-lib-slug]");
    const slug = card.dataset.libSlug;
    const action = btn.dataset.libAction;

    if (action === "regen") {
      card.classList.add("generating");
      card.dataset.busyLabel = "Generating sheet (~30s)...";
      try {
        await jsonOrErr(fetch(`/api/library/${slug}/portrait`, { method: "POST" }));
        window.location.reload();
      } catch (e) {
        card.classList.remove("generating");
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
        await jsonOrErr(await fetch(`/api/library/${slug}/update`, { method: "POST", body: fd }));
        window.location.reload();
      } catch (err) {
        showError(`Update failed: ${err.message}`);
      }
    } else if (action === "delete") {
      if (!confirm("Delete this character from the library?")) return;
      try {
        await jsonOrErr(await fetch(`/api/library/${slug}`, { method: "DELETE" }));
        window.location.reload();
      } catch (err) {
        showError(`Delete failed: ${err.message}`);
      }
    }
  });

  // File upload via the hidden <input type=file> inside each card.
  grid.addEventListener("change", async (e) => {
    const input = e.target.closest('input[type="file"][data-lib-action="upload"]');
    if (!input || !input.files[0]) return;
    const card = input.closest("[data-lib-slug]");
    const slug = card.dataset.libSlug;
    card.classList.add("generating");
    card.dataset.busyLabel = "Uploading...";
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      await jsonOrErr(fetch(`/api/library/${slug}/upload`, { method: "POST", body: fd }));
      window.location.reload();
    } catch (err) {
      card.classList.remove("generating");
      showError(`Upload failed: ${err.message}`);
    }
  });
})();
