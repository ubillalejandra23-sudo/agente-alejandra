"""Persistencia: memoria de los agentes (checkpointer de LangGraph) + historial de consultas.

Backend elegido por entorno:
  · DATABASE_URL definida (Heroku Postgres)  -> Postgres      (sobrevive a reinicios y despliegues)
  · PERSISTENCIA=memoria                     -> solo memoria  (pruebas)
  · en otro caso                             -> SQLite local  (data/agentes.db, o DATA_DIR)
Si la base falla al abrir, el servicio sigue en memoria y lo informa (/health -> persistencia).
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

log = logging.getLogger("almacen")

DDL = """CREATE TABLE IF NOT EXISTS consultas (
  solicitud_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, creada_en TEXT NOT NULL, modo TEXT NOT NULL,
  automatico INTEGER NOT NULL, pregunta TEXT NOT NULL, respuesta TEXT NOT NULL, conclusion TEXT,
  agentes TEXT NOT NULL, verificacion TEXT NOT NULL, motivo_ruta TEXT, ms_total INTEGER NOT NULL)"""
DDL_IDX = "CREATE INDEX IF NOT EXISTS idx_consultas_conv ON consultas (conversation_id, creada_en)"
COLS = ("solicitud_id, conversation_id, creada_en, modo, automatico, pregunta, respuesta, conclusion, agentes, "
        "verificacion, motivo_ruta, ms_total")


def _fila_a_dict(f: dict, detalle: bool) -> dict:
    d = {"solicitud_id": f["solicitud_id"], "conversation_id": f["conversation_id"], "creada_en": f["creada_en"],
         "modo": f["modo"], "ms_total": f["ms_total"], "pregunta": f["pregunta"],
         "agentes": json.loads(f["agentes"])}
    if not detalle:
        d["pregunta"] = d["pregunta"][:140]
        d["agentes"] = [a["id"] for a in d["agentes"]]
        return d
    d.update(automatico=bool(f["automatico"]), respuesta=f["respuesta"], conclusion=f["conclusion"],
             verificacion=json.loads(f["verificacion"]), motivo_ruta=f["motivo_ruta"] or "")
    return d


class Almacen:
    def __init__(self):
        self.backend = "memoria"
        self.checkpointer = InMemorySaver()
        self._pool = None          # Postgres
        self._sqlite = None        # sqlite3 (consultas)
        self._aio = None           # aiosqlite (checkpointer)
        self._lock = threading.Lock()
        self._mem: OrderedDict[str, dict] = OrderedDict()

    # ── ciclo de vida ──
    async def abrir(self) -> None:
        url = os.environ.get("DATABASE_URL", "").strip()
        modo = os.environ.get("PERSISTENCIA", "auto").strip().lower()
        if modo == "memoria":
            return
        if url and sys.platform == "win32" and type(asyncio.get_running_loop()).__name__ == "ProactorEventLoop":
            # psycopg async no corre en el bucle por defecto de Windows (Heroku es Linux: sin problema).
            log.warning("Postgres no es compatible con el bucle asyncio por defecto de Windows; se usa SQLite local.")
            url = ""
        try:
            if url:
                await self._abrir_postgres(url)
            else:
                await self._abrir_sqlite(Path(os.environ.get("DATA_DIR") or "data") / "agentes.db")
        except Exception:
            log.exception("No se pudo abrir la base de datos; se usa memoria (se perderá al reiniciar).")
            await self.cerrar()
            self.backend, self.checkpointer = "memoria", InMemorySaver()
        log.info("persistencia: %s", self.backend)

    async def _abrir_postgres(self, url: str) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool
        if url.startswith("postgres://"):  # Heroku entrega el esquema antiguo
            url = "postgresql://" + url[len("postgres://"):]
        self._pool = AsyncConnectionPool(conninfo=url, max_size=5, open=False, check=AsyncConnectionPool.check_connection,
                                         kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        await self._pool.open(wait=True, timeout=10)
        saver = AsyncPostgresSaver(self._pool)
        await saver.setup()
        async with self._pool.connection() as c:
            await c.execute(DDL)
            await c.execute(DDL_IDX)
        self.checkpointer, self.backend = saver, "postgres"

    async def _abrir_sqlite(self, ruta: Path) -> None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite = sqlite3.connect(ruta, check_same_thread=False)
        self._sqlite.row_factory = sqlite3.Row
        self._sqlite.execute(DDL)
        self._sqlite.execute(DDL_IDX)
        self._sqlite.commit()
        self._aio = await aiosqlite.connect(ruta)
        saver = AsyncSqliteSaver(self._aio)
        await saver.setup()
        self.checkpointer, self.backend = saver, "sqlite"

    async def cerrar(self) -> None:
        for cerrar in (self._pool and self._pool.close(), self._aio and self._aio.close()):
            if cerrar:
                try:
                    await cerrar
                except Exception:
                    log.exception("error al cerrar la base")
        if self._sqlite:
            self._sqlite.close()
        self._pool = self._aio = self._sqlite = None

    # ── historial de consultas ──
    async def guardar_consulta(self, res: dict, pregunta: str) -> None:
        fila = (res["solicitud_id"], res["conversation_id"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                res["modo"], int(res["automatico"]), pregunta, res["respuesta"], res["conclusion"],
                json.dumps(res["agentes"], ensure_ascii=False), json.dumps(res["verificacion"], ensure_ascii=False),
                res["motivo_ruta"], res["ms_total"])
        if self.backend == "postgres":
            async with self._pool.connection() as c:
                await c.execute(f"INSERT INTO consultas ({COLS}) VALUES ({','.join(['%s'] * 12)}) ON CONFLICT DO NOTHING", fila)
        elif self.backend == "sqlite":
            def _ins():
                with self._lock:
                    self._sqlite.execute(f"INSERT OR IGNORE INTO consultas ({COLS}) VALUES ({','.join('?' * 12)})", fila)
                    self._sqlite.commit()
            await asyncio.to_thread(_ins)
        else:
            self._mem[fila[0]] = dict(zip(COLS.replace("\n", "").replace(" ", "").split(","), fila))
            while len(self._mem) > 200:
                self._mem.popitem(last=False)

    async def _filas(self, sql_pg: str, sql_lite: str, params: tuple) -> list[dict]:
        if self.backend == "postgres":
            async with self._pool.connection() as c:
                cur = await c.execute(sql_pg, params)
                return list(await cur.fetchall())
        if self.backend == "sqlite":
            def _sel():
                with self._lock:
                    return [dict(f) for f in self._sqlite.execute(sql_lite, params).fetchall()]
            return await asyncio.to_thread(_sel)
        return []

    async def listar(self, conversation_id: str | None = None, limite: int = 30, detalle: bool = False) -> list[dict]:
        limite = max(1, min(limite, 200))
        if self.backend == "memoria":
            filas = [f for f in reversed(self._mem.values()) if not conversation_id or f["conversation_id"] == conversation_id]
            filas = filas[:limite]
        else:
            donde, p = ("WHERE conversation_id = {}", (conversation_id,)) if conversation_id else ("", ())
            base = f"SELECT {COLS} FROM consultas {donde} ORDER BY creada_en DESC LIMIT {limite}"
            filas = await self._filas(base.format("%s"), base.format("?"), p)
        out = [_fila_a_dict(f, detalle) for f in filas]
        return list(reversed(out)) if (conversation_id and detalle) else out  # una conversación se lee en orden cronológico

    async def obtener(self, solicitud_id: str) -> dict | None:
        if self.backend == "memoria":
            f = self._mem.get(solicitud_id)
            return _fila_a_dict(f, True) if f else None
        base = f"SELECT {COLS} FROM consultas WHERE solicitud_id = {{}}"
        filas = await self._filas(base.format("%s"), base.format("?"), (solicitud_id,))
        return _fila_a_dict(filas[0], True) if filas else None


almacen = Almacen()
