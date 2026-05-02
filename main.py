from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import qrcode
from io import BytesIO
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

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PLANTILLA_PERMISO = "Guerrero.pdf"
PLANTILLA_RECIBO  = "recibo_permiso_guerrero_img.pdf"
OUTPUT_DIR        = "static/pdfs"
URL_CONSULTA_BASE = "https://tlapadecomonfortexpediciondepermisosgob2-k6u7.onrender.com"
RFC_FIJO          = "XAXX010101000"
DOMICILIO_FIJO    = "MEXICO"
COSTO_FIJO        = "250"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── COORDENADAS (extraídas del bot) ─────────────────────────────────────────
coords_guerrero = {
    "folio":         (360, 769,  8, (1,0,0)),
    "fecha_exp":     (135, 755,  8, (0,0,0)),
    "fecha_ven":     (135, 768,  8, (0,0,0)),
    "serie":         (360, 742,  8, (0,0,0)),
    "motor":         (360, 729,  8, (0,0,0)),
    "marca":         (360, 700,  8, (0,0,0)),
    "linea":         (360, 714,  8, (0,0,0)),
    "color":         (360, 756,  8, (0,0,0)),
    "nombre":        (135, 700,  8, (0,0,0)),
    "rfc":           (135, 713,  8, (0,0,0)),
    "domicilio":     (135, 726,  8, (0,0,0)),
    "costo":         (135, 742,  8, (0,0,0)),
    "rot_folio":     (440, 200, 83, (0,0,0)),
    "rot_fecha_exp": ( 77, 205,  8, (0,0,0)),
    "rot_fecha_ven": ( 63, 205,  8, (0,0,0)),
    "rot_serie":     (168, 110, 18, (0,0,0)),
    "rot_motor":     (224, 110, 18, (0,0,0)),
    "rot_marca":     (280, 110, 18, (0,0,0)),
    "rot_linea":     (280, 290, 18, (0,0,0)),
    "rot_anio":      (305, 520, 18, (0,0,0)),
    "rot_color":     (224, 420, 18, (0,0,0)),
    "rot_nombre":    (115, 205,  8, (0,0,0)),
    "rot_rfc":       (102, 205,  8, (0,0,0)),
    "rot_domicilio": ( 89, 205,  8, (0,0,0)),
}

coords_recibo = {
    "folio":     ( 85, 210, 12, (0,0,0)),
    "nombre":    (117, 231, 12, (0,0,0)),
    "domicilio": (117, 255, 12, (0,0,0)),
    "costo":     (432, 352, 12, (0,0,0)),
    "fecha_exp": (265, 210, 12, (0,0,0)),
    "fecha_ven": (480, 210, 12, (0,0,0)),
    "qr":        ( 55, 307, 110, None),
}

# ─── HELPERS PDF ─────────────────────────────────────────────────────────────

