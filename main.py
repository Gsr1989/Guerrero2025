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

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'elwarrior' and password == 'Warrior2025':
            session['admin'] = True
            return redirect(url_for('panel'))
        flash("Credenciales incorrectas", "error")
    return render_template("inicio de sesión.html")

@app.route('/panel')
def panel():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template("panel.html")

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        folio = request.form['folio']
        contribuyente = request.form['contribuyente']
        vigencia = int(request.form['vigencia'])
        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)
        ruta_salida = f"static/pdfs/{folio}.pdf"

        data = {
            "folio": folio,
            "nombre_contribuyente": contribuyente,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }
        supabase.table("folios_registrados").insert(data).execute()

        plantilla = "recibo_permiso_guerrero_img.pdf"
        doc = fitz.open(plantilla)
        page = doc[0]
        page.insert_text((87, 662), folio, fontsize=12, fontname="helv", color=(1, 0, 0))
        page.insert_text((147, 650), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=12, fontname="helv", color=(0, 0, 0))
        page.insert_text((87, 638), fecha_vencimiento.strftime("%d/%m/%Y"), fontsize=12, fontname="helv", color=(0, 0, 0))
        page.insert_text((87, 626), contribuyente, fontsize=12, fontname="helv", color=(0, 0, 0))
        os.makedirs("static/pdfs", exist_ok=True)
        doc.save(ruta_salida)
        return render_template("exitoso.html", folio=folio)
    return render_template("registro_admin.html")

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio']
        response = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        if response.data:
            registro = response.data[0]
            estado = "VIGENTE" if datetime.now() <= datetime.fromisoformat(registro["fecha_vencimiento"]) else "VENCIDO"
            resultado = {
                "folio": folio,
                "estado": estado,
                "fecha_expedicion": registro["fecha_expedicion"][:10],
                "fecha_vencimiento": registro["fecha_vencimiento"][:10]
            }
        else:
            resultado = {"folio": folio, "estado": "NO SE ENCUENTRA REGISTRADO"}
    return render_template("resultado_consulta.html", resultado=resultado)

@app.route('/descargar/<folio>')
def descargar(folio):
    ruta_pdf = f"static/pdfs/{folio}.pdf"
    if os.path.exists(ruta_pdf):
        return send_file(ruta_pdf, as_attachment=True)
    else:
        return "PDF no encontrado", 404

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
