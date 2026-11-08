"use strict";
/* Consola de la red de agentes A2A. Sin dependencias externas. La clave API la escribe la persona y vive solo en
   sessionStorage (esta pestaña); nunca está en el código ni se envía a terceros. */

const $ = (id) => document.getElementById(id);
const estado = { agentes: [], sel: new Set(), conv: null, ocupado: false };

/* ── almacenamiento seguro (puede fallar en ventanas privadas) ── */
const guardar = {
  get: () => { try { return sessionStorage.getItem("a2a_key") || ""; } catch { return ""; } },
  set: (v) => { try { sessionStorage.setItem("a2a_key", v); } catch { /* sin persistencia */ } },
};
let claveMem = guardar.get();

/* ── utilidades ── */
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const corto = (s, n = 8) => (s || "").slice(0, n) + "…" + (s || "").slice(-4);
const seg = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms");

/* Markdown mínimo y seguro: todo se escapa primero, luego se aplican reglas. */
function md(src) {
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  const out = [], lineas = src.replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lineas.length) {
    const l = lineas[i];
    let m;
    if (!l.trim()) { i++; continue; }
    if (/^\s*---+\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
    if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { const n = Math.min(m[1].length + 1, 4); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*>/.test(l)) {
      const b = []; while (i < lineas.length && /^\s*>/.test(lineas[i])) b.push(lineas[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${inline(b.join(" "))}</blockquote>`); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|[\s:|-]+\|\s*$/.test(lineas[i + 1] || "")) {
      const cel = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const cab = cel(l); i += 2; const filas = [];
      while (i < lineas.length && /^\s*\|.*\|\s*$/.test(lineas[i])) filas.push(cel(lineas[i++]));
      out.push("<table><thead><tr>" + cab.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
        filas.map((f) => "<tr>" + f.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const orden = /^\s*\d+[.)]/.test(l), it = [];
      while (i < lineas.length && /^\s*([-*•]|\d+[.)])\s+/.test(lineas[i])) it.push(lineas[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      out.push((orden ? "<ol>" : "<ul>") + it.map((x) => `<li>${inline(x)}</li>`).join("") + (orden ? "</ol>" : "</ul>")); continue;
    }
    const p = []; while (i < lineas.length && lineas[i].trim() && !/^(\s*([-*•]|\d+[.)])\s+|#{1,4}\s|\s*>|\s*---+\s*$|\s*\|)/.test(lineas[i])) p.push(lineas[i++]);
    if (!p.length) { p.push(lineas[i++]); }
    out.push(`<p>${inline(p.join(" "))}</p>`);
  }
  return out.join("");
}

/* ── interfaz ── */
function render_agentes() {
  const ul = $("listaAgentes"); ul.innerHTML = "";
  for (const a of estado.agentes) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button"; b.className = "agente"; b.setAttribute("aria-pressed", estado.sel.has(a.id));
    b.innerHTML = `<b>${esc(a.nombre)}</b><span>${esc(a.especialidad)}</span>${a.local ? "" : '<span class="ext">Agente externo A2A</span>'}`;
    b.onclick = () => { estado.sel.has(a.id) ? estado.sel.delete(a.id) : estado.sel.add(a.id); render_agentes(); };
    li.append(b); ul.append(li);
  }
  const n = estado.sel.size;
  $("badgeModo").textContent = n === 0 ? "Automático" : n === 1 ? "Directo" : `${n} agentes`;
  const ej = $("ejemplos"); ej.innerHTML = "";
  const base = estado.agentes.filter((a) => n === 0 || estado.sel.has(a.id)).flatMap((a) => a.ejemplos || []).slice(0, 4);
  for (const t of base) { const c = document.createElement("button"); c.type = "button"; c.className = "chip"; c.textContent = t; c.onclick = () => { $("texto").value = t; $("texto").focus(); actualizar_contador(); }; ej.append(c); }
}

function burbuja(clase, html) {
  $("vacio")?.remove();
  const d = document.createElement("div"); d.className = "msg " + clase; d.innerHTML = html;
  $("msgs").append(d);
  if (clase.includes("md")) d.scrollIntoView({ block: "start" });      // respuesta larga: se lee desde el inicio
  else $("msgs").scrollTop = $("msgs").scrollHeight;
  return d;
}
const actualizar_contador = () => { $("contador").textContent = `${$("texto").value.length}/4000`; };

function pedir_clave() { $("inClave").value = ""; $("dlgClave").showModal(); $("inClave").focus(); }

/* ── traza ── */
function traza_inicio(rid, modo) {
  $("traza").innerHTML = `<dl class="kv">
    <dt>Solicitud</dt><dd class="mono" id="trRid">${esc(rid)}</dd>
    <dt>Estado</dt><dd><span class="estado run" id="trEstado">en curso</span></dd>
    <dt>Modo</dt><dd id="trModo">${esc(modo)}</dd>
    <dt>Ruta</dt><dd id="trRuta">—</dd>
    <dt>Total</dt><dd id="trTotal">—</dd></dl><div id="trAgentes" class="traza"></div>`;
}
function traza_agente(a) {
  const d = document.createElement("details"); d.className = "tr-agente"; d.open = false;
  d.innerHTML = `<summary><div><b>${esc(a.nombre)}</b> · <span class="mono">${esc(a.id)}</span>
      <div class="sub">${seg(a.ms)} · ctx <span class="mono">${esc(corto(a.context_id))}</span></div></div>
      <span class="estado ${a.estado}">${a.estado}</span></summary>
      <pre>${esc(a.estado === "ok" ? a.respuesta : a.error)}</pre>`;
  $("trAgentes")?.append(d);
}


/* ── diagrama animado del flujo A2A ── */
const FL = { modo: "paralelo", sel: [], n: {}, t0: 0, tRouter: 0 };
const NS = "http://www.w3.org/2000/svg";
const fl_estado_txt = (t, c) => { const e = $("flujoEstado"); e.textContent = t; e.className = "flujo-estado " + (c || ""); };

function fl_reset(modo) {
  FL.modo = modo || $("modo").value; FL.sel = []; FL.n = {}; FL.t0 = performance.now();
  fl_set("user", "ok", "tu consulta"); fl_render();
}
function fl_set(id, s, t = "") { FL.n[id] = { s, t }; }
const fl_corto = (a) => (a.nombre || a.id).split(" ")[0];

function fl_render() {
  const ag = estado.agentes, n = Math.max(ag.length, 1), GAP = 46, H = Math.max(150, n * GAP + 16);
  const cy = H / 2, W = 760;
  const pos = { user: [8, cy - 20, 104, 40], router: [150, cy - 20, 112, 40], synth: [566, cy - 20, 92, 40], resp: [684, cy - 20, 68, 40] };
  ag.forEach((a, i) => { pos[a.id] = [326, 8 + i * GAP, 200, 36]; });
  const est = (id) => FL.n[id] || { s: "idle", t: "" };
  const multi = FL.sel.length > 1, seq = FL.modo === "secuencial" && multi;
  const dr = (id) => [pos[id][0] + pos[id][2], pos[id][1] + pos[id][3] / 2];   // derecha-centro
  const iz = (id) => [pos[id][0], pos[id][1] + pos[id][3] / 2];                // izquierda-centro
  const curva = (a, b) => { const dx = Math.max(24, (b[0] - a[0]) / 2); return `M${a[0]},${a[1]} C${a[0] + dx},${a[1]} ${b[0] - dx},${b[1]} ${b[0]},${b[1]}`; };
  const aristas = [["user", "router", "router"]];
  if (FL.sel.length) {
    if (seq) {
      aristas.push(["router", FL.sel[0], FL.sel[0]]);
      for (let i = 1; i < FL.sel.length; i++) aristas.push([FL.sel[i - 1], FL.sel[i], FL.sel[i], "cadena"]);
      aristas.push([FL.sel[FL.sel.length - 1], "synth", "synth"]);
    } else {
      FL.sel.forEach((id) => aristas.push(["router", id, id]));
      if (multi) FL.sel.forEach((id) => aristas.push([id, "synth", "synth", null, id]));
      else aristas.push([FL.sel[0], "resp", "resp"]);
    }
    if (multi) aristas.push(["synth", "resp", "resp"]);
  }
  const clase = (dest, origen) => {
    const d = est(dest).s, o = est(origen || dest).s;
    if (d === "error" || o === "error") return "error";
    if (d === "activo") return "active";
    return d === "ok" ? "done" : "";
  };
  let svg = `<svg xmlns="${NS}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`;
  for (const [a, b, dest, tipo, origen] of aristas) {
    const d = tipo === "cadena"
      ? (() => { const p1 = dr(a), p2 = dr(b), x = p1[0] + 34; return `M${p1[0]},${p1[1]} C${x},${p1[1]} ${x},${p2[1]} ${p2[0]},${p2[1]}`; })()
      : curva(dr(a), iz(b));
    svg += `<path class="fl-edge ${clase(dest, origen || (tipo === "cadena" ? a : null))}" d="${d}"/>`;
  }
  const nodo = (id, t1, vacio) => {
    const [x, y, w, h] = pos[id], e = est(id);
    let s = e.s;
    if (id !== "user" && id !== "router" && FL.sel.length && id !== "resp" && id !== "synth" && !FL.sel.includes(id)) s = "omitido";
    if (id === "synth" && FL.sel.length === 1) s = "omitido";
    const t2 = s === "omitido" ? "no consultado" : e.t || (s === "espera" ? "en espera" : s === "activo" ? "trabajando…" : vacio || "");
    return `<g class="fl-nodo ${s}"><rect class="fl-box" x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>` +
      `<text class="fl-t1" x="${x + w / 2}" y="${y + (t2 ? 16 : 24)}" text-anchor="middle">${esc(t1)}</text>` +
      (t2 ? `<text class="fl-t2" x="${x + w / 2}" y="${y + 30}" text-anchor="middle">${esc(t2)}</text>` : "") + `</g>`;
  };
  svg += nodo("user", "Usuario") + nodo("router", "Enrutador", FL.sel.length ? "" : "elige agentes");
  for (const a of ag) svg += nodo(a.id, fl_corto(a), "disponible");
  svg += nodo("synth", "Síntesis", "") + nodo("resp", "Respuesta", "") + "</svg>";
  $("flujo").innerHTML = svg;
}

/* aplica un evento del servidor al diagrama */
function fl_evento(ev) {
  const ms = () => seg(Math.round(performance.now() - FL.tRouter));
  if (ev.evento === "inicio") { fl_reset(ev.modo); fl_set("router", "activo", "eligiendo…"); FL.tRouter = performance.now(); fl_estado_txt("en curso", "run"); }
  else if (ev.evento === "enrutado") {
    FL.sel = ev.agentes; fl_set("router", "ok", ev.automatico ? "automático " + ms() : "manual");
    const seq = FL.modo === "secuencial" && FL.sel.length > 1;
    FL.sel.forEach((id, i) => fl_set(id, !seq || i === 0 ? "activo" : "espera"));
    if (FL.sel.length > 1) fl_set("synth", "espera"); fl_set("resp", "espera");
    FL.tAgente = performance.now();
  } else if (ev.evento === "agente") {
    fl_set(ev.id, ev.estado === "ok" ? "ok" : "error", ev.estado === "ok" ? seg(ev.ms) : "falló");
    if (FL.modo === "secuencial") { const sig = FL.sel[FL.sel.indexOf(ev.id) + 1]; if (sig) fl_set(sig, "activo"); }
    if (FL.sel.length === 1) fl_set("resp", ev.estado === "ok" ? "ok" : "error", "");
    FL.tSint = performance.now();
  } else if (ev.evento === "sintesis") fl_set("synth", "activo", "integrando…");
  else if (ev.evento === "final") {
    if (FL.sel.length > 1) fl_set("synth", "ok", "listo");
    fl_set("resp", "ok", seg(ev.ms_total)); fl_estado_txt("completado " + seg(ev.ms_total), "ok");
  } else if (ev.evento === "error") {
    for (const k of Object.keys(FL.n)) if (FL.n[k].s === "activo") fl_set(k, "error", "falló");
    fl_set("resp", "error", ""); fl_estado_txt("con errores", "error");
  } else return;
  fl_render();
}

/* estado final de una consulta guardada (historial) */
function fl_desde_resultado(r) {
  fl_reset(r.modo); FL.sel = r.agentes.map((a) => a.id);
  fl_set("router", "ok", r.automatico ? "automático" : "manual");
  for (const a of r.agentes) fl_set(a.id, a.estado === "ok" ? "ok" : "error", a.estado === "ok" ? seg(a.ms) : "falló");
  if (FL.sel.length > 1) fl_set("synth", "ok", "listo");
  fl_set("resp", "ok", seg(r.ms_total)); fl_estado_txt("guardada " + seg(r.ms_total), "ok"); fl_render();
}

/* ── envío con streaming NDJSON ── */
async function enviar(texto) {
  if (!claveMem) { pedir_clave(); return; }
  estado.ocupado = true; $("btnEnviar").disabled = true;
  burbuja("user", esc(texto));
  const prog = burbuja("bot progreso", '<span class="spin"></span><span id="progTxt">Enviando consulta…</span>');
  const setProg = (t) => { const e = prog.querySelector("#progTxt"); if (e) e.textContent = t; };
  const nombres = (ids) => ids.map((i) => estado.agentes.find((a) => a.id === i)?.nombre || i);
  let final = null;
  try {
    const r = await fetch("/api/consulta/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": claveMem },
      body: JSON.stringify({ mensaje: texto, agentes: [...estado.sel], modo: $("modo").value, conversation_id: estado.conv }),
    });
    if (!r.ok) {
      let det = ""; try { det = (await r.json()).detail; } catch { /* sin cuerpo */ }
      if (r.status === 401) { claveMem = ""; guardar.set(""); pedir_clave(); throw new Error("Clave ausente o inválida. Ingrésala de nuevo."); }
      if (r.status === 422) det = "Consulta inválida (vacía, muy larga o agente desconocido).";
      throw new Error(det || `Error HTTP ${r.status}`);
    }
    const lector = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await lector.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let k; while ((k = buf.indexOf("\n")) >= 0) {
        const linea = buf.slice(0, k).trim(); buf = buf.slice(k + 1); if (!linea) continue;
        const ev = JSON.parse(linea);
        fl_evento(ev);
        if (ev.evento === "inicio") { estado.conv = ev.conversation_id; traza_inicio(ev.solicitud_id, ev.modo); setProg("Eligiendo agentes…"); }
        else if (ev.evento === "enrutado") { $("trRuta").textContent = (ev.automatico ? "Automática: " : "Elegidos: ") + nombres(ev.agentes).join(", "); setProg("Consultando por A2A a " + nombres(ev.agentes).join(", ") + "…"); }
        else if (ev.evento === "agente") { traza_agente(ev); setProg(`${ev.nombre} respondió (${seg(ev.ms)})…`); }
        else if (ev.evento === "sintesis") setProg("Integrando los aportes…");
        else if (ev.evento === "final") final = ev;
        else if (ev.evento === "error") throw new Error(ev.detalle);
      }
    }
    if (!final) throw new Error("La consulta terminó sin resultado.");
    prog.remove();
    burbuja("bot md", md(final.respuesta));
    $("trEstado").className = "estado ok"; $("trEstado").textContent = "ok"; $("trTotal").textContent = seg(final.ms_total);
  } catch (e) {
    prog.remove(); burbuja("bot err", esc(e.message || "Error de conexión con el servidor."));
    const t = $("trEstado"); if (t) { t.className = "estado error"; t.textContent = "error"; }
  } finally {
    estado.ocupado = false; $("btnEnviar").disabled = false; $("texto").focus();
  }
}


/* ── historial ── */
async function api_get(ruta) {
  const r = await fetch(ruta, { headers: { "X-API-Key": claveMem } });
  if (r.status === 401) { claveMem = ""; guardar.set(""); throw new Error("Clave ausente o inválida."); }
  if (!r.ok) throw new Error(`Error HTTP ${r.status}`);
  return r.json();
}
async function abrir_historial() {
  if (!claveMem) { pedir_clave(); return; }
  const ul = $("listaHist"); ul.innerHTML = '<li class="hint">Cargando…</li>'; $("dlgHistorial").showModal();
  try {
    const lista = await api_get("/api/historial?limite=40");
    ul.innerHTML = lista.length ? "" : '<li class="hint">Aún no hay consultas guardadas.</li>';
    for (const c of lista) {
      const li = document.createElement("li"), b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<b>${esc(c.pregunta)}</b><small>${esc(c.creada_en.replace("T", " ").replace("+00:00", " UTC"))} · ${c.agentes.map(esc).join(", ")} · ${seg(c.ms_total)} · ${esc(c.modo)}</small>`;
      b.onclick = () => cargar_conversacion(c.conversation_id, c.solicitud_id);
      li.append(b); ul.append(li);
    }
  } catch (e) { ul.innerHTML = `<li class="hint">${esc(e.message)}</li>`; }
}
async function cargar_conversacion(conv, rid) {
  try {
    const items = await api_get(`/api/historial?conversation_id=${encodeURIComponent(conv)}&detalle=true&limite=50`);
    $("msgs").innerHTML = ""; estado.conv = conv;
    for (const it of items) { burbuja("user", esc(it.pregunta)); burbuja("bot md", md(it.respuesta)); }
    const sel = items.find((i) => i.solicitud_id === rid) || items[items.length - 1];
    traza_inicio(sel.solicitud_id, sel.modo); sel.agentes.forEach(traza_agente);
    $("trRuta").textContent = (sel.automatico ? "Automática: " : "Elegidos: ") + sel.agentes.map((a) => a.nombre).join(", ");
    $("trEstado").className = "estado ok"; $("trEstado").textContent = "guardada"; $("trTotal").textContent = seg(sel.ms_total);
    fl_desde_resultado(sel); $("dlgHistorial").close();
  } catch (e) { $("listaHist").innerHTML = `<li class="hint">${esc(e.message)}</li>`; }
}

/* ── arranque ── */
async function init() {
  try {
    const [h, a] = await Promise.all([fetch("/health").then((r) => r.json()), fetch("/api/agentes").then((r) => r.json())]);
    estado.agentes = a; render_agentes(); fl_reset(); fl_set("user", "idle", ""); fl_render();
    $("estado").classList.add("on"); $("estadoTxt").textContent = `Conectado · ${a.length} agentes${h.acceso_configurado ? "" : " · sin claves"}`;
  } catch {
    $("estado").classList.add("off"); $("estadoTxt").textContent = "Sin conexión con el servidor";
  }
  $("btnClave").onclick = pedir_clave;
  $("btnHistorial").onclick = abrir_historial;
  $("btnCierraHist").onclick = () => $("dlgHistorial").close();
  $("btnCancelaClave").onclick = () => $("dlgClave").close();
  $("formClave").onsubmit = () => { const v = $("inClave").value.trim(); if (v) { claveMem = v; guardar.set(v); } };
  $("btnNueva").onclick = () => { estado.conv = null; fl_reset(); fl_estado_txt("en espera"); FL.sel = []; fl_render(); $("msgs").innerHTML = ""; $("traza").innerHTML = '<p class="hint">Nueva conversación: los agentes no recordarán lo anterior.</p>'; };
  $("modo").onchange = () => { $("hintModo").textContent = $("modo").value === "secuencial"
    ? "Cada agente recibe como dato los aportes de los anteriores y puede señalar contradicciones."
    : "Se consultan todos los agentes elegidos a la vez. Si no eliges, se usan los que coincidan con la consulta."; };
  $("texto").addEventListener("input", actualizar_contador);
  $("texto").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); } });
  $("form").onsubmit = (e) => {
    e.preventDefault(); const t = $("texto").value.trim();
    if (!t || estado.ocupado) return;
    $("texto").value = ""; actualizar_contador(); enviar(t);
  };
}
init();
