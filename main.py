from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PDF_FOLDER = 'static/pdfs'
PLANTILLA_PDF = 'recibo_permiso_guerrero_img.pdf'

def generar_pdf(folio, contribuyente, fecha_expedicion, fecha_vencimiento):
    try:
        os.makedirs(PDF_FOLDER, exist_ok=True)
        ruta_pdf = os.path.join(PDF_FOLDER, f"{folio}.pdf")
        doc = fitz.open(PLANTILLA_PDF)
        page = doc[0]

        # Coordenadas bien posicionadas
        page.insert_text((150, 480), folio, fontsize=12, fontname="helv", color=(0, 0, 0))
        page.insert_text((150, 520), contribuyente, fontsize=12, fontname="helv", color=(0, 0, 0))
        page.insert_text((150, 560), fecha_expedicion.strftime('%d/%m/%Y'), fontsize=12, fontname="helv", color=(0, 0, 0))
        page.insert_text((150, 600), fecha_vencimiento.strftime('%d/%m/%Y'), fontsize=12, fontname="helv", color=(0, 0, 0))

        doc.save(ruta_pdf)
        doc.close()
        return True
    except Exception as e:
        print("Error al generar PDF:", e)
        return False

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))

        res = supabase.table("verificaciondigitalcdmx").select("*").eq("username", username).eq("password", password).execute()
        usuarios = res.data

        if usuarios:
            session['user_id'] = usuarios[0]['id']
            session['username'] = usuarios[0]['username']
            return redirect(url_for('registro_usuario'))
        else:
            flash("Credenciales incorrectas", "error")

    return render_template("login.html")

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template("panel.html")

@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios = int(request.form['folios'])

        existe = supabase.table("verificaciondigitalcdmx").select("id").eq("username", username).execute()
        if existe.data:
            flash("El usuario ya existe", "error")
            return render_template("crear_usuario.html")

        supabase.table("verificaciondigitalcdmx").insert({
            "username": username,
            "password": password,
            "folios_asignac": folios,
            "folios_usados": 0
        }).execute()
        flash("Usuario creado correctamente", "success")

    return render_template("crear_usuario.html")

@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        serie = request.form['serie']
        motor = request.form['motor']
        color = request.form['color']
        contribuyente = request.form['contribuyente']
        vigencia = int(request.form['vigencia'])

        existe = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if existe.data:
            flash("El folio ya existe", "error")
            return redirect(url_for('registro_usuario'))

        usuario = supabase.table("verificaciondigitalcdmx").select("folios_asignac", "folios_usados").eq("id", user_id).execute().data[0]
        if usuario['folios_usados'] >= usuario['folios_asignac']:
            flash("No tienes folios disponibles", "error")
            return redirect(url_for('registro_usuario'))

        fecha_exp = datetime.now()
        fecha_venc = fecha_exp + timedelta(days=vigencia)

        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": serie,
            "numero_motor": motor,
            "color": color,
            "contribuyente": contribuyente,
            "fecha_expedicion": fecha_exp.isoformat(),
            "fecha_vencimiento": fecha_venc.isoformat()
        }).execute()

        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": usuario['folios_usados'] + 1
        }).eq("id", user_id).execute()

        generar_pdf(folio, contribuyente, fecha_exp, fecha_venc)
        return render_template("exitoso.html", folio=folio)

    info = supabase.table("verificaciondigitalcdmx").select("folios_asignac", "folios_usados").eq("id", user_id).execute().data[0]
    return render_template("registro_usuario.html", folios_info=info)

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio']
        res = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        datos = res.data

        if not datos:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            reg = datos[0]
            exp = datetime.fromisoformat(reg['fecha_expedicion'])
            venc = datetime.fromisoformat(reg['fecha_vencimiento'])
            hoy = datetime.now()
            estado = "VIGENTE" if hoy <= venc else "VENCIDO"

            resultado = {
                "estado": estado,
                "folio": reg['folio'],
                "fecha_expedicion": exp.strftime("%d/%m/%Y"),
                "fecha_vencimiento": venc.strftime("%d/%m/%Y"),
                "marca": reg['marca'],
                "linea": reg['linea'],
                "año": reg['anio'],
                "numero_serie": reg['numero_serie'],
                "numero_motor": reg['numero_motor']
            }

    return render_template("resultado_consulta.html", resultado=resultado)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
