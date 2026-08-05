"""
GUERRERO — FUSIÓN: panel web (Flask) + bot de Telegram (aiogram) en UN servicio.

Antes eran dos servicios de Render ($14/mes). Ahora uno solo ($7/mes).

El bot se había perdido (sobrescrito por accidente) y se recuperó desde el
historial de GitHub. Al comparar con el panel se encontraron los mismos
problemas de siempre: cada uno tenía su PROPIA copia de las coordenadas del
PDF, del generador de PDF y del generador de folio — y ESTE ÚLTIMO con
watermarks de Supabase DISTINTOS ("GRO_FOLIO" en el panel, "GUE" en el bot),
o sea dos contadores corriendo en paralelo. Todo eso ya quedó unificado en
config_guerrero.py.

  · FastAPI atiende /webhook (async) → Dispatcher de aiogram
  · FastAPI también expone /consulta/{folio} — la página pública bonita con
    Bootstrap que ya traía el bot; se queda ahí (gana sobre cualquier ruta
    Flask con el mismo path, por cómo se resuelven las rutas al fusionar)
  · TODO lo demás se lo pasa a Flask con WSGIMiddleware, sin tocar rutas

Start command en Render:
    gunicorn main:app -k uvicorn.workers.UvicornWorker -w 1 --timeout 120
"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

try:
    from a2wsgi import WSGIMiddleware
except ImportError:  # pragma: no cover
    from starlette.middleware.wsgi import WSGIMiddleware

import config_guerrero as cfg
import bot_guerrero
import panel_guerrero


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg.logger.info("=" * 60)
    cfg.logger.info("[SISTEMA] GUERRERO FUSIONADO v8.0 — panel + bot en un servicio")
    cfg.logger.info("=" * 60)

    try:
        await bot_guerrero.arranque_bot()
    except Exception as e:
        cfg.logger.error(f"[ARRANQUE BOT] {e}")

    try:
        panel_guerrero.iniciar_scheduler()
    except Exception as e:
        cfg.logger.error(f"[ARRANQUE SCHEDULER] {e}")

    try:
        cfg.logger.info(f"[SISTEMA] Siguiente folio: {cfg.leer_siguiente_folio()}")
    except Exception:
        pass

    cfg.logger.info("[SISTEMA] Panel en /  ·  Webhook en /webhook  ·  Consulta en /consulta/{folio}")
    cfg.logger.info("[SISTEMA] Editor de tablas en /admin/editor")
    cfg.logger.info("[SISTEMA] Ajuste de fechas en /admin/fechas")

    yield

    cfg.logger.info("[CIERRE] Deteniendo servicios...")
    try:
        panel_guerrero.detener_scheduler()
    except Exception:
        pass
    try:
        await bot_guerrero.cierre_bot()
    except Exception:
        pass
    cfg.logger.info("[CIERRE] Listo")


app = FastAPI(
    lifespan=lifespan,
    title="GUERRERO — Panel + Bot",
    version="8.0",
    docs_url=None,
    redoc_url=None,
)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        await bot_guerrero.procesar_update(data)
        return {"ok": True}
    except Exception as e:
        cfg.logger.error(f"[WEBHOOK] {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# La página pública de verificación (Bootstrap, logos oficiales) ya venía
# hecha en el bot como ruta FastAPI — se conserva tal cual. Por cómo se
# resuelven las rutas al fusionar (FastAPI primero, Flask solo si nada más
# respondió), esta SIEMPRE gana sobre cualquier ruta /consulta/<folio> que
# tuviera el panel — es la misma que ya estaba viva en producción.
@app.get("/consulta/{folio}")
async def consulta_publica(folio: str):
    return await bot_guerrero.render_consulta_publica(folio)


# El bot original exponía su propio "/" con un JSON de salud — el panel
# también quiere "/" (redirige a login). Gana el panel; el health se movió aquí.
@app.get("/health")
async def health():
    return {
        "ok":               True,
        "sistema":          "GUERRERO FUSIONADO v8.0",
        "panel":            "Flask montado en /",
        "bot":              "aiogram via /webhook",
        "costo_fijo":       f"${cfg.COSTO_FIJO}",
        "dias_permiso":     cfg.DIAS_PERMISO,
        "horas_timer_bot":  cfg.HORAS_TIMER_BOT,
        "horas_limite_pago": cfg.HORAS_LIMITE_PAGO,
        "timers_activos":   len(bot_guerrero.timers_activos),
        "siguiente_folio":  cfg.leer_siguiente_folio(),
        "editor_tablas":    "/admin/editor",
        "ajuste_fechas":    "/admin/fechas",
    }


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "guerrero-fusionado",
            "time": datetime.now(cfg.TZ_MX).isoformat()}


@app.get("/status")
async def status_detail():
    return {
        "sistema":         "GUERRERO FUSIONADO v8.0",
        "timers_activos":  len(bot_guerrero.timers_activos),
        "folios":          bot_guerrero.snapshot_timers(),
        "siguiente_folio": cfg.leer_siguiente_folio(),
        "timestamp":       datetime.now(cfg.TZ_MX).isoformat(),
    }


# Panel Flask al final: atrapa todo lo que no sea /webhook /consulta/{folio}
# /health /healthz /status
app.mount("/", WSGIMiddleware(panel_guerrero.flask_app))


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
