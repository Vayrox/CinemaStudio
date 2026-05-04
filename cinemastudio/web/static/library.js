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

  grid.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-lib-action]");
    if (!btn) return;
    const card = btn.closest("[data-lib-slug]");
    const slug = card.dataset.libSlug;
    const action = btn.dataset.libAction;

    if (action === "regen") {
      try {
        await busy(card, "regen", () =>
          jsonOrErr(fetch(`/api/library/${slug}/portrait`, { method: "POST" }))
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
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      await busy(card, "upload", () =>
        jsonOrErr(fetch(`/api/library/${slug}/upload`, { method: "POST", body: fd }))
      );
      window.location.reload();
    } catch (err) {
      showError(`Upload failed: ${err.message}`);
    }
  });
})();
