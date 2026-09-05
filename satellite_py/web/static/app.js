/* Satellite — chat UI */
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
  busy: false,
  currentAssistant: null, // nodo DOM del mensaje asistente en curso
  currentProc: null,      // contenedor de tarjetas de proceso
};

const PROVIDERS = ["local", "openai", "openai-compatible", "deepseek", "anthropic"];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ============================== api ============================== */
function authHeaders() {
  const token = localStorage.getItem("satellite_token") || "";
  return token ? { "X-Satellite-Token": token } : {};
}
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json", ...authHeaders(), ...opts.headers }, ...opts });
  if (res.status === 401) {
    // pedir el token una sola vez y reintentar
    const ask = () => {
      const t = window.prompt("Consola protegida — ingresa el token (SATELLITE_WEB_TOKEN)");
      if (t) localStorage.setItem("satellite_token", t);
      return t || "";
    };
    if (!localStorage.getItem("satellite_token")) {
      const t = ask();
      if (t) return api(path, opts);
    } else {
      localStorage.removeItem("satellite_token");
      const t = ask();
      if (t) return api(path, opts);
    }
    throw new Error("401: no autorizado");
  }
  if (!res.ok) {
    let d = res.statusText;
    try { d = (await res.json()).detail || d; } catch (_) {}
    throw new Error(`${res.status}: ${d}`);
  }
  return res.status === 204 ? null : res.json();
}

