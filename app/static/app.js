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
  $("msgs").append(d); $("msgs").scrollTop = $("msgs").scrollHeight; return d;
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

/* ── arranque ── */
async function init() {
  try {
    const [h, a] = await Promise.all([fetch("/health").then((r) => r.json()), fetch("/api/agentes").then((r) => r.json())]);
    estado.agentes = a; render_agentes();
    $("estado").classList.add("on"); $("estadoTxt").textContent = `Conectado · ${a.length} agentes${h.acceso_configurado ? "" : " · sin claves"}`;
  } catch {
    $("estado").classList.add("off"); $("estadoTxt").textContent = "Sin conexión con el servidor";
  }
  $("btnClave").onclick = pedir_clave;
  $("btnCancelaClave").onclick = () => $("dlgClave").close();
  $("formClave").onsubmit = () => { const v = $("inClave").value.trim(); if (v) { claveMem = v; guardar.set(v); } };
  $("btnNueva").onclick = () => { estado.conv = null; $("msgs").innerHTML = ""; $("traza").innerHTML = '<p class="hint">Nueva conversación: los agentes no recordarán lo anterior.</p>'; };
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
