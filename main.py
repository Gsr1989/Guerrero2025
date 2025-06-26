from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz  # PyMuPDF
import os

# Librerías para la constancia
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

# Configuración de Supabase
SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0."
    "NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))
        resp = supabase.table("verificaciondigitalcdmx") \
            .select("*") \
            .eq("username", username) \
            .eq("password", password) \
            .execute()
        usuarios = resp.data
        if usuarios:
            session['user_id'] = usuarios[0]['id']
            session['username'] = usuarios[0]['username']
            return redirect(url_for('registro_usuario'))
        flash('Credenciales incorrectas', 'error')
    return render_template('login.html')

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')

@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        folios = int(request.form['folios'])
        existe = supabase.table("verificaciondigitalcdmx")\
            .select("id")\
            .eq("username", username)\
            .execute()
        if existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            return render_template('crear_usuario.html')
        data = {"username": username, "password": password, "folios_asignac": folios, "folios_usados": 0}
        supabase.table("verificaciondigitalcdmx").insert(data).execute()
        flash('Usuario creado exitosamente.', 'success')
    return render_template('crear_usuario.html')

# Generación de recibo
def generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente):
    try:
        plantilla = "recibo_permiso_guerrero_img.pdf"
        ruta = f"static/pdfs/{folio}.pdf"
        os.makedirs("static/pdfs", exist_ok=True)
        doc = fitz.open(plantilla)
        page = doc[0]
        page.insert_text((700, 1750), folio, fontsize=120, fontname="helv")
        page.insert_text((2200, 1750), fecha_expedicion.strftime('%d/%m/%Y'), fontsize=120, fontname="helv")
        page.insert_text((4000, 1750), fecha_vencimiento.strftime('%d/%m/%Y'), fontsize=120, fontname="helv")
        page.insert_text((950, 1930), contribuyente.upper(), fontsize=120, fontname="helv")
        doc.save(ruta)
        return True
    except Exception as e:
        print(f"ERROR al generar PDF: {e}")
        return False

# Generación de constancia usando plantilla
def generar_constancia_guerrero(folio, fecha_expedicion, fecha_vencimiento,
                                 contribuyente, marca, linea, anio,
                                 numero_serie, numero_motor):
    try:
        meses = ["ENERO","FEBRERO","MARZO","ABRIL","MAYO","JUNIO",
                 "JULIO","AGOSTO","SEPTIEMBRE","OCTUBRE","NOVIEMBRE","DICIEMBRE"]
        dia = fecha_expedicion.day
        mes = meses[fecha_expedicion.month-1]
        año = fecha_expedicion.year
        fecha_str = f"A {dia:02d} DE {mes} DEL {año}"
        fol_comp = f"AB{int(folio):05d}"
        fol_sup = f"{fol_comp}/2025"
        plantilla = "Certificado_Guerrero.pdf"
        salida = f"static/constancias/{fol_comp}_constancia.pdf"
        os.makedirs("static/constancias", exist_ok=True)
        ovl = "/mnt/data/overlay_constancia.pdf"
        c = canvas.Canvas(ovl, pagesize=letter)
        c.setFont("Helvetica",10)
        c.drawString(410,735,fol_sup)
        c.drawString(375,710,fecha_str)
        c.setFont("Helvetica",9.5)
        c.drawString(150,625,fol_comp)
        c.drawString(150,610,numero_serie.upper())
        c.drawString(150,595,numero_motor.upper())
        c.drawString(150,580,marca.upper())
        c.drawString(150,565,anio.upper())
        c.drawString(150,550,linea.upper())
        c.drawString(150,535,contribuyente.upper())
        c.drawString(150,520,fecha_vencimiento.strftime('%d/%m/%Y'))  # solo fecha final
        c.save()
        bg = PdfReader(plantilla)
        ov = PdfReader(ovl)
        writer = PdfWriter()
        page = bg.pages[0]
        page.merge_page(ov.pages[0])
        writer.add_page(page)
        with open(salida, "wb") as f:
            writer.write(f)
        return True
    except Exception as e:
        print(f"Error generando constancia: {e}")
        return False

