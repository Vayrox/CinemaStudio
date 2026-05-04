// CinemaStudio frontend.
// - Wires action buttons (moodboard, generate, regen-keyframe, regen-clip)
// - Streams job logs over SSE into #log
// - Auto-reloads the page when a long-running job finishes so new media shows up

(function () {
  const slug = window.PROJECT_SLUG;
  const logEl = document.getElementById("log");
  const live = window.LIVE_JOBS || [];

  if (!slug || !logEl) return;

  function append(line, level) {
    const span = document.createElement("span");
    span.className = level === "error" ? "err" : level === "done" ? "ok" : "";
    const ts = new Date().toLocaleTimeString();
    span.textContent = `[${ts}] ${line}\n`;
    logEl.appendChild(span);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function streamJob(jobId, { reloadOnDone = true } = {}) {
    const es = new EventSource(`/jobs/${jobId}/stream`);
    es.addEventListener("log", (e) => {
      try {
        const d = JSON.parse(e.data);
        append(d.message, d.level);
      } catch {
        append(e.data, "info");
      }
    });
    es.addEventListener("end", (e) => {
      append(`— job ${e.data}`, e.data === "done" ? "done" : "error");
      es.close();
      if (reloadOnDone && e.data === "done") {
        setTimeout(() => location.reload(), 800);
      }
    });
    es.onerror = () => {
      es.close();
      append("(connection lost)", "error");
    };
  }

  // Resume any in-flight jobs from server-side render.
  live.forEach((id) => streamJob(id, { reloadOnDone: false }));

  async function trigger(action, shot) {
    let url;
    let opts = { method: "POST" };
    if (action === "moodboard") url = `/projects/${slug}/moodboard`;
    else if (action === "generate") url = `/projects/${slug}/generate`;
    else if (action === "regen-keyframe")
      url = `/projects/${slug}/shots/${shot}/regen-keyframe`;
    else if (action === "regen-clip")
      url = `/projects/${slug}/shots/${shot}/regen-clip`;
    else return;

    append(`▸ ${action}${shot ? " shot " + shot : ""} requested`, "info");
    try {
      const r = await fetch(url, opts);
      if (!r.ok) {
        append(`error ${r.status}: ${await r.text()}`, "error");
        return;
      }
      const { job_id } = await r.json();
      streamJob(job_id);
    } catch (e) {
      append(`error: ${e}`, "error");
    }
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    ev.preventDefault();
    trigger(btn.dataset.action, btn.dataset.shot);
  });

  // If the screenplay is still being generated (no shots rendered), poll for it.
  if (!document.querySelector(".shots") && live.length === 0) {
    fetch(`/projects/${slug}/jobs`)
      .then((r) => r.json())
      .then((jobs) => {
        const running = jobs.find((j) => j.status === "running");
        if (running) streamJob(running.id);
        else setTimeout(() => location.reload(), 3000);
      });
  }
})();
