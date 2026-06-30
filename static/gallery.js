/**
 * Live polling gallery for analysis_results.json (BEST / KEEP / TRASH).
 * Mount FastAPI static at /static and open /static/gallery.html, or integrate the snippet below.
 */
(function () {
  const POLL_MS = 2000;
  const JSON_URL = "/analysis_results.json";
  const HIGHLIGHT_MS = 4500;

  const GRIDS = {
    best: document.getElementById("grid-best"),
    keep: document.getElementById("grid-keep"),
    trash: document.getElementById("grid-trash"),
  };

  const COUNTS = {
    best: document.getElementById("count-best"),
    keep: document.getElementById("count-keep"),
    trash: document.getElementById("count-trash"),
  };

  const statusEl = document.getElementById("gallery-status");

  /** @type {Map<string, HTMLElement>} */
  const cardById = new Map();
  /** @type {Map<string, string>} */
  const sigById = new Map();
  /** @type {Set<string>} */
  let lastIds = new Set();

  function setStatus(text, ok) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = ok ? "#9ca3af" : "#f87171";
  }

  function inferCategory(item) {
    const c = (item.category || "").toString().trim().toLowerCase();
    if (c === "best" || c === "keep" || c === "trash") return c;
    const p = (item.path || "").replace(/\\/g, "/").toLowerCase();
    if (p.includes("/best/") || p.includes("/ai_best")) return "best";
    if (p.includes("/keep/") || p.includes("/ai_keep")) return "keep";
    if (p.includes("/trash/") || p.includes("/ai_trash")) return "trash";
    return "keep";
  }

  function stableId(item) {
    const p = (item.path || "").trim();
    if (p) return "p:" + p;
    return "f:" + inferCategory(item) + "/" + (item.file || "");
  }

  function collectTags(item) {
    if (Array.isArray(item.tags) && item.tags.length) {
      return item.tags.map(String).slice(0, 16);
    }
    const dc = item.dimension_comments;
    if (dc && typeof dc === "object" && !Array.isArray(dc)) {
      return Object.keys(dc).slice(0, 10);
    }
    return [];
  }

  function itemSignature(item) {
    const tags = collectTags(item).join(",");
    const cat = inferCategory(item);
    const overall = Number(item.overall_score ?? (item.scores && item.scores.overall) ?? 0);
    return [cat, overall.toFixed(2), tags, item.path || "", item.file || ""].join("|");
  }

  function imageSrc(item) {
    const p = item.path || "";
    return "/image?path=" + encodeURIComponent(p);
  }

  function buildTagsHtml(tags) {
    if (!tags.length) return '<span class="tags-empty">—</span>';
    return tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function createCard(item, id) {
    const cat = inferCategory(item);
    const grid = GRIDS[cat];
    if (!grid) return null;

    const wrap = document.createElement("article");
    wrap.className = "g-card is-new";
    wrap.dataset.id = id;

    const overall = Number(item.overall_score ?? (item.scores && item.scores.overall) ?? 0).toFixed(1);
    const tags = collectTags(item);

    wrap.innerHTML = `
      <a class="g-thumb" href="${imageSrc(item)}" target="_blank" rel="noopener">
        <img src="${imageSrc(item)}" alt="" loading="lazy" width="280" height="200" />
      </a>
      <div class="g-meta">
        <div class="g-score"><span class="g-score-val">${overall}</span> <span class="g-score-lbl">overall</span></div>
        <div class="g-tags">${buildTagsHtml(tags)}</div>
        <div class="g-file" title="${escapeHtml(item.path || item.file || "")}">${escapeHtml(item.file || (item.path || "").split(/[/\\\\]/).pop() || "")}</div>
      </div>
    `;

    grid.appendChild(wrap);
    window.setTimeout(() => wrap.classList.remove("is-new"), HIGHLIGHT_MS);
    return wrap;
  }

  function updateCard(el, item) {
    const overall = Number(item.overall_score ?? (item.scores && item.scores.overall) ?? 0).toFixed(1);
    const tags = collectTags(item);
    const scoreVal = el.querySelector(".g-score-val");
    const tagsEl = el.querySelector(".g-tags");
    const img = el.querySelector("img");
    const link = el.querySelector(".g-thumb");
    if (scoreVal) scoreVal.textContent = overall;
    if (tagsEl) tagsEl.innerHTML = buildTagsHtml(tags);
    const src = imageSrc(item);
    if (img) img.src = src;
    if (link) link.href = src;
  }

  function moveCardIfNeeded(el, item) {
    const cat = inferCategory(item);
    const grid = GRIDS[cat];
    if (grid && el.parentElement !== grid) {
      grid.appendChild(el);
    }
  }

  function removeOrphans(nextIds) {
    for (const [id, el] of cardById) {
      if (!nextIds.has(id)) {
        el.remove();
        cardById.delete(id);
        sigById.delete(id);
      }
    }
  }

  function applyPayload(list) {
    if (!Array.isArray(list)) {
      setStatus("Invalid JSON (expected array)", false);
      return;
    }

    const nextIds = new Set();
    const byId = new Map();
    for (const item of list) {
      const id = stableId(item);
      nextIds.add(id);
      byId.set(id, item);
    }

    for (const id of nextIds) {
      const item = byId.get(id);
      const sig = itemSignature(item);
      let el = cardById.get(id);
      if (!el) {
        el = createCard(item, id);
        if (el) cardById.set(id, el);
        sigById.set(id, sig);
        continue;
      }
      if (sigById.get(id) !== sig) {
        updateCard(el, item);
        moveCardIfNeeded(el, item);
        sigById.set(id, sig);
      }
    }

    const newlyAdded = [...nextIds].filter((id) => !lastIds.has(id));
    for (const id of newlyAdded) {
      const el = cardById.get(id);
      if (el && !el.classList.contains("is-new")) {
        el.classList.add("is-new");
        window.setTimeout(() => el.classList.remove("is-new"), HIGHLIGHT_MS);
      }
    }

    lastIds = nextIds;
    removeOrphans(nextIds);

    const bc = [...list].filter((x) => inferCategory(x) === "best").length;
    const kc = [...list].filter((x) => inferCategory(x) === "keep").length;
    const tc = [...list].filter((x) => inferCategory(x) === "trash").length;
    if (COUNTS.best) COUNTS.best.textContent = String(bc);
    if (COUNTS.keep) COUNTS.keep.textContent = String(kc);
    if (COUNTS.trash) COUNTS.trash.textContent = String(tc);

    const ts = new Date().toLocaleTimeString();
    setStatus(`Updated ${ts} · ${list.length} items`, true);
  }

  async function poll() {
    try {
      const res = await fetch(JSON_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      applyPayload(data);
    } catch (e) {
      setStatus(String(e && e.message ? e.message : e), false);
    }
  }

  poll();
  window.setInterval(poll, POLL_MS);
})();
