const $ = (selector) => document.querySelector(selector);
const state = {
  result: null,
  messages: new Map(),
  pollTimer: null,
  lastFocus: null,
  mode: "sample",
  health: null,
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setHidden(node, hidden) {
  node.classList.toggle("is-hidden", hidden);
}

function clear(node) {
  node.replaceChildren();
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value ?? 0);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  setHidden(toast, false);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => setHidden(toast, true), 3200);
}

async function readJson(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return payload;
}

async function loadHealth() {
  const badge = $("#modelBadge");
  try {
    const health = await fetch("/api/health").then(readJson);
    state.health = health;
    if (health.ollama.available && health.ollama.model_present) {
      badge.textContent = `${health.model} · local`;
      badge.className = "model-badge is-ready";
      badge.title = "Ollama is reachable and the configured model is installed.";
    } else if (!health.ollama.available) {
      badge.textContent = "Ollama not running";
      badge.className = "model-badge is-error";
      badge.title = health.ollama.error || "Start Ollama with: ollama serve";
    } else {
      badge.textContent = "Model not installed";
      badge.className = "model-badge is-error";
      badge.title = health.ollama.error || "";
    }
  } catch (error) {
    badge.textContent = "Local model unknown";
    badge.className = "model-badge is-error";
    badge.title = "Could not reach the local server.";
  }
}

function phaseCopy(job) {
  const phases = {
    queued: ["Getting ready…", "Waiting for a worker on this machine."],
    preparing: ["Parsing the export…", "Timestamps, senders and unparsed lines are being handled by code."],
    analyzing: [
      "Triaging candidate messages…",
      `Batch ${Math.min(job.done + 1, job.total)} of ${job.total}. The model runs locally; each batch can take a minute or more.`,
    ],
    validating: ["Checking every claim…", "Citations, owners and deadlines are being grounded against the source messages."],
    done: ["Briefing ready.", "Every item below links to the exact message it came from."],
  };
  return phases[job.phase] || ["Working locally…", "Chat text stays in memory."];
}

function updateProgress(job) {
  const panel = $("#progressPanel");
  setHidden(panel, false);
  const [title, note] = phaseCopy(job);
  $("#progressTitle").textContent = title;
  $("#progressNote").textContent = note;
  $("#progressCount").textContent = `${formatNumber(job.done)} / ${formatNumber(job.total)}`;
  const pct = job.status === "done" ? 100 : Math.max(4, Math.round((job.done / Math.max(1, job.total)) * 100));
  $("#progressBar").style.width = `${pct}%`;
  $("#progressTrack").setAttribute("aria-valuenow", String(pct));
}

function showProgressError(message) {
  setHidden($("#progressError"), false);
  $("#progressErrorText").textContent = message;
  $("#progressTitle").textContent = "Analysis stopped.";
  $("#progressNote").textContent = "Nothing was sent online and no partial briefing is shown.";
  $("#progressBar").style.width = "100%";
  $("#progressBar").style.background = "linear-gradient(90deg, #a34b38, #c9822f)";
}

function resetProgress() {
  setHidden($("#progressError"), true);
  $("#progressBar").style.background = "";
  $("#progressBar").style.width = "2%";
}

async function startJob(url, options, mode) {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  resetProgress();
  setHidden($("#result"), true);
  setHidden($("#progressPanel"), false);
  $("#progressTitle").textContent = "Sending to the local worker…";
  $("#progressNote").textContent = "No network request leaves this machine.";
  $("#progressCount").textContent = "0 / 1";
  state.mode = mode;
  try {
    const payload = await fetch(url, options).then(readJson);
    pollJob(payload.job_id);
  } catch (error) {
    showProgressError(error.message);
  }
}

async function pollJob(jobId) {
  try {
    const job = await fetch(`/api/jobs/${jobId}`).then(readJson);
    updateProgress(job);
    if (job.status === "done") {
      setHidden($("#progressPanel"), true);
      renderResult(job.result);
      return;
    }
    if (job.status === "error") {
      showProgressError(job.error?.message || "The local analysis failed.");
      return;
    }
    state.pollTimer = window.setTimeout(() => pollJob(jobId), 1400);
  } catch (error) {
    showProgressError("The local worker stopped responding. Check the terminal running CatchUp Local.");
  }
}

