from flask import Flask, render_template, request, redirect, url_for, send_file
from supabase import create_client, Client
import io
import qrcode
from datetime import datetime, timedelta

app = Flask(__name__)

SUPABASE_URL = "https://iuwsippnvyynwnxanwnv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml1d3NpcHBudnl5bndueGFud252Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDU2NDU3MDcsImV4cCI6MjA2MTIyMTcwN30.bm7J6b3k_F0JxPFFRTklBDOgHRJTvEa1s-uwvSwVxTs"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def index():
    return redirect(url_for('registro_folio'))

@app.route('/registro_folio', methods=['GET', 'POST'])
def registro_folio():
    if request.method == 'POST':
        folio = request.form['folio']
        marca = request.form['marca']
        linea = request.form['linea']
        anio = request.form['anio']
        serie = request.form['serie']
        motor = request.form['motor']
        color = request.form['color']
        contribuyente = request.form['contribuyente']

        # Calcular fechas
        fecha_expedicion = datetime.now()
        fecha_vencimiento = fecha_expedicion + timedelta(days=90)
        fecha_expedicion_str = fecha_expedicion.strftime('%Y-%m-%d')
        fecha_vencimiento_str = fecha_vencimiento.strftime('%Y-%m-%d')

        # Insertar en Supabase
        data = {
            'folio': folio,
            'marca': marca,
            'linea': linea,
            'anio': anio,
            'serie': serie,
            'motor': motor,
            'color': color,
            'contribuyente': contribuyente,
            'vigencia': '90 días',
            'fecha_expedicion': fecha_expedicion_str,
            'fecha_vencimiento': fecha_vencimiento_str
        }

        try:
            supabase.table('permisos_guerrero').insert(data).execute()
        except Exception as e:
            return f"Error al guardar en Supabase: {e}"

        # Generar QR
        url_qr = f"https://tlapadecomonfortexpediciondepermisosgob2.onrender.com/consulta/{folio}"
        qr_img = qrcode.make(url_qr)
        buffer = io.BytesIO()
        qr_img.save(buffer, format="PNG")
        qr_bytes = buffer.getvalue()

        return send_file(
            io.BytesIO(qr_bytes),
            mimetype="image/png",
            as_attachment=True,
            download_name=f"{folio}_qr.png"
        )

    return render_template('registro_folio.html')

if __name__ == '__main__':
    app.run(debug=True)