def _make_qr_pixmap(folio: str):
    url = f"{URL_CONSULTA_BASE}/consulta/{folio}"
    qr  = qrcode.QRCode(version=2,
                        error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=4, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return fitz.Pixmap(buf.read())


def _generar_pdf_permiso(datos: dict) -> str:
    fol  = datos["folio"]
    path = f"{OUTPUT_DIR}/{fol}_permiso_tmp.pdf"
    try:
        doc = fitz.open(PLANTILLA_PERMISO)
        pg  = doc[0]

        # Campos normales
        for campo in ["folio","fecha_exp","fecha_ven","serie","motor",
                      "marca","linea","color","nombre"]:
            if campo in coords_guerrero and campo in datos:
                x, y, s, col = coords_guerrero[campo]
                pg.insert_text((x, y), str(datos[campo]), fontsize=s, color=col)

        for campo in ["rfc", "domicilio"]:
            x, y, s, col = coords_guerrero[campo]
            pg.insert_text((x, y), datos[campo], fontsize=s, color=col)

        x, y, s, col = coords_guerrero["costo"]
        pg.insert_text((x, y), f"${datos['costo']}", fontsize=s, color=col)

        # Campos rotados
        rot_campos = [
            ("rot_folio",     fol),
            ("rot_fecha_exp", datos["fecha_exp"]),
            ("rot_fecha_ven", datos["fecha_ven"]),
            ("rot_serie",     datos["serie"]),
            ("rot_motor",     datos["motor"]),
            ("rot_marca",     datos["marca"]),
            ("rot_linea",     datos["linea"]),
            ("rot_anio",      datos["anio"]),
            ("rot_color",     datos["color"]),
            ("rot_nombre",    datos["nombre"]),
            ("rot_rfc",       datos["rfc"]),
            ("rot_domicilio", datos["domicilio"]),
        ]
        for k, val in rot_campos:
            x, y, s, _ = coords_guerrero[k]
            pg.insert_text((x, y), val, fontsize=s, rotate=270)

        # QR
        qr_pix = _make_qr_pixmap(fol)
        pg.insert_image(fitz.Rect(80, 460, 80+97, 460+97), pixmap=qr_pix, overlay=True)

        doc.save(path)
        doc.close()
        return path
    except Exception as e:
        print(f"[ERROR PERMISO] {e}")
        return ""


def _generar_pdf_recibo(datos: dict) -> str:
    fol  = datos["folio"]
    path = f"{OUTPUT_DIR}/{fol}_recibo_tmp.pdf"
    try:
        doc  = fitz.open(PLANTILLA_RECIBO)
        page = doc[0]
        page.wrap_contents()

        for campo in ["folio","nombre","domicilio","fecha_exp","fecha_ven"]:
            if campo in coords_recibo:
                x, y, size, color = coords_recibo[campo]
                page.insert_text((x, y), str(datos.get(campo, "")),
                                 fontsize=size, color=color, fontname="helv")

        # Costo con signo $
        x, y, size, color = coords_recibo["costo"]
        page.insert_text((x, y), f"${datos.get('costo', '')}",
                         fontsize=size, color=color, fontname="helv")

        # QR
        x, y, size, _ = coords_recibo["qr"]
        qr_pix = _make_qr_pixmap(fol)
        page.insert_image(fitz.Rect(x, y, x+size, y+size), pixmap=qr_pix)

        doc.save(path)
        doc.close()
        return path
    except Exception as e:
        print(f"[ERROR RECIBO] {e}")
        return ""


def generar_pdf_unificado(datos: dict) -> str:
    """Genera permiso + recibo en un solo PDF. Devuelve la ruta final."""
    fol   = datos["folio"]
    final = f"{OUTPUT_DIR}/{fol}.pdf"
    try:
        p1 = _generar_pdf_permiso(datos)
        p2 = _generar_pdf_recibo(datos)

        if not p1 or not p2:
            fallback = p1 or p2
            if fallback and fallback != final:
                import shutil
                shutil.copy(fallback, final)
            return final if os.path.exists(final) else ""

        d1 = fitz.open(p1)
        d2 = fitz.open(p2)
        d1.insert_pdf(d2)
        d1.save(final)
        d1.close()
        d2.close()

        for tmp in [p1, p2]:
            try:
                os.remove(tmp)
            except Exception:
                pass

        print(f"[PDF] Unificado OK: {final}")
        return final
    except Exception as e:
        print(f"[ERROR UNIFICADO] {e}")
        return ""


# ─── FOLIO AUTOMÁTICO ────────────────────────────────────────────────────────

def generar_folio_automatico():
    prefijo = "ZY"
    try:
        resp   = supabase.table("folios_registrados").select("folio") \
                         .ilike("folio", f"{prefijo}%").execute()
        usados = {r["folio"] for r in resp.data} if resp.data else set()
    except Exception as e:
        print(f"Error obteniendo folios: {e}")
        usados = set()

    for i in range(4917, 9999):
        f = f"{prefijo}{str(i).zfill(4)}"
        if f not in usados:
            return f
    return f"{prefijo}9999"


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
            "limite_iso":        limite.isoformat(),
            "segundos_restantes": max(0, int(restante)),
            "vencido":           restante <= 0
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

        response = (
            supabase.table("verificaciondigitalcdmx")
            .select("*")
            .eq("username", username)
            .eq("password", password)
            .execute()
        )
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

        existe = (
            supabase.table("verificaciondigitalcdmx")
            .select("id").eq("username", username).execute()
        )
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


@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio_manual  = request.form.get('folio', '').strip().upper()
        folio         = folio_manual if folio_manual else generar_folio_automatico()
        marca         = request.form['marca'].upper()
        linea         = request.form['linea'].upper()
        anio          = request.form['anio']
        numero_serie  = request.form['serie'].upper()
        numero_motor  = request.form['motor'].upper()
        color         = request.form.get('color', 'N/D').upper()
        contribuyente = request.form['contribuyente'].upper()
        vigencia      = int(request.form['vigencia'])

        f_exp_str         = request.form.get('fecha_expedicion')
        fecha_expedicion  = datetime.strptime(f_exp_str, "%Y-%m-%d") if f_exp_str else datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        existente = (
            supabase.table("folios_registrados")
            .select("folio").eq("folio", folio).execute()
        )
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
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

        supabase.table("folios_registrados").insert({
            "folio":             folio,
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
        }).execute()

        generar_pdf_unificado(datos_pdf)
        return render_template("exitoso.html", folio=folio)

    return render_template('registro_admin.html', datetime=datetime)


@app.route('/ver_registros')
def ver_registros():
    if 'admin' not in session:
        return redirect(url_for('login'))
    resp = (
        supabase.table("folios_registrados")
        .select("*").order("fecha_expedicion", desc=True).execute()
    )
    return render_template("mis_registros.html", registros=resp.data or [])


@app.route('/ver_registros_admin')
def ver_registros_admin():
    return ver_registros()


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
    resp = (
        supabase.table("verificaciondigitalcdmx")
        .select("pagado, created_at, folios_asignac, folios_usados")
        .eq("id", session['user_id']).execute()
    )
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
        folio         = folio_manual if folio_manual else generar_folio_automatico()
        marca         = request.form['marca'].upper()
        linea         = request.form['linea'].upper()
        anio          = request.form['anio']
        numero_serie  = request.form['serie'].upper()
        numero_motor  = request.form['motor'].upper()
        color         = request.form.get('color', 'N/D').upper()
        contribuyente = request.form['contribuyente'].upper()
        vigencia      = int(request.form['vigencia'])

        f_exp_str         = request.form.get('fecha_expedicion')
        fecha_expedicion  = datetime.strptime(f_exp_str, "%Y-%m-%d") if f_exp_str else datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        # Verificar folio duplicado
        existente = (
            supabase.table("folios_registrados")
            .select("folio").eq("folio", folio).execute()
        )
        if existente.data:
            flash('Error: el folio ya existe.', 'error')
            return redirect(url_for('registro_usuario'))

        # Verificar folios disponibles
        u_data = (
            supabase.table("verificaciondigitalcdmx")
            .select("folios_asignac, folios_usados")
            .eq("id", user_id).execute()
        )
        if not u_data.data:
            flash("Error obteniendo datos del usuario.", "error")
            return redirect(url_for('registro_usuario'))

        folios    = u_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles.", "error")
            return redirect(url_for('registro_usuario'))

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

        # Guardar en Supabase
        supabase.table("folios_registrados").insert({
            "folio":             folio,
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
            "user_id":           user_id,
        }).execute()

        # Descontar folio
        supabase.table("verificaciondigitalcdmx") \
            .update({"folios_usados": folios["folios_usados"] + 1}) \
            .eq("id", user_id).execute()

        # Generar PDF unificado
        generar_pdf_unificado(datos_pdf)

        return render_template("exitoso.html", folio=folio)

    # ── GET ──────────────────────────────────────────────────────────────────
    try:
        response    = (
            supabase.table("verificaciondigitalcdmx")
            .select("folios_asignac, folios_usados, pagado, created_at")
            .eq("id", user_id).execute()
        )
        folios_info = response.data[0] if response.data else {}
    except Exception:
        response    = (
            supabase.table("verificaciondigitalcdmx")
            .select("folios_asignac, folios_usados")
            .eq("id", user_id).execute()
        )
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
        fecha_hoy=datetime.now().strftime("%Y-%m-%d")
    )


