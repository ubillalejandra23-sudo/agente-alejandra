"""Evaluación automática de fidelidad, A2A y seguridad contra un servidor en marcha (local o Heroku).

    python -m evaluacion.evaluar                          # contra http://127.0.0.1:8000 con la 1ª clave de API_KEYS (.env)
    python -m evaluacion.evaluar --url https://mi-app.herokuapp.com --key <clave> --entorno HEROKU
    python -m evaluacion.evaluar --solo F01,C01,SEG01     # solo algunos casos
    python -m evaluacion.evaluar --sin-modelo             # solo casos de seguridad/validación (no consumen tokens)

Genera `evidencias/resultados/<fecha>-<entorno>/REPORTE.md` (formato del taller: entorno y fecha, solicitud, resultado
esperado, resultado obtenido, observaciones) y `resultados.json`. La clave NUNCA se escribe en los reportes.
Termina con código 1 si algún caso falla.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import httpx

from evaluacion.casos import CASOS

RAIZ = Path(__file__).resolve().parent.parent


def norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def clave_por_defecto() -> str:
    if os.environ.get("EVAL_API_KEY"):
        return os.environ["EVAL_API_KEY"]
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
    except ImportError:
        pass
    return (os.environ.get("API_KEYS", "").split(",")[0] or "").strip()


class Ctx:
    def __init__(self, url, clave):
        self.url, self.clave = url.rstrip("/"), clave
        self.http = httpx.Client(timeout=240)
        self.ultima_consulta: str | None = None  # solicitud_id de la última consulta colaborativa

    def pedir(self, metodo, ruta, cuerpo=None, clave="OK"):
        h = {}
        k = self.clave if clave == "OK" else clave
        if k:
            h["X-API-Key"] = k
        for intento in (1, 2):
            r = self.http.request(metodo, self.url + ruta, json=cuerpo, headers=h)
            if r.status_code == 429 and intento == 1:  # límite de consultas por minuto: esperar y reintentar una vez
                time.sleep(int(r.headers.get("Retry-After", "30")))
                continue
            return r


    def consulta_stream(self, cuerpo: dict) -> dict:
        """Consulta colaborativa por /api/consulta/stream (NDJSON). Es la vía robusta en Heroku, que corta a los 30 s las
        respuestas sin bytes; el servidor envía eventos de progreso y 'ping' de mantenimiento."""
        for intento in (1, 2):
            with self.http.stream("POST", self.url + "/api/consulta/stream", json=cuerpo,
                                  headers={"X-API-Key": self.clave}) as r:
                if r.status_code == 429 and intento == 1:
                    time.sleep(int(r.headers.get("Retry-After", "30")))
                    continue
                if r.status_code != 200:
                    r.read()
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                for linea in r.iter_lines():
                    if not linea.strip():
                        continue
                    ev = json.loads(linea)
                    if ev["evento"] == "final":
                        return ev
                    if ev["evento"] == "error":
                        raise RuntimeError(f"{ev.get('status')}: {ev['detalle']}")
        raise RuntimeError("la consulta terminó sin resultado")


def comprobar(check, texto: str, datos: dict, ctx: Ctx) -> tuple[bool, str]:
    tipo, args = check
    n = norm(texto)
    if tipo == "contiene":
        ok = any(norm(a) in n for a in args)
        return ok, f"contiene alguna de {args}"
    if tipo == "contiene_todo":
        faltan = [a for a in args if norm(a) not in n]
        return not faltan, f"contiene todo {args}" + (f" (faltan {faltan})" if faltan else "")
    if tipo == "no_contiene":
        hallados = [a for a in args if norm(a) in n]
        return not hallados, f"no contiene {args}" + (f" (aparece {hallados})" if hallados else "")
    ids = [a["id"] for a in datos.get("agentes", [])]
    if tipo == "agentes_incluyen":
        return all(a in ids for a in args), f"participan {args} (participaron {ids})"
    if tipo == "agentes_excluyen":
        return not any(a in ids for a in args), f"no participan {args} (participaron {ids})"
    if tipo == "es_coordinador":
        return bool(datos.get("coordinador")) and not datos.get("agentes"), "respondió el Coordinador (ningún especialista)"
    if tipo == "todos_ok":
        malos = [a["id"] for a in datos.get("agentes", []) if a["estado"] != "ok"]
        return not malos, "todos los agentes respondieron OK" + (f" (fallaron {malos})" if malos else "")
    if tipo == "sin_cifras_dudosas":
        d = datos.get("verificacion", {}).get("cifras_no_respaldadas", ["?"])
        return not d, "conclusión sin cifras ausentes de los aportes" + (f" (dudosas {d})" if d else "")
    if tipo == "aporte_literal":
        a = next((x for x in datos.get("agentes", []) if x["id"] == args[0]), None)
        return bool(a and a.get("respuesta") and a["respuesta"] in datos.get("respuesta", "")), \
            f"aporte de {args[0]} incluido sin modificar"
    if tipo == "historial_contiene_ultima":
        rid = datos.get("solicitud_id")
        r = ctx.pedir("GET", f"/api/historial/{rid}")
        return r.status_code == 200 and r.json().get("solicitud_id") == rid, "la consulta quedó guardada en /api/historial"
    return False, f"comprobación desconocida: {tipo}"


def ejecutar_caso(c: dict, ctx: Ctx) -> dict:
    t0 = time.monotonic()
    res = {"id": c["id"], "nombre": c["nombre"], "tipo": c["tipo"], "solicitud": c["solicitud"], "esperado": c["esperado"],
           "checks": [], "obtenido": "", "ok": False}
    try:
        if c["tipo"] == "http":
            r = ctx.pedir(c["metodo"], c["ruta"], c["cuerpo"], c["clave"])
            res["obtenido"] = f"HTTP {r.status_code}: {r.text[:300]}"
            ok = r.status_code == c["status"]
            res["checks"].append({"ok": ok, "desc": f"HTTP {c['status']}"})
            for t in c.get("texto_contiene", []):
                res["checks"].append({"ok": t in r.text, "desc": f"respuesta contiene '{t}'"})
        else:
            if c["tipo"] == "chat":
                r = ctx.pedir("POST", "/chat", {"mensaje": c["solicitud"], "agente": c["agente"]})
                r.raise_for_status()
                texto, datos = r.json()["respuesta"], {}
            else:
                datos = ctx.consulta_stream({"mensaje": c["solicitud"], "agentes": c["agentes"], "modo": c["modo"]})
                texto = datos["respuesta"]
                ctx.ultima_consulta = datos["solicitud_id"]
                res["solicitud_id"] = datos["solicitud_id"]
                res["coordinador"] = datos.get("coordinador", False)
                res["agentes"] = [{"id": a["id"], "estado": a["estado"], "ms": a["ms"], "context_id": a["context_id"]}
                                  for a in datos["agentes"]]
            res["obtenido"] = texto
            for chk in c["checks"]:
                ok, desc = comprobar(chk, texto, datos, ctx)
                res["checks"].append({"ok": ok, "desc": desc})
    except Exception as e:
        res["obtenido"] = f"ERROR: {type(e).__name__}: {e}"
        res["checks"].append({"ok": False, "desc": "la solicitud se completó sin errores"})
    res["ok"] = bool(res["checks"]) and all(k["ok"] for k in res["checks"])
    res["ms"] = int((time.monotonic() - t0) * 1000)
    return res


def reporte(resultados, entorno, url, fecha) -> str:
    total, ok = len(resultados), sum(r["ok"] for r in resultados)
    host = re.sub(r"^https?://", "", url)
    L = [f"# Reporte de evaluación · {entorno}", "",
         f"- **Entorno:** {entorno} ({host})", f"- **Fecha:** {fecha:%Y-%m-%d %H:%M}",
         f"- **Resultado:** {ok}/{total} casos aprobados", "",
         "| Caso | Prueba | Resultado | Tiempo |", "|---|---|---|---|"]
    L += [f"| {r['id']} | {r['nombre']} | {'✅ aprobado' if r['ok'] else '❌ FALLÓ'} | {r['ms'] / 1000:.1f} s |" for r in resultados]
    L += ["", "---", ""]
    for r in resultados:
        obt = r["obtenido"] if len(r["obtenido"]) <= 1500 else r["obtenido"][:1500] + " […]"
        L += [f"## {r['id']} · {r['nombre']}", "",
              "| Dato | Registro |", "|---|---|",
              f"| Entorno y fecha | {entorno} · {fecha:%Y-%m-%d} |",
              f"| Solicitud | {r['solicitud']} |", f"| Resultado esperado | {r['esperado']} |",
              f"| Resultado | {'APROBADO' if r['ok'] else 'FALLÓ'} |"]
        if r.get("solicitud_id"):
            L.append(f"| Trazabilidad | `solicitud_id` = `{r['solicitud_id']}` (busque `rid={r['solicitud_id']}` en el log) |")
        if r.get("agentes"):
            L.append("| Llamadas A2A | " + "; ".join(f"{a['id']} {a['estado']} {a['ms']} ms (ctx `{a['context_id'][:8]}…`)"
                                                  for a in r["agentes"]) + " |")
        L += ["", "**Comprobaciones:**", ""]
        L += [f"- {'✅' if k['ok'] else '❌'} {k['desc']}" for k in r["checks"]]
        L += ["", "**Resultado obtenido:**", "", "> " + obt.replace("\n", "\n> "), ""]
    L += ["---", "", "_Observaciones: los datos de SII/CMF/fondos son simulados. Las comprobaciones buscan cifras y "
          "términos exactos en la respuesta; una redacción distinta pero correcta puede marcarse como fallo y debe "
          "revisarse a mano. La clave de acceso no se registra en este reporte._"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default=None, help="clave de acceso (por defecto la 1ª de API_KEYS en .env o EVAL_API_KEY)")
    ap.add_argument("--entorno", default=None, help="LOCAL, HEROKU... (por defecto según la URL)")
    ap.add_argument("--solo", default="", help="ids separados por coma, p. ej. F01,C01")
    ap.add_argument("--sin-modelo", action="store_true", help="solo casos de seguridad/validación (sin consumir tokens)")
    a = ap.parse_args()

    entorno = a.entorno or ("LOCAL" if re.search(r"127\.0\.0\.1|localhost", a.url) else "REMOTO")
    ctx = Ctx(a.url, a.key or clave_por_defecto())
    if not ctx.clave:
        sys.exit("Falta la clave de acceso: use --key, EVAL_API_KEY o API_KEYS en .env")
    try:
        ctx.http.get(ctx.url + "/health", timeout=15).raise_for_status()
    except Exception as e:
        sys.exit(f"No se pudo conectar con {a.url}: {e}")

    solo = {s.strip() for s in a.solo.split(",") if s.strip()}
    casos = [c for c in CASOS if (not solo or c["id"] in solo) and (not a.sin_modelo or c["tipo"] == "http")]
    resultados = []
    print(f"Evaluando {len(casos)} casos en {entorno} ({a.url})\n")
    for c in casos:
        r = ejecutar_caso(c, ctx)
        resultados.append(r)
        print(f"{'OK ' if r['ok'] else 'FALLA'} {r['id']:<6} {r['nombre']:<52} {r['ms'] / 1000:5.1f} s")
        for k in r["checks"]:
            if not k["ok"]:
                print(f"        ✗ {k['desc']}")

    fecha = datetime.now()
    carpeta = RAIZ / "evidencias" / "resultados" / f"{fecha:%Y%m%d-%H%M%S}-{entorno.lower()}"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "REPORTE.md").write_text(reporte(resultados, entorno, a.url, fecha), encoding="utf-8")
    (carpeta / "resultados.json").write_text(json.dumps(
        {"entorno": entorno, "url": a.url, "fecha": fecha.isoformat(timespec="seconds"), "resultados": resultados},
        ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(r["ok"] for r in resultados)
    print(f"\n{ok}/{len(resultados)} casos aprobados. Reporte: {carpeta.relative_to(RAIZ) / 'REPORTE.md'}")
    sys.exit(0 if ok == len(resultados) else 1)


if __name__ == "__main__":
    main()
