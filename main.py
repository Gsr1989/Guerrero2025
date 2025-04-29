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
TEMPLATE_PDF = 'Recibo_Permiso_Guerrero.pdf'
FOLIO_FILE = 'folio_actual.txt'

def cargar_folio():
    if not os.path.exists(FOLIO_FILE):
        with open(FOLIO_FILE, 'w') as f:
            f.write('AC0000')
    with open(FOLIO_FILE, 'r') as f:
        return f.read().strip()

def siguiente_folio(folio_actual):
    letras = folio_actual[:2]
    numeros = int(folio_actual[2:])
    if numeros < 9999:
        numeros += 1
    else:
        letras = incrementar_letras(letras)
        numeros = 1
    nuevo = f"{letras}{numeros:04d}"
    with open(FOLIO_FILE, 'w') as f:
        f.write(nuevo)
    return nuevo

def incrementar_letras(letras):
    a, b = letras
    if b != 'Z':
        b = chr(ord(b) + 1)
    else:
        b = 'A'
        if a != 'Z':
            a = chr(ord(a) + 1)
        else:
            a = 'A'
    return a + b

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
    ruta_pdf = os.path.join(PDF_FOLDER, f"{datos['folio']}.pdf")
    os.makedirs(PDF_FOLDER, exist_ok=True)
    doc = fitz.open(TEMPLATE_PDF)
    page = doc[0]

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
        folio_actual = cargar_folio()
        folio = siguiente_folio(folio_actual)
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
            "fecha_vencimiento": fecha_vencimiento.strftime("%d/%m/%Y"),
            "vigencia": request.form['vigencia']
        }

        existe = supabase.table("permisos_guerrero").select("*").eq("folio", folio).execute()
        if existe.data:
            flash("Este folio ya existe.", "error")
            return redirect(url_for('formulario'))

        supabase.table("permisos_guerrero").insert(datos).execute()
        generar_pdf(datos)

        return render_template("exitoso.html", folio=folio)

    return render_template("formulario.html")

@app.route('/descargar/<folio>')
def descargar(folio):
    ruta = os.path.join(PDF_FOLDER, f"{folio}.pdf")
    if os.path.exists(ruta):
        return send_file(ruta, as_attachment=True)
    else:
        flash("Archivo no encontrado.", "error")
        return redirect(url_for('formulario'))

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].upper()
        datos = supabase.table("permisos_guerrero").select("*").eq("folio", folio).execute()
        if not datos.data:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = datos.data[0]
            hoy = datetime.now()
            vencimiento = datetime.strptime(registro['fecha_vencimiento'], "%d/%m/%Y")
            estado = "VIGENTE" if hoy <= vencimiento else "VENCIDO"
            resultado = {
                "estado": estado,
                "folio": registro['folio'],
                "fecha_expedicion": registro['fecha_expedicion'],
                "fecha_vencimiento": registro['fecha_vencimiento'],
                "marca": registro['marca'],
                "linea": registro['linea'],
                "año": registro['anio'],
                "color": registro['color'],
                "numero_serie": registro['serie'],
                "numero_motor": registro['motor']
            }

    return render_template("resultado_consulta.html", resultado=resultado)

if __name__ == '__main__':
    app.run(debug=True)
