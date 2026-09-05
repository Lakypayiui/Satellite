/* Satellite Console — SPA (menus, graph, editor, folder picker, providers) */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  root: null,
  graph: null,
  agentsById: new Map(),
  cy: null,
  selected: null,
  currentDir: ".",
  openFile: null,
  runId: null,
  pollTimer: null,
  rendered: 0,
};

const PROVIDERS = ["local", "openai", "openai-compatible", "deepseek", "anthropic"];

/* ============================== api ============================== */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let d = res.statusText;
    try { d = (await res.json()).detail || d; } catch (_) {}
    throw new Error(`${res.status}: ${d}`);
  }
  return res.status === 204 ? null : res.json();
}

/* ============================== nav ============================== */
$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $(`#view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "agents" && state.cy) state.cy.resize().layout({ name: "cose", animate: false, padding: 30 }).run();
    if (btn.dataset.view === "editor" && editor) setTimeout(() => editor.layout(), 60);
  });
});

/* ============================== system ============================== */
async function refreshSystem() {
  try {
    const sys = await api("/api/system");
    state.root = sys.root;
    $("#ovRoot").textContent = sys.root;
    $("#crumbRoot").textContent = sys.root;
    $("#ovAgents").textContent = sys.agents;
    $("#ovInit").textContent = sys.initialized ? "listo" : "sin inicializar";
    $("#ovProvider").textContent = sys.provider ? sys.provider.provider : "—";
    $("#sysRootShort").textContent = sys.root.split(/[\\/]/).filter(Boolean).slice(-2).join("/");
    const pill = $("#sysPill");
    pill.textContent = sys.initialized ? `● ${sys.agents} agentes` : "○ no inicializado";
    pill.className = "sys-pill " + (sys.initialized ? "ok" : "no");
    $("#ovInitBtn").hidden = sys.initialized;
    if (sys.initialized) refreshGraph();
    loadDir(state.currentDir, "#ovTree");
  } catch (_) {}
}

/* ============================== folder picker ============================== */
function openFolderModal(path) {
  const dirs = $("#fmDirs");
  dirs.innerHTML = "";
  api(`/api/dirs?path=${encodeURIComponent(path || "")}`).then((data) => {
    $("#fmPath").value = data.path;
    const parent = data.path.split(/[\\/]/).filter(Boolean).slice(0, -1).join("/") || (data.path.includes(":") ? data.path.split(":")[0] + ":\\" : "");
    const upRow = document.createElement("div");
    upRow.className = "dir-row";
    upRow.innerHTML = `<span class="back">⬆</span><span>..</span>`;
    upRow.addEventListener("click", () => openFolderModal(parent || data.path));
    dirs.appendChild(upRow);
    data.entries.forEach((e) => {
      const row = document.createElement("div");
      row.className = "dir-row";
      row.innerHTML = `<span>📁</span><span>${e.name}</span>`;
      row.addEventListener("dblclick", () => openFolderModal(e.path));
      row.addEventListener("click", () => { $("#fmPath").value = e.path; });
      dirs.appendChild(row);
    });
  }).catch((e) => { dirs.innerHTML = `<span class="muted">${e.message}</span>`; });
  $("#folderModal").classList.remove("hidden");
}

function setProject(path) {
  return api("/api/project/set", { method: "POST", body: JSON.stringify({ path }) })
    .then(refreshSystem)
    .finally(() => $("#folderModal").classList.add("hidden"));
}

$("#btnPickFolder").addEventListener("click", () => openFolderModal(""));
$("#ovOpenFolder").addEventListener("click", () => openFolderModal(""));
$("#fmClose").addEventListener("click", () => $("#folderModal").classList.add("hidden"));
$("#fmOpen").addEventListener("click", () => { const p = $("#fmPath").value.trim(); if (p) setProject(p); });
$("#ovInitBtn").addEventListener("click", async () => {
  try {
    await api("/api/project/init", { method: "POST", body: JSON.stringify({}) });
    await refreshSystem();
  } catch (e) { log(`init: ${e.message}`, "err"); }
});
$("#fmPath").addEventListener("keydown", (e) => { if (e.key === "Enter") { const p = e.target.value.trim(); if (p) setProject(p); } });

/* ============================== graph ============================== */
function nodeVisual(agent) {
  if (!agent.enabled) return { c: "#ff6b8a", glow: "rgba(255,107,138,.8)", ring: "#ff6b8a" };
  return agent.is_native
    ? { c: "#35e0a1", glow: "rgba(53,224,161,.85)", ring: "#35e0a1" }
    : { c: "#55e6ff", glow: "rgba(85,230,255,.85)", ring: "#55e6ff" };
}

function nodeGlyph(agent) {
  const icon = agent.is_native ? "◆" : "⬢";
  return icon;
}

function buildGraph(graph) {
  state.graph = graph;
  state.agentsById = new Map(graph.nodes.map((a) => [a.id, a]));

  const els = [
    ...graph.nodes.map((a) => {
      const v = nodeVisual(a);
      return {
        data: {
          id: String(a.id), agent: a,
          glyph: nodeGlyph(a),
          c: v.c, ring: v.ring,
        },
      };
    }),
    ...graph.edges.map((e, i) => ({
      data: {
        id: `e${i}`, source: String(e.source), target: String(e.target),
        cap: e.capability,
      },
    })),
  ];

  if (!state.cy) {
    state.cy = cytoscape({
      container: $("#cy"),
      elements: els,
      style: [
        {
          selector: "node",
          style: {
            "width": 46, "height": 46,
            "background-color": "data(c)",
            "background-gradient-stop-colors": "data(c) #0a0c12",
            "background-gradient-stop-positions": "0% 100%",
            "border-width": 2,
            "border-color": "data(c)",
            "border-opacity": .9,
            "label": "data(glyph)",
            "color": "#05070c",
            "font-size": 18,
            "font-weight": "bold",
            "text-valign": "center",
            "text-halign": "center",
            "overlay-opacity": 0,
            "shadow-blur": 22,
            "shadow-color": "data(c)",
            "shadow-opacity": .65,
            "z-index": 10,
          },
        },
        {
          selector: "node.agent-name",
          style: { "label": "data(name)", "text-valign": "bottom", "text-margin-y": 14, "font-size": 10, "color": "#7e8aa6", "text-wrap": "ellipsis", "text-max-width": "90px" },
        },
        {
          selector: "node:selected",
          style: { "border-width": 4, "shadow-opacity": .9, "shadow-blur": 30 },
        },
        {
          selector: "edge",
          style: {
            "width": 1.6,
            "line-color": "#9d8cff",
            "line-style": "dashed",
            "opacity": .7,
            "target-arrow-color": "#9d8cff",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": .9,
          },
        },
        {
          selector: "edge[cap]",
          style: { "label": "data(cap)", "font-size": 7.5, "color": "#7e8aa6", "text-rotation": "autorotate", "text-margin-x": 6 },
        },
      ],
      layout: { name: "cose", animate: false, padding: 40, nodeRepulsion: 9000 },
    });

    state.cy.on("tap", "node", (evt) => {
      const a = evt.target.data("agent");
      state.selected = a;
      showAgentPanel(a);
      // enfocar
      state.cy.nodes().forEach((n) => n.removeClass("focused"));
      evt.target.addClass("focused");
    });
    state.cy.on("tap", (evt) => {
      if (evt.target === state.cy) hideAgentPanel();
    });
    state.cy.style().selector("node.focused").style({
      "border-width": 5, "shadow-opacity": .95, "shadow-blur": 34,
    }).update();
  } else {
    state.cy.elements().remove();
    state.cy.add(els);
    state.cy.layout({ name: "cose", animate: false, padding: 40, nodeRepulsion: 9000 }).run();
  }

  // nombre bajo el nodo (segunda pasada con el dato)
  state.cy.nodes().forEach((n) => {
    n.data("name", n.data("agent").name || `agente ${n.data("agent").id}`);
    n.addClass("agent-name");
  });
}

async function refreshGraph() {
  try {
    const graph = await api("/api/agents");
    buildGraph(graph);
  } catch (e) { log(`grafo: ${e.message}`, "err"); }
}

/* ---- panel detalle agente (overlay flotante) ---- */
function showAgentPanel(a) {
  const old = $("#agentPanel");
  if (old) old.remove();
  const panel = document.createElement("div");
  panel.id = "agentPanel";
  const caps = (a.capabilities || []).map((c) => `<span class="cap-chip">${c}</span>`).join("");
  const reqs = (a.context_requirements || []).map((r) => `<div class="req-line">⤷ ${r}</div>`).join("");
  const comps = (a.complements || []).length ? `<div class="req-line">⇄ complementa: ${a.complements.join(", ")}</div>` : "";
  const kind = a.is_native ? "nativo" : "plugin";
  panel.innerHTML = `
    <div class="ap-head">
      <span class="ap-dot" style="background:${nodeVisual(a).c};box-shadow:0 0 12px ${nodeVisual(a).glow}"></span>
      <div>
        <div class="ap-name">${a.name || "agente " + a.id} <span class="muted">#${a.id}</span></div>
        <div class="ap-kind">${kind} · v${a.version || "0.1.0"}</div>
      </div>
      <button class="icon-btn ap-close">✕</button>
    </div>
    <div class="ap-desc">${a.description || "—"}</div>
    <div class="ap-section">capabilities</div>
    <div class="ap-caps">${caps || "<i>ninguna</i>"}</div>
    ${reqs ? `<div class="ap-section">requiere</div>${reqs}` : ""}
    ${comps}
    <div class="ap-section">estado</div>
    <label class="switch">
      <input type="checkbox" id="apEnabled" ${a.enabled ? "checked" : ""} />
      <span class="sw-track"></span>
      <span id="apEnabledLabel">${a.enabled ? "Habilitado" : "BLOQUEADO"}</span>
    </label>
    <div class="ap-actions">
      <button id="apBlock" class="btn ${a.enabled ? "ghost" : "primary"} small">${a.enabled ? "Bloquear agente" : "Desbloquear agente"}</button>
    </div>
    <div id="apMsg" class="muted small"></div>`;
  panel.style.cssText = `
    position:absolute; top:72px; right:26px; z-index:30; width:300px;
    background:rgba(13,16,26,.92); border:1px solid rgba(85,230,255,.25);
    border-radius:16px; padding:16px; backdrop-filter:blur(16px);
    box-shadow:0 20px 60px rgba(0,0,0,.6), 0 0 30px rgba(85,230,255,.08);`;
  const host = $("#view-agents");
  host.appendChild(panel);

  panel.querySelector(".ap-close").addEventListener("click", hideAgentPanel);
  panel.querySelector("#apBlock").addEventListener("click", async () => {
    const enabled = !a.enabled;
    try {
      await api(`/api/agents/${a.id}/enabled`, { method: "POST", body: JSON.stringify({ enabled }) });
      a.enabled = enabled;
      refreshGraph();
      showAgentPanel(a);
    } catch (e) { $("#apMsg").textContent = e.message; }
  });
}
function hideAgentPanel() {
  const old = $("#agentPanel");
  if (old) old.remove();
  state.selected = null;
}

/* ============================== file explorer ============================== */
const ICONS = { py: "🐍", js: "📜", jsx: "📜", ts: "🔷", tsx: "🔷", cpp: "⚙", h: "⚙", hpp: "⚙", cc: "⚙", json: "🧾", md: "📄", html: "🌐", css: "🎨", bat: "⚡", sh: "⚡", txt: "📄", cmake: "🔧", toml: "🔧", yaml: "🔧", yml: "🔧", sql: "🗄" };

async function loadDir(path, containerSel) {
  const container = $(containerSel);
  if (!container) return;
  state.currentDir = path;
  if (containerSel === "#fileTree") $("#edDir").value = path;
  try {
    const data = await api(`/api/files?path=${encodeURIComponent(path)}`);
    container.innerHTML = "";
    if (data.path !== ".") {
      const up = document.createElement("div");
      up.className = "file-row dir";
      up.innerHTML = `<span>⬆</span><span>..</span>`;
      up.addEventListener("click", () => {
        const parts = data.path.split("/").filter(Boolean);
        parts.pop();
        loadDir(parts.join("/") || ".", containerSel);
      });
      container.appendChild(up);
    }
    data.entries.forEach((e) => {
      const row = document.createElement("div");
      row.className = "file-row" + (e.type === "dir" ? " dir" : "");
      row.innerHTML = `<span>${e.type === "dir" ? "📁" : ICONS[e.ext] || "📄"}</span><span>${e.name}</span>${e.type === "file" ? `<span class="sz">${fmt(e.size)}</span>` : ""}`;
      row.addEventListener("click", () => {
        if (e.type === "dir") loadDir(e.path, containerSel);
        else openFile(e.path);
      });
      container.appendChild(row);
    });
  } catch (err) { container.innerHTML = `<span class="muted">${err.message}</span>`; }
}
function fmt(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + "M" : n > 1024 ? (n / 1024).toFixed(1) + "k" : n + "B"; }

$("#btnUpDir").addEventListener("click", () => {
  const p = state.currentDir.split("/").filter(Boolean);
  p.pop();
  loadDir(p.join("/") || ".", "#fileTree");
});
$("#btnRefreshTree").addEventListener("click", () => loadDir(state.currentDir, "#fileTree"));
$("#edDir").addEventListener("keydown", (e) => { if (e.key === "Enter") loadDir(e.target.value.trim() || ".", "#fileTree"); });

/* ============================== editor ============================== */
let editor = null;
require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs" } });
require(["vs/editor/editor.main"], () => {
  editor = monaco.editor.create($("#monaco"), {
    value: "",
    language: "plaintext",
    theme: "vs-dark",
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13.5,
    fontFamily: "JetBrains Mono",
    padding: { top: 14 },
    scrollbar: { verticalScrollbarSize: 8 },
  });
});

const LANG = { py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", cpp: "cpp", h: "cpp", hpp: "cpp", cc: "cpp", json: "json", md: "markdown", html: "html", css: "css", sh: "shell", bat: "bat", yaml: "yaml", yml: "yaml", sql: "sql", toml: "ini", cmake: "cmake" };

async function openFile(path) {
  try {
    const d = await api(`/api/file?path=${encodeURIComponent(path)}`);
    state.openFile = path;
    editor.setValue(d.content);
    const ext = path.split(".").pop().toLowerCase();
    monaco.editor.setModelLanguage(editor.getModel(), LANG[ext] || "plaintext");
    $("#edPath").textContent = path;
    $("#saveMsg").textContent = "";
  } catch (e) { $("#saveMsg").textContent = "error: " + e.message; }
}
$("#btnSave").addEventListener("click", async () => {
  if (!state.openFile) return;
  try {
    await api("/api/file", { method: "PUT", body: JSON.stringify({ path: state.openFile, content: editor.getValue() }) });
    $("#saveMsg").textContent = "✓ guardado";
    setTimeout(() => { $("#saveMsg").textContent = ""; }, 2000);
  } catch (e) { $("#saveMsg").textContent = "error: " + e.message; }
});

/* ============================== run ============================== */
function log(text, kind = "info") {
  const d = document.createElement("div");
  d.className = `log-line ${kind}`;
  d.textContent = text;
  $("#runLog").appendChild(d);
  $("#runLog").scrollTop = $("#runLog").scrollHeight;
}
function setStatus(s) {
  const chip = $("#runStatus");
  chip.textContent = s;
  chip.className = "status-chip " + ({ idle: "idle", running: "running", ok: "ok", err: "err" }[s] || "idle");
}

function renderEvent(ev) {
  switch (ev.type) {
    case "providers":
      log(`proveedores → ctx: ${ev.context.provider}/${ev.context.model} · orq: ${ev.orchestrator.provider}/${ev.orchestrator.model}`, "muted");
      break;
    case "preprocess":
      log(ev.result.needs_user_input ? `preproceso pide: ${ev.result.user_prompt}` : "preproceso: contexto listo", ev.result.needs_user_input ? "err" : "ok");
      break;
    case "compressed":
      log("contexto comprimido (neutro)", "ok");
      break;
    case "plan_start":
      log(`► plan: ${ev.goal}`, "step");
      break;
    case "step_started":
      log(`▶ ${ev.name}${ev.routed_from ? ` ⇄ desde ${ev.routed_from}` : ""}`, "info");
      if (state.cy && ev.agent_id != null) state.cy.getElementById(String(ev.agent_id))?.style({ "shadow-opacity": 1, "shadow-blur": 30 });
      break;
    case "step_result": {
      const okk = ev.status === "SUCCESS";
      log(`${okk ? "✔" : "✖"} ${ev.name} → ${ev.status}${ev.error ? ": " + ev.error : ""}`, okk ? "ok" : "err");
      const a = state.agentsById.get(ev.agent_id);
      if (state.cy && a) {
        const v = nodeVisual(a);
        setTimeout(() => state.cy.getElementById(String(a.id))?.style({ "shadow-opacity": v.glow ? .65 : .65 }), 400);
      }
      break;
    }
    case "done":
      log(`✓ run ${ev.ok ? "completado" : "falló"}`, "done");
      setStatus(ev.ok ? "ok" : "err");
      break;
    case "error":
      log(`✖ ${ev.error}`, "err");
      setStatus("err");
      break;
    default:
      log(`· ${ev.type}`, "muted");
  }
}

async function startRun(goal) {
  if (!goal) return;
  $("#runLog").innerHTML = "";
  state.rendered = 0;
  stopPolling();
  try {
    const d = await api("/api/run", { method: "POST", body: JSON.stringify({ goal }) });
    state.runId = d.run_id;
    setStatus("running");
    log(`run ${state.runId} iniciado`, "info");
    state.pollTimer = setInterval(poll, 700);
    poll();
  } catch (e) { log(`no se pudo iniciar: ${e.message}`, "err"); setStatus("err"); }
}
async function poll() {
  if (!state.runId) return;
  try {
    const d = await api(`/api/run/${state.runId}`);
    const pend = d.events.slice(state.rendered);
    state.rendered = d.events.length;
    pend.forEach(renderEvent);
    if (["completed", "failed", "error"].includes(d.status)) {
      stopPolling();
      if (d.status !== "completed") setStatus("err");
    }
  } catch (e) { stopPolling(); log(`poll: ${e.message}`, "err"); }
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}
$("#btnRun").addEventListener("click", () => startRun($("#goalInput").value.trim()));
$("#goalInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) startRun(e.target.value.trim()); });

/* ============================== settings ============================== */
function fillSel(sel, value) {
  sel.innerHTML = PROVIDERS.map((p) => `<option ${p === value ? "selected" : ""}>${p}</option>`).join("");
}
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    const llm = cfg.llm || {};
    fillSel($("#gProvider"), llm.provider || "local");
    $("#gModel").value = llm.model || "";
    $("#gBase").value = llm.base_url || "";
    $("#gKey").value = "";
    $$(".role-card").forEach((card) => {
      const role = card.dataset.role;
      const sec = llm[role] || {};
      fillSel(card.querySelector(".r-provider"), sec.provider || llm.provider || "local");
      card.querySelector(".r-model").value = sec.model || "";
    });
  } catch (_) {}
}
$("#btnApplyConfig").addEventListener("click", async () => {
  const roles = {
    global: {
      provider: $("#gProvider").value,
      model: $("#gModel").value.trim(),
      base_url: $("#gBase").value.trim(),
      api_key: $("#gKey").value.trim(),
    },
  };
  $$(".role-card").forEach((card) => {
    const role = card.dataset.role;
    const sec = {
      provider: card.querySelector(".r-provider").value,
      model: card.querySelector(".r-model").value.trim(),
    };
    if (!sec.model) delete sec.model;
    roles[role] = sec;
  });
  try {
    await api("/api/config/roles", { method: "PUT", body: JSON.stringify({ roles }) });
    $("#cfgMsg").textContent = "✓ configuración aplicada";
    setTimeout(() => { $("#cfgMsg").textContent = ""; }, 2500);
    loadConfig();
  } catch (e) { $("#cfgMsg").textContent = "error: " + e.message; }
});

/* ============================== boot ============================== */
refreshSystem().then(() => {
  loadDir(".", "#ovTree");
  loadConfig();
});
