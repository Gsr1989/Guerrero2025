"""
GUERRERO — Configuración compartida entre el panel web (Flask) y el bot (aiogram).

El código original venía duplicado casi al 100% entre el panel y el bot:
mismas coordenadas de PDF (coords_guerrero, coords_recibo), mismo generador
de PDF, mismo esquema de folio letra+número — cada uno con su propia copia,
sin candado, y CON WATERMARKS DISTINTOS EN SUPABASE (el panel usaba la clave
"GRO_FOLIO", el bot usaba "GUE"). Eso significa que panel y bot venían
repartiendo folios de dos contadores separados — con el tiempo iban a
coincidir en el mismo folio para dos vehículos distintos.

Aquí vive UNA sola copia de todo eso. Panel y bot importan de aquí.
"""

import os
import sys
import logging
import threading
import asyncio
import string
from datetime import datetime, date
from zoneinfo import ZoneInfo
from io import BytesIO

import fitz
import qrcode
from supabase import create_client, Client

# ===================== LOGGING =====================
sys.dont_write_bytecode = True
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("guerrero")

# ===================== ZONA HORARIA =====================
TZ_MX = ZoneInfo("America/Mexico_City")


def now_mx() -> datetime:
    return datetime.now(TZ_MX)


def today_mx() -> date:
    return now_mx().date()


# ===================== SUPABASE =====================
SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://xsagwqepoljfsogusubw.supabase.co"
)
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== CONFIG GENERAL =====================
BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
BASE_URL          = os.getenv(
    "BASE_URL",
    "https://tlapadecomonfortexpediciondepermisosgob2-k6u7.onrender.com"
).rstrip("/")
URL_CONSULTA_BASE = BASE_URL

OUTPUT_DIR        = "static/pdfs"     # antes: bot usaba "documentos", panel "static/pdfs" — unificado
PLANTILLA_PERMISO = "Guerrero.pdf"
PLANTILLA_RECIBO  = "recibo_permiso_guerrero_img.pdf"

ENTIDAD           = "Guerrero"        # panel y bot ya coincidían en esta (mayúscula) — se conservó
COSTO_FIJO        = "250"
RFC_FIJO          = "XAXX010101000"
DOMICILIO_FIJO    = "MEXICO"
DIAS_PERMISO      = 30
HORAS_LIMITE_PAGO = 48                # limpieza del panel (scheduler)
HORAS_TIMER_BOT   = 36                # timer del bot
BUCKET_NAME       = "permisos-guerrero"

ADMIN_USER        = os.getenv("ADMIN_USER", "Serg890105tm3")
ADMIN_PASS        = os.getenv("ADMIN_PASS", "Serg890105tm3")
SECRET_KEY        = os.getenv("SECRET_KEY", "clave_muy_segura_123456")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===================== CANDADOS GLOBALES =====================
# ⚠️ PDF_LOCK — ni panel ni bot tenían candado compartido (cada uno generaba
# con PyMuPDF por su cuenta). Al correr en el mismo proceso, sin esto dos
# permisos simultáneos corrompen los documentos entre sí.
PDF_LOCK   = threading.Lock()

# Serializa la asignación de folios entre panel y bot
FOLIO_LOCK = threading.Lock()

# ===================== COORDENADAS PDF (idénticas panel/bot) =====================
COORDS_GUERRERO = {
    "folio":         (360, 769,  8, (1, 0, 0)),
    "fecha_exp":     (135, 755,  8, (0, 0, 0)),
    "fecha_ven":     (135, 768,  8, (0, 0, 0)),
    "serie":         (360, 742,  8, (0, 0, 0)),
    "motor":         (360, 729,  8, (0, 0, 0)),
    "marca":         (360, 700,  8, (0, 0, 0)),
    "linea":         (360, 714,  8, (0, 0, 0)),
    "color":         (360, 756,  8, (0, 0, 0)),
    "nombre":        (135, 700,  8, (0, 0, 0)),
    "rfc":           (135, 713,  8, (0, 0, 0)),
    "domicilio":     (135, 726,  8, (0, 0, 0)),
    "costo":         (135, 742,  8, (0, 0, 0)),
    "rot_folio":     (440, 200, 83, (0, 0, 0)),
    "rot_fecha_exp": ( 77, 205,  8, (0, 0, 0)),
    "rot_fecha_ven": ( 63, 205,  8, (0, 0, 0)),
    "rot_serie":     (168, 110, 18, (0, 0, 0)),
    "rot_motor":     (224, 110, 18, (0, 0, 0)),
    "rot_marca":     (280, 110, 18, (0, 0, 0)),
    "rot_linea":     (280, 290, 18, (0, 0, 0)),
    "rot_anio":      (305, 520, 18, (0, 0, 0)),
    "rot_color":     (224, 420, 18, (0, 0, 0)),
    "rot_nombre":    (115, 205,  8, (0, 0, 0)),
    "rot_rfc":       (102, 205,  8, (0, 0, 0)),
    "rot_domicilio": ( 89, 205,  8, (0, 0, 0)),
}

