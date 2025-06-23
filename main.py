from flask importar Flask, render_template, solicitud, redirigir, url_para, flash, sesión, enviar_desde_directorio
desde datetime importar datetime, timedelta
desde supabase importar create_client, Cliente
importar fitz # PyMuPDF
importar sistema operativo

aplicación = Flask(__nombre__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
CLAVE SUPABASE = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Cliente = crear_cliente(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def inicio():
    retorno redirección(url_para('login'))

@app.route('/login', métodos=['GET', 'POST'])
definición de inicio de sesión():
    si solicitud.metodo == 'POST':
        nombre de usuario = request.form['nombre de usuario']
        contraseña = request.form['contraseña']
        si el nombre de usuario == 'Gsr89roja.' y la contraseña == 'serg890105':
            sesión['admin'] = Verdadero
            retorno redirección(url_para('admin'))
        respuesta = supabase.table("verificaciondigitalcdmx").select("*").eq("nombreusuario", nombreusuario).eq("contraseña", contraseña).execute()
        usuarios = respuesta.datos
        Si usuarios:
            sesión['user_id'] = usuarios[0]['id']
            sesión['nombre de usuario'] = usuarios[0]['nombre de usuario']
            devolver redirección(url_for('registro_usuario'))
        demás:
            flash('Credenciales incorrectas', 'error')
    devolver render_template('login.html')

@app.route('/admin')
definición admin():
    si 'admin' no está en sesión:
        retorno redirección(url_para('login'))
    devolver render_template('panel.html')

@app.route('/crear_usuario', métodos=['GET', 'POST'])
def crear_usuario():
    si 'admin' no está en sesión:
        retorno redirección(url_para('login'))
    si solicitud.metodo == 'POST':
        nombre de usuario = request.form['nombre de usuario']
        contraseña = request.form['contraseña']
        folios = int(solicitud.formulario['folios'])
        existe = supabase.table("verificaciondigitalcdmx").select("id").eq("nombreusuario", nombreusuario).execute()
        si existe.data:
            flash('Error: el nombre de usuario ya existe.', 'error')
            devolver render_template('crear_usuario.html')
        datos = {
            "nombre de usuario": nombre de usuario,
            "contraseña": contraseña,
            "folios_asignac": folios,
            "folios_usados": 0
        }
        supabase.table("verificaciondigitalcdmx").insert(data).execute()
        flash('Usuario creado exitosamente.', 'success')
    devolver render_template('crear_usuario.html')

def generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente):
    intentar:
        plantilla = "recibo_permiso_guerrero_img.pdf"
        ruta_pdf = f"static/pdfs/{folio}.pdf"
        os.makedirs("static/pdfs", exist_ok=True)
        doc = fitz.open(plantilla)
        página = doc[0]
        página.insertar_texto((700, 1750), folio, tamaño de fuente=120, nombre de fuente="helv")
        página.insertar_texto((2200, 1750), fecha_expedicion.strftime('%d/%m/%Y'), tamaño de fuente=120, nombre de fuente="helv")
        page.insert_text((4000, 1750), fecha_vencimiento.strftime('%d/%m/%Y'), fontsize=120, fontname="helv")
        página.insertar_texto((950, 1930), contribuyente.upper(), tamaño de fuente=120, nombre de fuente="helv")
        doc.save(ruta_pdf)
        devuelve Verdadero
    excepto Excepción como e:
        print(f"ERROR al generar PDF: {e}")
        devuelve Falso

@app.route('/registro_usuario', métodos=['GET', 'POST'])
def registro_usuario():
    si 'user_id' no está en sesión:
        retorno redirección(url_para('login'))
    user_id = sesión['user_id']
    si solicitud.metodo == 'POST':
        folio = solicitud.formulario['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = solicitud.formulario['anio']
        numero_serie = solicitud.formulario['serie']
        numero_motor = solicitud.formulario['motor']
        vigencia = int(solicitud.formulario['vigencia'])
        contribuyente = solicitud.formulario['contribuyente']
        existente = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        si existente.data:
            flash('Error: el folio ya existe.', 'error')
            devolver redirección(url_for('registro_usuario'))
        usuario_data = supabase.table("verificaciondigitalcdmx").select("folios_asignac, folios_usados").eq("id", user_id).execute()
        si no es usuario_data.data:
            flash("No se pudo obtener la información del usuario.", "error")
            devolver redirección(url_for('registro_usuario'))
        folios = usuario_data.data[0]
        restantes = folios['folios_asignac'] - folios['folios_usados']
        si restantes <= 0:
            flash("No tienes folios disponibles para registrar.", "error")
            devolver redirección(url_for('registro_usuario'))
        fecha_expedicion = fechahora.ahora()
        fecha_vencimiento = fecha_expedicion + timedelta(dias=vigencia)
        datos = {
            "folio": folio,
            "marca": marca,
            "línea": línea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_expedicion.isoformat(),
            "fecha_vencimiento": fecha_vencimiento.isoformat()
        }
        supabase.table("folios_registrados").insert(data).execute()
        supabase.table("verificaciondigitalcdmx").update({"folios_usados": folios["folios_usados"] + 1}).eq("id", user_id).execute()
        generar_pdf(folio, fecha_expedicion, fecha_vencimiento, contribuyente)
        return render_template("exitoso.html", folio=folio)
    respuesta = supabase.table("verificaciondigitalcdmx").select("folios_asignac, folios_usados").eq("id", user_id).execute()
    folios_info = respuesta.datos[0] si respuesta.datos de lo contrario {}
    return render_template("registro_usuario.html", folios_info=folios_info)

@app.route('/consulta_folio', métodos=['GET', 'POST'])
def consulta_folio():
    resultado = Ninguno
    si solicitud.metodo == 'POST':
        folio = solicitud.formulario['folio'].strip().upper()
        respuesta = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
        registros = respuesta.datos
        si no registros:
            resultado = {"estado": "No encontrado", "folio": folio}
        demás:
            registro = registros[0]
            fecha_expedicion = fechahora.fromisoformat(registro['fecha_expedicion'])
            fecha_vencimiento = fechahora.fromisoformat(registro['fecha_vencimiento'])
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
                "numero_serie": registro['numero_serie'],
                "numero_motor": registro['numero_motor']
            }
        return render_template("resultado_consulta.html", resultado=resultado)
    devolver render_template("consulta_folio.html")

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    ruta_archivo = f"static/pdfs/{folio}.pdf"
    si os.path.exists(ruta_archivo):
        devolver enviar_desde_directorio(directorio="estático/pdfs", ruta=f"{folio}.pdf", como_archivo_adjunto=Verdadero)
    demás:
        flash("El archivo PDF no existe.", "error")
        devolver redirección(url_for("registro_usuario"))

@app.route('/cerrar sesión')
def cerrar sesión():
    sesión.clear()
    retorno redirección(url_para('login'))

si __nombre__ == '__principal__':
    aplicación.run(debug=True)
