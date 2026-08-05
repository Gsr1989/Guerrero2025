"""
GUERRERO — Panel web (Flask).

Cambios respecto al original recuperado:
  · Config, candado de PDF, coordenadas, generador de PDF y generador de
    folio ahora vienen de config_guerrero — antes el panel tenía su PROPIA
    copia de absolutamente todo eso (idéntica a la del bot), incluido un
    watermark de folio DISTINTO ("GRO_FOLIO" vs "GUE" del bot): dos
    contadores corriendo en paralelo que tarde o temprano iban a repetir
    un folio para dos vehículos distintos.
  · El scheduler de limpieza 48h ya no arranca al importar el módulo — lo
    llama main.py en el lifespan (evita que se dispare dos veces).
  · Se agregó editor de tablas (/admin/editor) y ajuste de fechas libres
    (/admin/fechas), además del /admin/test_fechas original que se dejó tal
    cual por si lo siguen usando.
  · Se quitó el app.run() del final — ahora lo monta main.py.
Las rutas y los templates existentes NO cambiaron.
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify, Response
import html as _html
from datetime import datetime, timedelta
import os
import string
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler

from config_guerrero import (
    supabase, logger, TZ_MX, now_mx, today_mx,
    OUTPUT_DIR, PLANTILLA_PERMISO, PLANTILLA_RECIBO, URL_CONSULTA_BASE,
    ENTIDAD, RFC_FIJO, DOMICILIO_FIJO, COSTO_FIJO, DIAS_PERMISO,
    HORAS_LIMITE_PAGO, BUCKET_NAME, TABLAS_DISPONIBLES, COLUMNAS_FECHA,
    ADMIN_USER, ADMIN_PASS, SECRET_KEY,
    generar_folio_guerrero, generar_subir_y_guardar_pdf, leer_siguiente_folio,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

flask_app = app   # alias que usa main.py para montarlo

os.makedirs(OUTPUT_DIR, exist_ok=True)



# ===================== TABLAS DEL EDITOR (nuevo) =====================
TABLAS_DISPONIBLES = {
    'folios_registrados': {
        'nombre':   'Folios Registrados',
        'pk_col':   'folio',
        'columnas': ['folio', 'marca', 'linea', 'anio', 'numero_serie', 'numero_motor',
                     'color', 'nombre', 'fecha_expedicion', 'fecha_vencimiento',
                     'entidad', 'estado', 'estado_pago', 'creado_por'],
    },
    'verificaciondigitalcdmx': {
        'nombre':   'Usuarios del Sistema',
        'pk_col':   'id',
        'columnas': ['id', 'username', 'password', 'folios_asignac', 'folios_usados'],
    },
    'folio_watermark': {
        'nombre':   'Watermark de Folios',
        'pk_col':   'prefijo',
        'columnas': ['prefijo', 'ultimo_asignado'],
    },
}

COLUMNAS_FECHA = {
    'fecha_expedicion', 'fecha_vencimiento', 'fecha_comprobante',
    'fecha_detencion', 'created_at',
}

# ─── LOCK GLOBAL PDF ─────────────────────────────────────────────────────────
_pdf_generation_lock = threading.Lock()

# ─── COORDENADAS ─────────────────────────────────────────────────────────────
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

        if username == 'Serg890105tm3' and password == 'Serg890105tm3':
            session['admin'] = True
            return redirect(url_for('admin'))

        response = supabase.table("verificaciondigitalcdmx") \
            .select("*").eq("username", username).eq("password", password).execute()
        if response.data:
            usuario = response.data[0]
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

    if request.method == 'POST':
        folio_manual  = request.form.get('folio', '').strip().upper()
        marca         = request.form['marca'].upper()
        linea         = request.form['linea'].upper()
        anio          = request.form['anio']
        numero_serie  = request.form['serie'].upper()
        numero_motor  = request.form['motor'].upper()
        color         = request.form.get('color', 'N/D').upper()
        contribuyente = request.form['contribuyente'].upper()
        vigencia      = int(request.form['vigencia'])

        f_exp_str        = request.form.get('fecha_expedicion')
        fecha_expedicion = datetime.strptime(f_exp_str, "%Y-%m-%d").replace(tzinfo=TZ_MX) \
                           if f_exp_str else datetime.now(TZ_MX)
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        payload_base = {
            "marca":             marca,
            "linea":             linea,
            "anio":              anio,
            "numero_serie":      numero_serie,
            "numero_motor":      numero_motor,
            "color":             color,
            "nombre":            contribuyente,
            "fecha_expedicion":  fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "entidad":           "Guerrero",
            "estado":            "ADMIN",
            "estado_pago":       "VALIDADO",
            # SIN user_id → folio oficial, puede renovarse con autoservicio
        }

        if folio_manual:
            existente = supabase.table("folios_registrados").select("folio").eq("folio", folio_manual).execute()
            if existente.data:
                flash('Error: el folio ya existe.', 'error')
                return render_template('registro_admin.html', datetime=datetime)
            payload_base["folio"] = folio_manual
            try:
                supabase.table("folios_registrados").insert(payload_base).execute()
                folio = folio_manual
            except Exception as e:
                flash(f'Error al registrar: {e}', 'error')
                return render_template('registro_admin.html', datetime=datetime)
        else:
            ok, folio = guardar_folio_con_reintento(payload_base)
            if not ok:
                flash('Error al generar folio. Intenta de nuevo.', 'error')
                return render_template('registro_admin.html', datetime=datetime)

        datos_pdf = {
            "folio":     folio,
            "marca":     marca,
            "linea":     linea,
            "anio":      anio,
            "serie":     numero_serie,
            "motor":     numero_motor,
            "color":     color,
            "nombre":    contribuyente,
            "costo":     COSTO_FIJO,
            "rfc":       RFC_FIJO,
            "domicilio": DOMICILIO_FIJO,
            "fecha_exp": fecha_expedicion.strftime("%d/%m/%Y"),
            "fecha_ven": fecha_vencimiento.strftime("%d/%m/%Y"),
        }

        threading.Thread(target=generar_subir_y_guardar_pdf, args=(datos_pdf,), daemon=True).start()
        return render_template("exitoso.html", folio=folio)

    return render_template('registro_admin.html', datetime=datetime)


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

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        folio_manual  = request.form.get('folio', '').strip().upper()
        marca         = request.form['marca'].upper()
        linea         = request.form['linea'].upper()
        anio          = request.form['anio']
        numero_serie  = request.form['serie'].upper()
        numero_motor  = request.form['motor'].upper()
        color         = request.form.get('color', 'N/D').upper()
        contribuyente = request.form['contribuyente'].upper()
        vigencia      = int(request.form['vigencia'])

        f_exp_str        = request.form.get('fecha_expedicion')
        fecha_expedicion = datetime.strptime(f_exp_str, "%Y-%m-%d").replace(tzinfo=TZ_MX) \
                           if f_exp_str else datetime.now(TZ_MX)
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        u_data = supabase.table("verificaciondigitalcdmx") \
            .select("folios_asignac, folios_usados").eq("id", user_id).execute()
        if not u_data.data:
            flash("Error obteniendo datos del usuario.", "error")
            return redirect(url_for('registro_usuario'))

        folios    = u_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles.", "error")
            return redirect(url_for('registro_usuario'))

        payload_base = {
            "marca":             marca,
            "linea":             linea,
            "anio":              anio,
            "numero_serie":      numero_serie,
            "numero_motor":      numero_motor,
            "color":             color,
            "nombre":            contribuyente,
            "fecha_expedicion":  fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "entidad":           "Guerrero",
            "estado":            "PENDIENTE",
            "estado_pago":       "VALIDADO",
            "user_id":           user_id,   # CON user_id → lote, sin autoservicio renovación
        }

        if folio_manual:
            existente = supabase.table("folios_registrados").select("folio").eq("folio", folio_manual).execute()
            if existente.data:
                flash('Error: el folio ya existe.', 'error')
                return redirect(url_for('registro_usuario'))
            payload_base["folio"] = folio_manual
            try:
                supabase.table("folios_registrados").insert(payload_base).execute()
                folio = folio_manual
            except Exception as e:
                flash(f'Error al registrar: {e}', 'error')
                return redirect(url_for('registro_usuario'))
        else:
            ok, folio = guardar_folio_con_reintento(payload_base)
            if not ok:
                flash('Error al generar folio. Intenta de nuevo.', 'error')
                return redirect(url_for('registro_usuario'))

        supabase.table("verificaciondigitalcdmx") \
            .update({"folios_usados": folios["folios_usados"] + 1}) \
            .eq("id", user_id).execute()

        datos_pdf = {
            "folio":     folio,
            "marca":     marca,
            "linea":     linea,
            "anio":      anio,
            "serie":     numero_serie,
            "motor":     numero_motor,
            "color":     color,
            "nombre":    contribuyente,
            "costo":     COSTO_FIJO,
            "rfc":       RFC_FIJO,
            "domicilio": DOMICILIO_FIJO,
            "fecha_exp": fecha_expedicion.strftime("%d/%m/%Y"),
            "fecha_ven": fecha_vencimiento.strftime("%d/%m/%Y"),
        }

        threading.Thread(target=generar_subir_y_guardar_pdf, args=(datos_pdf,), daemon=True).start()
        return render_template("exitoso.html", folio=folio)

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
        fecha_hoy=datetime.now(TZ_MX).strftime("%Y-%m-%d")
    )


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
    """
    fe = datetime.fromisoformat(registro['fecha_expedicion'])
    fv = datetime.fromisoformat(registro['fecha_vencimiento'])
    if fe.tzinfo is None:
        fe = fe.replace(tzinfo=TZ_MX)
    if fv.tzinfo is None:
        fv = fv.replace(tzinfo=TZ_MX)

    ahora  = datetime.now(TZ_MX)
    estado = "VIGENTE" if ahora <= fv else "VENCIDO"

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
        "fecha_expedicion":  fe.strftime("%d/%m/%Y"),
        "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
        "marca":             registro.get('marca', ''),
        "linea":             registro.get('linea', ''),
        "año":               registro.get('anio', ''),
        "numero_serie":      registro.get('numero_serie', ''),
        "numero_motor":      registro.get('numero_motor', ''),
        "puede_renovar":     puede_renovar,
        "ya_renovado":       ya_renovado,   # ← nuevo para el template
    }


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
        return render_template("resultado_consulta.html", resultado=resultado)
    return render_template("consulta_folio.html")