COORDS_RECIBO = {
    "folio":     ( 85, 210, 12, (0, 0, 0)),
    "nombre":    (117, 231, 12, (0, 0, 0)),
    "domicilio": (117, 255, 12, (0, 0, 0)),
    "costo":     (432, 352, 12, (0, 0, 0)),
    "fecha_exp": (265, 210, 12, (0, 0, 0)),
    "fecha_ven": (480, 210, 12, (0, 0, 0)),
    "qr":        ( 55, 307, 110, None),
}

# ===================== TABLAS EDITABLES DESDE EL PANEL =====================
TABLAS_DISPONIBLES = {
    'folios_registrados': {
        'nombre':   'Folios Registrados',
        'pk_col':   'folio',
        'columnas': ['folio', 'marca', 'linea', 'anio', 'numero_serie', 'numero_motor',
                     'color', 'nombre', 'rfc', 'domicilio', 'costo',
                     'fecha_expedicion', 'fecha_vencimiento',
                     'entidad', 'estado', 'creado_por'],
    },
    'verificaciondigitalcdmx': {
        'nombre':   'Usuarios del Sistema',
        'pk_col':   'id',
        'columnas': ['id', 'username', 'password', 'folios_asignac', 'folios_usados'],
    },
    'folio_watermark': {
        'nombre':   'Watermark de Folios',
        'pk_col':   'prefijo',
        'columnas': ['prefijo', 'ultimo_asignado'],
    },
}

COLUMNAS_FECHA = {
    'fecha_expedicion', 'fecha_vencimiento', 'fecha_comprobante',
    'fecha_detencion', 'created_at',
}


# ===================== GENERADOR DE FOLIO COMPARTIDO =====================
# Esquema: 2 letras (AA-ZZ) + 4 dígitos (0001-9999), empezando en ZY4917.
# Cuando se agotan los 9999 números de un par de letras, rota al siguiente
# par automáticamente (AA -> AB -> ... -> ZZ). Idéntico en panel y bot;
# ahora vive UNA sola vez aquí.
NUMEROS_POR_PREFIJO = 9999
FOLIO_INICIO        = "ZY4917"

# ⚠️ Migración de watermark: el panel usaba la clave "GRO_FOLIO" y el bot
# usaba "GUE" — dos contadores INDEPENDIENTES corriendo en paralelo. La
# clave unificada de aquí en adelante es "GUE". Al arrancar, si "GRO_FOLIO"
# tiene un valor MÁS ALTO que "GUE" (o si "GUE" no existe todavía), se copia
# el máximo hacia "GUE" una sola vez — así no se repiten folios que el panel
# ya haya repartido bajo su clave vieja.
FOLIO_WATERMARK_KEY     = "GUE"
FOLIO_WATERMARK_KEY_OLD = "GRO_FOLIO"


def _letras_a_indice(l1: str, l2: str) -> int:
    return (ord(l1) - 65) * 26 + (ord(l2) - 65)


def _indice_a_letras(idx: int) -> str:
    l1 = chr(65 + idx // 26)
    l2 = chr(65 + idx % 26)
    return f"{l1}{l2}"


def _folio_a_entero(folio: str) -> int:
    l1, l2, numero = folio[0], folio[1], int(folio[2:])
    return _letras_a_indice(l1, l2) * NUMEROS_POR_PREFIJO + (numero - 1)


def _entero_a_folio(n: int) -> str:
    total_slots = 26 * 26 * NUMEROS_POR_PREFIJO
    n = n % total_slots
    idx_prefijo = n // NUMEROS_POR_PREFIJO
    numero = (n % NUMEROS_POR_PREFIJO) + 1
    prefijo = _indice_a_letras(idx_prefijo)
    return f"{prefijo}{str(numero).zfill(4)}"


def _leer_watermark(clave: str):
    try:
        r = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", clave).execute()
        return r.data[0]["ultimo_asignado"] if r.data else None
    except Exception as e:
        logger.error(f"[WATERMARK] leer {clave}: {e}")
        return None


def _guardar_watermark(clave: str, numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo": clave, "ultimo_asignado": numero
        }).execute()
    except Exception as e:
        logger.error(f"[WATERMARK] guardar {clave}: {e}")


