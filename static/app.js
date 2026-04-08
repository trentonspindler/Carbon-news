/* Carbon Markets Daily — frontend */

"use strict";

// ── State ────────────────────────────────────────────────────────────────────

let currentTopic      = "all";
let currentRegion     = "all";
let currentSearch     = "";
let currentPaywall    = "all";
let currentWeekOffset = 0;       // 0 = this week, -1 = last week, etc.
let debounceTimer     = null;
let refreshPoller     = null;
let refreshing        = false;

let allArticles       = [];
const REGION_META     = {};      // populated from DOM on load
let weekMeta          = [];      // populated from /api/weeks

// ── Topic metadata ────────────────────────────────────────────────────────────

const TOPIC_META = {
  vcm:        { label: "💱 VCM",        color: "#16a34a" },
  cdr:        { label: "⚗️ CDR",        color: "#2563eb" },
  nature:     { label: "🌿 Nature",     color: "#15803d" },
  policy:     { label: "⚖️ Policy",     color: "#92400e" },
  compliance: { label: "📋 Compliance", color: "#6d28d9" },
  corporate:  { label: "🏦 Corporate",  color: "#1e40af" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function relDate(isoStr) {
  if (!isoStr) return "";
  try {
    const ms   = Date.now() - new Date(isoStr).getTime();
    const mins = Math.floor(ms / 60000);
    if (mins < 1)  return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days === 1) return "Yesterday";
    return `${days}d ago`;
  } catch { return ""; }
}

function $id(id) { return document.getElementById(id); }

// ── Fetch & Render ────────────────────────────────────────────────────────────

async function loadArticles({ showSpinner = true } = {}) {
  if (showSpinner) {
    $id("loadingState").classList.remove("hidden");
    $id("articleGrid").classList.add("hidden");
    $id("statsBar").classList.add("hidden");
    $id("emptyState").classList.add("hidden");
    $id("digestPanel").classList.add("hidden");
    $id("regionBanner").classList.add("hidden");
  }

  try {
    const params = new URLSearchParams({
      topic:     currentTopic,
      region:    currentRegion,
      search:    currentSearch,
      paywalled: currentPaywall,
      week:      currentWeekOffset,
    });
    const res  = await fetch(`/api/articles?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    allArticles = data.articles || [];
    renderArticles(allArticles);
    updateCacheLabel(data.cache_age_minutes);
    updateStats(data);
  } catch (err) {
    console.error("Fetch failed:", err);
    $id("loadingState").classList.add("hidden");
    $id("emptyState").classList.remove("hidden");
  }
}

function renderArticles(articles) {
  $id("loadingState").classList.add("hidden");

  if (!articles.length) {
    const msg = $id("emptyMsg");
    if (msg) {
      msg.textContent = currentWeekOffset < 0
        ? "No archived articles for this week yet — history builds up as the app runs."
        : "No articles found. Try a different topic or search term.";
    }
    $id("emptyState").classList.remove("hidden");
    $id("articleGrid").classList.add("hidden");
    $id("statsBar").classList.add("hidden");
    return;
  }

  $id("emptyState").classList.add("hidden");
  $id("statsBar").classList.remove("hidden");

  const grid = $id("articleGrid");
  grid.innerHTML = articles.map(buildCard).join("");
  grid.classList.remove("hidden");
}

function buildCard(a) {
  const isPaywall  = a.paywalled;
  const isPartial  = !a.paywalled && a.partial_paywall;
  const isFree     = !a.paywalled && !a.partial_paywall;

  const cardClass  = isPaywall ? "card is-paywall" : isPartial ? "card is-partial" : "card";

  const badge      = isPaywall
    ? `<span class="badge badge-pay">🔒 Paywalled</span>`
    : isPartial
    ? `<span class="badge badge-partial">⚠ Partial</span>`
    : `<span class="badge badge-free">✓ Free</span>`;

  const summaryHtml = a.summary
    ? `<p class="card-summary">${esc(a.summary)}</p>`
    : "";

  const paywallNote = isPaywall
    ? `<p class="card-paywall-note">Summary from feed/metadata — full article requires subscription.</p>`
    : "";

  const topicInfo = TOPIC_META[a.topic] || null;
  const topicTag  = topicInfo
    ? `<span class="card-topic" style="background:${topicInfo.color}18;color:${topicInfo.color}">${topicInfo.label}</span>`
    : `<span></span>`;

  // Region tags (show up to 3 to avoid clutter)
  const regionTags = (a.regions || []).slice(0, 3).map(rid => {
    const rm = REGION_META[rid];
    return rm
      ? `<span class="region-tag" title="${rm.name}" onclick="setRegionById('${rid}')">${rm.flag} ${rm.name}</span>`
      : "";
  }).join("");
  const regionHtml = regionTags
    ? `<div class="card-regions">${regionTags}</div>`
    : "";

  const readLabel = isPaywall ? "View →" : "Read →";
  const dateStr   = a.published_rel || relDate(a.published);

  return `
<div class="${cardClass}">
  <div class="card-meta">
    <span class="card-source">${esc(a.source || "Unknown")}</span>
    ${dateStr ? `<span class="card-dot">·</span><span>${esc(dateStr)}</span>` : ""}
    <span class="card-dot">·</span>
    ${badge}
  </div>
  <a class="card-title" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">
    ${esc(a.title)}
  </a>
  ${summaryHtml}
  ${paywallNote}
  ${regionHtml}
  <div class="card-footer">
    ${topicTag}
    <a class="card-read" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">
      ${readLabel}
    </a>
  </div>
</div>`;
}

function updateStats(data) {
  const txt = $id("statsText");
  if (!txt) return;
  txt.textContent =
    `${data.total} articles this week — ` +
    `${data.free_count} free, ${data.paywalled_count} paywalled`;
}

function updateCacheLabel(ageMinutes) {
  const el = $id("cacheLabel");
  if (!el) return;
  if (ageMinutes === null || ageMinutes === undefined) {
    el.textContent = "Live";
    return;
  }
  el.textContent = ageMinutes === 0 ? "Just updated" : `Updated ${ageMinutes}m ago`;
}

// ── Filtering ─────────────────────────────────────────────────────────────────

function setTopic(topic, btn) {
  currentTopic = topic;
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadArticles();
}

function setRegion(region, btn) {
  currentRegion = region;
  document.querySelectorAll(".region-chip").forEach(c => c.classList.remove("active"));
  if (btn) btn.classList.add("active");
  updateRegionBanner();
  loadArticles();
}

function setRegionById(regionId) {
  const btn = document.querySelector(`.region-chip[data-region="${regionId}"]`);
  setRegion(regionId, btn);
  // Scroll the region tab row to show the selected chip
  if (btn) btn.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
}

function updateRegionBanner() {
  const banner = $id("regionBanner");
  if (!banner) return;
  if (currentRegion === "all") {
    banner.classList.add("hidden");
    return;
  }
  const rm = REGION_META[currentRegion];
  if (rm) {
    banner.textContent = `${rm.flag} Showing articles tagged: ${rm.name}`;
    banner.classList.remove("hidden");
  }
}

function applyFilters() {
  currentPaywall = $id("paywallFilter").value;
  loadArticles();
}

// ── Week navigation ───────────────────────────────────────────────────────────

function goWeek(delta) {
  const next = currentWeekOffset + delta;
  if (next > 0) return;                         // can't go into the future
  if (next < -(weekMeta.length - 1)) return;    // can't go past available data
  jumpToWeek(next);
}

function jumpToWeek(offset) {
  currentWeekOffset = offset;
  renderWeekPills();
  loadArticles();
}

function renderWeekPills() {
  const container = $id("weekPills");
  const prevBtn   = $id("weekPrev");
  const nextBtn   = $id("weekNext");
  if (!container) return;

  // Build pills from weekMeta (always show at least current + last 4)
  const slots = weekMeta.length ? weekMeta : buildDefaultWeeks(5);

  container.innerHTML = slots.map(w => {
    const label  = weekLabel(w.offset);
    const dates  = weekDates(w.week_start);
    const count  = w.count || 0;
    const active = w.offset === currentWeekOffset ? "active" : "";
    const empty  = (w.offset !== 0 && count === 0) ? "empty" : "";
    const title  = count > 0 ? `${count} articles` : w.offset === 0 ? "Live" : "No data yet";
    return `<button class="week-pill ${active} ${empty}"
              data-offset="${w.offset}"
              onclick="jumpToWeek(${w.offset})"
              title="${title}">
      ${label}
      <span class="week-pill-dates">${dates}</span>
      ${count > 0 && w.offset !== 0 ? `<span class="week-pill-count">${count}</span>` : ""}
    </button>`;
  }).join("");

  // Arrow states
  if (prevBtn) prevBtn.disabled = currentWeekOffset <= -(slots.length - 1);
  if (nextBtn) nextBtn.disabled = currentWeekOffset >= 0;
}

function weekLabel(offset) {
  if (offset === 0)  return "This Week";
  if (offset === -1) return "Last Week";
  return `${Math.abs(offset)} Weeks Ago`;
}

function weekDates(isoStart) {
  if (!isoStart) return "";
  try {
    const start = new Date(isoStart);
    const end   = new Date(start);
    end.setDate(start.getDate() + 6);
    const fmt = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    // If same month show "Apr 1–7", else "Mar 30 – Apr 5"
    if (start.getMonth() === end.getMonth()) {
      return `${fmt(start).replace(/\s\d+/, "")} ${start.getDate()}–${end.getDate()}`;
    }
    return `${fmt(start)} – ${fmt(end)}`;
  } catch { return ""; }
}

function buildDefaultWeeks(n) {
  // Before /api/weeks loads, show placeholder pills
  const now     = new Date();
  const monday  = new Date(now);
  monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  monday.setHours(0,0,0,0);
  return Array.from({ length: n }, (_, i) => {
    const start = new Date(monday);
    start.setDate(monday.getDate() - i * 7);
    return { offset: -i, week_start: start.toISOString(), count: i === 0 ? 1 : 0 };
  });
}

async function loadWeekMeta() {
  try {
    const data = await fetch("/api/weeks").then(r => r.json());
    weekMeta = data;
    renderWeekPills();
  } catch { /* fallback to default pills already shown */ }
}

function debounceSearch(val) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    currentSearch = val.trim();
    loadArticles({ showSpinner: false });
  }, 350);
}

// ── Refresh ───────────────────────────────────────────────────────────────────

async function triggerRefresh() {
  if (refreshing) return;
  refreshing = true;

  const btn = $id("refreshBtn");
  btn.classList.add("busy");
  btn.textContent = "↻ Refreshing…";

  try {
    await fetch("/api/refresh", { method: "POST" });
    startRefreshPoller();
  } catch {
    stopRefresh(btn);
  }
}

function startRefreshPoller() {
  let attempts = 0;
  clearInterval(refreshPoller);
  refreshPoller = setInterval(async () => {
    attempts++;
    try {
      const s = await fetch("/api/status").then(r => r.json());
      if (!s.refreshing || attempts > 90) {
        clearInterval(refreshPoller);
        stopRefresh($id("refreshBtn"));
        loadArticles({ showSpinner: false });
      }
    } catch {
      clearInterval(refreshPoller);
      stopRefresh($id("refreshBtn"));
    }
  }, 1000);
}

function stopRefresh(btn) {
  refreshing = false;
  if (btn) {
    btn.classList.remove("busy");
    btn.textContent = "↻ Refresh";
  }
}

// ── Digest ────────────────────────────────────────────────────────────────────

async function showDigest() {
  const panel = $id("digestPanel");
  // Toggle off
  if (!panel.classList.contains("hidden")) {
    panel.classList.add("hidden");
    return;
  }

  const content = $id("digestContent");
  content.innerHTML = "<p>Loading digest…</p>";
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const data = await fetch("/api/digest").then(r => r.json());
    renderDigest(data);
  } catch {
    content.innerHTML = "<p>Failed to load digest.</p>";
  }
}

function hideDigest() {
  $id("digestPanel").classList.add("hidden");
}

function renderDigest(data) {
  const content = $id("digestContent");
  const html = Object.values(data)
    .filter(t => t.count > 0)
    .map(t => {
      const rows = t.top.map(a => {
        const payIcon = a.paywalled ? `<span class="d-pay">🔒</span>` : "";
        return `
        <div class="digest-article">
          ${payIcon}
          <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">
            ${esc(a.title)}
          </a>
        </div>`;
      }).join("");

      return `
      <div class="digest-topic">
        <div class="digest-topic-header">
          <span>${t.icon}</span>
          <span>${esc(t.name)}</span>
          <span class="digest-topic-count">${t.count} article${t.count !== 1 ? "s" : ""}</span>
        </div>
        ${rows || "<p style='font-size:12px;color:#9ca3af'>No articles this week.</p>"}
      </div>`;
    })
    .join("");

  content.innerHTML = html || "<p>No articles found.</p>";
}

// ── Auto-refresh every 30 min ─────────────────────────────────────────────────

setInterval(() => {
  if (!refreshing) triggerRefresh();
}, 30 * 60 * 1000);

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Build REGION_META from rendered chips
  document.querySelectorAll(".region-chip[data-region]").forEach(chip => {
    const id = chip.dataset.region;
    if (id && id !== "all") {
      const text  = chip.textContent.trim();
      const parts = text.split(" ");
      REGION_META[id] = { flag: parts[0], name: parts.slice(1).join(" ") };
    }
  });

  // Render default week pills immediately, then fetch real counts
  renderWeekPills();
  loadWeekMeta();   // updates pill counts asynchronously
  loadArticles();
});