@app.route('/consulta/<folio>')
def consulta_qr_guerrero(folio):
    folio = folio.strip().upper()
    resp  = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if not resp.data:
        resultado = {"estado": "No encontrado", "folio": folio,
                     "puede_renovar": False, "ya_renovado": False}
    else:
        resultado = _armar_resultado(resp.data[0], folio)
    return render_template("resultado_consulta.html", resultado=resultado)


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

    flash("El PDF no existe. Puede que aún esté generándose.", "error")
    return redirect(url_for("ver_registros") if 'admin' in session else url_for("mis_permisos"))


@app.route('/descargar_constancia/<folio>')
def descargar_constancia(folio):
    filename = f"AB{int(folio):05d}_constancia.pdf"
    ruta     = os.path.join("static", "constancias", filename)
    if os.path.exists(ruta):
        return send_from_directory("static/constancias", filename, as_attachment=True)
    flash("La constancia no existe.", "error")
    return redirect(url_for('consulta_folio'))


# ─── MAIN ────────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════
#  EDITOR DE TABLAS — administra Supabase desde AQUÍ, sin entrar al sitio
#  · Click en cualquier celda → se edita en línea y se guarda solo
#  · Las columnas de fecha abren calendario (fechas pasadas Y futuras libres)
#  · Botón para borrar renglón y para agregar uno nuevo
# ═══════════════════════════════════════════════════════════════════════════

