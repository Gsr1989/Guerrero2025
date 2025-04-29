from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

# Conexión a Supabase (NO TOCADA)
SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0."
    "NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OUTPUT_DIR = "static/pdfs"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# LOGIN
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        contrasena = request.form['contrasena']
        if usuario == 'elwarrior' and contrasena == 'Warrior2025':
            session['usuario'] = usuario
            return redirect(url_for('panel'))
        else:
            flash('Usuario o contraseña incorrectos')
    return render_template('login.html')

# PANEL PRINCIPAL
@app.route('/panel')
def panel():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')

# REGISTRO DE FOLIOS
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        año = request.form['anio']
        serie = request.form['serie']
        motor = request.form['motor']
        color = request.form['color']
        nombre = request.form['nombre']
        vigencia = int(request.form['vigencia'])

        fecha_expedicion = datetime.now().strftime('%d/%m/%Y')
        fecha_vencimiento = (datetime.now() + timedelta(days=vigencia)).strftime('%d/%m/%Y')

        # Insertar en Supabase
        data = {
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "año": año,
            "serie": serie,
            "motor": motor,
            "color": color,
            "nombre": nombre,
            "vigencia": vigencia,
            "fecha_expedicion": fecha_expedicion,
            "fecha_vencimiento": fecha_vencimiento
        }
        supabase.table('folios_registrados').insert(data).execute()

        # Generar PDF
        plantilla_path = 'static/recibo_permiso_guerrero_img.pdf'
        output_path = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
        generar_pdf(plantilla_path, output_path, folio, nombre, fecha_expedicion, fecha_vencimiento)

        return redirect(url_for('exitoso', folio=folio))

    return render_template('registro_admin.html')

# GENERAR PDF
def generar_pdf(plantilla_path, output_path, folio, nombre, fecha_expedicion, fecha_vencimiento):
    doc = fitz.open(plantilla_path)
    page = doc[0]

    # Imprimir datos
    page.insert_text((100, 150), f"FOLIO: {folio}", fontsize=14, color=(1, 0, 0))  # Rojo
    page.insert_text((100, 180), f"NOMBRE: {nombre}", fontsize=12, color=(0, 0, 0))  # Negro
    page.insert_text((100, 210), f"EXPEDICIÓN: {fecha_expedicion}", fontsize=12, color=(0, 0, 0))  # Negro
    page.insert_text((100, 240), f"VENCIMIENTO: {fecha_vencimiento}", fontsize=12, color=(0, 0, 0))  # Negro

    doc.save(output_path)
    doc.close()

# VER TODOS LOS FOLIOS
@app.route('/admin_folios')
def admin_folios():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    data = supabase.table('folios_registrados').select('*').execute()
    registros = data.data
    return render_template('admin_folios.html', registros=registros)

# EDITAR FOLIO
@app.route('/editar_folio/<folio>', methods=['GET', 'POST'])
def editar_folio(folio):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nuevos_datos = {
            "marca": request.form['marca'],
            "linea": request.form['linea'],
            "año": request.form['anio'],
            "serie": request.form['serie'],
            "motor": request.form['motor'],
            "color": request.form['color'],
            "nombre": request.form['nombre'],
            "vigencia": int(request.form['vigencia']),
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento']
        }
        supabase.table('folios_registrados').update(nuevos_datos).eq('folio', folio).execute()
        flash('Folio actualizado exitosamente')
        return redirect(url_for('admin_folios'))

    data = supabase.table('folios_registrados').select('*').eq('folio', folio).execute()
    registro = data.data[0] if data.data else None
    return render_template('editar_folio.html', registro=registro)

# ELIMINAR FOLIO
@app.route('/eliminar_folio/<folio>')
def eliminar_folio(folio):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    supabase.table('folios_registrados').delete().eq('folio', folio).execute()
    flash('Folio eliminado exitosamente')
    return redirect(url_for('admin_folios'))

# CONSULTAR FOLIO (PÚBLICO)
@app.route('/consulta', methods=['GET', 'POST'])
def consulta():
    if request.method == 'POST':
        folio = request.form['folio']
        data = supabase.table('folios_registrados').select('*').eq('folio', folio).execute()
        registros = data.data

        if registros:
            registro = registros[0]
            return render_template('resultado_consulta.html', registro=registro)
        else:
            flash('Folio no encontrado')
            return redirect(url_for('consulta'))

    return render_template('consulta_folio.html')

# DESCARGAR PDF
@app.route('/descargar/<folio>')
def descargar(folio):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    file_path = os.path.join(OUTPUT_DIR, f"{folio}.pdf")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        flash('Archivo no encontrado')
        return redirect(url_for('panel'))

# CERRAR SESIÓN
@app.route('/logout')
def logout():
    session.pop('usuario', None)
    return redirect(url_for('login'))

# PÁGINA DE REGISTRO EXITOSO
@app.route('/exitoso')
def exitoso():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    folio = request.args.get('folio')
    return render_template('exitoso.html', folio=folio)

if __name__ == '__main__':
    app.run(debug=True)
