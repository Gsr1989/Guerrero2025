from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0."
    "NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── HELPERS ────────────────────────────────────────────────────────────────

def generar_folio_automatico():
    """
    Busca el último folio registrado con prefijo 'GRO' y devuelve el siguiente.
    Formato: GRO00001, GRO00002, ...
    Cambia el prefijo según el estado.
    """
    resp = (
        supabase.table("folios_registrados")
        .select("folio")
        .ilike("folio", "GRO%")
        .order("folio", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        ultimo = resp.data[0]["folio"]  # ej: GRO00042
        try:
            numero = int(ultimo[3:]) + 1
        except ValueError:
            numero = 1
    else:
        numero = 1
    return f"GRO{numero:05d}"


def generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente):
    try:
        plantilla = "recibo_permiso_guerrero_img.pdf"
        ruta_pdf = f"static/pdfs/{folio}.pdf"
        os.makedirs("static/pdfs", exist_ok=True)

        doc = fitz.open(plantilla)
        page = doc[0]
        page.insert_text((700,  1750), folio,                                fontsize=120, fontname="helv")
        page.insert_text((2200, 1750), fecha_expedicion.strftime('%d/%m/%Y'), fontsize=120, fontname="helv")
        page.insert_text((4000, 1750), fecha_vencimiento.strftime('%d/%m/%Y'),fontsize=120, fontname="helv")
        page.insert_text((950,  1930), contribuyente.upper(),                 fontsize=120, fontname="helv")
        doc.save(ruta_pdf)
        return True
    except Exception as e:
        print(f"ERROR al generar PDF: {e}")
        return False


def get_timer_info(usuario):
    """
    Devuelve dict con info del timer de pago para un usuario.
    Retorna None si el usuario ya está marcado como pagado.
    """
    if usuario.get("pagado"):
        return None

    creado_en = usuario.get("created_at")
    if not creado_en:
        return None

    try:
        if isinstance(creado_en, str):
            # Supabase devuelve ISO con zona horaria, normalizamos
            creado_en = datetime.fromisoformat(creado_en.replace("Z", "+00:00")).replace(tzinfo=None)
        limite = creado_en + timedelta(hours=2)
        ahora = datetime.utcnow()
        restante = (limite - ahora).total_seconds()
        return {
            "limite_iso": limite.isoformat(),
            "segundos_restantes": max(0, int(restante)),
            "vencido": restante <= 0
        }
    except Exception as e:
        print(f"Error calculando timer: {e}")
        return None


# ─── RUTAS GENERALES ────────────────────────────────────────────────────────

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

        response = (
            supabase.table("verificaciondigitalcdmx")
            .select("*")
            .eq("username", username)
            .eq("password", password)
            .execute()
        )
        usuarios = response.data
        if usuarios:
            usuario = usuarios[0]
            session['user_id']  = usuario['id']
            session['username'] = usuario['username']
            return redirect(url_for('registro_usuario'))

        return render_template('bloqueado.html')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─── ADMIN ──────────────────────────────────────────────────────────────────

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

        existe = (
            supabase.table("verificaciondigitalcdmx")
            .select("id")
            .eq("username", username)
            .execute()
        )
        if existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            return render_template('crear_usuario.html')

        data = {
            "username":       username,
            "password":       password,
            "folios_asignac": folios,
            "folios_usados":  0,
            "pagado":         False   # <── nuevo campo
        }
        supabase.table("verificaciondigitalcdmx").insert(data).execute()
        flash('Usuario creado exitosamente.', 'success')

    return render_template('crear_usuario.html')


@app.route('/ver_registros_admin')
def ver_registros_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    resp = (
        supabase.table("folios_registrados")
        .select("*")
        .order("fecha_expedicion", desc=True)
        .execute()
    )
    registros = resp.data if resp.data else []
    return render_template("mis_registros.html", registros=registros)


@app.route('/ver_registros')
def ver_registros():
    if 'admin' not in session:
        return redirect(url_for('login'))

    resp = (
        supabase.table("folios_registrados")
        .select("*")
        .order("fecha_expedicion", desc=True)
        .execute()
    )
    registros = resp.data if resp.data else []
    return render_template("mis_registros.html", registros=registros)


@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio_manual  = request.form.get('folio', '').strip().upper()
        folio         = folio_manual if folio_manual else generar_folio_automatico()
        marca         = request.form['marca']
        linea         = request.form['linea']
        anio          = request.form['anio']
        numero_serie  = request.form['serie']
        numero_motor  = request.form['motor']
        vigencia      = int(request.form['vigencia'])
        contribuyente = request.form['contribuyente']

        f_exp_str = request.form.get('fecha_expedicion')
        fecha_expedicion  = datetime.strptime(f_exp_str, "%Y-%m-%d") if f_exp_str else datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        existente = (
            supabase.table("folios_registrados")
            .select("*")
            .eq("folio", folio)
            .execute()
        )
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
            return render_template('registro_admin.html', datetime=datetime)

        data = {
            "folio":            folio,
            "marca":            marca,
            "linea":            linea,
            "anio":             anio,
            "numero_serie":     numero_serie,
            "numero_motor":     numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento":fecha_vencimiento.isoformat()
        }
        supabase.table("folios_registrados").insert(data).execute()
        generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente)
        return render_template("exitoso.html", folio=folio)

    return render_template('registro_admin.html', datetime=datetime)