@app.route('/registro_usuario', methods=['GET','POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method=='POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        num_ser = request.form['serie']
        num_mot = request.form['motor']
        vig = int(request.form['vigencia'])
        contrib = request.form['contribuyente']
        ex = supabase.table("folios_registrados").select("*").eq("folio",folio).execute()
        if ex.data:
            flash('Error: el folio ya existe.','error')
            return redirect(url_for('registro_usuario'))
        ud = supabase.table("verificaciondigitalcdmx").select("folios_asignac,folios_usados").eq("id",user_id).execute()
        if not ud.data:
            flash('Error obteniendo usuario.','error')
            return redirect(url_for('registro_usuario'))
        us = ud.data[0]
        if us['folios_asignac']-us['folios_usados']<=0:
            flash('No tienes folios disponibles.','error')
            return redirect(url_for('registro_usuario'))
        fe = datetime.now()
        fv = fe+timedelta(days=vig)
        registro = {"folio":folio,"marca":marca,"linea":linea,"anio":anio,
                    "numero_serie":num_ser,"numero_motor":num_mot,
                    "fecha_expedicion":fe.isoformat(),
                    "fecha_vencimiento":fv.isoformat()}
        supabase.table("folios_registrados").insert(registro).execute()
        supabase.table("verificaciondigitalcdmx")\
            .update({"folios_usados":us['folios_usados']+1})\
            .eq("id",user_id).execute()
        generar_pdf(folio,fe,fv,contrib)
        generar_constancia_guerrero(folio,fe,fv,contrib,marca,linea,anio,num_ser,num_mot)
        return render_template("exitoso.html",folio=folio)
    info = supabase.table("verificaciondigitalcdmx")\
        .select("folios_asignac,folios_usados")\
        .eq("id",session['user_id']).execute()
    return render_template("registro_usuario.html",folios_info=info.data[0] if info.data else {})

@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method=='POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        num_ser = request.form['serie']
        num_mot = request.form['motor']
        vig = int(request.form['vigencia'])
        contrib = request.form['contribuyente']
        ex = supabase.table("folios_registrados").select("*").eq("folio",folio).execute()
        if ex.data:
            flash('Error: el folio ya exista.','error')
            return render_template('registro_admin.html')
        fe = datetime.now()
        fv = fe+timedelta(days=vig)
        data = {"folio":folio,"marca":marca,"linea":linea,"anio":anio,
                "numero_serie":num_ser,"numero_motor":num_mot,
                "fecha_expedicion":fe.isoformat(),
                "fecha_vencimiento":fv.isoformat()}
        supabase.table("folios_registrados").insert(data).execute()
        generar_pdf(folio,fe,fv,contrib)
        generar_constancia_guerrero(folio,fe,fv,contrib,marca,linea,anio,num_ser,num_mot)
        return render_template("exitoso.html",folio=folio)
    return render_template('registro_admin.html')

@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():  
    resultado = None  
    if request.method=='POST':  
        folio = request.form['folio'].strip().upper()  
        resp = supabase.table("folios_registrados").select("*").eq("folio",folio).execute()  
        if not resp.data:  
            return render_template("resultado_consulta.html", resultado={"estado":"No encontrado","folio":folio})  
        reg = resp.data[0]  
        fe = datetime.fromisoformat(reg['fecha_expedicion'])  
        fv = datetime.fromisoformat(reg['fecha_vencimiento'])  
        estado = "VIGENTE" if datetime.now()<=fv else "VENCIDO"  
        return render_template("resultado_consulta.html", resultado={  
            "estado":estado,  
            "folio":folio,  
            "fecha_expedicion":fe.strftime("%d/%m/%Y"),  
            "fecha_vencimiento":fv.strftime("%d/%m/%Y"),  
            "marca":reg["marca"],  
            "linea":reg["linea"],  
            "año":reg["anio"],  
            "numero_serie":reg["numero_serie"],  
            "numero_motor":reg["numero_motor"]  
        })  
    return render_template("consulta_folio.html")

@app.route('/logout')  
def logout():  
    session.clear()  
    return redirect(url_for('login'))

@app.route('/ver_registros')  
def ver_registros():  
    if 'admin' not in session:  
        return redirect(url_for('login'))  
    response = supabase.table("folios_registrados").select("*").order("fecha_expedicion", desc=True).execute()  
    registros = response.data if response.data else []  
    return render_template("mis_registros.html", registros=registros)

@app.route('/descargar_pdf/<folio>')  
def descargar_pdf(folio):  
    ruta_archivo = f"static/pdfs/{folio}.pdf"  
    if os.path.exists(ruta_archivo):  
        return send_from_directory(directory="static/pdfs", path=f"{folio}.pdf", as_attachment=True)  
    flash("El archivo PDF no existe.", "error")  
    return redirect(url_for("ver_registros_admin"))

@app.route('/ver_registros_admin')  
def ver_registros_admin():  
    if 'admin' not in session:  
        return redirect(url_for('login'))  
    response = supabase.table("folios_registrados").select("*").order("fecha_expedicion", desc=True).execute()  
    registros = response.data if response.data else []  
    return render_template("mis_registros.html", registros=registros)

@app.route('/descargar_constancia/<folio>')
def descargar_constancia(folio):
    fol_comp = f"AB{int(folio):05d}"
    filename = f"{fol_comp}_constancia.pdf"
    ruta = os.path.join("static", "constancias", filename)
    if os.path.exists(ruta):
        return send_from_directory(directory="static/constancias", path=filename, as_attachment=True)
    flash("La constancia no existe.", "error")
    return redirect(url_for("consulta_folio"))
    
if __name__ == '__main__':  
    app.run(debug=True)
