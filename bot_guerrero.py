"""
GUERRERO — Bot de Telegram (aiogram).

Recuperado de un .docx que se había sobrescrito por accidente (el usuario
puso el código del panel encima del código del bot en su repo — el archivo
local se perdió, pero seguía vivo en GitHub 2 meses atrás y en el propio
Render, que nunca recibió el deploy malo).

Cambios respecto al original recuperado:
  · Ya NO crea su propia app FastAPI (eso lo hace main.py)
  · Folio, PDF, QR y coordenadas ahora vienen de config_guerrero (antes el
    bot tenía su PROPIA copia de todo eso, con su PROPIO watermark de folio
    "GUE" mientras el panel usaba "GRO_FOLIO" — dos contadores corriendo en
    paralelo que tarde o temprano iban a repetir un folio)
  · ⚠️ El PDF ahora se genera con el candado compartido (antes ninguno tenía)
  · Se agregó snapshot_timers() para que el panel web pueda listarlos
  · El lifespan se partió en arranque_bot() / cierre_bot()
  · /consulta/{folio} (la página bonita con Bootstrap) se movió a una función
    reutilizable — main.py la expone como ruta de nivel superior
Los handlers, el FSM y los mensajes quedaron exactamente igual.
"""

from datetime import datetime, timedelta
import asyncio
import aiohttp

from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import (FSInputFile, ContentType, InlineKeyboardMarkup,
                           InlineKeyboardButton, CallbackQuery)

from config_guerrero import (
    supabase, logger, BOT_TOKEN, BASE_URL, TZ_MX, now_mx, ENTIDAD,
    COSTO_FIJO, RFC_FIJO, DOMICILIO_FIJO, DIAS_PERMISO, HORAS_TIMER_BOT,
    generar_folio_guerrero, generar_subir_y_guardar_pdf, leer_siguiente_folio,
)

_bot_session = AiohttpSession(timeout=aiohttp.ClientTimeout(total=300))
bot     = Bot(token=BOT_TOKEN, session=_bot_session)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

timers_activos       = {}
user_folios          = {}
pending_comprobantes = {}
TOTAL_MINUTOS_TIMER  = HORAS_TIMER_BOT * 60

_folio_lock = asyncio.Lock()


async def _generar_folio_guerrero() -> str:
    """Usa el generador compartido (mismo candado que el panel)."""
    async with _folio_lock:
        return await asyncio.to_thread(generar_folio_guerrero)


# ------------ TIMERS ------------
timers_activos       = {}
user_folios          = {}
pending_comprobantes = {}
TOTAL_MINUTOS_TIMER  = 36 * 60

async def eliminar_folio_automatico(folio: str):
    try:
        uid = timers_activos[folio]["user_id"] if folio in timers_activos else None
        await asyncio.to_thread(lambda:
            supabase.table("folios_registrados").delete().eq("folio", folio).execute()
        )
        if uid:
            await bot.send_message(uid,
                f"⏰ TIEMPO AGOTADO - GUERRERO\n\nEl folio {folio} fue eliminado por no "
                f"completar el pago en 36 horas.\n\n📋 Use /banamex para generar otro permiso.")
        limpiar_timer_folio(folio)
    except Exception as e:
        print(f"Error eliminando folio {folio}: {e}")

async def enviar_recordatorio(folio: str, mins: int):
    try:
        if folio not in timers_activos: return
        uid = timers_activos[folio]["user_id"]
        await bot.send_message(uid,
            f"⚡ RECORDATORIO - GUERRERO\nFolio: {folio}\n"
            f"Tiempo restante: {mins} minutos\nMonto: ${COSTO_FIJO}\n\n"
            f"📸 Envía tu comprobante (foto).\n\n📋 /banamex para nuevo permiso")
    except Exception as e:
        print(f"Error recordatorio {folio}: {e}")

async def iniciar_timer_eliminacion(user_id: int, folio: str):
    async def _task():
        await asyncio.sleep(34.5 * 3600)
        for mins, sleep in [(90, 1800), (60, 1800), (30, 1200), (10, 600)]:
            if folio not in timers_activos: return
            await enviar_recordatorio(folio, mins)
            await asyncio.sleep(sleep)
        if folio in timers_activos:
            await eliminar_folio_automatico(folio)
    task = asyncio.create_task(_task())
    timers_activos[folio] = {"task": task, "user_id": user_id, "start_time": datetime.now()}
    user_folios.setdefault(user_id, []).append(folio)
    print(f"[TIMER] Iniciado folio {folio}")

