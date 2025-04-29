from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import os
import fitz  # PyMuPDF
import qrcode

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OUTPUT_DIR = 'static/pdfs'
PLANTILLA_PDF = 'Recibo_Permiso_Guerrero.pdf'
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
    nuevo_folio = f"{letras}{numeros:04d}"
    with open(FOLIO_FILE, 'w') as f:
        f.write(nuevo_folio)
    return nuevo_folio

def incrementar_letras(letras):
    l1, l2 = letras
    if l2 != 'Z':
        l2 = chr(ord(l2) + 1)
    else:
        l2 = 'A'
        if l1 != 'Z':
            l1 = chr(ord(l1) + 1)
        else:
            l1 = 'A'
    return l1 + l2

def generar_pdf_con_qr(folio, contribuyente, fecha_expedicion, fecha_vencimiento, marca, linea, anio, serie, motor, color):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    doc = fitz.open(PLANTILLA_PDF)
    page = doc[0]

    page.insert_text((250, 270), f"{fecha_expedicion}", fontsize=12)
    page.insert_text((250, 305), f"{fecha_vencimiento}", fontsize=12)
    page.insert_text((250, 340), f"{folio}", fontsize=12)
    page.insert_text((250, 375), f"{contribuyente}", fontsize=12)

    datos_qr = (
        f"[PERMISO-AUTÉNTICO] [MUNICIPIO-TLAPA DE COMONFORT-GRO]\n"
        f"FOLIO: {folio}\n"
        f"CONTRIBUYENTE: {contribuyente}\n"
        f"MARCA: {marca}\n"
        f"LÍNEA: {linea}\n"
        f"AÑO: {anio}\n"
        f"SERIE: {serie}\n"
        f"MOTOR: {motor}\n"
        f"COLOR: {color}\n"
        f"VIGENCIA: {fecha_expedicion} AL {fecha_vencimiento}"
    )
    qr = qrcode.make(datos_qr)
    qr_path = os.path.join(OUTPUT_DIR, f"{folio}_qr.png")
    qr.save(qr_path)

    rect = fitz.Rect(70, 450, 220, 600)
    page.insert_image(rect, filename=qr_path)

    pdf_path = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    doc.save(pdf_path)
    doc.close()

@app.route('/')
def inicio():
    return redirect(url_for('formulario'))

@app.route('/formulario', methods=['GET', 'POST'])
def formulario():
    if request.method == 'POST':
        folio_actual = cargar_folio()
        folio_generado = siguiente_folio(folio_actual)

        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        serie = request.form['serie']
        motor = request.form['motor']
        color = request.form['color']
        contribuyente = request.form['contribuyente']

        fecha_expedicion = datetime.now().strftime("%d/%m/%Y")
        fecha_vencimiento = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")

        data = {
            "folio": folio_generado,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": serie,
            "numero_motor": motor,
            "color": color,
            "nombre_contribuyente": contribuyente,
            "fecha_expedicion": fecha_expedicion,
            "fecha_vencimiento": fecha_vencimiento
        }

        supabase.table("permisos_guerrero").insert(data).execute()
        generar_pdf_con_qr(folio_generado, contribuyente, fecha_expedicion, fecha_vencimiento, marca, linea, anio, serie, motor, color)

        return redirect(url_for('exito', folio=folio_generado))

    return render_template('registro_folio.html')

@app.route('/exito/<folio>')
def exito(folio):
    return render_template('exitoso.html', folio=folio)

@app.route('/descargar/<folio>')
def descargar(folio):
    path = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    return send_file(path, as_attachment=True)

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        response = supabase.table("permisos_guerrero").select("*").eq("folio", folio).execute()
        registros = response.data

        if not registros:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = registros[0]
            fecha_exp = datetime.strptime(registro['fecha_expedicion'], "%d/%m/%Y")
            fecha_ven = datetime.strptime(registro['fecha_vencimiento'], "%d/%m/%Y")
            estado = "VIGENTE" if datetime.now() <= fecha_ven else "VENCIDO"

            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": registro['fecha_expedicion'],
                "fecha_vencimiento": registro['fecha_vencimiento'],
                "marca": registro['marca'],
                "linea": registro['linea'],
                "anio": registro['anio'],
                "numero_serie": registro['numero_serie'],
                "numero_motor": registro['numero_motor'],
                "color": registro['color'],
                "nombre_contribuyente": registro['nombre_contribuyente']
            }
        return render_template("resultado_consulta.html", resultado=resultado)

    return render_template("consulta_folio.html")

if __name__ == '__main__':
    app.run(debug=True)
