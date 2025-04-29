from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import qrcode
import io
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

# Datos de tu Supabase
SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ruta a tu plantilla
PDF_FOLDER = 'static/pdf'
TEMPLATE_PDF = 'static/pdf/recibo_permiso_guerrero_img.pdf'

def generar_qr(datos):
    contenido = "[PERMISO-AUTÉNTICO] [MUNICIPIO-TLAPA DE COMONFORT-GRO]\n"
    for clave, valor in datos.items():
        contenido += f"{clave.upper()}: {valor}\n"
    qr = qrcode.make(contenido)
    buf = io.BytesIO()
    qr.save(buf)
    buf.seek(0)
    return buf

def generar_pdf(datos):
    ruta_pdf = os.path.join(PDF_FOLDER, f"{datos['folio']}.pdf")
    os.makedirs(PDF_FOLDER, exist_ok=True)
    doc = fitz.open(TEMPLATE_PDF)
    page = doc[0]

    # Ajusta las coordenadas según tu plantilla
    page.insert_text((135, 525), datos['folio'], fontsize=10)
    page.insert_text((135, 565), datos['contribuyente'], fontsize=10)
    page.insert_text((135, 605), datos['fecha_expedicion'], fontsize=10)
    page.insert_text((135, 645), datos['fecha_vencimiento'], fontsize=10)

    qr_img = generar_qr(datos)
    qr_pix = fitz.Pixmap(fitz.csRGB, fitz.open("png", qr_img).get_page_pixmap(0))
    page.insert_image(fitz.Rect(135, 685, 285, 835), pixmap=qr_pix)

    doc.save(ruta_pdf)
    doc.close()

@app.route('/', methods=['GET', 'POST'])
def formulario():
    if request.method == 'POST':
        folio = request.form['folio'].upper()
        fecha_expedicion = datetime.now()
        dias_vigencia = int(request.form['vigencia'])
        fecha_vencimiento = fecha_expedicion + timedelta(days=dias_vigencia)

        datos = {
            "folio": folio,
            "marca": request.form['marca'].upper(),
            "linea": request.form['linea'].upper(),
            "anio": request.form['anio'].upper(),
            "serie": request.form['serie'].upper(),
            "motor": request.form['motor'].upper(),
            "color": request.form['color'].upper(),
            "contribuyente": request.form['contribuyente'].upper(),
            "vigencia": f"{dias_vigencia} días",
            "fecha_expedicion": fecha_expedicion.strftime("%Y-%m-%d"),
            "fecha_vencimiento": fecha_vencimiento.strftime("%Y-%m-%d"),
        }

        try:
            supabase.table('permisos_guerrero').insert(datos).execute()
        except Exception as e:
            flash(f"Error al guardar en Supabase: {e}", "error")
            return redirect(url_for('formulario'))

        generar_pdf(datos)
        return render_template("exitoso.html", folio=folio)

    return render_template("registro_folio.html")

@app.route('/descargar/<folio>')
def descargar(folio):
    ruta = os.path.join(PDF_FOLDER, f"{folio}.pdf")
    if os.path.exists(ruta):
        return send_file(ruta, as_attachment=True)
    else:
        flash("Archivo no encontrado.", "error")
        return redirect(url_for('formulario'))

if __name__ == '__main__':
    app.run(debug=True)
