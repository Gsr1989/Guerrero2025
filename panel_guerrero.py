"""
GUERRERO — Panel web (Flask).

Qué se corrigió respecto a la versión que tronaba al arrancar:
  · La ruta /admin/menu estaba definida DOS veces con el mismo nombre de
    función (admin_menu). Flask lo rechaza con un AssertionError al importar
    el módulo → main.py no podía ni arrancar. Ahora hay UNA sola (en
    editor_guerrero.py).
  · PAGE_SIZE no estaba definido → el editor de tablas daba 500 (ya definido
    en editor_guerrero.py).
  · El editor / fechas / menú se movieron a editor_guerrero.py para que este
    archivo no crezca (se registran solos al final de este archivo).
  · Formularios de 3ros y admin: piden los MISMOS datos que el bot (marca,
    línea, año, serie, motor, color, nombre) y validan igual. Fecha de
    expedición: por defecto HOY, acepta pasadas o futuras.
  · Los 3ros ya NO pueden escoger folio manual (solo el admin).
  · Consulta pública: ahora también muestra COLOR y NOMBRE.
  · VIGENTE/VENCIDO se calcula por FECHA (vale todo el día de vencimiento).
  · Descarga de PDF: si el folio no trae pdf_url (los permisos del bot no lo
    guardaban), se busca el archivo en Supabase Storage.
  · login limpia la sesión (antes un 3ro podía heredar 'admin' si el admin
    no había cerrado sesión en ese navegador).
Las rutas y templates existentes NO cambiaron de nombre.
"""

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, send_from_directory, jsonify)
import os
import re
import threading
import time
from datetime import datetime, timedelta, date
from apscheduler.schedulers.background import BackgroundScheduler

