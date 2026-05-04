(() => {
  const slug = window.SLUG;
  const logEl = document.getElementById("log");
  const statusEl = document.getElementById("job-status");
  let evtSource = null;

  function fmtTs(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString();
  }

  function appendLine(entry) {
    if (!entry) return;
    const line = document.createElement("div");
    line.className = "line";
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = fmtTs(entry.ts);
    const lvl = document.createElement("span");
    lvl.className = `lvl ${entry.level}`;
    lvl.textContent = `[${entry.level}]`;
    const msg = document.createElement("span");
    msg.textContent = " " + entry.msg;
    line.appendChild(ts);
    line.appendChild(lvl);
    line.appendChild(msg);
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.className = "badge " + text;
  }

  function connect({ reloadOnEnd } = { reloadOnEnd: true }) {
    if (evtSource) {
      evtSource.close();
      evtSource = null;
    }
    evtSource = new EventSource(`/projects/${slug}/events`);
    evtSource.onmessage = (e) => {
      try {
        const entry = JSON.parse(e.data);
        appendLine(entry);
        if (!entry) return;
        if (entry.level === "error") setStatus("failed");
        if (entry.level === "success" && entry.msg && entry.msg.startsWith("Job completed")) {
          setStatus("succeeded");
        }
      } catch {}
    };
    evtSource.addEventListener("end", () => {
      evtSource.close();
      evtSource = null;
      if (reloadOnEnd) setTimeout(() => window.location.reload(), 1200);
    });
    evtSource.onerror = () => {
      // Silent — server may briefly close between subscriptions.
    };
  }

  async function runStep(kind) {
    logEl.innerHTML = "";
    setStatus("running");
    const r = await fetch(`/projects/${slug}/run/${kind}`, { method: "POST" });
    if (r.status === 409) {
      appendLine({ ts: Date.now() / 1000, level: "warn", msg: "A job is already running for this project." });
      return;
    }
    if (!r.ok) {
      const text = await r.text();
      appendLine({ ts: Date.now() / 1000, level: "error", msg: `Failed to start: ${text}` });
      setStatus("failed");
      return;
    }
    connect({ reloadOnEnd: true });
  }

  async function cancelJob() {
    await fetch(`/projects/${slug}/cancel`, { method: "POST" });
  }

  // Wire run buttons.
  document.querySelectorAll("[data-run]").forEach((btn) => {
    btn.addEventListener("click", () => runStep(btn.dataset.run));
  });
  const cancelBtn = document.querySelector("[data-cancel]");
  if (cancelBtn) cancelBtn.addEventListener("click", cancelJob);

  // Tabs.
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      if (tab.disabled) return;
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      document
        .querySelectorAll(".tab-panel")
        .forEach((p) => p.classList.add("hidden"));
      const panel = document.getElementById(`panel-${tab.dataset.tab}`);
      if (panel) panel.classList.remove("hidden");
    });
  });

  // Regenerate buttons on individual moodboards / keyframes.
  document.querySelectorAll("[data-regen-moodboard]").forEach((btn) => {
    btn.addEventListener("click", () => regenAsset("moodboard", btn));
  });
  document.querySelectorAll("[data-regen-keyframe]").forEach((btn) => {
    btn.addEventListener("click", () => regenAsset("keyframe", btn));
  });

  async function regenAsset(kind, btn) {
    const filename = btn.dataset[kind === "moodboard" ? "regenMoodboard" : "regenKeyframe"];
    let idx;
    if (kind === "moodboard") {
      const m = filename.match(/scene_(\d+)_moodboard/);
      if (!m) return;
      idx = parseInt(m[1], 10);
    } else {
      const m = filename.match(/shot_(\d+)/);
      if (!m) return;
      idx = parseInt(m[1], 10);
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Regenerating...";
    try {
      const r = await fetch(`/projects/${slug}/${kind === "moodboard" ? "regenerate/moodboard" : "regenerate/keyframe"}/${idx}`.replace("/projects/", "/api/projects/"), { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      // Force-refresh the thumb via cache-busting query param.
      const card = btn.closest(".thumb");
      const img = card.querySelector("img");
      if (img) img.src = img.src.split("?")[0] + "?t=" + Date.now();
      const link = card.querySelector("a");
      if (link) link.href = link.href.split("?")[0] + "?t=" + Date.now();
      btn.textContent = "Done";
      setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1500);
    } catch (err) {
      btn.textContent = "Failed";
      btn.disabled = false;
      console.error(err);
      alert(`Regenerate failed: ${err.message || err}`);
      setTimeout(() => { btn.textContent = original; }, 2000);
    }
  }

  // On load: if a job is currently running, stream and reload at end.
  // Otherwise, replay prior history once without reload.
  if (window.JOB_STATUS === "running") {
    connect({ reloadOnEnd: true });
  } else if (window.JOB_STATUS && window.JOB_STATUS !== "idle") {
    connect({ reloadOnEnd: false });
  }
})();