def cancelar_timer_folio(folio: str):
    if folio not in timers_activos: return
    timers_activos[folio]["task"].cancel()
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]

def limpiar_timer_folio(folio: str):
    if folio not in timers_activos: return
    uid = timers_activos[folio]["user_id"]
    del timers_activos[folio]
    if uid in user_folios and folio in user_folios[uid]:
        user_folios[uid].remove(folio)
        if not user_folios[uid]: del user_folios[uid]

def obtener_folios_usuario(uid: int) -> list:
    return user_folios.get(uid, [])


def snapshot_timers() -> list:
    """Lista los timers activos para que el PANEL WEB pueda mostrarlos."""
    salida = []
    for folio, info in timers_activos.items():
        mins = max(0, TOTAL_MINUTOS_TIMER - int(
            (datetime.now() - info["start_time"]).total_seconds() / 60))
        salida.append({
            "folio":    folio,
            "user_id":  info.get("user_id"),
            "restante": f"{mins//60}h {mins%60}min",
            "minutos":  mins,
        })
    salida.sort(key=lambda x: x["minutos"])
    return salida


# ------------ FSM ------------
class PermisoForm(StatesGroup):
    marca  = State()
    linea  = State()
    anio   = State()
    serie  = State()
    motor  = State()
    color  = State()
    nombre = State()

def _sb_insertar(datos: dict, user_id: int, username: str):
    hoy = datos["fecha_exp_obj"]; ven = datos["fecha_ven_obj"]
    supabase.table("folios_registrados").insert({
        "folio":             datos["folio"],
        "marca":             datos["marca"],
        "linea":             datos["linea"],
        "anio":              datos["anio"],
        "numero_serie":      datos["serie"],
        "numero_motor":      datos["motor"],
        "color":             datos["color"],
        "nombre":            datos["nombre"],
        "rfc":               datos["rfc"],
        "domicilio":         datos["domicilio"],
        "costo":             datos["costo"],
        "fecha_expedicion":  hoy.date().isoformat(),
        "fecha_vencimiento": ven.date().isoformat(),
        "entidad":           ENTIDAD,
        "estado":            "PENDIENTE",
        "user_id":           user_id,
        "username":          username or "Sin username",
    }).execute()

# ------------ BACKGROUND TASK ------------------------------------------------
async def _generar_y_enviar_background(chat_id: int, datos: dict, user_id: int, username: str):
    folio = datos["folio"]
    try:
        pdf, _url = await asyncio.to_thread(generar_subir_y_guardar_pdf, datos)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔑 Validar Admin",  callback_data=f"validar_{folio}"),
            InlineKeyboardButton(text="⏹️ Detener Timer", callback_data=f"detener_{folio}")
        ]])

        if pdf:
            await bot.send_document(
                chat_id, FSInputFile(pdf),
                caption=(
                    f"📋 PERMISO DE CIRCULACIÓN - GUERRERO\n"
                    f"Folio: {folio}\n"
                    f"Titular: {datos['nombre']}\n"
                    f"Vigencia: 30 días | Costo: ${COSTO_FIJO}\n\n"
                    f"✅ Permiso + Recibo (PDF unificado)\n\n"
                    f"⏰ TIMER ACTIVO - 36 horas"
                ),
                reply_markup=kb
            )
        else:
            await bot.send_message(user_id,
                "❌ Error al generar PDF. Intenta de nuevo con /banamex")
            return

        await asyncio.to_thread(_sb_insertar, datos, user_id, username)
        await iniciar_timer_eliminacion(user_id, folio)

        await bot.send_message(user_id,
            f"💰 INSTRUCCIONES DE PAGO\n\n"
            f"📄 Folio: {folio}\n💵 Monto: ${COSTO_FIJO}\n⏰ Límite: 36 horas\n\n"
            f"🏦 TRANSFERENCIA:\n• Titular: GUILLERMO S.R.J\n"
            f"• Número: 7289690000484424454\n\n"
            f"🏪 OXXO:\n• Referencia: 2242170180214090\n• Monto: ${COSTO_FIJO}\n\n"
            f"📸 Envía foto del comprobante para validar.\n\n📋 /banamex para nuevo permiso")

    except Exception as e:
        print(f"[ERROR background] folio {folio}: {e}")
        try:
            await bot.send_message(user_id,
                f"❌ Error al generar el documento: {e}\n\nUse /banamex para reintentar.")
        except Exception:
            pass