# ── Admin: marcar usuario como pagado ──────────────────────────────────────

@app.route('/admin/usuarios')
def admin_usuarios():
    """Panel donde el admin ve todos los usuarios y puede marcarlos como pagado."""
    if 'admin' not in session:
        return redirect(url_for('login'))

    resp = supabase.table("verificaciondigitalcdmx").select("*").order("id", desc=True).execute()
    usuarios = resp.data if resp.data else []

    # Enriquecer con info del timer
    for u in usuarios:
        u['timer_info'] = get_timer_info(u)

    return render_template("admin_usuarios.html", usuarios=usuarios)


@app.route('/admin/marcar_pagado/<int:user_id>', methods=['POST'])
def marcar_pagado(user_id):
    """Marca un usuario como pagado → desactiva su timer."""
    if 'admin' not in session:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": True}) \
        .eq("id", user_id) \
        .execute()
    flash("Usuario marcado como PAGADO. Timer desactivado.", "success")
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/marcar_pendiente/<int:user_id>', methods=['POST'])
def marcar_pendiente(user_id):
    """Revierte a pendiente (reinicia el timer desde ahora)."""
    if 'admin' not in session:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    # Actualizamos created_at a ahora para que el timer arranque de cero
    supabase.table("verificaciondigitalcdmx") \
        .update({"pagado": False, "created_at": datetime.utcnow().isoformat()}) \
        .eq("id", user_id) \
        .execute()
    flash("Usuario marcado como PENDIENTE. Timer reiniciado.", "warning")
    return redirect(url_for('admin_usuarios'))


# ── API: estado del timer (llamada por JS del usuario) ─────────────────────

@app.route('/api/timer_estado')
def api_timer_estado():
    """El frontend del usuario llama esto para saber cuánto tiempo queda."""
    if 'user_id' not in session:
        return jsonify({"error": "no auth"}), 401

    resp = (
        supabase.table("verificaciondigitalcdmx")
        .select("pagado, created_at, folios_asignac, folios_usados")
        .eq("id", session['user_id'])
        .execute()
    )
    if not resp.data:
        return jsonify({"error": "usuario no encontrado"}), 404

    usuario = resp.data[0]
    timer   = get_timer_info(usuario)
    pct     = 0
    asig    = usuario.get("folios_asignac", 0)
    usados  = usuario.get("folios_usados",  0)
    if asig > 0:
        pct = round((usados / asig) * 100)

    return jsonify({
        "pagado":  usuario.get("pagado", False),
        "timer":   timer,
        "porcentaje": pct
    })


# ─── USUARIO (3RO) ──────────────────────────────────────────────────────────

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        folio_manual  = request.form.get('folio', '').strip().upper()
        folio         = folio_manual if folio_manual else generar_folio_automatico()
        marca         = request.form['marca']
        linea         = request.form['linea']
        anio          = request.form['anio']
        numero_serie  = request.form['serie']
        numero_motor  = request.form['motor']
        vigencia      = int(request.form['vigencia'])
        contribuyente = request.form['contribuyente']

        # Fecha desde el calendario (retroactivo/futuro)
        f_exp_str = request.form.get('fecha_expedicion')
        fecha_expedicion  = datetime.strptime(f_exp_str, "%Y-%m-%d") if f_exp_str else datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        existente = (
            supabase.table("folios_registrados")
            .select("*")
            .eq("folio", folio)
            .execute()
        )
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
            return redirect(url_for('registro_usuario'))

        usuario_data = (
            supabase.table("verificaciondigitalcdmx")
            .select("folios_asignac, folios_usados")
            .eq("id", user_id)
            .execute()
        )
        if not usuario_data.data:
            flash("No se pudo obtener la información del usuario.", "error")
            return redirect(url_for('registro_usuario'))

        folios    = usuario_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles para registrar.", "error")
            return redirect(url_for('registro_usuario'))

        data = {
            "folio":             folio,
            "marca":             marca,
            "linea":             linea,
            "anio":              anio,
            "numero_serie":      numero_serie,
            "numero_motor":      numero_motor,
            "fecha_expedicion":  fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "user_id":           user_id        # <── para filtrar en "mis permisos"
        }
        supabase.table("folios_registrados").insert(data).execute()
        supabase.table("verificaciondigitalcdmx") \
            .update({"folios_usados": folios["folios_usados"] + 1}) \
            .eq("id", user_id) \
            .execute()

        generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente)
        return render_template("exitoso.html", folio=folio)

    # GET ─ datos del usuario para mostrar porcentaje + timer
    response = (
        supabase.table("verificaciondigitalcdmx")
        .select("folios_asignac, folios_usados, pagado, created_at")
        .eq("id", user_id)
        .execute()
    )
    folios_info = response.data[0] if response.data else {}
    timer_info  = get_timer_info(folios_info)

    asig   = folios_info.get("folios_asignac", 0)
    usados = folios_info.get("folios_usados",  0)
    pct    = round((usados / asig) * 100) if asig > 0 else 0

    return render_template(
        "registro_usuario.html",
        folios_info=folios_info,
        timer_info=timer_info,
        porcentaje=pct,
        fecha_hoy=datetime.now().strftime("%Y-%m-%d")
    )