function uploadFile(file) {
  if (!file) return;
  const name = file.name.toLowerCase();
  if (!name.endsWith(".txt") && !name.endsWith(".text")) {
    showToast("Please choose a .txt WhatsApp export.");
    return;
  }
  if (file.size > 8 * 1024 * 1024) {
    showToast("That file is larger than the 8 MB limit.");
    return;
  }
  const body = new FormData();
  body.append("file", file);
  startJob("/api/analyze", { method: "POST", body }, "upload");
}

function evidenceButton(messageId) {
  const button = el("button", "evidence-chip", messageId);
  button.type = "button";
  button.title = "Open the source message";
  button.addEventListener("click", () => openDrawer(messageId));
  return button;
}

function itemCard(item, kind) {
  const article = el("article", `item kind-${kind}`);
  if (item.conflict_group) article.classList.add("is-conflict");

  const top = el("div", "item-top");
  top.append(el("span", "item-kind", kind));
  if (kind === "action") {
    if (item.owner && item.owner !== "unknown") {
      const owner = el("span", "badge owner", `owner · ${item.owner}`);
      owner.title = item.owner_status === "supported" ? "Owner is stated in the cited message." : "Owner status is uncertain.";
      top.append(owner);
    } else {
      top.append(el("span", "badge unknown", "owner not stated"));
    }
    if (item.deadline && item.deadline !== "unknown") {
      const deadline = el("span", "badge deadline", `due · ${item.deadline}`);
      deadline.title = "Deadline phrase copied from the cited message.";
      top.append(deadline);
    } else {
      top.append(el("span", "badge unknown", "no deadline"));
    }
  }
  const certainty = el("span", "badge", item.certainty === "explicit" ? "explicit" : "interpreted");
  certainty.title = item.certainty === "explicit"
    ? "The wording explicitly supports this label."
    : "The label is a reasonable reading, not an explicit statement.";
  top.append(certainty);
  if (item.conflict_group) {
    const conflict = el("span", "badge conflict", "conflict · read both");
    conflict.title = "Related messages disagree. The tool did not choose a winner.";
    top.append(conflict);
  }
  article.append(top);
  article.append(el("p", "item-text", item.text));

  if (Array.isArray(item.warnings) && item.warnings.length) {
    const list = el("ul", "item-warnings");
    for (const warning of item.warnings) list.append(el("li", "", warning));
    article.append(list);
  }

  const foot = el("div", "item-foot");
  const evidence = el("div", "evidence-row");
  for (const messageId of item.evidence_ids || []) evidence.append(evidenceButton(messageId));
  foot.append(evidence);
  article.append(foot);
  return article;
}

function renderItems(selector, items, kind, emptyText) {
  const container = $(selector);
  clear(container);
  if (!items || !items.length) {
    container.append(el("p", "empty", emptyText));
    return;
  }
  for (const item of items) container.append(itemCard(item, kind));
}

function renderReadFirst(items) {
  const container = $("#readFirstList");
  clear(container);
  const rows = (items || []).map((read) => ({ read, message: state.messages.get(read.message_id) })).filter((row) => row.message);
  if (!rows.length) {
    container.append(el("p", "empty", "No single message needed to be pulled forward."));
    return;
  }
  for (const { read, message } of rows) {
    const card = el("button", "read-card");
    card.type = "button";
    card.append(el("span", "read-reason", read.reason));
    card.append(el("blockquote", "read-quote", message.text));
    const foot = el("p", "read-foot");
    foot.append(el("span", "", `${message.message_id} · ${message.sender || "system"}`));
    foot.append(el("span", "open", "Open source →"));
    card.append(foot);
    card.addEventListener("click", () => openDrawer(message.message_id));
    container.append(card);
  }
}

function renderSummary(result) {
  const briefing = result.briefing || {};
  $("#summaryText").textContent = briefing.summary || "No overview was produced.";
  const evidence = $("#summaryEvidence");
  clear(evidence);
  for (const messageId of briefing.summary_evidence_ids || []) evidence.append(evidenceButton(messageId));
  const kind = briefing.summary_kind === "derived_from_items"
    ? "Derived from validated items — not written by the model."
    : "No derived overview was available.";
  $("#summaryKind").textContent = kind;
}