# ============================================================
#  HANDLERS
# ============================================================

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏛️ SISTEMA DIGITAL DEL ESTADO DE GUERRERO\n\n"
        f"💰 Costo fijo: ${COSTO_FIJO}\n"
        "⏰ Tiempo límite de pago: 36 horas\n\n"
        "📋 Use /banamex para generar un permiso."
    )

@dp.message(Command("banamex"))
async def banamex_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    folios_activos = obtener_folios_usuario(message.from_user.id)

    if folios_activos:
        lineas = []
        for f in folios_activos:
            if f in timers_activos:
                seg  = max(0, int(TOTAL_MINUTOS_TIMER * 60 -
                                  (datetime.now() - timers_activos[f]["start_time"]).total_seconds()))
                h, m = divmod(seg // 60, 60)
                lineas.append(f"• {f}  ({h}h {m}min restantes)")
            else:
                lineas.append(f"• {f}  (sin timer)")
        botones = [
            [InlineKeyboardButton(text=f"⏹️ Detener {f}", callback_data=f"detener_{f}")]
            for f in folios_activos
        ]
        await message.answer(
            f"📋 FOLIOS GUERRERO ACTIVOS ({len(folios_activos)}):\n\n" +
            "\n".join(lineas) + "\n\nPuedes detener el timer de cualquier folio:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=botones)
        )

    await message.answer(
        f"🚗 NUEVO PERMISO - GUERRERO\n\n"
        f"💰 Costo: ${COSTO_FIJO} (fijo)\n"
        f"⏰ Plazo de pago: 36 horas\n\n"
        f"Paso 1: MARCA del vehículo:"
    )
    await state.set_state(PermisoForm.marca)

@dp.message(PermisoForm.marca)
async def get_marca(message: types.Message, state: FSMContext):
    await state.update_data(marca=message.text.strip().upper())
    await message.answer("LÍNEA/MODELO:")
    await state.set_state(PermisoForm.linea)

@dp.message(PermisoForm.linea)
async def get_linea(message: types.Message, state: FSMContext):
    await state.update_data(linea=message.text.strip().upper())
    await message.answer("AÑO (4 dígitos):")
    await state.set_state(PermisoForm.anio)

@dp.message(PermisoForm.anio)
async def get_anio(message: types.Message, state: FSMContext):
    a = message.text.strip()
    if not a.isdigit() or len(a) != 4:
        await message.answer("⚠️ Año inválido. Ej: 2021"); return
    await state.update_data(anio=a)
    await message.answer("NÚMERO DE SERIE / NIV:")
    await state.set_state(PermisoForm.serie)

@dp.message(PermisoForm.serie)
async def get_serie(message: types.Message, state: FSMContext):
    await state.update_data(serie=message.text.strip().upper())
    await message.answer("NÚMERO DE MOTOR:")
    await state.set_state(PermisoForm.motor)

@dp.message(PermisoForm.motor)
async def get_motor(message: types.Message, state: FSMContext):
    await state.update_data(motor=message.text.strip().upper())
    await message.answer("COLOR:")
    await state.set_state(PermisoForm.color)

@dp.message(PermisoForm.color)
async def get_color(message: types.Message, state: FSMContext):
    await state.update_data(color=message.text.strip().upper())
    await message.answer("NOMBRE COMPLETO del propietario:")
    await state.set_state(PermisoForm.nombre)

@dp.message(PermisoForm.nombre)
async def get_nombre(message: types.Message, state: FSMContext):
    datos  = await state.get_data()
    nombre = message.text.strip().upper()

    folio = await _generar_folio_guerrero()
    hoy   = datetime.now()
    ven   = hoy + timedelta(days=30)

    datos_pdf = {
        "folio":         folio,
        "marca":         datos["marca"],
        "linea":         datos["linea"],
        "anio":          datos["anio"],
        "serie":         datos["serie"],
        "motor":         datos["motor"],
        "color":         datos["color"],
        "nombre":        nombre,
        "costo":         COSTO_FIJO,
        "rfc":           RFC_FIJO,
        "domicilio":     DOMICILIO_FIJO,
        "fecha_exp":     hoy.strftime("%d/%m/%Y"),
        "fecha_ven":     ven.strftime("%d/%m/%Y"),
        "fecha_exp_obj": hoy,
        "fecha_ven_obj": ven,
    }

    # state.clear() ANTES del create_task - evita re-triggers
    await state.clear()

    await message.answer(
        f"🔄 Generando...\n<b>Folio:</b> {folio}\n<b>Titular:</b> {nombre}",
        parse_mode="HTML"
    )

    asyncio.create_task(
        _generar_y_enviar_background(
            message.chat.id, datos_pdf,
            message.from_user.id,
            message.from_user.username or "Sin username"
        )
    )

# ------------ CALLBACKS ------------
@dp.callback_query(lambda c: c.data and c.data.startswith("validar_"))
async def cb_validar(callback: CallbackQuery):
    folio = callback.data.replace("validar_", "")
    if not folio.startswith("Z"):
        await callback.answer("❌ Folio inválido", show_alert=True); return
    if folio in timers_activos:
        uid = timers_activos[folio]["user_id"]
        cancelar_timer_folio(folio)
        try:
            await asyncio.to_thread(lambda:
                supabase.table("folios_registrados").update(
                    {"estado": "VALIDADO_ADMIN", "fecha_comprobante": datetime.now().isoformat()}
                ).eq("folio", folio).execute()
            )
        except Exception as e:
            print(f"Error BD validar {folio}: {e}")
        await callback.answer("✅ Validado", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        try:
            await bot.send_message(uid,
                f"✅ PAGO VALIDADO - GUERRERO\nFolio: {folio}\n"
                f"Tu permiso está activo.\n\n📋 /banamex para nuevo permiso")
        except Exception: pass
    else:
        await callback.answer("❌ Timer no activo", show_alert=True)

@dp.callback_query(lambda c: c.data and c.data.startswith("detener_"))
async def cb_detener(callback: CallbackQuery):
    folio = callback.data.replace("detener_", "")
    if folio in timers_activos:
        cancelar_timer_folio(folio)
        try:
            await asyncio.to_thread(lambda:
                supabase.table("folios_registrados").update(
                    {"estado": "TIMER_DETENIDO", "fecha_detencion": datetime.now().isoformat()}
                ).eq("folio", folio).execute()
            )
        except Exception as e:
            print(f"Error BD detener {folio}: {e}")
        await callback.answer("⏹️ Timer detenido", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"⏹️ Timer detenido\nFolio: {folio}\n\n📋 /banamex")
    else:
        await callback.answer("❌ Timer ya no activo", show_alert=True)

@dp.message(lambda m: m.text and m.text.upper().startswith("SERO") and len(m.text) > 4)
async def cmd_sero(message: types.Message):
    fa = message.text.upper()[4:].strip()
    if not fa.startswith("Z"):
        await message.answer(f"❌ Folio {fa} no es GUERRERO (debe empezar con Z)"); return
    if fa in timers_activos:
        uid = timers_activos[fa]["user_id"]
        cancelar_timer_folio(fa)
        try:
            await asyncio.to_thread(lambda:
                supabase.table("folios_registrados").update(
                    {"estado": "VALIDADO_ADMIN", "fecha_comprobante": datetime.now().isoformat()}
                ).eq("folio", fa).execute()
            )
        except Exception: pass
        await message.answer(f"✅ VALIDADO\nFolio: {fa}")
        try:
            await bot.send_message(uid,
                f"✅ PAGO VALIDADO - GUERRERO\nFolio: {fa}\n"
                f"Tu permiso está activo.\n\n📋 /banamex para nuevo permiso")
        except Exception: pass
    else:
        await message.answer(f"❌ Folio {fa} no encontrado en timers activos")

@dp.message(lambda m: m.content_type == ContentType.PHOTO)
async def recibir_comprobante(message: types.Message):
    uid = message.from_user.id
    fl  = obtener_folios_usuario(uid)
    if not fl:
        await message.answer("ℹ️ No hay trámites pendientes.\n\n📋 /banamex"); return
    if len(fl) > 1:
        lista = "\n".join(f"• {f}" for f in fl)
        pending_comprobantes[uid] = "waiting_folio"
        await message.answer(
            f"📄 Varios folios activos:\n\n{lista}\n\n"
            f"Responde con el FOLIO al que corresponde este comprobante.\n\n📋 /banamex")
        return
    folio = fl[0]; cancelar_timer_folio(folio)
    try:
        await asyncio.to_thread(lambda:
            supabase.table("folios_registrados").update(
                {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": datetime.now().isoformat()}
            ).eq("folio", folio).execute()
        )
    except Exception: pass
    await message.answer(
        f"✅ Comprobante recibido.\n📄 Folio: {folio}\n⏹️ Timer detenido.\n\n📋 /banamex")

@dp.message(lambda m: m.from_user.id in pending_comprobantes
            and pending_comprobantes[m.from_user.id] == "waiting_folio")
async def especificar_folio(message: types.Message):
    uid = message.from_user.id
    fe  = message.text.strip().upper()
    fl  = obtener_folios_usuario(uid)
    if fe not in fl:
        await message.answer("❌ Folio no encontrado en tu lista.\n\n📋 /banamex"); return
    cancelar_timer_folio(fe); del pending_comprobantes[uid]
    try:
        await asyncio.to_thread(lambda:
            supabase.table("folios_registrados").update(
                {"estado": "COMPROBANTE_ENVIADO", "fecha_comprobante": datetime.now().isoformat()}
            ).eq("folio", fe).execute()
        )
    except Exception: pass
    await message.answer(f"✅ Comprobante asociado.\n📄 Folio: {fe}\n\n📋 /banamex")

@dp.message(Command("folios"))
async def ver_folios(message: types.Message):
    fl = obtener_folios_usuario(message.from_user.id)
    if not fl:
        await message.answer("ℹ️ No hay folios activos.\n\n📋 /banamex"); return
    rows    = []
    botones = []
    for f in fl:
        if f in timers_activos:
            seg  = max(0, int(TOTAL_MINUTOS_TIMER * 60 -
                               (datetime.now()-timers_activos[f]["start_time"]).total_seconds()))
            h, m = divmod(seg // 60, 60)
            rows.append(f"• {f} ({h}h {m}min)")
        else:
            rows.append(f"• {f} (sin timer)")
        botones.append([InlineKeyboardButton(
            text=f"⏹️ Detener {f}", callback_data=f"detener_{f}"
        )])
    await message.answer(
        f"📋 FOLIOS GUERRERO ACTIVOS ({len(fl)})\n\n" +
        "\n".join(rows) + "\n\n📋 /banamex",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=botones)
    )

@dp.message(lambda m: m.text and any(p in m.text.lower() for p in
    ["costo","precio","cuanto","cuánto","deposito","depósito","pago","valor","monto"]))
async def info_costo(message: types.Message):
    await message.answer(f"💰 Costo fijo del permiso: ${COSTO_FIJO}\n\n📋 Use /banamex")

@dp.message()
async def fallback(message: types.Message):
    await message.answer("🏛️ Sistema Digital Guerrero.")

# ── ARRANQUE / CIERRE (los llama main.py) ─────────────────────────────────────
_keep_task = None


async def _keep_alive():
    while True:
        await asyncio.sleep(600)
        print(f"[HEARTBEAT] Guerrero activo — timers: {len(timers_activos)}")


async def arranque_bot():
    global _keep_task
    if not BOT_TOKEN:
        print("[BOT] Sin BOT_TOKEN — el bot queda inactivo")
        return
    await bot.delete_webhook(drop_pending_updates=True)
    if BASE_URL:
        wh = f"{BASE_URL}/webhook"
        await bot.set_webhook(wh, allowed_updates=["message", "callback_query"])
        print(f"[WEBHOOK] {wh}")
    else:
        print("[BOT] Sin BASE_URL — no se registró webhook")
    _keep_task = asyncio.create_task(_keep_alive())


async def cierre_bot():
    global _keep_task
    if _keep_task:
        _keep_task.cancel()
        try:
            await _keep_task
        except asyncio.CancelledError:
            pass
        _keep_task = None
    try:
        await bot.session.close()
    except Exception:
        pass


async def procesar_update(data: dict):
    update = types.Update(**data)
    await dp.feed_webhook_update(bot, update)


async def render_consulta_publica(folio: str) -> HTMLResponse:
    """La página bonita de verificación pública (antes vivía como ruta propia del bot)."""
    CSS = "https://direcciontransitotlapadecomonfort.gob.mx/css/bootstrap.min.css"
    JQ = "https://direcciontransitotlapadecomonfort.gob.mx/js/jquery-3.4.1.min.js"
    BSJS = "https://direcciontransitotlapadecomonfort.gob.mx/js/bootstrap.min.js"
    LOGO = "https://direcciontransitotlapadecomonfort.gob.mx/img/transformando_guerrero.jpg"
    FAV = "https://direcciontransitotlapadecomonfort.gob.mx/img/favicon-96x96.png?v2"

    STYLE = """
<style>
  #ssp{width:300px;margin:0 auto;border-radius:5px}
  #ltm{color:#636b6f;font-family:'Nunito',sans-serif;font-weight:200;font-size:50px;text-align:center}
  body{background:url(https://direcciontransitotlapadecomonfort.gob.mx/img/intersection.png)}
  .etiqueta{margin:0;padding:3px 10px 3px 0;text-align:right;font-weight:bold}
  .valor{margin:0;padding:3px 0;text-align:left}
  #logo-y{width:110px;margin:-15px auto 0;border-radius:5px}
  #logo-x{width:70px;margin:-15px auto 0;border-radius:5px}
</style>"""

    def wrap(body):
        return f"""<!doctype html><html lang="es"><head>
<meta charset="UTF-8"><title>LTM | Licencias de Tránsito Municipal</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<link rel="icon" href="{FAV}">
<link href=https://fonts.googleapis.com/css?family=Nunito:200,600 rel="stylesheet">
<link href="{CSS}" rel="stylesheet">{STYLE}</head><body>
<div class="container">
<div class="col-md-8 col-md-offset-2 col-sm-10 col-sm-offset-1" style="padding:0;margin-top:3em">
<div class="row" style="margin-bottom:1em">
  <div class="col-md-12"><img id="ssp" src="{LOGO}" class="img-responsive" alt=""></div>
</div>
{body}
<div class="col-md-12 text-center">
  <div class="col-md-6 col-xs-6" style="text-align:right">
    <img id="logo-y" src="{LOGO}" class="img-responsive">
  </div>
  <div class="col-md-6 col-xs-6" style="text-align:left">
    <img id="logo-x" src="{FAV}" class="img-responsive">
  </div>
</div>
<div class="clearfix"></div>
<div id="ltm" onclick="window.location.assign('https://direcciontransitotlapadecomonfort.gob.mx');"
     style="cursor:pointer">LTM | Tlapa de Comonfort Gro.</div>
</div></div>
<script src="{JQ}"></script><script src="{BSJS}"></script>
</body></html>"""

    try:
        r = await asyncio.to_thread(lambda:
            supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        )
    except Exception as e:
        return HTMLResponse(wrap(f'<div class="alert alert-danger">Error DB: {e}</div>'), 500)

    if not r.data:
        return HTMLResponse(wrap(f"""
<div class="panel panel-danger">
  <div class="panel-heading"><div class="panel-title">Verificador de Permiso</div></div>
  <div class="panel-body" style="padding:20px 10px">
    <div class="alert alert-danger">
      el folio <strong>{folio}</strong> no fue encontrado en el sistema.
    </div>
  </div>
  <div class="panel-footer"><div class="text-center">Permiso de Tr&aacute;nsito Municipal</div></div>
</div>"""), 404)

    d = r.data[0]

    def fd(s):
        try: return datetime.strptime(s, "%Y-%m-%d").strftime("%d-%m-%Y")
        except: return s or "N/D"

    def row(lbl, val):
        return (f'<div class="row">'
                f'<div class="col-xs-5 etiqueta">{lbl}:</div>'
                f'<div class="col-xs-7 valor">{val}</div></div>')

    panel = f"""
<div class="panel panel-primary">
  <div class="panel-heading"><div class="panel-title">Verificador de Permiso</div></div>
  <div style="padding:20px 10px" class="panel-body">
    <div class="col-lg-12 text-center" style="padding:0;margin:0">
      <div class="row">
        <div class="col-xs-12" style="font-size:13px;margin-left:-25px">
          {row("Marca",                   d.get("marca","N/D"))}
          {row("L&iacute;nea",            d.get("linea","N/D"))}
          {row("Modelo",                  d.get("anio","N/D"))}
          {row("Color",                   d.get("color","N/D"))}
          {row("N&uacute;m. Serie",       d.get("numero_serie","N/D"))}
          {row("N&uacute;m. Motor",       d.get("numero_motor","N/D"))}
          {row("CONTRIBUYENTE",           d.get("nombre","N/D"))}
          {row("Fecha Expedici&oacute;n", fd(d.get("fecha_expedicion","")))}
          {row("Fecha Vencimiento",        fd(d.get("fecha_vencimiento","")))}
        </div>
      </div>
    </div>
  </div>
  <div class="panel-footer">
    <div class="text-center">Permiso de Tr&aacute;nsito Municipal</div>
  </div>
</div>"""

    return HTMLResponse(wrap(panel))

