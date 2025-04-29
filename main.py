from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from datetime import datetime, timedelta
from supabase import create_client, Client
import qrcode
import fitz  # PyMuPDF
import os
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PLANTILLA_PDF = "static/recibo_permiso_guerrero_img.pdf"

def generar_folio():
    response = supabase.table('permisos_guerrero').select('folio').order('folio', desc=True).limit(1).execute()
    if not response.data:
        return 'AC0001'

    ultimo_folio = response.data[0]['folio']
    letras, numeros = ultimo_folio[:2], int(ultimo_folio[2:])
    if numeros < 9999:
        numeros += 1
    else:
        numeros = 1
        letras = incrementar_letras(letras)
    return f"{letras}{numeros:04d}"

def incrementar_letras(letras):
    abecedario = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    primera, segunda = letras
    if segunda != 'Z':
        segunda = abecedario[abecedario.index(segunda) + 1]
    else:
        segunda = 'A'
        if primera != 'Z':
            primera = abecedario[abecedario.index(primera) + 1]
        else:
            primera = 'A'
    return primera + segunda

def crear_qr(texto):
    qr = qrcode.make(texto)
    buffer = BytesIO()
    qr.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer

def generar_pdf(datos, qr_buffer):
    doc = fitz.open(PLANTILLA_PDF)
    page = doc[0]

    # Insertar datos
    page.insert_text((100, 150), f"Folio: {datos['folio']}", fontsize=12)
    page.insert_text((100, 180), f"Contribuyente: {datos['contribuyente']}", fontsize=12)
    page.insert_text((100, 210), f"Fecha Expedición: {datos['fecha_expedicion']}", fontsize=12)
    page.insert_text((100, 240), f"Fecha Vencimiento: {datos['fecha_vencimiento']}", fontsize=12)

    # Insertar QR
    qr_img = fitz.Pixmap(qr_buffer)
    page.insert_image(fitz.Rect(60, 500, 260, 700), pixmap=qr_img)

    output_folder = "static/pdfs"
    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, f"{datos['folio']}.pdf")
    doc.save(output_path)
    doc.close()
    return output_path

@app.route('/', methods=['GET', 'POST'])
def registro_folio():
    if request.method == 'POST':
        marca = request.form['marca'].upper()
        linea = request.form['linea'].upper()
        anio = request.form['anio']
        serie = request.form['serie'].upper()
        motor = request.form['motor'].upper()
        color = request.form['color'].upper()
        contribuyente = request.form['contribuyente'].upper()
        vigencia = int(request.form['vigencia'])

        folio = generar_folio()
        fecha_actual = datetime.now()
        fecha_expedicion = fecha_actual.strftime("%d/%m/%Y")
        fecha_vencimiento = (fecha_actual + timedelta(days=vigencia)).strftime("%d/%m/%Y")

        texto_qr = f"[PERMISO-AUTÉNTICO] [MUNICIPIO-TLAPA DE COMONFORT-GRO]\nFOLIO: {folio}\nMARCA: {marca}\nLÍNEA: {linea}\nAÑO: {anio}\nSERIE: {serie}\nMOTOR: {motor}\nCOLOR: {color}\nCONTRIBUYENTE: {contribuyente}\nEXPEDICIÓN: {fecha_expedicion}\nVENCIMIENTO: {fecha_vencimiento}"
        qr = crear_qr(texto_qr)

        datos = {
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "serie": serie,
            "motor": motor,
            "color": color,
            "contribuyente": contribuyente,
            "vigencia": f"{vigencia} DÍAS",
            "fecha_expedicion": fecha_expedicion,
            "fecha_vencimiento": fecha_vencimiento
        }

        supabase.table("permisos_guerrero").insert(datos).execute()
        generar_pdf(datos, qr)

        flash(f'Permiso generado exitosamente: {folio}', 'success')
        return redirect(url_for('registro_folio'))

    return render_template('registro_folio.html')

@app.route('/descargar/<folio>')
def descargar(folio):
    path = f"static/pdfs/{folio}.pdf"
    if os.path.exists(path):
        return send_file(path, as_attachment=True)
    else:
        return "Archivo no encontrado.", 404

@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].upper()
        response = supabase.table('permisos_guerrero').select('*').eq('folio', folio).execute()
        registros = response.data

        if registros:
            registro = registros[0]
            hoy = datetime.now()
            fecha_vencimiento = datetime.strptime(registro['fecha_vencimiento'], "%d/%m/%Y")
            estado = "VIGENTE" if hoy <= fecha_vencimiento else "VENCIDO"

            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": registro['fecha_expedicion'],
                "fecha_vencimiento": registro['fecha_vencimiento'],
                "marca": registro['marca'],
                "linea": registro['linea'],
                "año": registro['anio'],
                "numero_serie": registro['serie'],
                "numero_motor": registro['motor'],
                "color": registro['color'],
                "contribuyente": registro['contribuyente']
            }
        else:
            resultado = {"estado": "No encontrado", "folio": folio}

        return render_template('resultado_consulta.html', resultado=resultado)

    return render_template('consulta_folio.html')

if __name__ == '__main__':
    app.run(debug=True)