function renderWarnings(result) {
  const banner = $("#warningBanner");
  const warnings = result.briefing?.warnings || [];
  clear(banner);
  if (!warnings.length) {
    setHidden(banner, true);
    return;
  }
  for (const warning of warnings) banner.append(el("p", "", warning));
  setHidden(banner, false);
}

function metric(value, label) {
  const box = el("div", "metric");
  box.append(el("span", "metric-value", value));
  box.append(el("span", "metric-label", label));
  return box;
}

function renderTrace(result) {
  const briefing = result.briefing || {};
  const coverage = briefing.coverage || {};
  const grid = $("#coverageGrid");
  clear(grid);
  grid.append(metric(`${formatNumber(coverage.messages_analysed)} / ${formatNumber(coverage.messages_total)}`, "Messages read"));
  grid.append(metric(`${formatNumber(coverage.candidates_labelled)} / ${formatNumber(coverage.candidates_total)}`, "Candidates labelled"));
  grid.append(metric(`${formatNumber(coverage.citations_valid)} / ${formatNumber(coverage.citations_checked)}`, "Evidence checked"));
  grid.append(metric(formatNumber(coverage.model_calls), "Model calls"));
  const seconds = Number(coverage.duration_seconds || 0);
  grid.append(metric(seconds >= 60 ? `${(seconds / 60).toFixed(1)} min` : `${seconds.toFixed(1)} s`, "Local run time"));
  const failed = coverage.candidate_batches_failed || 0;
  grid.append(metric(`${formatNumber(coverage.candidate_batches_total - failed)} / ${formatNumber(coverage.candidate_batches_total)}`, "Batches triaged"));
  if (coverage.items_deferred) {
    grid.append(metric(formatNumber(coverage.items_deferred), "Deferred to transcript"));
  }

  const refused = $("#refusedList");
  clear(refused);
  const refusedClaims = briefing.refused_claims || [];
  if (!refusedClaims.length) {
    refused.append(el("p", "muted", "Nothing was refused. Every displayed item kept a valid source."));
  } else {
    for (const claim of refusedClaims) {
      const row = el("p");
      row.append(el("strong", "", `${claim.count}× `));
      row.append(document.createTextNode(claim.reason));
      refused.append(row);
    }
  }

  const notes = $("#runNotes");
  clear(notes);
  const runNotes = [];
  const parse = result.parse || {};
  if (parse.date_order) {
    const ambiguous = parse.date_order_ambiguous ? " (ambiguous export)" : "";
    runNotes.push(`Date order: ${String(parse.date_order).toUpperCase()}${ambiguous}.`);
  }
  if (coverage.instruction_like_messages) {
    runNotes.push(`${coverage.instruction_like_messages} instruction-like message(s) were treated as quoted content, never executed.`);
  }
  if (coverage.invalid_labels) {
    runNotes.push(`${coverage.invalid_labels} malformed or out-of-batch label(s) were ignored.`);
  }
  if (coverage.degraded) {
    runNotes.push("At least one batch was not model-triaged; those items rely on explicit wording only.");
  }
  runNotes.push("Live jobs live in memory only and disappear when the server restarts.");
  for (const note of runNotes) notes.append(el("p", "muted", note));
}

function renderMeta(result) {
  const meta = result.meta || {};
  const parse = result.parse || {};
  const coverage = result.briefing?.coverage || {};
  const bits = [];
  if (meta.kind === "precomputed_sample") {
    bits.push("Precomputed sample");
  } else if (state.mode === "live_sample") {
    bits.push("Live sample run");
  } else {
    bits.push(parse.source_name || "Your export");
  }
  if (coverage.model) bits.push(coverage.model);
  if (meta.generated_at) bits.push(`generated ${meta.generated_at.replace("T", " ").replace("Z", " UTC")}`);
  else if (coverage.duration_seconds !== undefined) bits.push(`${coverage.duration_seconds}s local run`);
  $("#resultMeta").textContent = bits.join(" · ");
}

