from flask import Flask, render_template, request, redirect, url_for, send_file, flash, session from datetime import datetime, timedelta from supabase import create_client, Client import fitz  # PyMuPDF import os import qrcode from io import BytesIO

app = Flask(name) app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co" SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs" supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PDF_OUTPUT_FOLDER = 'static/pdfs' PLANTILLA_PDF = 'recibo_permiso_guerrero_img.pdf' FOLIO_FILE = 'folio_actual.txt'

--- Funciones auxiliares ---

def cargar_folio(): if not os.path.exists(FOLIO_FILE): with open(FOLIO_FILE, 'w') as f: f.write('AC0000') with open(FOLIO_FILE, 'r') as f: return f.read().strip()

def siguiente_folio(folio_actual): letras = folio_actual[:2] numeros = int(folio_actual[2:]) if numeros < 9999: numeros += 1 else: letras = incrementar_letras(letras) numeros = 1 nuevo_folio = f"{letras}{numeros:04d}" with open(FOLIO_FILE, 'w') as f: f.write(nuevo_folio) return nuevo_folio

def incrementar_letras(letras): letra1, letra2 = letras if letra2 != 'Z': letra2 = chr(ord(letra2) + 1) else: letra2 = 'A' if letra1 != 'Z': letra1 = chr(ord(letra1) + 1) else: letra1 = 'A' return letra1 + letra2

def generar_qr_texto(data): qr = qrcode.QRCode(box_size=2, border=2) qr.add_data(data) qr.make(fit=True) img = qr.make_image(fill='black', back_color='white') buffer = BytesIO() img.save(buffer, format="PNG") return buffer.getvalue()

def generar_pdf_con_qr(folio, marca, linea, anio, serie, motor, color, contribuyente, fecha_exp, fecha_venc): doc = fitz.open(PLANTILLA_PDF) page = doc[0]

texto_qr = (
    f"[PERMISO-AUTÉNTICO] [MUNICIPIO-TLAPA DE COMONFORT-GRO]\n"
    f"Folio: {folio}\nMarca: {marca}\nLínea: {linea}\nAño: {anio}\n"
    f"Serie: {serie}\nMotor: {motor}\nColor: {color}\nContribuyente: {contribuyente}\n"
    f"Fecha expedición: {fecha_exp}\nFecha vencimiento: {fecha_venc}"
)
qr_img = generar_qr_texto(texto_qr)
qr_rect = fitz.Rect(30, 740, 130, 840)  # Posición del QR
page.insert_image(qr_rect, stream=qr_img)

page.insert_text((150, 120), f"{folio}", fontsize=12, color=(1, 0, 0))
page.insert_text((150, 135), f"{fecha_exp}", fontsize=12)
page.insert_text((150, 150), f"{fecha_venc}", fontsize=12)
page.insert_text((150, 165), f"{contribuyente}", fontsize=12)

output_path = os.path.join(PDF_OUTPUT_FOLDER, f"{folio}.pdf")
os.makedirs(PDF_OUTPUT_FOLDER, exist_ok=True)
doc.save(output_path)
doc.close()

--- Rutas ---

@app.route('/', methods=['GET', 'POST']) def formulario(): if request.method == 'POST': marca = request.form['marca'].upper() linea = request.form['linea'].upper() anio = request.form['anio'].upper() serie = request.form['serie'].upper() motor = request.form['motor'].upper() color = request.form['color'].upper() contribuyente = request.form['contribuyente'].upper()

folio_actual = cargar_folio()
    folio = siguiente_folio(folio_actual)

    fecha_expedicion = datetime.now()
    fecha_vencimiento = fecha_expedicion + timedelta(days=30)

    fecha_exp_txt = fecha_expedicion.strftime('%d/%m/%Y')
    fecha_venc_txt = fecha_vencimiento.strftime('%d/%m/%Y')

    supabase.table("permisos_guerrero").insert({
        "folio": folio,
        "marca": marca,
        "linea": linea,
        "anio": anio,
        "serie": serie,
        "motor": motor,
        "color": color,
        "contribuyente": contribuyente,
        "fecha_expedicion": fecha_exp_txt,
        "fecha_vencimiento": fecha_venc_txt
    }).execute()

    generar_pdf_con_qr(folio, marca, linea, anio, serie, motor, color, contribuyente, fecha_exp_txt, fecha_venc_txt)

    return render_template('exitoso.html', folio=folio)

return render_template('registro_folio.html')

@app.route('/descargar/<folio>') def descargar(folio): path = os.path.join(PDF_OUTPUT_FOLDER, f"{folio}.pdf") return send_file(path, as_attachment=True)

if name == 'main': app.run(debug=True)