def _migrar_watermark_una_vez():
    """
    Se corre en cada arranque; es barata e idempotente. Si el watermark
    viejo del panel (GRO_FOLIO) tiene un número más alto que el nuevo
    unificado (GUE), sube el unificado a ese valor para no pisar folios
    que el panel ya haya entregado.
    """
    actual = _leer_watermark(FOLIO_WATERMARK_KEY)
    viejo  = _leer_watermark(FOLIO_WATERMARK_KEY_OLD)
    if viejo is not None and (actual is None or viejo > actual):
        _guardar_watermark(FOLIO_WATERMARK_KEY, viejo)
        logger.info(f"[FOLIO] Migrado watermark: GRO_FOLIO={viejo} -> GUE={viejo}")


def _inicializar_watermark() -> int:
    _migrar_watermark_una_vez()
    actual = _leer_watermark(FOLIO_WATERMARK_KEY)
    if actual is not None:
        return actual
    inicio = _folio_a_entero(FOLIO_INICIO) - 1
    _guardar_watermark(FOLIO_WATERMARK_KEY, inicio)
    logger.info(f"[FOLIO] Watermark inicializado en {FOLIO_INICIO}")
    return inicio


def _folio_existe(folio: str) -> bool:
    try:
        r = supabase.table("folios_registrados").select("folio").eq("folio", folio).limit(1).execute()
        return len(r.data) > 0
    except Exception as e:
        logger.error(f"[FOLIO] verificando {folio}: {e}")
        return False


def generar_folio_guerrero() -> str:
    """ÚNICO generador de folios — lo usan el panel Y el bot, con candado."""
    with FOLIO_LOCK:
        actual = _inicializar_watermark()
        total_slots = 26 * 26 * NUMEROS_POR_PREFIJO
        revisados = 0
        while revisados < total_slots:
            candidato = _entero_a_folio(actual + 1)
            if not _folio_existe(candidato):
                _guardar_watermark(FOLIO_WATERMARK_KEY, actual + 1)
                logger.info(f"[FOLIO] ✅ Asignado: {candidato}")
                return candidato
            actual += 1
            revisados += 1
        raise Exception("Sin folio disponible — se agotó el espacio de letras")


def leer_siguiente_folio() -> str:
    """Sólo informativo (para /health) — no reserva nada."""
    wm = _leer_watermark(FOLIO_WATERMARK_KEY)
    if wm is None:
        return FOLIO_INICIO
    return _entero_a_folio(wm + 1)


# ===================== QR =====================
def _make_qr_pixmap(folio: str):
    url = f"{URL_CONSULTA_BASE}/consulta/{folio}"
    qr  = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=4, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return fitz.Pixmap(buf.read())


# ===================== PDF COMPLETO COMPARTIDO =====================
def _generar_pdf_permiso(datos: dict) -> str:
    fol  = datos["folio"]
    path = f"{OUTPUT_DIR}/{fol}_permiso_tmp.pdf"
    c = COORDS_GUERRERO
    try:
        doc = fitz.open(PLANTILLA_PERMISO)
        pg  = doc[0]
        for campo in ["folio", "fecha_exp", "fecha_ven", "serie", "motor",
                      "marca", "linea", "color", "nombre"]:
            if campo in c and campo in datos:
                x, y, s, col = c[campo]
                pg.insert_text((x, y), str(datos[campo]), fontsize=s, color=col)
        for campo in ["rfc", "domicilio"]:
            x, y, s, col = c[campo]
            pg.insert_text((x, y), datos[campo], fontsize=s, color=col)
        x, y, s, col = c["costo"]
        pg.insert_text((x, y), f"${datos['costo']}", fontsize=s, color=col)
        for k, val in [
            ("rot_folio", fol), ("rot_fecha_exp", datos["fecha_exp"]),
            ("rot_fecha_ven", datos["fecha_ven"]), ("rot_serie", datos["serie"]),
            ("rot_motor", datos["motor"]), ("rot_marca", datos["marca"]),
            ("rot_linea", datos["linea"]), ("rot_anio", datos["anio"]),
            ("rot_color", datos["color"]), ("rot_nombre", datos["nombre"]),
            ("rot_rfc", datos["rfc"]), ("rot_domicilio", datos["domicilio"]),
        ]:
            pg.insert_text(c[k][:2], val, fontsize=c[k][2], rotate=270)
        qr_pix = _make_qr_pixmap(fol)
        pg.insert_image(fitz.Rect(80, 460, 80 + 97, 460 + 97), pixmap=qr_pix, overlay=True)
        doc.save(path)
        doc.close()
        logger.info(f"[PERMISO] OK: {path}")
        return path
    except Exception as e:
        logger.error(f"[ERROR PERMISO] {e}")
        return ""