function renderResult(result, scroll = true) {
  state.result = result;
  state.messages = new Map((result.messages || []).map((message) => [message.message_id, message]));
  const briefing = result.briefing || {};

  renderMeta(result);
  renderSummary(result);
  renderWarnings(result);
  renderReadFirst(briefing.read_first);
  renderItems("#decisionList", briefing.decisions, "decision", "No explicit decision survived validation.");
  renderItems("#actionList", briefing.actions, "action", "No action item survived validation.");
  renderItems("#questionList", briefing.questions, "question", "No open question survived validation.");
  renderItems("#updateList", briefing.updates, "update", "No material update survived validation.");
  $("#decisionCount").textContent = formatNumber((briefing.decisions || []).length);
  $("#actionCount").textContent = formatNumber((briefing.actions || []).length);
  $("#questionCount").textContent = formatNumber((briefing.questions || []).length);
  $("#updateCount").textContent = formatNumber((briefing.updates || []).length);
  renderTrace(result);

  setHidden($("#result"), false);
  if (scroll) $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function flagBadge(text, className = "badge") {
  return el("span", className, text);
}

function openDrawer(messageId) {
  const message = state.messages.get(messageId);
  if (!message) {
    showToast("That source message is not in this briefing.");
    return;
  }
  state.lastFocus = document.activeElement;
  $("#drawerSender").textContent = message.sender || "System";
  $("#drawerTime").textContent = `${message.timestamp || "no timestamp"} · source line ${message.source_line}`;
  $("#drawerText").textContent = message.text;
  const flags = $("#drawerFlags");
  clear(flags);
  if (message.instruction_like) flags.append(flagBadge("instruction-like · quoted, not executed", "badge conflict"));
  if (message.is_edited) flags.append(flagBadge("edited"));
  if (message.is_deleted) flags.append(flagBadge("deleted"));
  if (message.is_system) flags.append(flagBadge("system message"));
  if (message.media_kind) flags.append(flagBadge(message.media_kind));
  if (!flags.children.length) flags.append(flagBadge("ordinary message"));
  $("#drawerLine").textContent = `evidence id ${message.message_id}`;
  const drawer = $("#drawer");
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  setHidden($("#drawerBackdrop"), false);
  document.body.style.overflow = "hidden";
  $("#drawerClose").focus();
}

function closeDrawer() {
  const drawer = $("#drawer");
  drawer.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  setHidden($("#drawerBackdrop"), true);
  document.body.style.overflow = "";
  if (state.lastFocus && state.lastFocus.focus) state.lastFocus.focus();
}

function downloadResult() {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  const source = (state.result.parse?.source_name || "briefing").replace(/\.(txt|text)$/i, "");
  link.href = URL.createObjectURL(blob);
  link.download = `catchup-${source}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  showToast("Briefing JSON downloaded.");
}

function resetView() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.result = null;
  setHidden($("#result"), true);
  setHidden($("#progressPanel"), true);
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("#fileInput").value = "";
}

async function loadSample(options = {}) {
  try {
    const result = await fetch("/api/sample").then(readJson);
    state.mode = "sample";
    setHidden($("#progressPanel"), true);
    renderResult(result, options.scroll !== false);
  } catch (error) {
    showToast(error.message || "The precomputed sample is unavailable.");
  }
}

function wireEvents() {
  $("#fileInput").addEventListener("change", (event) => uploadFile(event.target.files?.[0]));
  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
    });
  });
  dropzone.addEventListener("drop", (event) => uploadFile(event.dataTransfer?.files?.[0]));

  $("#sampleBtn").addEventListener("click", loadSample);

  $("#liveSampleBtn").addEventListener("click", () => {
    const ok = window.confirm("A live run on the sample can take several minutes on this machine. Start it?");
    if (!ok) return;
    startJob("/api/analyze-sample", { method: "POST" }, "live_sample");
  });

  $("#resetBtn").addEventListener("click", resetView);
  $("#downloadBtn").addEventListener("click", downloadResult);
  $("#retryBtn").addEventListener("click", () => {
    if (state.mode === "live_sample") startJob("/api/analyze-sample", { method: "POST" }, "live_sample");
    else $("#fileInput").click();
  });

  $("#toggleTrace").addEventListener("click", (event) => {
    const button = event.currentTarget;
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    button.textContent = expanded ? "Show details" : "Hide details";
    setHidden($("#traceContent"), expanded);
  });

  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
}

window.addEventListener("DOMContentLoaded", () => {
  wireEvents();
  loadHealth();
  const params = new URLSearchParams(window.location.search);
  if (params.get("sample") === "1") {
    loadSample({ scroll: params.get("screenshot") !== "1" });
  }
});