@app.route('/mis_permisos')
def mis_permisos():
    """El usuario 3ro ve y descarga solo sus propios permisos."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    resp = (
        supabase.table("folios_registrados")
        .select("*")
        .eq("user_id", user_id)
        .order("fecha_expedicion", desc=True)
        .execute()
    )
    registros = resp.data if resp.data else []

    # Marcar cuáles tienen PDF generado
    for r in registros:
        r['tiene_pdf'] = os.path.exists(f"static/pdfs/{r['folio']}.pdf")

    return render_template("mis_permisos.html", registros=registros)


# ─── CONSULTA PÚBLICA ───────────────────────────────────────────────────────

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        resp  = (
            supabase.table("folios_registrados")
            .select("*")
            .eq("folio", folio)
            .execute()
        )
        registros = resp.data
        if not registros:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = registros[0]
            fe       = datetime.fromisoformat(registro['fecha_expedicion'])
            fv       = datetime.fromisoformat(registro['fecha_vencimiento'])
            estado   = "VIGENTE" if datetime.now() <= fv else "VENCIDO"
            resultado = {
                "estado":           estado,
                "folio":            folio,
                "fecha_expedicion": fe.strftime("%d/%m/%Y"),
                "fecha_vencimiento":fv.strftime("%d/%m/%Y"),
                "marca":            registro['marca'],
                "linea":            registro['linea'],
                "año":              registro['anio'],
                "numero_serie":     registro['numero_serie'],
                "numero_motor":     registro['numero_motor']
            }
        return render_template("resultado_consulta.html", resultado=resultado)

    return render_template("consulta_folio.html")


@app.route('/consulta/<folio>')
def consulta_qr_guerrero(folio):
    folio = folio.strip().upper()
    resp  = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()

    if not resp.data:
        resultado = {"estado": "No encontrado", "folio": folio}
    else:
        registro = resp.data[0]
        fe       = datetime.fromisoformat(registro['fecha_expedicion'])
        fv       = datetime.fromisoformat(registro['fecha_vencimiento'])
        estado   = "VIGENTE" if datetime.now() <= fv else "VENCIDO"
        resultado = {
            "estado":           estado,
            "folio":            folio,
            "fecha_expedicion": fe.strftime("%d/%m/%Y"),
            "fecha_vencimiento":fv.strftime("%d/%m/%Y"),
            "marca":            registro['marca'],
            "linea":            registro['linea'],
            "año":              registro['anio'],
            "numero_serie":     registro['numero_serie'],
            "numero_motor":     registro['numero_motor']
        }

    return render_template("resultado_consulta.html", resultado=resultado)


# ─── DESCARGAS ──────────────────────────────────────────────────────────────

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    # Admin descarga cualquiera; usuario solo los suyos
    if 'admin' not in session and 'user_id' not in session:
        return redirect(url_for('login'))

    if 'user_id' in session:
        # Verificar que el folio pertenece a este usuario
        resp = (
            supabase.table("folios_registrados")
            .select("user_id")
            .eq("folio", folio)
            .execute()
        )
        if resp.data and resp.data[0].get("user_id") != session['user_id']:
            flash("No tienes permiso para descargar este permiso.", "error")
            return redirect(url_for('mis_permisos'))

    ruta_archivo = f"static/pdfs/{folio}.pdf"
    if os.path.exists(ruta_archivo):
        return send_from_directory("static/pdfs", f"{folio}.pdf", as_attachment=True)
    flash("El archivo PDF no existe.", "error")
    return redirect(url_for("ver_registros") if 'admin' in session else url_for("mis_permisos"))


@app.route('/descargar_constancia/<folio>')
def descargar_constancia(folio):
    fol_comp = f"AB{int(folio):05d}"
    filename = f"{fol_comp}_constancia.pdf"
    ruta     = os.path.join("static", "constancias", filename)
    if os.path.exists(ruta):
        return send_from_directory("static/constancias", filename, as_attachment=True)
    flash("La constancia no existe.", "error")
    return redirect(url_for('consulta_folio'))


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True)