def _generar_pdf_recibo(datos: dict) -> str:
    fol  = datos["folio"]
    path = f"{OUTPUT_DIR}/{fol}_recibo_tmp.pdf"
    c = COORDS_RECIBO
    try:
        doc  = fitz.open(PLANTILLA_RECIBO)
        page = doc[0]
        page.wrap_contents()
        for campo in ["folio", "nombre", "domicilio", "costo", "fecha_exp", "fecha_ven"]:
            if campo in c:
                x, y, size, color = c[campo]
                valor = datos.get(campo, "")
                if campo == "costo":
                    valor = f"${valor}"
                page.insert_text((x, y), str(valor), fontsize=size, color=color, fontname="helv")
        x, y, size, _ = c["qr"]
        qr_pix = _make_qr_pixmap(fol)
        page.insert_image(fitz.Rect(x, y, x + size, y + size), pixmap=qr_pix)
        doc.save(path)
        doc.close()
        logger.info(f"[RECIBO] OK: {path}")
        return path
    except Exception as e:
        logger.error(f"[ERROR RECIBO] {e}")
        return ""


def generar_pdf_unificado(datos: dict) -> str:
    """
    ⚠️ Candado compartido: PyMuPDF no es thread-safe. El panel generaba en el
    hilo de Flask y el bot en el threadpool de asyncio — sin este lock, dos
    permisos simultáneos corrompían los documentos entre sí.
    """
    with PDF_LOCK:
        fol   = datos["folio"]
        final = f"{OUTPUT_DIR}/{fol}.pdf"
        try:
            p1 = _generar_pdf_permiso(datos)
            p2 = _generar_pdf_recibo(datos)
            if not p1 or not p2:
                fallback = p1 or p2
                if fallback and fallback != final:
                    import shutil
                    shutil.copy(fallback, final)
                return final if os.path.exists(final) else ""
            d1 = fitz.open(p1)
            d2 = fitz.open(p2)
            d1.insert_pdf(d2)
            d1.save(final)
            d1.close()
            d2.close()
            for tmp in (p1, p2):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            logger.info(f"[PDF] Unificado OK: {final}")
            return final
        except Exception as e:
            logger.error(f"[ERROR UNIFICADO] {e}")
            return ""


def subir_pdf_a_storage(ruta_local: str, folio: str) -> str:
    try:
        with open(ruta_local, "rb") as f:
            contenido = f.read()
        nombre = f"{folio}.pdf"
        supabase.storage.from_(BUCKET_NAME).upload(
            path=nombre, file=contenido,
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )
        url = supabase.storage.from_(BUCKET_NAME).get_public_url(nombre)
        logger.info(f"[STORAGE] Subido: {url}")
        return url
    except Exception as e:
        logger.error(f"[STORAGE] Error {folio}: {e}")
        return ""


def generar_subir_y_guardar_pdf(datos: dict) -> tuple:
    ruta = generar_pdf_unificado(datos)
    url  = ""
    if ruta and os.path.exists(ruta):
        url = subir_pdf_a_storage(ruta, datos["folio"])
        if url:
            try:
                supabase.table("folios_registrados").update({"pdf_url": url}).eq("folio", datos["folio"]).execute()
            except Exception as e:
                logger.error(f"[DB] No se pudo guardar pdf_url: {e}")
    return ruta, url
