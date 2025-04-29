from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import os
import qrcode

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def inicio():
    return redirect(url_for('registro_folio'))

@app.route('/registro_folio', methods=['GET', 'POST'])
def registro_folio():
    if request.method == 'POST':
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        serie = request.form['serie']
        motor = request.form['motor']
        color = request.form['color']
        nombre = request.form['nombre']
        vigencia = int(request.form['vigencia'])

        ultimo_folio = supabase.table('permisos_guerrero').select('folio').order('id', desc=True).limit(1).execute()
        if ultimo_folio.data:
            folio_actual = ultimo_folio.data[0]['folio']
            letras = folio_actual[:2]
            numeros = int(folio_actual[2:])

            if numeros < 9999:
                nuevo_numero = numeros + 1
                nuevo_folio = f"{letras}{nuevo_numero:04d}"
            else:
                nueva_letra = chr(ord(letras[1]) + 1) if letras[1] != 'Z' else 'A'
                nueva_primera = chr(ord(letras[0]) + 1) if nueva_letra == 'A' else letras[0]
                nuevo_folio = f"{nueva_primera}{nueva_letra}0001"
        else:
            nuevo_folio = "AC0001"

        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=vigencia)

        data = {
            "folio": nuevo_folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "serie": serie,
            "motor": motor,
            "color": color,
            "nombre": nombre,
            "vigencia": vigencia,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }

        supabase.table('permisos_guerrero').insert(data).execute()

        generar_pdf(nuevo_folio, marca, linea, anio, serie, motor, color, nombre, fecha_expedicion, fecha_vencimiento)

        return render_template('exitoso.html', folio=nuevo_folio)

    return render_template('registro_folio.html')

def generar_pdf(folio, marca, linea, anio, serie, motor, color, nombre, fecha_expedicion, fecha_vencimiento):
    plantilla = "static/pdf_templates/recibo_permiso_guerrero_img.pdf"
    output_path = f"static/pdfs/{folio}.pdf"
    os.makedirs("static/pdfs", exist_ok=True)

    doc = fitz.open(plantilla)
    page = doc[0]

    page.insert_text((100, 150), f"Folio: {folio}", fontsize=12)
    page.insert_text((100, 170), f"Marca: {marca}", fontsize=12)
    page.insert_text((100, 190), f"Línea: {linea}", fontsize=12)
    page.insert_text((100, 210), f"Año: {anio}", fontsize=12)
    page.insert_text((100, 230), f"Serie: {serie}", fontsize=12)
    page.insert_text((100, 250), f"Motor: {motor}", fontsize=12)
    page.insert_text((100, 270), f"Color: {color}", fontsize=12)
    page.insert_text((100, 290), f"Contribuyente: {nombre}", fontsize=12)
    page.insert_text((100, 310), f"Expedición: {fecha_expedicion.strftime('%d/%m/%Y')}", fontsize=12)
    page.insert_text((100, 330), f"Vigencia: {fecha_vencimiento.strftime('%d/%m/%Y')}", fontsize=12)

    qr = qrcode.make(f"Folio: {folio}\nMarca: {marca}\nLínea: {linea}\nAño: {anio}\nSerie: {serie}\nMotor: {motor}\nColor: {color}\nContribuyente: {nombre}")
    qr_path = f"static/pdfs/{folio}_qr.png"
    qr.save(qr_path)
    qr_img = fitz.Pixmap(qr_path)
    page.insert_image(fitz.Rect(30, 650, 130, 750), pixmap=qr_img)

    doc.save(output_path)
    os.remove(qr_path)

if __name__ == '__main__':
    app.run(debug=True)
