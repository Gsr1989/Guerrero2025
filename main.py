from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import qrcode
import io
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PDF_FOLDER = 'static/pdfs'
TEMPLATE_PDF = 'static/pdf/recibo_permiso_guerrero_img.pdf'

def generar_qr(data_dict):
    contenido = "[PERMISO-AUTÉNTICO] [MUNICIPIO-TLAPA DE COMONFORT-GRO]\n"
    for clave, valor in data_dict.items():
        contenido += f"{clave.upper()}: {valor}\n"
    qr = qrcode.make(contenido)
    buf = io.BytesIO()
    qr.save(buf)
    buf.seek(0)
    return buf

def generar_pdf(datos):
    os.makedirs(PDF_FOLDER, exist_ok=True)
    ruta_pdf = os.path.join(PDF_FOLDER, f"{datos['folio']}.pdf")
    doc = fitz.open(TEMPLATE_PDF)
    page = doc[0]

    # Coordenadas ajustadas a tu plantilla
    page.insert_text((87, 662), datos['folio'], fontsize=12, color=(1, 0, 0))
    page.insert_text((147, 650), datos['fecha_expedicion'], fontsize=12)
    page.insert_text((97, 365), datos['marca'], fontsize=12)
    page.insert_text((455, 365), datos['serie'], fontsize=12)
    page.insert_text((97, 358), datos['linea'], fontsize=12)
    page.insert_text((430, 358), datos['motor'], fontsize=12)
    page.insert_text((97, 333), datos['anio'], fontsize=12)
    page.insert_text((455, 333), datos['vigencia'] + " días", fontsize=12)
    page.insert_text((451, 326), datos['contribuyente'], fontsize=8)

    # QR insertado
    qr_img = generar_qr(datos)
    qr_pix = fitz.Pixmap(fitz.csRGB, fitz.open("png", qr_img).get_page_pixmap(0))
    page.insert_image(fitz.Rect(87, 680, 187, 780), pixmap=qr_pix)

    doc.save(ruta_pdf)
    doc.close()

@app.route('/', methods=['GET', 'POST'])
def formulario():
    if request.method == 'POST':
        folio = request.form['folio'].upper()
        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=int(request.form['vigencia']))

        datos = {
            "folio": folio,
            "marca": request.form['marca'].upper(),
            "linea": request.form['linea'].upper(),
            "anio": request.form['anio'].upper(),
            "serie": request.form['serie'].upper(),
            "motor": request.form['motor'].upper(),
            "color": request.form['color'].upper(),
            "contribuyente": request.form['contribuyente'].upper(),
            "fecha_expedicion": fecha_expedicion.strftime("%d/%m/%Y"),
            "fecha_vencimiento": fecha_vencimiento.strftime("%Y-%m-%d"),
            "vigencia": request.form['vigencia']
        }

        existe = supabase.table("permisos_guerrero").select("*").eq("folio", folio).execute()
        if existe.data:
            flash("Este folio ya existe, elige otro.", "error")
            return redirect(url_for('formulario'))

        supabase.table("permisos_guerrero").insert(datos).execute()
        generar_pdf(datos)

        return render_template("exitoso.html", folio=folio)

    return render_template("registro_folio.html")

@app.route('/descargar/<folio>')
def descargar(folio):
    ruta = os.path.join(PDF_FOLDER, f"{folio}.pdf")
    if os.path.exists(ruta):
        return send_file(ruta, as_attachment=True)
    else:
        flash("PDF no encontrado.", "error")
        return redirect(url_for('formulario'))

if __name__ == '__main__':
    app.run(debug=True)
