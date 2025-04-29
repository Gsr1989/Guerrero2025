from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import qrcode
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OUTPUT_DIR = 'static/pdfs'
TEMPLATE_FILE = 'recibo_permiso_guerrero_img.pdf'

def cargar_folio():
    if not os.path.exists('folio_actual.txt'):
        with open('folio_actual.txt', 'w') as f:
            f.write('AC0001')
    with open('folio_actual.txt', 'r') as f:
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
    with open('folio_actual.txt', 'w') as f:
        f.write(nuevo_folio)
    return nuevo_folio

def incrementar_letras(letras):
    letra1, letra2 = letras
    if letra2 != 'Z':
        letra2 = chr(ord(letra2) + 1)
    else:
        letra2 = 'A'
        if letra1 != 'Z':
            letra1 = chr(ord(letra1) + 1)
        else:
            letra1 = 'A'
    return letra1 + letra2

def generar_pdf(folio, contribuyente, fecha_expedicion, fecha_vencimiento, qr_data):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    doc = fitz.open(TEMPLATE_FILE)
    page = doc[0]

    page.insert_text((320, 300), fecha_expedicion.strftime("%d/%m/%Y"), fontsize=14)
    page.insert_text((320, 340), fecha_vencimiento.strftime("%d/%m/%Y"), fontsize=14)
    page.insert_text((320, 380), folio, fontsize=14)
    page.insert_text((320, 420), contribuyente, fontsize=14)

    qr = qrcode.make(qr_data)
    qr_path = os.path.join(OUTPUT_DIR, f"{folio}_qr.png")
    qr.save(qr_path)
    qr_image = fitz.Pixmap(qr_path)
    page.insert_image((60, 600, 260, 800), pixmap=qr_image)

    pdf_output = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    doc.save(pdf_output)
    doc.close()
    os.remove(qr_path)

def construir_qr_texto(folio, contribuyente, fecha_expedicion, fecha_vencimiento, marca, linea, anio, serie, motor, color):
    return (
        f"[PERMISO-AUTÉNTICO]\n"
        f"[MUNICIPIO-TLAPA DE COMONFORT-GRO]\n"
        f"Folio: {folio}\n"
        f"Contribuyente: {contribuyente}\n"
        f"Fecha Expedición: {fecha_expedicion.strftime('%d/%m/%Y')}\n"
        f"Fecha Vencimiento: {fecha_vencimiento.strftime('%d/%m/%Y')}\n"
        f"Marca: {marca}\n"
        f"Línea: {linea}\n"
        f"Año: {anio}\n"
        f"Número de Serie: {serie}\n"
        f"Número de Motor: {motor}\n"
        f"Color: {color}"
    )

@app.route('/')
def formulario():
    return render_template('registro_folio.html')

@app.route('/registrar', methods=['POST'])
def registrar():
    marca = request.form['marca'].upper()
    linea = request.form['linea'].upper()
    anio = request.form['anio'].upper()
    serie = request.form['serie'].upper()
    motor = request.form['motor'].upper()
    color = request.form['color'].upper()
    contribuyente = request.form['contribuyente'].upper()
    vigencia_dias = int(request.form['vigencia'])

    folio_actual = cargar_folio()
    folio_generado = siguiente_folio(folio_actual)

    fecha_expedicion = datetime.now()
    fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia_dias)

    data = {
        "folio": folio_generado,
        "marca": marca,
        "linea": linea,
        "anio": anio,
        "serie": serie,
        "motor": motor,
        "color": color,
        "contribuyente": contribuyente,
        "fecha_expedicion": fecha_expedicion.isoformat(),
        "fecha_vencimiento": fecha_vencimiento.isoformat(),
    }

    supabase.table("permisos_guerrero").insert(data).execute()

    qr_texto = construir_qr_texto(folio_generado, contribuyente, fecha_expedicion, fecha_vencimiento, marca, linea, anio, serie, motor, color)
    generar_pdf(folio_generado, contribuyente, fecha_expedicion, fecha_vencimiento, qr_texto)

    return render_template('exitoso.html', folio=folio_generado)

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].upper()
        response = supabase.table("permisos_guerrero").select("*").eq("folio", folio).execute()
        registros = response.data

        if not registros:
            resultado = {"estado": "No encontrado", "folio": folio}
        else:
            registro = registros[0]
            fecha_expedicion = datetime.fromisoformat(registro['fecha_expedicion'])
            fecha_vencimiento = datetime.fromisoformat(registro['fecha_vencimiento'])
            hoy = datetime.now()
            estado = "VIGENTE" if hoy <= fecha_vencimiento else "VENCIDO"

            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": fecha_expedicion.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fecha_vencimiento.strftime("%d/%m/%Y"),
                "marca": registro['marca'],
                "linea": registro['linea'],
                "año": registro['anio'],
                "numero_serie": registro['serie'],
                "numero_motor": registro['motor'],
                "color": registro['color'],
                "contribuyente": registro['contribuyente'],
            }

        return render_template("resultado_consulta.html", resultado=resultado)

    return render_template("consulta_folio.html")

if __name__ == '__main__':
    app.run(debug=True)