from config_guerrero import (
    supabase, logger, TZ_MX, today_mx,
    OUTPUT_DIR, COSTO_FIJO, RFC_FIJO, DOMICILIO_FIJO, DIAS_PERMISO,
    HORAS_LIMITE_PAGO, BUCKET_NAME,
    ADMIN_USER, ADMIN_PASS, SECRET_KEY,
    generar_folio_guerrero, generar_subir_y_guardar_pdf,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

flask_app = app   # alias que usa main.py para montarlo

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Datos de pago que se muestran al renovar (mismos que manda el bot).
# ⚠️ La cuenta del bot tiene 19 dígitos y una CLABE tiene 18 — revísala.
DATOS_PAGO = {
    "titular": "GUILLERMO S.R.J",
    "cuenta":  "7289690000484424454",
    "oxxo":    "2242170180214090",
}

FOLIO_RE = re.compile(r"^[A-Z0-9]{3,12}$")


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _parse_fecha(valor) -> date:
    """Acepta 'YYYY-MM-DD', ISO con hora/zona, datetime o date. Devuelve date."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor or "").strip()
    if not s:
        raise ValueError("fecha vacía")
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def _leer_datos_vehiculo(form):
    """
    Lee y valida los MISMOS campos que pide el bot:
    marca, línea, año, serie, motor, color y nombre.
    Devuelve (datos, error). Si 'color' ni viene en el formulario (template
    viejo) se pone N/D como antes; si viene vacío, es error.
    """
    marca = (form.get('marca') or '').strip().upper()
    linea = (form.get('linea') or '').strip().upper()
    anio  = (form.get('anio')  or '').strip()
    serie = (form.get('serie') or '').strip().upper()
    motor = (form.get('motor') or '').strip().upper()
    color_raw = form.get('color')
    color = 'N/D' if color_raw is None else color_raw.strip().upper()
    nombre = (form.get('contribuyente') or form.get('nombre') or '').strip().upper()

    faltan = [n for n, v in (("marca", marca), ("línea", linea), ("año", anio),
                             ("serie", serie), ("motor", motor),
                             ("color", color), ("nombre", nombre)) if not v]
    if faltan:
        return None, "Faltan datos: " + ", ".join(faltan) + "."
    if not (anio.isdigit() and len(anio) == 4):
        return None, "Año inválido: usa 4 dígitos (ej. 2021)."

    return {"marca": marca, "linea": linea, "anio": anio, "serie": serie,
            "motor": motor, "color": color, "nombre": nombre}, None


def _leer_fechas(form):
    """
    Fecha de expedición: por defecto HOY. Acepta pasada o futura.
    Devuelve (fecha_exp, fecha_ven, error).
    """
    f_exp_str = (form.get('fecha_expedicion') or '').strip()
    if f_exp_str:
        try:
            exp = datetime.strptime(f_exp_str, "%Y-%m-%d").replace(tzinfo=TZ_MX)
        except ValueError:
            return None, None, "Fecha de expedición inválida."
        if not (2000 <= exp.year <= 2100):
            return None, None, "La fecha debe estar entre los años 2000 y 2100."
    else:
        exp = datetime.now(TZ_MX)

    try:
        vigencia = int(form.get('vigencia') or DIAS_PERMISO)
    except ValueError:
        return None, None, "Vigencia inválida."
    if not (1 <= vigencia <= 3650):
        return None, None, "La vigencia debe ser de 1 a 3650 días."

    return exp, exp + timedelta(days=vigencia), None


def _crear_folio(datos, fecha_exp, fecha_ven, extra, folio_manual=""):
    """Registra el folio en Supabase. Devuelve (ok, folio, error)."""
    payload_base = {
        "marca":             datos["marca"],
        "linea":             datos["linea"],
        "anio":              datos["anio"],
        "numero_serie":      datos["serie"],
        "numero_motor":      datos["motor"],
        "color":             datos["color"],
        "nombre":            datos["nombre"],
        "fecha_expedicion":  fecha_exp.isoformat(),
        "fecha_vencimiento": fecha_ven.isoformat(),
        "entidad":           "Guerrero",
        **extra,
    }

    if folio_manual:
        existente = supabase.table("folios_registrados").select("folio") \
            .eq("folio", folio_manual).execute()
        if existente.data:
            return False, "", "El folio ya existe."
        payload_base["folio"] = folio_manual
        try:
            supabase.table("folios_registrados").insert(payload_base).execute()
            return True, folio_manual, ""
        except Exception as e:
            return False, "", f"Error al registrar: {e}"

    ok, folio = guardar_folio_con_reintento(payload_base)
    if not ok:
        return False, "", "Error al generar folio. Intenta de nuevo."
    return True, folio, ""


def _datos_pdf(folio, datos, fecha_exp, fecha_ven) -> dict:
    return {
        "folio":     folio,
        "marca":     datos["marca"],
        "linea":     datos["linea"],
        "anio":      datos["anio"],
        "serie":     datos["serie"],
        "motor":     datos["motor"],
        "color":     datos["color"],
        "nombre":    datos["nombre"],
        "costo":     COSTO_FIJO,
        "rfc":       RFC_FIJO,
        "domicilio": DOMICILIO_FIJO,
        "fecha_exp": fecha_exp.strftime("%d/%m/%Y"),
        "fecha_ven": fecha_ven.strftime("%d/%m/%Y"),
    }


def guardar_folio_con_reintento(payload_base: dict, max_intentos=10) -> tuple:
    """Usa el generador COMPARTIDO (mismo candado y watermark que el bot)."""
    for intento in range(max_intentos):
        try:
            folio = generar_folio_guerrero()
        except Exception as e:
            print(f"[ERROR] generando folio: {e}")
            return False, ""
        payload = {**payload_base, "folio": folio}
        try:
            supabase.table("folios_registrados").insert(payload).execute()
            return True, folio
        except Exception as e:
            em = str(e).lower()
            if "duplicate" in em or "unique" in em or "23505" in em:
                print(f"[DUPLICADO] {folio} ya existe, reintentando ({intento+1})")
                continue
            print(f"[ERROR BD] {e}")
            return False, ""
    return False, ""


def _buscar_pdf_storage(folio: str) -> str:
    """
    Los permisos del bot suben el PDF a Storage pero nunca guardaban pdf_url
    en la tabla. Si existe el archivo en el bucket, devuelve su URL pública
    y la deja guardada para la próxima vez.
    """
    try:
        nombre = f"{folio}.pdf"
        archivos = supabase.storage.from_(BUCKET_NAME).list("", {"search": nombre}) or []
        if any(a.get("name") == nombre for a in archivos):
            url = supabase.storage.from_(BUCKET_NAME).get_public_url(nombre)
            try:
                supabase.table("folios_registrados").update({"pdf_url": url}) \
                    .eq("folio", folio).execute()
            except Exception as e:
                print(f"[WARN] No se pudo guardar pdf_url de {folio}: {e}")
            return url
    except Exception as e:
        print(f"[WARN] Buscando {folio}.pdf en Storage: {e}")
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# LIMPIEZA 48H
# ═══════════════════════════════════════════════════════════════════════════

def limpiar_folios_no_pagados():
    try:
        limite = (datetime.now(TZ_MX) - timedelta(hours=HORAS_LIMITE_PAGO)).isoformat()
        vencidos = supabase.table("folios_registrados") \
            .select("folio") \
            .eq("estado_pago", "PENDIENTE_PAGO") \
            .lt("fecha_expedicion", limite) \
            .execute()

        for row in (vencidos.data or []):
            folio = row["folio"]
            supabase.table("folios_registrados").delete().eq("folio", folio).execute()
            ruta = f"{OUTPUT_DIR}/{folio}.pdf"
            if os.path.exists(ruta):
                os.remove(ruta)
            try:
                supabase.storage.from_(BUCKET_NAME).remove([f"{folio}.pdf"])
            except Exception as e:
                print(f"[WARN] No se pudo borrar {folio}.pdf de Storage: {e}")
            print(f"[LIMPIEZA 48H] Folio {folio} eliminado por falta de pago")

    except Exception as e:
        print(f"[ERROR limpieza 48h] {e}")


_scheduler = None


def iniciar_scheduler():
    """Antes arrancaba al importar el módulo. Ahora lo llama main.py en el lifespan."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="America/Mexico_City")
    _scheduler.add_job(limpiar_folios_no_pagados, 'interval', minutes=15)
    _scheduler.start()
    print("[SCHEDULER] Limpieza 48h activa")
    return _scheduler


def detener_scheduler():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


# ─── TIMER ───────────────────────────────────────────────────────────────────

def get_timer_info(usuario):
    if usuario.get("pagado"):
        return None
    creado_en = usuario.get("created_at")
    if not creado_en:
        return None
    try:
        if isinstance(creado_en, str):
            creado_en = datetime.fromisoformat(
                creado_en.replace("Z", "+00:00")).replace(tzinfo=None)
        limite   = creado_en + timedelta(hours=2)
        restante = (limite - datetime.utcnow()).total_seconds()
        return {
            "limite_iso":         limite.isoformat(),
            "segundos_restantes": max(0, int(restante)),
            "vencido":            restante <= 0
        }
    except Exception as e:
        print(f"Error timer: {e}")
        return None


# ─── RUTAS GENERALES ─────────────────────────────────────────────────────────

@app.route('/')
def inicio():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username == ADMIN_USER and password == ADMIN_PASS:
            session.clear()
            session['admin'] = True
            return redirect(url_for('admin'))

        response = supabase.table("verificaciondigitalcdmx") \
            .select("*").eq("username", username).eq("password", password).execute()
        if response.data:
            usuario = response.data[0]
            session.clear()
            session['user_id']  = usuario['id']
            session['username'] = usuario['username']
            return redirect(url_for('registro_usuario'))

        return render_template('bloqueado.html')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─── ADMIN ───────────────────────────────────────────────────────────────────

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')


@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios   = int(request.form['folios'])
        existe   = supabase.table("verificaciondigitalcdmx") \
            .select("id").eq("username", username).execute()
        if existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            return render_template('crear_usuario.html')
        supabase.table("verificaciondigitalcdmx").insert({
            "username":       username,
            "password":       password,
            "folios_asignac": folios,
            "folios_usados":  0,
            "pagado":         False
        }).execute()
        flash('Usuario creado exitosamente.', 'success')
    return render_template('crear_usuario.html')


@app.route('/admin/test_fechas', methods=['GET', 'POST'])
def admin_test_fechas():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        accion = request.form.get('accion')
        folio  = request.form.get('folio', '').strip().upper()

        if not folio:
            flash("Escribe un folio.", "error")
            return redirect(url_for('admin_test_fechas'))

        resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if not resp.data:
            flash(f"Folio {folio} no encontrado.", "error")
            return redirect(url_for('admin_test_fechas'))

        if accion == 'vencer_permiso':
            nueva_ven = datetime.now(TZ_MX) - timedelta(days=1)
            supabase.table("folios_registrados") \
                .update({"fecha_vencimiento": nueva_ven.isoformat()}) \
                .eq("folio", folio).execute()
            flash(f"Folio {folio} marcado VENCIDO. Pruébalo en /consulta/{folio}", "success")

        elif accion == 'vencer_pago_48h':
            nueva_exp = datetime.now(TZ_MX) - timedelta(hours=49)
            supabase.table("folios_registrados") \
                .update({"fecha_expedicion": nueva_exp.isoformat()}) \
                .eq("folio", folio).execute()
            flash(f"Folio {folio}: expedición movida 49h atrás. "
                  f"Si sigue PENDIENTE_PAGO, el scheduler lo borra en máx 15 min.", "success")

        elif accion == 'restaurar':
            hoy = datetime.now(TZ_MX)
            ven = hoy + timedelta(days=30)
            supabase.table("folios_registrados") \
                .update({
                    "fecha_expedicion": hoy.isoformat(),
                    "fecha_vencimiento": ven.isoformat()
                }).eq("folio", folio).execute()
            flash(f"Folio {folio} restaurado a vigencia normal (30 días).", "success")

        return redirect(url_for('admin_test_fechas', folio=folio))

    folio_buscar = request.args.get('folio', '').strip().upper()
    resultado = None
    if folio_buscar:
        resp = supabase.table("folios_registrados").select("*").eq("folio", folio_buscar).execute()
        if resp.data:
            resultado = resp.data[0]
        else:
            flash(f"Folio {folio_buscar} no encontrado.", "error")

    return render_template('admin_test_fechas.html', resultado=resultado, folio_buscar=folio_buscar)


@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    def _form():
        return render_template('registro_admin.html', datetime=datetime,
                               fecha_hoy=today_mx().isoformat(), f=request.form)

    if request.method == 'POST':
        folio_manual = request.form.get('folio', '').strip().upper()
        if folio_manual and not FOLIO_RE.match(folio_manual):
            flash('Folio inválido: solo letras y números (3 a 12 caracteres).', 'error')
            return _form()

        datos, err = _leer_datos_vehiculo(request.form)
        if err:
            flash(err, 'error')
            return _form()

        fecha_exp, fecha_ven, err = _leer_fechas(request.form)
        if err:
            flash(err, 'error')
            return _form()

        # SIN user_id → folio oficial, puede renovarse con autoservicio
        ok, folio, err = _crear_folio(
            datos, fecha_exp, fecha_ven,
            {"estado": "ADMIN", "estado_pago": "VALIDADO"}, folio_manual)
        if not ok:
            flash(err, 'error')
            return _form()

        threading.Thread(target=generar_subir_y_guardar_pdf,
                         args=(_datos_pdf(folio, datos, fecha_exp, fecha_ven),),
                         daemon=True).start()
        return render_template("exitoso.html", folio=folio)

    return _form()


@app.route('/ver_registros')
def ver_registros():
    if 'admin' not in session:
        return redirect(url_for('login'))
    resp = supabase.table("folios_registrados") \
        .select("*").order("fecha_expedicion", desc=True).execute()
    return render_template("mis_registros.html", registros=resp.data or [])


@app.route('/ver_registros_admin')
def ver_registros_admin():
    return ver_registros()


@app.route('/admin/validar_pago/<folio>', methods=['POST'])
def validar_pago(folio):
    if 'admin' not in session:
        return redirect(url_for('login'))
    folio = folio.strip().upper()
    supabase.table("folios_registrados") \
        .update({"estado_pago": "VALIDADO"}).eq("folio", folio).execute()
    flash(f"Folio {folio} validado. Ya no se eliminará.", "success")
    return redirect(request.referrer or url_for('ver_registros'))


@app.route('/admin/usuarios')
def admin_usuarios():
    if 'admin' not in session:
        return redirect(url_for('login'))
    resp     = supabase.table("verificaciondigitalcdmx").select("*").order("id", desc=True).execute()
    usuarios = resp.data or []
    for u in usuarios:
        u['timer_info'] = get_timer_info(u)
    return render_template("admin_usuarios.html", usuarios=usuarios)


@app.route('/admin/marcar_pagado/<int:user_id>', methods=['POST'])
def marcar_pagado(user_id):
    if 'admin' not in session:
        return redirect(url_for('login'))
    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": True}).eq("id", user_id).execute()
    flash("Usuario marcado como PAGADO.", "success")
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/marcar_pendiente/<int:user_id>', methods=['POST'])
def marcar_pendiente(user_id):
    if 'admin' not in session:
        return redirect(url_for('login'))
    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": False, "created_at": datetime.utcnow().isoformat()}) \
        .eq("id", user_id).execute()
    flash("Usuario marcado como PENDIENTE. Timer reiniciado.", "warning")
    return redirect(url_for('admin_usuarios'))


# ─── API TIMER ───────────────────────────────────────────────────────────────

@app.route('/api/timer_estado')
def api_timer_estado():
    if 'user_id' not in session:
        return jsonify({"error": "no auth"}), 401
    resp = supabase.table("verificaciondigitalcdmx") \
        .select("pagado, created_at, folios_asignac, folios_usados") \
        .eq("id", session['user_id']).execute()
    if not resp.data:
        return jsonify({"error": "no encontrado"}), 404
    usuario = resp.data[0]
    asig    = usuario.get("folios_asignac", 0)
    usados  = usuario.get("folios_usados",  0)
    return jsonify({
        "pagado":     usuario.get("pagado", False),
        "timer":      get_timer_info(usuario),
        "porcentaje": round((usados / asig) * 100) if asig > 0 else 0
    })


# ─── USUARIO (3RO) ───────────────────────────────────────────────────────────

def _render_usuario(user_id):
    try:
        response    = supabase.table("verificaciondigitalcdmx") \
            .select("folios_asignac, folios_usados, pagado, created_at") \
            .eq("id", user_id).execute()
        folios_info = response.data[0] if response.data else {}
    except Exception:
        response    = supabase.table("verificaciondigitalcdmx") \
            .select("folios_asignac, folios_usados").eq("id", user_id).execute()
        folios_info = response.data[0] if response.data else {}
        folios_info['pagado'] = True

    asig   = folios_info.get("folios_asignac", 0)
    usados = folios_info.get("folios_usados",  0)
    pct    = round((usados / asig) * 100) if asig > 0 else 0

    return render_template(
        "registro_usuario.html",
        folios_info=folios_info,
        timer_info=get_timer_info(folios_info),
        porcentaje=pct,
        fecha_hoy=datetime.now(TZ_MX).strftime("%Y-%m-%d"),
        f=request.form
    )


@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        datos, err = _leer_datos_vehiculo(request.form)
        if err:
            flash(err, 'error')
            return _render_usuario(user_id)

        fecha_exp, fecha_ven, err = _leer_fechas(request.form)
        if err:
            flash(err, 'error')
            return _render_usuario(user_id)

        u_data = supabase.table("verificaciondigitalcdmx") \
            .select("folios_asignac, folios_usados").eq("id", user_id).execute()
        if not u_data.data:
            flash("Error obteniendo datos del usuario.", "error")
            return redirect(url_for('registro_usuario'))

        folios    = u_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles.", "error")
            return _render_usuario(user_id)

        # CON user_id → lote, sin autoservicio de renovación.
        # El 3ro ya NO puede escoger folio: siempre sale del generador compartido.
        ok, folio, err = _crear_folio(
            datos, fecha_exp, fecha_ven,
            {"estado": "PENDIENTE", "estado_pago": "VALIDADO", "user_id": user_id})
        if not ok:
            flash(err, 'error')
            return _render_usuario(user_id)

        supabase.table("verificaciondigitalcdmx") \
            .update({"folios_usados": folios["folios_usados"] + 1}) \
            .eq("id", user_id).execute()

        threading.Thread(target=generar_subir_y_guardar_pdf,
                         args=(_datos_pdf(folio, datos, fecha_exp, fecha_ven),),
                         daemon=True).start()
        return render_template("exitoso.html", folio=folio)

    return _render_usuario(user_id)


@app.route('/mis_permisos')
def mis_permisos():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    resp    = supabase.table("folios_registrados") \
        .select("*").eq("user_id", user_id) \
        .order("fecha_expedicion", desc=True).execute()
    registros = resp.data or []
    for r in registros:
        r['tiene_pdf'] = bool(r.get('pdf_url')) or os.path.exists(f"{OUTPUT_DIR}/{r['folio']}.pdf")
    return render_template("mis_permisos.html", registros=registros)


# ─── CONSULTA PÚBLICA ────────────────────────────────────────────────────────

def _armar_resultado(registro: dict, folio: str) -> dict:
    """
    PARCHE ANTIRROBO:
    puede_renovar = True solo si VENCIDO + no es lote + NO tiene renovación PENDIENTE_PAGO activa.
    ya_renovado   = True si existe folio con folio_origen=folio y estado_pago=PENDIENTE_PAGO.
    evaluación server-side - aplica desde cualquier cel/browser.

    VIGENTE/VENCIDO se compara por FECHA: el permiso vale todo el día de su
    fecha de vencimiento (antes se comparaba contra las 00:00 de ese día).
    """
    try:
        fe = _parse_fecha(registro.get('fecha_expedicion'))
        fv = _parse_fecha(registro.get('fecha_vencimiento'))
        estado = "VIGENTE" if today_mx() <= fv else "VENCIDO"
        fe_txt = fe.strftime("%d/%m/%Y")
        fv_txt = fv.strftime("%d/%m/%Y")
    except Exception as e:
        print(f"[CONSULTA] fechas inválidas en {folio}: {e}")
        estado, fe_txt, fv_txt = "SIN FECHA", "N/D", "N/D"

    es_de_lote = registro.get('user_id') is not None

    # ── PARCHE: bloquear si ya hay renovación pendiente de pago ───────────────
    ya_renovado = False
    if estado == "VENCIDO" and not es_de_lote:
        try:
            chk = supabase.table("folios_registrados") \
                .select("folio") \
                .eq("folio_origen", folio) \
                .eq("estado_pago", "PENDIENTE_PAGO") \
                .limit(1).execute()
            ya_renovado = bool(chk.data)
        except Exception as e:
            print(f"[RENOVAR CHECK] {e}")

    puede_renovar = (estado == "VENCIDO") and not es_de_lote and not ya_renovado

    return {
        "estado":            estado,
        "folio":             folio,
        "folio_actual":      folio,
        "fecha_expedicion":  fe_txt,
        "fecha_vencimiento": fv_txt,
        "marca":             registro.get('marca', ''),
        "linea":             registro.get('linea', ''),
        "año":               registro.get('anio', ''),
        "color":             registro.get('color', ''),
        "nombre":            registro.get('nombre', ''),
        "numero_serie":      registro.get('numero_serie', ''),
        "numero_motor":      registro.get('numero_motor', ''),
        "puede_renovar":     puede_renovar,
        "ya_renovado":       ya_renovado,
    }


def _render_consulta(resultado, status=200):
    return render_template("resultado_consulta.html", resultado=resultado,
                           horas_limite=HORAS_LIMITE_PAGO, costo=COSTO_FIJO,
                           pago=DATOS_PAGO), status


@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        resp  = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if not resp.data:
            resultado = {"estado": "No encontrado", "folio": folio,
                         "puede_renovar": False, "ya_renovado": False}
        else:
            resultado = _armar_resultado(resp.data[0], folio)
        return _render_consulta(resultado)
    return render_template("consulta_folio.html")


@app.route('/consulta/<folio>')
def consulta_qr_guerrero(folio):
    folio = folio.strip().upper()
    resp  = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if not resp.data:
        resultado = {"estado": "No encontrado", "folio": folio,
                     "puede_renovar": False, "ya_renovado": False}
        return _render_consulta(resultado, 404)
    return _render_consulta(_armar_resultado(resp.data[0], folio))


# ═══════════════════════════════════════════════════════════════════════════
# RENOVACIÓN - PARCHE ANTIRROBO COMPLETO
# Bloqueo 1: folios de lote (user_id != null)
# Bloqueo 2: ya existe PENDIENTE_PAGO para este folio → 409, no crea otro
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/renovar_folio/<folio_viejo>', methods=['POST'])
def renovar_folio(folio_viejo):
    t0 = time.time()
    folio_viejo = folio_viejo.strip().upper()
    print(f"[RENOVAR] INICIO folio_viejo={folio_viejo}")

    resp = supabase.table("folios_registrados").select("*").eq("folio", folio_viejo).execute()
    if not resp.data:
        return jsonify({"ok": False, "error": "Folio original no encontrado"}), 404

    original = resp.data[0]

    # Bloqueo 1: folios de lote
    if original.get("user_id"):
        return jsonify({
            "ok": False,
            "error": "Este folio fue emitido por un proveedor. Contacta a quien te lo entregó para renovarlo."
        }), 403

    # ── PARCHE ANTIRROBO: bloquear si ya hay renovación pendiente ─────────────
    ya_existente = supabase.table("folios_registrados") \
        .select("folio") \
        .eq("folio_origen", folio_viejo) \
        .eq("estado_pago", "PENDIENTE_PAGO") \
        .order("fecha_expedicion", desc=True) \
        .limit(1).execute()

    if ya_existente.data:
        folio_existente = ya_existente.data[0]["folio"]
        print(f"[RENOVAR] Bloqueado - ya existe pendiente: {folio_existente}")
        return jsonify({
            "ok": False,
            "error": "Ya tienes una renovación pendiente de pago. Envía tu comprobante para activarla o espera 48 horas.",
            "folio_pendiente": folio_existente
        }), 409

    fecha_exp = datetime.now(TZ_MX)
    fecha_ven = fecha_exp + timedelta(days=30)

    payload_base = {
        "marca":             original.get("marca", ""),
        "linea":             original.get("linea", ""),
        "anio":              original.get("anio", ""),
        "numero_serie":      original.get("numero_serie", ""),
        "numero_motor":      original.get("numero_motor", ""),
        "color":             original.get("color", "N/D"),
        "nombre":            original.get("nombre", ""),
        "fecha_expedicion":  fecha_exp.isoformat(),
        "fecha_vencimiento": fecha_ven.isoformat(),
        "entidad":           "Guerrero",
        "estado":            "RENOVACION",
        "estado_pago":       "PENDIENTE_PAGO",
        "folio_origen":      folio_viejo,
        # SIN user_id → sigue siendo oficial, puede volver a renovarse
    }

    ok, folio_nuevo = guardar_folio_con_reintento(payload_base)
    print(f"[RENOVAR] folio_nuevo={folio_nuevo} ({time.time()-t0:.2f}s)")

    if not ok:
        return jsonify({"ok": False, "error": "No se pudo registrar la renovación"}), 500

    datos_pdf = {
        "folio":     folio_nuevo,
        "marca":     original.get("marca", ""),
        "linea":     original.get("linea", ""),
        "anio":      original.get("anio", ""),
        "serie":     original.get("numero_serie", ""),
        "motor":     original.get("numero_motor", ""),
        "color":     original.get("color", "N/D"),
        "nombre":    original.get("nombre", ""),
        "costo":     COSTO_FIJO,
        "rfc":       RFC_FIJO,
        "domicilio": DOMICILIO_FIJO,
        "fecha_exp": fecha_exp.strftime("%d/%m/%Y"),
        "fecha_ven": fecha_ven.strftime("%d/%m/%Y"),
    }

    threading.Thread(target=generar_subir_y_guardar_pdf, args=(datos_pdf,), daemon=True).start()

    print(f"[RENOVAR] FIN {time.time()-t0:.2f}s total")
    return jsonify({
        "ok": True,
        "folio_nuevo": folio_nuevo,
        "horas_limite": HORAS_LIMITE_PAGO
    })


@app.route('/estado_pdf/<folio>')
def estado_pdf(folio):
    resp    = supabase.table("folios_registrados").select("pdf_url").eq("folio", folio).execute()
    pdf_url = resp.data[0].get("pdf_url", "") if resp.data else ""
    if not pdf_url and resp.data:
        pdf_url = _buscar_pdf_storage(folio)
    return jsonify({"pdf_url": pdf_url})


# ─── DESCARGAS ───────────────────────────────────────────────────────────────

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    if 'admin' not in session and 'user_id' not in session:
        return redirect(url_for('login'))

    resp = supabase.table("folios_registrados") \
        .select("user_id, pdf_url").eq("folio", folio).execute()

    if 'user_id' in session:
        if resp.data and resp.data[0].get("user_id") != session['user_id']:
            flash("No tienes permiso para descargar este archivo.", "error")
            return redirect(url_for('mis_permisos'))

    if resp.data and resp.data[0].get("pdf_url"):
        return redirect(resp.data[0]["pdf_url"])

    ruta = f"{OUTPUT_DIR}/{folio}.pdf"
    if os.path.exists(ruta):
        return send_from_directory(OUTPUT_DIR, f"{folio}.pdf", as_attachment=True)

    # Permisos del bot (o PDFs de antes de un deploy): buscar en Storage
    if resp.data:
        url = _buscar_pdf_storage(folio)
        if url:
            return redirect(url)

    flash("El PDF no existe. Puede que aún esté generándose.", "error")
    return redirect(url_for("ver_registros") if 'admin' in session else url_for("mis_permisos"))


@app.route('/descargar_constancia/<folio>')
def descargar_constancia(folio):
    try:
        filename = f"AB{int(folio):05d}_constancia.pdf"
    except ValueError:
        flash("La constancia no existe.", "error")
        return redirect(url_for('consulta_folio'))
    ruta = os.path.join("static", "constancias", filename)
    if os.path.exists(ruta):
        return send_from_directory("static/constancias", filename, as_attachment=True)
    flash("La constancia no existe.", "error")
    return redirect(url_for('consulta_folio'))


# Editor de tablas, ajuste de fechas, menú y timers del bot (registran sus
# rutas sobre este mismo `app`). Va al final para evitar import circular.
import editor_guerrero  # noqa: E402,F401