/* ============================== nav ============================== */
$$(".side-link").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".side-link").forEach((b) => b.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $(`#view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "agents" && state.cy) {
      state.cy.resize();
      setTimeout(() => {
        state.cy.layout({ name: "cose", animate: false, padding: 40, nodeRepulsion: 12000, idealEdgeLength: 160 }).run();
        try { state.cy.fit(undefined, 40); } catch (_) {}
      }, 60);
    }
    if (btn.dataset.view === "files" && editor) setTimeout(() => editor.layout(), 60);
  });
});

/* ============================== system ============================== */
async function refreshSystem() {
  try {
    const sys = await api("/api/system");
    state.root = sys.root;
    $("#sysRoot").textContent = sys.root;
    $("#sysRoot").title = sys.root;
    const pill = $("#sysPill");
    pill.textContent = sys.initialized ? `● ${sys.agents} agentes · ${sys.provider ? sys.provider.provider : ""}` : "○ no inicializado";
    pill.className = "sys-pill " + (sys.initialized ? "ok" : "no");
    if (sys.initialized) refreshGraph();
  } catch (_) {}
}

/* ============================== chat ============================== */
const chatLog = () => $("#chatLog");
const chatEmpty = () => $("#chatEmpty");

function scrollBottom() { const el = $("#chatScroll"); el.scrollTop = el.scrollHeight; }

function addMessage(role, text) {
  chatEmpty()?.classList.add("hidden");
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const avatar = role === "user" ? "Tú" : "◈";
  wrap.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble"><div class="text"></div></div>`;
  wrap.querySelector(".text").textContent = text;
  chatLog().appendChild(wrap);
  scrollBottom();
  return wrap;
}

function addAssistantMessage() {
  chatEmpty()?.classList.add("hidden");
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="avatar">◈</div><div class="bubble"><div class="text typing">pensando</div></div>`;
  chatLog().appendChild(wrap);
  state.currentAssistant = wrap;
  state.currentProc = document.createElement("div");
  wrap.querySelector(".bubble").appendChild(state.currentProc);
  scrollBottom();
  return wrap;
}

function setAssistantText(text) {
  if (!state.currentAssistant) return;
  const t = state.currentAssistant.querySelector(".text");
  t.classList.remove("typing");
  t.textContent = text || "(sin respuesta)";
  scrollBottom();
}

function appendAssistantText(text) {
  if (!state.currentAssistant) return;
  const t = state.currentAssistant.querySelector(".text");
  t.classList.remove("typing");
  t.textContent += (t.textContent && text ? "\n\n" : "") + (text || "");
  scrollBottom();
}

function addProcCard(title, sub, htmlBody) {
  if (!state.currentAssistant) return;
  const card = document.createElement("div");
  card.className = "proc open";
  card.innerHTML = `
    <div class="proc-head">
      <span class="caret">▶</span>
      <span class="proc-title">${esc(title)}</span>
      <span class="proc-sub">${sub ? esc(sub) : ""}</span>
    </div>
    <div class="proc-body">${htmlBody}</div>`;
  card.querySelector(".proc-head").addEventListener("click", () => card.classList.toggle("open"));
  (state.currentProc || chatLog()).appendChild(card);
  scrollBottom();
}

function procKV(k, v) { return `<div class="kv"><b>${esc(k)}:</b> ${esc(v)}</div>`; }
function procPre(txt) { return `<pre>${esc(txt)}</pre>`; }

/* ---- render de eventos como proceso ---- */
function handleEvent(ev) {
  switch (ev.type) {
    case "providers":
      addProcCard("Proveedores", "", procKV("contexto", `${ev.context.provider}/${ev.context.model}`) + procKV("orquestador", `${ev.orchestrator.provider}/${ev.orchestrator.model}`));
      break;
    case "preprocess": {
      const r = ev.result || {};
      addProcCard(
        "Contexto del proyecto",
        r.needs_user_input ? "pide información" : "listo",
        procPre((r.refined || "").slice(0, 3000)) + (r.needs_user_input ? `<div class="step-line st-err">⚠ ${esc(r.user_prompt)}</div>` : "")
      );
      break;
    }
    case "compressed":
      addProcCard("Compresión semántica", "doc neutro", procPre(JSON.stringify(ev.context || {}, null, 1).slice(0, 2000)));
      break;
    case "compressed_fallback":
      addProcCard("Compresión semántica", "omitida", `<div class="kv"><b>motivo:</b> ${esc(ev.error)}</div>`);
      break;
    case "plan_start":
      addProcCard("Plan", esc(ev.goal), "");
      break;
    case "step_started": {
      const label = `▶ ${ev.name || "agente"}${ev.routed_from ? ` · cadena desde paso ${ev.routed_from}` : ""}`;
      addProcCard("Ejecutando agente", label, "");
      break;
    }
    case "step_result": {
      const okk = ev.status === "SUCCESS";
      const lines = [];
      if (ev.output !== undefined && ev.output !== null) lines.push(`output: ${JSON.stringify(ev.output, null, 1)}`);
      if (ev.error) lines.push(`error: ${typeof ev.error === "string" ? ev.error : JSON.stringify(ev.error)}`);
      addProcCard(
        okk ? "✔ " + (ev.name || "agente") : "✖ " + (ev.name || "agente"),
        ev.status,
        lines.length ? procPre(lines.join("\n")) : ""
      );
      break;
    }
    case "user_input_needed":
      addProcCard("Se necesita tu aporte", "", `<div class="step-line st-err">${esc(ev.prompt)}</div>`);
      break;
    case "error":
      addProcCard("Error", "", `<div class="step-line st-err">${esc(ev.error)}</div>`);
      break;
    default:
      break;
  }
}

function finishRun(finalText, ok) {
  stopPolling();
  const existing = state.currentAssistant?.querySelector(".text");
  if (existing && existing.textContent === "pensando") {
    setAssistantText(finalText || (ok ? "Listo." : "El run falló."));
  } else if (finalText) {
    // el proceso ya mostró algo; añadimos la respuesta final como bloque
    if (!state.currentAssistant) addAssistantMessage();
    appendAssistantText(finalText);
  }
  state.busy = false;
  updateSend();
  scrollBottom();
}

async function startRun(text) {
  if (!text || state.busy) return;
  state.busy = true;
  updateSend();
  addMessage("user", text);
  addAssistantMessage();
  state.rendered = 0;
  stopPolling();
  try {
    const d = await api("/api/run", { method: "POST", body: JSON.stringify({ goal: text, max_rounds: 2 }) });
    state.runId = d.run_id;
    state.pollTimer = setInterval(poll, 900);
    poll();
  } catch (e) {
    setAssistantText("No se pudo iniciar el run: " + e.message);
    state.busy = false;
    updateSend();
  }
}

async function poll() {
  if (!state.runId) return;
  try {
    const d = await api(`/api/run/${state.runId}`);
    const pend = d.events.slice(state.rendered);
    state.rendered = d.events.length;
    pend.forEach(handleEvent);
    if (["completed", "failed", "error"].includes(d.status)) {
      const r = d.result || {};
      const finalText = r.answer || r.summary || "";
      finishRun(finalText, !!r.ok);
      if (!r.ok && !finalText) addProcCard("Run", "falló", `<div class="step-line st-err">${esc(r.summary || d.error || "")}</div>`);
      // enfocar chat
      $$(".side-link").forEach((b) => b.classList.remove("active"));
      $$(".view").forEach((v) => v.classList.remove("active"));
      $(".side-link[data-view='chat']").classList.add("active");
      $("#view-chat").classList.add("active");
    }
  } catch (e) {
    stopPolling();
    setAssistantText("Error de conexión: " + e.message);
    state.busy = false;
    updateSend();
  }
}
function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

function updateSend() { $("#btnSend").disabled = state.busy; }
function sendCurrent() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  startRun(text);
}

$("#btnSend").addEventListener("click", sendCurrent);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCurrent(); }
});
$("#input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px";
});
$("#btnNewChat").addEventListener("click", () => {
  $("#chatLog").innerHTML = "";
  $("#chatEmpty").classList.remove("hidden");
});
$$(".sugg").forEach((b) => b.addEventListener("click", () => { $("#input").value = b.dataset.g; sendCurrent(); }));

/* ============================== graph ============================== */
// Paleta por tipo de agente (nativo / plugin) y estado (bloqueado).
function nodeVisual(a) {
  if (!a.enabled) {
    // bloqueado: gris con anillo rojo
    return { c: "#b3403a", bg: "#f3e6e6", ring: "#b3403a", glow: "rgba(179,64,58,.35)", border: "#c98a86" };
  }
  if (a.is_native) {
    return { c: "#2f6f4f", bg: "#e9f2ec", ring: "#2f6f4f", glow: "rgba(47,111,79,.4)", border: "#a5c9b3" };
  }
  return { c: "#3b6ea8", bg: "#eaf0f7", ring: "#3b6ea8", glow: "rgba(59,110,168,.4)", border: "#a8c4e0" };
}

function buildGraph(graph) {
  state.graph = graph;
  state.agentsById = new Map(graph.nodes.map((a) => [a.id, a]));
  const els = [
    ...graph.nodes.map((a) => {
      const v = nodeVisual(a);
      return { data: { id: String(a.id), agent: a, c: v.c, bg: v.bg, ring: v.ring, border: v.border, glow: v.glow, label: a.name || `agente ${a.id}` } };
    }),
    ...graph.edges.map((e, i) => ({ data: { id: `e${i}`, source: String(e.source), target: String(e.target) } })),
  ];
  if (!state.cy) {
    state.cy = cytoscape({
      container: $("#cy"),
      elements: els,
      style: [
        { selector: "node", style: {
            "width": 44, "height": 44,
            // núcleo sólido de acento (evita data() en gradientes, que rompe
            // el render de Cytoscape) — el borde de color + glow dan el estilo.
            "background-color": "data(c)",
            // anillo exterior (borde con color de acento)
            "border-width": 2,
            "border-color": "data(ring)",
            // glow suave
            "shadow-blur": 18,
            "shadow-color": "data(glow)",
            "shadow-opacity": 0.9,
            // nombre debajo del nodo (label único)
            "label": "data(label)",
            "color": "#4a4a45",
            "font-size": 11,
            "font-weight": 600,
            "text-valign": "bottom",
            "text-margin-y": 14,
            "text-wrap": "ellipsis",
            "text-max-width": "120px",
            "overlay-opacity": 0,
        } },
        { selector: "node:selected", style: {
            "border-width": 3,
            "shadow-blur": 26,
            "shadow-opacity": 1,
        } },
        { selector: "edge", style: {
            "width": 1.5,
            "line-color": "#d6d6d0",
            "target-arrow-color": "#c9c9c4",
            "target-arrow-shape": "circle",
            "curve-style": "bezier",
            "opacity": .85,
        } },
      ],
      layout: { name: "cose", animate: false, padding: 40, nodeRepulsion: 12000, idealEdgeLength: 160 },
    });
    state.cy.on("tap", "node", (evt) => showAgentPanel(evt.target.data("agent")));
    state.cy.on("tap", (evt) => { if (evt.target === state.cy) hideAgentPanel(); });
  } else {
    state.cy.elements().remove();
    state.cy.add(els);
    state.cy.layout({ name: "cose", animate: false, padding: 40, nodeRepulsion: 12000, idealEdgeLength: 160 }).run();
  }

  // si la vista de agentes está visible, ajustar encuadre tras el layout
  if ($("#view-agents").classList.contains("active")) {
    setTimeout(() => { try { state.cy.fit(undefined, 40); } catch (_) {} }, 80);
  }
}

async function refreshGraph() {
  try {
    const g = await api("/api/agents");
    buildGraph(g);
  } catch (_) {}
}

function showAgentPanel(a) {
  const old = $("#agentPanel");
  if (old) old.remove();
  const p = document.createElement("div");
  p.id = "agentPanel";
  const caps = (a.capabilities || []).map((c) => `<span class="cap-chip">${esc(c)}</span>`).join("") || "<i>ninguna</i>";
  const reqs = (a.context_requirements || []).map((r) => `<div class="req-line">requiere: ${esc(r)}</div>`).join("");
  const comps = (a.complements || []).length ? `<div class="req-line">complementa: ${esc(a.complements.join(", "))}</div>` : "";
  p.innerHTML = `
    <button class="ap-close">✕</button>
    <div class="ap-name">${esc(a.name || "agente " + a.id)} <span class="muted">#${a.id}</span></div>
    <div class="ap-kind">${a.is_native ? "nativo" : "plugin"} · v${esc(a.version || "0.1.0")} · ${a.enabled ? "habilitado" : "<b style='color:var(--danger)'>BLOQUEADO</b>"}</div>
    <div class="ap-desc">${esc(a.description || "")}</div>
    <div class="ap-section">capabilities</div>
    <div class="ap-caps">${caps}</div>
    ${reqs ? `<div class="ap-section">requiere</div>${reqs}` : ""}
    ${comps}
    <div class="ap-actions">
      <button id="apBlock">${a.enabled ? "Bloquear" : "Desbloquear"}</button>
    </div>
    <div id="apMsg" class="muted small"></div>`;
  $("#view-agents").appendChild(p);
  p.querySelector(".ap-close").addEventListener("click", hideAgentPanel);
  p.querySelector("#apBlock").addEventListener("click", async () => {
    const enabled = !a.enabled;
    try {
      await api(`/api/agents/${a.id}/enabled`, { method: "POST", body: JSON.stringify({ enabled }) });
      refreshGraph();
      hideAgentPanel();
    } catch (e) { p.querySelector("#apMsg").textContent = e.message; }
  });
}
function hideAgentPanel() { const o = $("#agentPanel"); if (o) o.remove(); }

/* ============================== files ============================== */
const ICONS = { py: "🐍", js: "📜", jsx: "📜", ts: "🔷", tsx: "🔷", cpp: "⚙", h: "⚙", hpp: "⚙", cc: "⚙", json: "🧾", md: "📄", html: "🌐", css: "🎨", txt: "📄", cmake: "🔧", sql: "🗄" };
const LANG = { py: "python", js: "javascript", ts: "typescript", cpp: "cpp", h: "cpp", hpp: "cpp", cc: "cpp", json: "json", md: "markdown", html: "html", css: "css", sh: "shell", bat: "bat", yaml: "yaml", sql: "sql", cmake: "cmake" };

let editor = null;
require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs" } });
require(["vs/editor/editor.main"], () => {
  editor = monaco.editor.create($("#monaco"), {
    value: "", language: "plaintext", theme: "vs", automaticLayout: true,
    minimap: { enabled: false }, fontSize: 13, fontFamily: "JetBrains Mono",
  });
});

function fmt(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + "M" : n > 1024 ? (n / 1024).toFixed(1) + "k" : n + "B"; }

/* ---- árbol de archivos (modo árbol colapsable) ---- */
const treeState = { expanded: new Set(), depth: 0 };
let selectedFilePath = null;

// Carga un directorio y renderiza sus entradas como filas de árbol con
// indentación por `depth`. El clic alterna expandir (crea los hijos una vez)
// / colapsar (elimina el contenedor de hijos) sin duplicar.
function renderTreeDir(path, depth, container) {
  const id = path;
  const row = document.createElement("div");
  row.className = "tree-row dir";
  row.style.paddingLeft = (8 + depth * 16) + "px";
  row.innerHTML = `
    <span class="chev">▶</span>
    <span class="tn">📁 ${esc(path === "." ? "." : path.split("/").pop())}</span>`;
  let childrenBox = null;

  row.addEventListener("click", async () => {
    if (childrenBox && childrenBox.parentNode) {
      // ya expandido → colapsar
      childrenBox.remove();
      row.classList.remove("expanded");
      treeState.expanded.delete(id);
      return;
    }
    // expandir (solo crea los hijos la primera vez)
    treeState.expanded.add(id);
    row.classList.add("expanded");
    childrenBox = document.createElement("div");
    childrenBox.className = "tree-children";
    row.after(childrenBox);
    try {
      const data = await api(`/api/files?path=${encodeURIComponent(path)}`);
      // vaciar por si hubo doble clic rápido
      childrenBox.innerHTML = "";
      data.entries.forEach((e) => {
        if (e.type === "dir") childrenBox.appendChild(renderTreeDir(e.path, depth + 1, childrenBox));
        else childrenBox.appendChild(renderTreeFile(e, depth + 1));
      });
      if (treeState.expanded.has(id)) row.classList.add("expanded");
    } catch (err) {
      childrenBox.innerHTML = `<div class="tree-row muted" style="padding-left:${8 + (depth + 1) * 16}px">${esc(err.message)}</div>`;
    }
  });

  // si viene pre-expandido (p.ej. al recargar), expandir sin duplicar
  if (treeState.expanded.has(id)) {
    row.classList.add("expanded");
  }
  return row;
}

function renderTreeFile(entry, depth) {
  const row = document.createElement("div");
  row.className = "tree-row" + (selectedFilePath === entry.path ? " selected" : "");
  row.style.paddingLeft = (8 + depth * 16) + "px";
  row.innerHTML = `<span class="spacer-i"></span><span class="tn">${ICONS[entry.ext] || "📄"} ${esc(entry.name)}</span><span class="sz">${fmt(entry.size)}</span>`;
  row.addEventListener("click", () => openFile(entry.path));
  return row;
}

async function loadTree(rootPath) {
  const tree = $("#fileTree");
  state.currentDir = rootPath;
  tree.innerHTML = "";
  // raíz
  const rootRow = document.createElement("div");
  rootRow.className = "tree-row dir";
  rootRow.style.paddingLeft = "8px";
  rootRow.innerHTML = `<span class="chev">▶</span><span class="tn">📁 ${esc(rootPath === "." ? "." : rootPath)}</span>`;
  tree.appendChild(rootRow);
  let sub = null;
  rootRow.addEventListener("click", async () => {
    if (sub && sub.parentNode) {
      sub.remove();
      rootRow.classList.remove("expanded");
      treeState.expanded.delete(rootPath);
      return;
    }
    rootRow.classList.add("expanded");
    treeState.expanded.add(rootPath);
    sub = document.createElement("div");
    sub.className = "tree-children";
    rootRow.after(sub);
    try {
      const data = await api(`/api/files?path=${encodeURIComponent(rootPath)}`);
      sub.innerHTML = "";
      data.entries.forEach((e) => {
        if (e.type === "dir") sub.appendChild(renderTreeDir(e.path, 1, sub));
        else sub.appendChild(renderTreeFile(e, 1));
      });
    } catch (err) {
      sub.innerHTML = `<div class="tree-row muted">${esc(err.message)}</div>`;
    }
  });
  updateBreadcrumb(rootPath);
}

/* ---- breadcrumb de la ruta actual ---- */
function updateBreadcrumb(currentPath) {
  const crumb = $("#crumb");
  crumb.innerHTML = "";
  const segs = currentPath === "." ? [] : currentPath.split("/");
  const all = ["."].concat(segs);
  all.forEach((seg, i) => {
    const label = seg === "." ? "proyecto" : seg;
    const pathUp = all.slice(0, i + 1).join("/") || ".";
    const el = document.createElement("span");
    el.className = "crumb-seg";
    el.textContent = i === 0 ? "📁 " + label : label;
    el.addEventListener("click", () => loadTree(pathUp));
    crumb.appendChild(el);
    if (i < all.length - 1) {
      const sep = document.createElement("span");
      sep.className = "crumb-sep";
      sep.textContent = " › ";
      crumb.appendChild(sep);
    }
  });
  // también permite navegar editando la ruta
  $("#edDir").value = currentPath;
}

async function openFile(path) {
  try {
    const d = await api(`/api/file?path=${encodeURIComponent(path)}`);
    state.openFile = path;
    selectedFilePath = path;
    editor.setValue(d.content);
    const ext = path.split(".").pop().toLowerCase();
    monaco.editor.setModelLanguage(editor.getModel(), LANG[ext] || "plaintext");
    $("#openPath").textContent = path;
    $("#askFileHint").textContent = path;
    // marcar seleccionado en el árbol
    $$("#fileTree .tree-row").forEach((r) => r.classList.remove("selected"));
  } catch (e) { $("#saveMsg").textContent = e.message; }
}

/* ---- chat de preguntas sobre el archivo seleccionado ---- */
function askMessage(q, a) {
  const log = $("#askLog");
  const empty = log.querySelector(".ask-empty");
  if (empty) empty.remove();
  const wrap = document.createElement("div");
  wrap.className = "ask-msg";
  if (q) {
    const qEl = document.createElement("div");
    qEl.className = "q";
    qEl.textContent = q;
    wrap.appendChild(qEl);
  }
  if (a !== undefined) {
    const aEl = document.createElement("div");
    aEl.className = "a";
    aEl.textContent = a; // respuesta del LLM SIEMPRE como texto (evita XSS)
    wrap.appendChild(aEl);
  } else {
    const t = document.createElement("div");
    t.className = "a muted";
    t.textContent = "…";
    wrap.appendChild(t);
  }
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

async function askFile() {
  const input = $("#askInput");
  const q = input.value.trim();
  if (!q || !selectedFilePath) return;
  input.value = "";
  const placeholder = askMessage(q);
  const btn = $("#btnAsk");
  btn.disabled = true;
  try {
    const r = await api("/api/file/ask", { method: "POST", body: JSON.stringify({ path: selectedFilePath, question: q }) });
    placeholder.querySelector(".a").textContent = r.answer;
  } catch (e) {
    placeholder.querySelector(".a").textContent = "Error: " + e.message;
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

$("#btnAsk").addEventListener("click", askFile);
$("#askInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askFile(); }
});
$("#askInput").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
});

$("#btnUpDir").addEventListener("click", () => {
  const parts = state.currentDir.split("/").filter(Boolean);
  parts.pop();
  loadTree(parts.join("/") || ".");
});
$("#btnRefreshTree").addEventListener("click", () => loadTree(state.currentDir));
$("#btnCollapseAll").addEventListener("click", () => { treeState.expanded.clear(); loadTree(state.currentDir); });
$("#edDir").addEventListener("keydown", (e) => { if (e.key === "Enter") loadTree(e.target.value.trim() || "."); });
$("#btnSave").addEventListener("click", async () => {
  if (!state.openFile) return;
  try {
    await api("/api/file", { method: "PUT", body: JSON.stringify({ path: state.openFile, content: editor.getValue() }) });
    $("#saveMsg").textContent = "guardado ✓";
    setTimeout(() => { $("#saveMsg").textContent = ""; }, 2000);
  } catch (e) { $("#saveMsg").textContent = "error: " + e.message; }
});

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
    $$(".role").forEach((card) => {
      const role = card.dataset.role;
      const sec = llm[role] || {};
      fillSel(card.querySelector(".r-provider"), sec.provider || llm.provider || "local");
      card.querySelector(".r-model").value = sec.model || "";
    });
  } catch (_) {}
}
$("#btnApplyConfig").addEventListener("click", async () => {
  const roles = { global: {
    provider: $("#gProvider").value, model: $("#gModel").value.trim(),
    base_url: $("#gBase").value.trim(), api_key: $("#gKey").value.trim(),
  } };
  $$(".role").forEach((card) => {
    const role = card.dataset.role;
    const sec = { provider: card.querySelector(".r-provider").value };
    const m = card.querySelector(".r-model").value.trim();
    if (m) sec.model = m;
    roles[role] = sec;
  });
  try {
    await api("/api/config/roles", { method: "PUT", body: JSON.stringify({ roles }) });
    $("#cfgMsg").textContent = "configuración aplicada ✓";
    setTimeout(() => { $("#cfgMsg").textContent = ""; }, 2500);
    loadConfig();
  } catch (e) { $("#cfgMsg").textContent = "error: " + e.message; }
});

/* ============================== folder picker ============================== */
function openFolderModal(path) {
  const dirs = $("#fmDirs");
  dirs.innerHTML = "";
  api(`/api/dirs?path=${encodeURIComponent(path || "")}`).then((data) => {
    $("#fmPath").value = data.path;
    const parts = data.path.split(/[\\/]/).filter(Boolean); parts.pop();
    const parent = parts.join("/") || (data.path.includes(":") ? data.path.slice(0, 3) : "");
    const up = document.createElement("div");
    up.className = "dir-row"; up.textContent = "↑ ..";
    up.addEventListener("click", () => openFolderModal(parent || data.path));
    dirs.appendChild(up);
    data.entries.forEach((e) => {
      const row = document.createElement("div");
      row.className = "dir-row"; row.textContent = "📁 " + e.name;
      row.addEventListener("dblclick", () => openFolderModal(e.path));
      row.addEventListener("click", () => { $("#fmPath").value = e.path; });
      dirs.appendChild(row);
    });
  }).catch((e) => { dirs.innerHTML = `<span class="muted">${e.message}</span>`; });
  $("#folderModal").classList.remove("hidden");
}
$("#btnPickFolder").addEventListener("click", () => openFolderModal(""));
$("#fmClose").addEventListener("click", () => $("#folderModal").classList.add("hidden"));
$("#fmOpen").addEventListener("click", async () => {
  const p = $("#fmPath").value.trim();
  if (!p) return;
  try {
    await api("/api/project/set", { method: "POST", body: JSON.stringify({ path: p }) });
    $("#folderModal").classList.add("hidden");
    await refreshSystem();
  } catch (e) { /* modal abierto */ }
});
$("#fmPath").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#fmOpen").click(); });

/* ============================== boot ============================== */
refreshSystem();
loadConfig();
loadTree(".");
updateSend();

// activar vista por hash (p.ej. #agents, #files, #settings) o por defecto chat.
// Para agentes espera a que el grafo exista y re-encuadra (la vista nace con
// altura 0 al crearse el cy en el boot).
(function activateView() {
  const target = (location.hash || "").replace("#", "");
  const activate = () => {
    const btn = document.querySelector(`.side-link[data-view="${target}"]`);
    if (btn) btn.click();
    if (target === "agents") {
      const wait = () => {
        if (state.cy) {
          state.cy.resize();
          setTimeout(() => {
            state.cy.layout({ name: "cose", animate: false, padding: 40, nodeRepulsion: 12000, idealEdgeLength: 160 }).run();
            try { state.cy.fit(undefined, 40); } catch (_) {}
          }, 80);
        } else {
          setTimeout(wait, 100);
        }
      };
      wait();
    }
  };
  if (target) setTimeout(activate, 120);
})();