_EDITOR_CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f2f4f7;color:#1d1d1b}
.bar{background:#8a5a00;color:#fff;padding:13px 18px;font-weight:700;font-size:15px;
     display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:50}
.bar a{color:#f0d5de;text-decoration:none;font-size:13px;font-weight:600}
.bar a:hover{color:#fff}
.wrap{max-width:1400px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.07);margin-bottom:16px}
.nota{background:#fbf3e3;border-left:4px solid #8a5a00;border-radius:8px;padding:12px 14px;
      font-size:13px;line-height:1.7;margin-bottom:16px}
.tablas-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.tabla-card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;
            text-decoration:none;color:inherit;display:block;transition:.15s}
.tabla-card:hover{border-color:#8a5a00;transform:translateY(-2px);box-shadow:0 4px 14px rgba(138,90,0,.15)}
.tabla-card h3{margin:0 0 6px;font-size:15px;color:#8a5a00}
.tabla-card p{margin:0;font-size:12px;color:#777}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
input[type=text],input[type=search]{padding:9px 12px;border:1.5px solid #d6dae0;border-radius:8px;font-size:14px;font-family:inherit}
input:focus{outline:none;border-color:#8a5a00}
.btn{padding:9px 16px;border:none;border-radius:8px;font-weight:700;font-size:13px;
     cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-family:inherit}
.btn-p{background:#8a5a00;color:#fff}.btn-p:hover{background:#6e4700}
.btn-o{background:#fff;border:1.5px solid #d6dae0;color:#444}.btn-o:hover{border-color:#8a5a00;color:#8a5a00}
.tabla-wrap{overflow-x:auto;background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
thead th{background:#8a5a00;color:#fff;padding:11px 10px;text-align:left;position:sticky;top:0}
tbody td{padding:6px 10px;border-bottom:1px solid #eef0f2;vertical-align:middle}
tbody tr:hover td{background:#fdf8ef}
.cv{display:inline-block;min-width:60px;max-width:240px;overflow:hidden;text-overflow:ellipsis;
    cursor:pointer;padding:4px 7px;border-radius:5px;border:1px solid transparent}
.cv:hover{border-color:#c8ccd2;background:#fff}
.cv.nv{color:#bbb;font-style:italic}
.cv.fecha{background:#fff8e6;border-color:#f0dfae}
.cell-input{border:2px solid #8a5a00;border-radius:5px;padding:4px 7px;font-size:13px;
            min-width:130px;outline:none;background:#fdf8ef;font-family:inherit}
.del{background:#fff;border:1px solid #e0c3c3;color:#c0392b;border-radius:5px;
     padding:4px 9px;font-size:12px;cursor:pointer}
.del:hover{background:#c0392b;color:#fff}
.toast{position:fixed;bottom:22px;right:18px;z-index:999;padding:11px 18px;border-radius:9px;
       font-size:13px;font-weight:600;opacity:0;transition:opacity .25s;pointer-events:none;
       box-shadow:0 4px 14px rgba(0,0,0,.15)}
.toast.show{opacity:1}
.toast.ok{background:#e7f8ee;color:#0a6b3d;border:1px solid #9ad9b8}
.toast.err{background:#fdeaea;color:#a32020;border:1px solid #eba9a9}
.paginacion{display:flex;gap:8px;justify-content:center;align-items:center;padding:14px}
.pg{background:#8a5a00;color:#fff;padding:7px 13px;border-radius:6px;font-size:13px;font-weight:700}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:900;display:flex;
       align-items:center;justify-content:center;padding:16px}
.modal-box{background:#fff;border-radius:12px;padding:24px;max-width:520px;width:100%;
           max-height:85vh;overflow-y:auto}
.modal-box h3{margin:0 0 16px;color:#8a5a00}
.campo{margin-bottom:12px}
.campo label{display:block;font-size:13px;font-weight:600;margin-bottom:4px}
.campo input{width:100%;padding:9px 12px;border:1.5px solid #d6dae0;border-radius:7px;
             font-size:14px;font-family:inherit}
"""

_EDITOR_JS = """
let TABLA='', PK='';
function initEditor(t,p){TABLA=t;PK=p;}

function editCell(span){
  if(span.dataset.editing==='1') return;
  span.dataset.editing='1';
  const col=span.dataset.col, pk=span.dataset.pk, orig=span.dataset.val;
  const esFecha = span.classList.contains('fecha');
  const inp=document.createElement('input');
  inp.type = esFecha ? 'date' : 'text';
  inp.className='cell-input';
  inp.value = esFecha ? (orig||'').substring(0,10) : orig;
  inp._span=span; inp._orig=orig; inp._col=col; inp._pk=pk;
  span.parentNode.insertBefore(inp,span);
  span.style.display='none';
  inp.focus(); if(!esFecha) inp.select();
  inp.addEventListener('blur',()=>fin(inp));
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();inp.blur();}
    if(e.key==='Escape'){inp._cancel=true;inp.blur();}
  });
}

function fin(inp){
  const span=inp._span, nv=inp.value.trim(), orig=inp._orig;
  inp.remove(); span.style.display=''; span.dataset.editing='0';
  if(inp._cancel||nv===orig) return;
  span.textContent = nv||'null';
  span.dataset.val = nv;
  span.classList.toggle('nv',!nv);
  fetch('/api/update_cell',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:inp._pk,col:inp._col,val:nv})})
   .then(r=>r.json()).then(d=>{
      if(d.ok) toast('✓ Guardado en Supabase',true);
      else{span.textContent=orig||'null';span.dataset.val=orig;toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{span.textContent=orig||'null';span.dataset.val=orig;toast('Error de red',false);});
}

function delRow(btn,pk,rowId){
  if(!confirm('¿Eliminar este registro de Supabase?\\n\\nEsto NO se puede deshacer.')) return;
  btn.disabled=true; btn.textContent='...';
  fetch('/api/delete_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:pk})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){const tr=document.getElementById(rowId);
        if(tr){tr.style.opacity='0';setTimeout(()=>tr.remove(),250);}
        toast('Eliminado',true);}
      else{btn.disabled=false;btn.textContent='Borrar';toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{btn.disabled=false;btn.textContent='Borrar';toast('Error de red',false);});
}

function abrirNuevo(){document.getElementById('modalNuevo').style.display='flex';}
function cerrarNuevo(){document.getElementById('modalNuevo').style.display='none';}

function guardarNuevo(){
  const campos={};
  document.querySelectorAll('#formNuevo [data-campo]').forEach(el=>{
    const v=el.value.trim(); if(v) campos[el.dataset.campo]=v;
  });
  if(Object.keys(campos).length===0){toast('Llena al menos un campo',false);return;}
  fetch('/api/add_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,datos:campos})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){toast('Agregado — recargando...',true);setTimeout(()=>location.reload(),700);}
      else toast('Error: '+(d.error||'?'),false);
   }).catch(()=>toast('Error de red',false));
}

let tt;
function toast(msg,ok){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+(ok?'ok':'err');
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('show'),2600);
}
"""


def _editor_head(titulo: str) -> str:
    return f"""<!DOCTYPE html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo} — GUERRERO</title><style>{_EDITOR_CSS}</style></head><body>"""


@app.route('/admin/editor')
def admin_editor():
    if not session.get('admin'):
        return redirect(url_for('login'))
    cards = "".join(
        f"""<a class="tabla-card" href="/admin/editor/{nombre}">
              <h3>🗄️ {info['nombre']}</h3>
              <p><code>{nombre}</code> · {len(info['columnas'])} columnas</p>
            </a>"""
        for nombre, info in TABLAS_DISPONIBLES.items()
    )
    return Response(_editor_head("Editor de Tablas") + f"""
<div class="bar">🛠️ Editor de Base de Datos — GUERRERO
  <span style="margin-left:auto"><a href="/admin">← Panel</a></span>
</div>
<div class="wrap">
  <div class="nota">
    Desde aquí administras <strong>Supabase directamente</strong>, sin entrar a su sitio.<br>
    Entra a una tabla, haz click en cualquier celda y edítala: se guarda sola.<br>
    Las columnas de fecha abren calendario y aceptan <strong>cualquier fecha,
    pasada o futura</strong>.
  </div>
  <div class="tablas-grid">{cards}</div>
</div>
</body></html>""", mimetype="text/html")


@app.route('/admin/editor/<nombre_tabla>')
def admin_editor_tabla(nombre_tabla):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))

    info   = TABLAS_DISPONIBLES[nombre_tabla]
    pk_col = info['pk_col']
    q      = request.args.get('q', '').strip()
    page   = max(1, int(request.args.get('page', 1) or 1))

    try:
        todos = supabase.table(nombre_tabla).select("*").limit(20000).execute().data or []
        if q:
            ql = q.lower()
            filtrados = [r for r in todos
                         if any(ql in str(v).lower() for v in r.values() if v is not None)]
        else:
            filtrados = todos
        total     = len(filtrados)
        offset    = (page - 1) * PAGE_SIZE
        registros = filtrados[offset: offset + PAGE_SIZE]
    except Exception as e:
        logger.error(f"[EDITOR] {e}")
        todos = filtrados = registros = []
        total = offset = 0

    columnas = list(registros[0].keys()) if registros else (
        list(todos[0].keys()) if todos else info['columnas'])
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    def esc(v):
        return _html.escape(str(v), quote=True)

    th = "".join(f"<th>{esc(c)}</th>" for c in columnas) + "<th>Acción</th>"

    filas = ""
    for i, reg in enumerate(registros):
        celdas = f'<td style="color:#bbb;font-size:11px">{offset + i + 1}</td>'
        pk_val = esc(reg.get(pk_col, ""))
        for col in columnas:
            val = reg.get(col)
            disp = str(val) if val is not None else "null"
            clases = "cv"
            if val is None:
                clases += " nv"
            if col in COLUMNAS_FECHA:
                clases += " fecha"
            celdas += (
                f'<td><span class="{clases}" data-col="{esc(col)}" data-pk="{pk_val}" '
                f'data-val="{esc(val if val is not None else "")}" '
                f'onclick="editCell(this)">{esc(disp[:40])}</span></td>'
            )
        celdas += (f'<td><button class="del" onclick="delRow(this,\'{pk_val}\',\'r{i}\')">'
                   f'Borrar</button></td>')
        filas += f'<tr id="r{i}">{celdas}</tr>'

    if not filas:
        filas = (f'<tr><td colspan="{len(columnas)+2}" '
                 f'style="text-align:center;padding:26px;color:#999">Sin registros</td></tr>')

    pag = ""
    if total_pages > 1:
        pag = '<div class="paginacion">'
        if page > 1:
            pag += f'<a class="btn btn-o" href="?q={q}&page={page-1}">← Anterior</a>'
        pag += f'<span class="pg">{page} / {total_pages}</span>'
        if page < total_pages:
            pag += f'<a class="btn btn-o" href="?q={q}&page={page+1}">Siguiente →</a>'
        pag += '</div>'

    campos_nuevo = "".join(
        f"""<div class="campo"><label>{esc(c)}</label>
            <input type="{'date' if c in COLUMNAS_FECHA else 'text'}" data-campo="{esc(c)}"></div>"""
        for c in info['columnas'] if c != 'id'
    )

    return Response(_editor_head(info['nombre']) + f"""
<div class="bar">🗄️ {esc(info['nombre'])}
  <span style="margin-left:auto">
    <a href="/admin/editor">← Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Click en cualquier celda para editarla. Se guarda sola en Supabase al salir
    del campo o al presionar Enter. <strong>Esc</strong> cancela.<br>
    Las celdas amarillas son fechas: abren calendario y aceptan
    <strong>cualquier fecha, pasada o futura</strong>.
  </div>

  <div class="toolbar">
    <form method="GET" style="display:contents">
      <input type="search" name="q" value="{esc(q)}" placeholder="Buscar en toda la tabla...">
      <button class="btn btn-p" type="submit">Buscar</button>
      {'<a class="btn btn-o" href="/admin/editor/' + nombre_tabla + '">Limpiar</a>' if q else ''}
    </form>
    <button class="btn btn-p" onclick="abrirNuevo()">+ Agregar registro</button>
    <span style="margin-left:auto;font-size:13px;color:#777">{total} registros</span>
  </div>

  <div class="tabla-wrap">
    <table><thead><tr><th>#</th>{th}</tr></thead><tbody>{filas}</tbody></table>
    {pag}
  </div>
</div>

<div class="modal" id="modalNuevo" style="display:none">
  <div class="modal-box">
    <h3>Agregar registro a {esc(info['nombre'])}</h3>
    <div id="formNuevo">{campos_nuevo}</div>
    <div style="display:flex;gap:8px;margin-top:18px">
      <button class="btn btn-p" onclick="guardarNuevo()">Guardar</button>
      <button class="btn btn-o" onclick="cerrarNuevo()">Cancelar</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}
initEditor("{nombre_tabla}","{pk_col}");
</script>
</body></html>""", mimetype="text/html")


@app.route('/api/update_cell', methods=['POST'])
def api_update_cell():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    col    = d.get('col')
    val    = d.get('val', '')
    if tabla not in TABLAS_DISPONIBLES or not col or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).update({col: val if val != "" else None}) \
            .eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] {tabla}.{col} de {pk_val} → {val!r}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[EDITOR] update: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/delete_row', methods=['POST'])
def api_delete_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    if tabla not in TABLAS_DISPONIBLES or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        # Guerrero no tiene bot de Telegram — no hay timers que cancelar
        supabase.table(tabla).delete().eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] borrado {tabla} pk={pk_val}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/add_row', methods=['POST'])
def api_add_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d     = request.get_json(force=True)
    tabla = d.get('tabla')
    datos = d.get('datos') or {}
    if tabla not in TABLAS_DISPONIBLES or not datos:
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).insert(datos).execute()
        logger.info(f"[EDITOR] insertado en {tabla}: {list(datos.keys())}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  AJUSTE RÁPIDO DE FECHAS — pon cualquier fecha a un folio, pasada o futura
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/fechas', methods=['GET', 'POST'])
def admin_fechas():
    if not session.get('admin'):
        return redirect(url_for('login'))

    msg = ""
    folio_buscar = request.args.get('folio', '').strip()
    registro = None

    if request.method == 'POST':
        folio  = request.form.get('folio', '').strip()
        accion = request.form.get('accion', '')
        f_exp  = request.form.get('fecha_expedicion', '').strip()
        f_ven  = request.form.get('fecha_vencimiento', '').strip()
        try:
            if accion == 'manual':
                parches = {}
                if f_exp: parches['fecha_expedicion'] = f_exp
                if f_ven: parches['fecha_vencimiento'] = f_ven
                if parches:
                    supabase.table("folios_registrados").update(parches).eq("folio", folio).execute()
                    msg = f"Fechas del folio {folio} actualizadas."
                else:
                    msg = "No mandaste ninguna fecha."
            elif accion == 'vencer':
                ayer = (datetime.now(TZ_MX) - timedelta(days=1)).date().isoformat()
                supabase.table("folios_registrados").update(
                    {"fecha_vencimiento": ayer}).eq("folio", folio).execute()
                msg = f"Folio {folio} marcado VENCIDO."
            elif accion == 'restaurar':
                hoy = datetime.now(TZ_MX)
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  hoy.date().isoformat(),
                    "fecha_vencimiento": (hoy + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} restaurado a {DIAS_PERMISO} días desde hoy."
            elif accion == 'retro':
                dias = int(request.form.get('dias_atras', '30') or 30)
                base = datetime.now(TZ_MX) - timedelta(days=dias)
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  base.date().isoformat(),
                    "fecha_vencimiento": (base + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} expedido {dias} días atrás."
        except Exception as e:
            msg = f"Error: {e}"
        folio_buscar = folio

    if folio_buscar:
        try:
            r = supabase.table("folios_registrados").select("*").eq("folio", folio_buscar).execute()
            registro = r.data[0] if r.data else None
            if not registro and not msg:
                msg = f"Folio {folio_buscar} no encontrado."
        except Exception as e:
            msg = f"Error: {e}"

    def esc(v):
        return _html.escape(str(v), quote=True)

    ficha = ""
    if registro:
        fe = str(registro.get('fecha_expedicion', ''))[:10]
        fv = str(registro.get('fecha_vencimiento', ''))[:10]
        ficha = f"""
        <div class="card">
          <div style="font-size:15px;font-weight:700;color:#8a5a00;margin-bottom:12px">
            Folio {esc(registro.get('folio',''))}
          </div>
          <div style="font-size:13px;line-height:2;margin-bottom:16px">
            <strong>Titular:</strong> {esc(registro.get('nombre',''))}<br>
            <strong>Vehículo:</strong> {esc(registro.get('marca',''))} {esc(registro.get('linea',''))} {esc(registro.get('anio',''))}<br>
            <strong>Expedición actual:</strong> {esc(fe)}<br>
            <strong>Vencimiento actual:</strong> {esc(fv)}<br>
            <a href="/consulta/{esc(registro.get('folio',''))}" target="_blank"
               style="color:#8a5a00">🔗 Ver consulta pública</a>
          </div>
          <form method="POST">
            <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
            <input type="hidden" name="accion" value="manual">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="campo"><label>Nueva fecha de expedición</label>
                <input type="date" name="fecha_expedicion" value="{esc(fe)}"></div>
              <div class="campo"><label>Nueva fecha de vencimiento</label>
                <input type="date" name="fecha_vencimiento" value="{esc(fv)}"></div>
            </div>
            <button class="btn btn-p" type="submit">Guardar estas fechas</button>
            <p style="font-size:12px;color:#777;margin-top:8px">
              Sin límite: puedes poner fechas de años atrás o muy adelante.
            </p>
          </form>
          <hr style="margin:20px 0;border:none;border-top:1px solid #eee">
          <div style="font-size:13px;font-weight:700;margin-bottom:10px">Atajos</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="vencer">
              <button class="btn" style="background:#b38b00;color:#fff">⏰ Marcar vencido</button>
            </form>
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="restaurar">
              <button class="btn" style="background:#1a6e2e;color:#fff">✅ Restaurar {DIAS_PERMISO} días</button>
            </form>
            <form method="POST" style="display:inline;display:flex;gap:6px;align-items:center">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="retro">
              <input type="text" name="dias_atras" value="30" style="width:70px">
              <button class="btn btn-o">📅 Días atrás</button>
            </form>
          </div>
        </div>"""

    msg_html = (f'<div class="nota" style="border-left-color:#8a5a00">{esc(msg)}</div>'
                if msg else "")

    return Response(_editor_head("Ajuste de Fechas") + f"""
<div class="bar">📅 Ajuste de Fechas — GUERRERO
  <span style="margin-left:auto">
    <a href="/admin/editor">Editor de Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Cambia las fechas de cualquier folio sin restricción: <strong>pasadas o
    futuras</strong>. Útil para permisos retroactivos o para pruebas.
  </div>
  {msg_html}
  <div class="card">
    <form method="GET">
      <div class="toolbar">
        <input type="text" name="folio" value="{esc(folio_buscar)}"
               placeholder="ZY4917" style="min-width:220px">
        <button class="btn btn-p" type="submit">Buscar folio</button>
      </div>
    </form>
  </div>
  {ficha}
</div>
<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}</script>
</body></html>""", mimetype="text/html")


@app.route('/admin/menu')
def admin_menu():
    """Menú de accesos rápidos — no depende de panel.html."""
    if not session.get('admin'):
        return redirect(url_for('login'))
    items = [
        ("🗄️ Editor de Tablas",  "/admin/editor",     "Edita cualquier celda de Supabase sin entrar a su sitio"),
        ("📅 Ajuste de Fechas",  "/admin/fechas",      "Pon cualquier fecha a un folio, pasada o futura"),
        ("⏱️ Timers del Bot",   "/admin/timers_bot",  "Ver y detener los timers de 36h del bot de Telegram"),
        ("📋 Folios (clásico)", "/admin_folios",       "Tu vista de folios de siempre"),
        ("🏠 Panel principal",  "/admin",              "Volver al panel"),
    ]
    filas = "".join(
        f"""<a class="tabla-card" href="{url}"><h3>{titulo}</h3><p>{desc}</p></a>"""
        for titulo, url, desc in items
    )
    return Response(_editor_head("Menú Admin") + f"""
<div class="bar">🧭 Menú Admin — GUERRERO</div>
<div class="wrap">
  <div class="nota">Accesos nuevos que no están enlazados desde tu panel.html original.</div>
  <div class="tablas-grid">{filas}</div>
</div>
</body></html>""", mimetype="text/html")



@app.route('/admin/menu')
def admin_menu():
    """Menú de accesos rápidos a las herramientas nuevas."""
    if 'admin' not in session:
        return redirect(url_for('login'))
    items = [
        ("🗄️ Editor de Tablas",  "/admin/editor",       "Edita cualquier celda de Supabase sin entrar a su sitio"),
        ("📅 Ajuste de Fechas",  "/admin/fechas",        "Pon cualquier fecha a un folio, pasada o futura"),
        ("🧪 Test Fechas (viejo)", "/admin/test_fechas", "Tu herramienta de pruebas original"),
        ("📋 Ver Registros",     "/ver_registros_admin", "Tu vista de folios de siempre"),
        ("🏠 Panel principal",   "/admin",               "Volver al panel"),
    ]
    filas = "".join(
        f"""<a class="tabla-card" href="{url}"><h3>{titulo}</h3><p>{desc}</p></a>"""
        for titulo, url, desc in items
    )
    return Response(_editor_head("Menú Admin") + f"""
<div class="bar">🧭 Menú Admin — Guerrero</div>
<div class="wrap">
  <div class="nota">Accesos nuevos que no están enlazados desde tu panel.html original.</div>
  <div class="tablas-grid">{filas}</div>
</div>
</body></html>""", mimetype="text/html")