@app.route('/mis_permisos')
def mis_permisos():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    resp    = (
        supabase.table("folios_registrados")
        .select("*").eq("user_id", user_id)
        .order("fecha_expedicion", desc=True).execute()
    )
    registros = resp.data or []
    for r in registros:
        r['tiene_pdf'] = os.path.exists(f"{OUTPUT_DIR}/{r['folio']}.pdf")
    return render_template("mis_permisos.html", registros=registros)


# ─── CONSULTA PÚBLICA ────────────────────────────────────────────────────────

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        resp  = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if not resp.data:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = resp.data[0]
            fe = datetime.fromisoformat(registro['fecha_expedicion'])
            fv = datetime.fromisoformat(registro['fecha_vencimiento'])
            resultado = {
                "estado":           "VIGENTE" if datetime.now() <= fv else "VENCIDO",
                "folio":            folio,
                "fecha_expedicion": fe.strftime("%d/%m/%Y"),
                "fecha_vencimiento":fv.strftime("%d/%m/%Y"),
                "marca":            registro.get('marca',''),
                "linea":            registro.get('linea',''),
                "año":              registro.get('anio',''),
                "numero_serie":     registro.get('numero_serie',''),
                "numero_motor":     registro.get('numero_motor',''),
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
        fe = datetime.fromisoformat(registro['fecha_expedicion'])
        fv = datetime.fromisoformat(registro['fecha_vencimiento'])
        resultado = {
            "estado":           "VIGENTE" if datetime.now() <= fv else "VENCIDO",
            "folio":            folio,
            "fecha_expedicion": fe.strftime("%d/%m/%Y"),
            "fecha_vencimiento":fv.strftime("%d/%m/%Y"),
            "marca":            registro.get('marca',''),
            "linea":            registro.get('linea',''),
            "año":              registro.get('anio',''),
            "numero_serie":     registro.get('numero_serie',''),
            "numero_motor":     registro.get('numero_motor',''),
        }
    return render_template("resultado_consulta.html", resultado=resultado)


# ─── DESCARGAS ───────────────────────────────────────────────────────────────

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    if 'admin' not in session and 'user_id' not in session:
        return redirect(url_for('login'))

    if 'user_id' in session:
        resp = supabase.table("folios_registrados") \
                       .select("user_id").eq("folio", folio).execute()
        if resp.data and resp.data[0].get("user_id") != session['user_id']:
            flash("No tienes permiso para descargar este archivo.", "error")
            return redirect(url_for('mis_permisos'))

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

if __name__ == '__main__':
    app.run(debug=True)
