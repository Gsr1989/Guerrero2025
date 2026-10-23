"""
GUERRERO — Herramientas de admin: editor de tablas, ajuste de fechas, menú y
vista de timers del bot. Se registran sobre el mismo `app` de panel_guerrero.

Correcciones respecto a la versión que vivía dentro de panel_guerrero.py:
  · /admin/menu estaba definida DOS veces (mismo nombre de función) → Flask
    tronaba al importar. Ahora hay UNA sola, con todos los enlaces válidos.
  · PAGE_SIZE no existía → el editor de tablas daba 500.
  · /admin/timers_bot (enlazada desde el menú) no existía → ya existe
    (solo lectura).
  · Paginación: el texto de búsqueda ahora se codifica en la URL.
"""

import html as _html
from urllib.parse import quote_plus
from datetime import datetime, timedelta

from flask import request, redirect, url_for, session, jsonify, Response

from config_guerrero import supabase, logger, TZ_MX, DIAS_PERMISO
from panel_guerrero import app

PAGE_SIZE = 50

# ===================== TABLAS DEL EDITOR =====================
TABLAS_DISPONIBLES = {
    'folios_registrados': {
        'nombre':   'Folios Registrados',
        'pk_col':   'folio',
        'columnas': ['folio', 'marca', 'linea', 'anio', 'numero_serie', 'numero_motor',
                     'color', 'nombre', 'fecha_expedicion', 'fecha_vencimiento',
                     'entidad', 'estado', 'estado_pago', 'creado_por'],
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

# ═══════════════════════════════════════════════════════════════════════════
#  EDITOR DE TABLAS — administra Supabase desde AQUÍ, sin entrar al sitio
#  · Click en cualquier celda → se edita en línea y se guarda solo
#  · Las columnas de fecha abren calendario (fechas pasadas Y futuras libres)
#  · Botón para borrar renglón y para agregar uno nuevo
# ═══════════════════════════════════════════════════════════════════════════

_EDITOR_CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f2f4f7;color:#1d1d1b}
.bar{background:#8a5a00;color:#fff;padding:13px 18px;font-weight:700;font-size:15px;
     display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:50}
.bar a{color:#f0d5de;text-decoration:none;font-size:13px;font-weight:600}
.bar a:hover{color:#fff}
.wrap{max-width:1400px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.07);margin-bottom:16px}
.nota{background:#fbf3e3;border-left:4px solid #8a5a00;border-radius:8px;padding:12px 14px;
      font-size:13px;line-height:1.7;margin-bottom:16px}
.tablas-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.tabla-card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;
            text-decoration:none;color:inherit;display:block;transition:.15s}
.tabla-card:hover{border-color:#8a5a00;transform:translateY(-2px);box-shadow:0 4px 14px rgba(138,90,0,.15)}
.tabla-card h3{margin:0 0 6px;font-size:15px;color:#8a5a00}
.tabla-card p{margin:0;font-size:12px;color:#777}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
input[type=text],input[type=search]{padding:9px 12px;border:1.5px solid #d6dae0;border-radius:8px;font-size:14px;font-family:inherit}
input:focus{outline:none;border-color:#8a5a00}
.btn{padding:9px 16px;border:none;border-radius:8px;font-weight:700;font-size:13px;
     cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-family:inherit}
.btn-p{background:#8a5a00;color:#fff}.btn-p:hover{background:#6e4700}
.btn-o{background:#fff;border:1.5px solid #d6dae0;color:#444}.btn-o:hover{border-color:#8a5a00;color:#8a5a00}
.tabla-wrap{overflow-x:auto;background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
thead th{background:#8a5a00;color:#fff;padding:11px 10px;text-align:left;position:sticky;top:0}
tbody td{padding:6px 10px;border-bottom:1px solid #eef0f2;vertical-align:middle}
tbody tr:hover td{background:#fdf8ef}
.cv{display:inline-block;min-width:60px;max-width:240px;overflow:hidden;text-overflow:ellipsis;
    cursor:pointer;padding:4px 7px;border-radius:5px;border:1px solid transparent}
.cv:hover{border-color:#c8ccd2;background:#fff}
.cv.nv{color:#bbb;font-style:italic}
.cv.fecha{background:#fff8e6;border-color:#f0dfae}
.cell-input{border:2px solid #8a5a00;border-radius:5px;padding:4px 7px;font-size:13px;
            min-width:130px;outline:none;background:#fdf8ef;font-family:inherit}
.del{background:#fff;border:1px solid #e0c3c3;color:#c0392b;border-radius:5px;
     padding:4px 9px;font-size:12px;cursor:pointer}
.del:hover{background:#c0392b;color:#fff}
.toast{position:fixed;bottom:22px;right:18px;z-index:999;padding:11px 18px;border-radius:9px;
       font-size:13px;font-weight:600;opacity:0;transition:opacity .25s;pointer-events:none;
       box-shadow:0 4px 14px rgba(0,0,0,.15)}
.toast.show{opacity:1}
.toast.ok{background:#e7f8ee;color:#0a6b3d;border:1px solid #9ad9b8}
.toast.err{background:#fdeaea;color:#a32020;border:1px solid #eba9a9}
.paginacion{display:flex;gap:8px;justify-content:center;align-items:center;padding:14px}
.pg{background:#8a5a00;color:#fff;padding:7px 13px;border-radius:6px;font-size:13px;font-weight:700}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:900;display:flex;
       align-items:center;justify-content:center;padding:16px}
.modal-box{background:#fff;border-radius:12px;padding:24px;max-width:520px;width:100%;
           max-height:85vh;overflow-y:auto}
.modal-box h3{margin:0 0 16px;color:#8a5a00}
.campo{margin-bottom:12px}
.campo label{display:block;font-size:13px;font-weight:600;margin-bottom:4px}
.campo input{width:100%;padding:9px 12px;border:1.5px solid #d6dae0;border-radius:7px;
             font-size:14px;font-family:inherit}
"""

_EDITOR_JS = """
let TABLA='', PK='';
function initEditor(t,p){TABLA=t;PK=p;}

function editCell(span){
  if(span.dataset.editing==='1') return;
  span.dataset.editing='1';
  const col=span.dataset.col, pk=span.dataset.pk, orig=span.dataset.val;
  const esFecha = span.classList.contains('fecha');
  const inp=document.createElement('input');
  inp.type = esFecha ? 'date' : 'text';
  inp.className='cell-input';
  inp.value = esFecha ? (orig||'').substring(0,10) : orig;
  inp._span=span; inp._orig=orig; inp._col=col; inp._pk=pk;
  span.parentNode.insertBefore(inp,span);
  span.style.display='none';
  inp.focus(); if(!esFecha) inp.select();
  inp.addEventListener('blur',()=>fin(inp));
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();inp.blur();}
    if(e.key==='Escape'){inp._cancel=true;inp.blur();}
  });
}

function fin(inp){
  const span=inp._span, nv=inp.value.trim(), orig=inp._orig;
  inp.remove(); span.style.display=''; span.dataset.editing='0';
  if(inp._cancel||nv===orig) return;
  span.textContent = nv||'null';
  span.dataset.val = nv;
  span.classList.toggle('nv',!nv);
  fetch('/api/update_cell',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:inp._pk,col:inp._col,val:nv})})
   .then(r=>r.json()).then(d=>{
      if(d.ok) toast('✓ Guardado en Supabase',true);
      else{span.textContent=orig||'null';span.dataset.val=orig;toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{span.textContent=orig||'null';span.dataset.val=orig;toast('Error de red',false);});
}

function delRow(btn,pk,rowId){
  if(!confirm('¿Eliminar este registro de Supabase?\\n\\nEsto NO se puede deshacer.')) return;
  btn.disabled=true; btn.textContent='...';
  fetch('/api/delete_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:pk})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){const tr=document.getElementById(rowId);
        if(tr){tr.style.opacity='0';setTimeout(()=>tr.remove(),250);}
        toast('Eliminado',true);}
      else{btn.disabled=false;btn.textContent='Borrar';toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{btn.disabled=false;btn.textContent='Borrar';toast('Error de red',false);});
}

function abrirNuevo(){document.getElementById('modalNuevo').style.display='flex';}
function cerrarNuevo(){document.getElementById('modalNuevo').style.display='none';}

function guardarNuevo(){
  const campos={};
  document.querySelectorAll('#formNuevo [data-campo]').forEach(el=>{
    const v=el.value.trim(); if(v) campos[el.dataset.campo]=v;
  });
  if(Object.keys(campos).length===0){toast('Llena al menos un campo',false);return;}
  fetch('/api/add_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,datos:campos})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){toast('Agregado — recargando...',true);setTimeout(()=>location.reload(),700);}
      else toast('Error: '+(d.error||'?'),false);
   }).catch(()=>toast('Error de red',false));
}

let tt;
function toast(msg,ok){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+(ok?'ok':'err');
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('show'),2600);
}
"""


def _esc(v):
    return _html.escape(str(v), quote=True)


def _editor_head(titulo: str) -> str:
    return f"""<!DOCTYPE html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo} — GUERRERO</title><style>{_EDITOR_CSS}</style></head><body>"""


@app.route('/admin/editor')
def admin_editor():
    if not session.get('admin'):
        return redirect(url_for('login'))
    cards = "".join(
        f"""<a class="tabla-card" href="/admin/editor/{nombre}">
              <h3>🗄️ {info['nombre']}</h3>
              <p><code>{nombre}</code> · {len(info['columnas'])} columnas</p>
            </a>"""
        for nombre, info in TABLAS_DISPONIBLES.items()
    )
    return Response(_editor_head("Editor de Tablas") + f"""
<div class="bar">🛠️ Editor de Base de Datos — GUERRERO
  <span style="margin-left:auto"><a href="/admin">← Panel</a></span>
</div>
<div class="wrap">
  <div class="nota">
    Desde aquí administras <strong>Supabase directamente</strong>, sin entrar a su sitio.<br>
    Entra a una tabla, haz click en cualquier celda y edítala: se guarda sola.<br>
    Las columnas de fecha abren calendario y aceptan <strong>cualquier fecha,
    pasada o futura</strong>.
  </div>
  <div class="tablas-grid">{cards}</div>
</div>
</body></html>""", mimetype="text/html")


@app.route('/admin/editor/<nombre_tabla>')
def admin_editor_tabla(nombre_tabla):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))

    info   = TABLAS_DISPONIBLES[nombre_tabla]
    pk_col = info['pk_col']
    q      = request.args.get('q', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except ValueError:
        page = 1

    try:
        todos = supabase.table(nombre_tabla).select("*").limit(20000).execute().data or []
        if q:
            ql = q.lower()
            filtrados = [r for r in todos
                         if any(ql in str(v).lower() for v in r.values() if v is not None)]
        else:
            filtrados = todos
        total     = len(filtrados)
        offset    = (page - 1) * PAGE_SIZE
        registros = filtrados[offset: offset + PAGE_SIZE]
    except Exception as e:
        logger.error(f"[EDITOR] {e}")
        todos = filtrados = registros = []
        total = offset = 0

    columnas = list(registros[0].keys()) if registros else (
        list(todos[0].keys()) if todos else info['columnas'])
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    esc = _esc
    th = "".join(f"<th>{esc(c)}</th>" for c in columnas) + "<th>Acción</th>"

    filas = ""
    for i, reg in enumerate(registros):
        celdas = f'<td style="color:#bbb;font-size:11px">{offset + i + 1}</td>'
        pk_val = esc(reg.get(pk_col, ""))
        for col in columnas:
            val = reg.get(col)
            disp = str(val) if val is not None else "null"
            clases = "cv"
            if val is None:
                clases += " nv"
            if col in COLUMNAS_FECHA:
                clases += " fecha"
            celdas += (
                f'<td><span class="{clases}" data-col="{esc(col)}" data-pk="{pk_val}" '
                f'data-val="{esc(val if val is not None else "")}" '
                f'onclick="editCell(this)">{esc(disp[:40])}</span></td>'
            )
        celdas += (f'<td><button class="del" onclick="delRow(this,\'{pk_val}\',\'r{i}\')">'
                   f'Borrar</button></td>')
        filas += f'<tr id="r{i}">{celdas}</tr>'

    if not filas:
        filas = (f'<tr><td colspan="{len(columnas)+2}" '
                 f'style="text-align:center;padding:26px;color:#999">Sin registros</td></tr>')

    qurl = quote_plus(q)
    pag = ""
    if total_pages > 1:
        pag = '<div class="paginacion">'
        if page > 1:
            pag += f'<a class="btn btn-o" href="?q={qurl}&page={page-1}">← Anterior</a>'
        pag += f'<span class="pg">{page} / {total_pages}</span>'
        if page < total_pages:
            pag += f'<a class="btn btn-o" href="?q={qurl}&page={page+1}">Siguiente →</a>'
        pag += '</div>'

    campos_nuevo = "".join(
        f"""<div class="campo"><label>{esc(c)}</label>
            <input type="{'date' if c in COLUMNAS_FECHA else 'text'}" data-campo="{esc(c)}"></div>"""
        for c in info['columnas'] if c != 'id'
    )

    return Response(_editor_head(info['nombre']) + f"""
<div class="bar">🗄️ {esc(info['nombre'])}
  <span style="margin-left:auto">
    <a href="/admin/editor">← Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Click en cualquier celda para editarla. Se guarda sola en Supabase al salir
    del campo o al presionar Enter. <strong>Esc</strong> cancela.<br>
    Las celdas amarillas son fechas: abren calendario y aceptan
    <strong>cualquier fecha, pasada o futura</strong>.
  </div>

  <div class="toolbar">
    <form method="GET" style="display:contents">
      <input type="search" name="q" value="{esc(q)}" placeholder="Buscar en toda la tabla...">
      <button class="btn btn-p" type="submit">Buscar</button>
      {'<a class="btn btn-o" href="/admin/editor/' + nombre_tabla + '">Limpiar</a>' if q else ''}
    </form>
    <button class="btn btn-p" onclick="abrirNuevo()">+ Agregar registro</button>
    <span style="margin-left:auto;font-size:13px;color:#777">{total} registros</span>
  </div>

  <div class="tabla-wrap">
    <table><thead><tr><th>#</th>{th}</tr></thead><tbody>{filas}</tbody></table>
    {pag}
  </div>
</div>

<div class="modal" id="modalNuevo" style="display:none">
  <div class="modal-box">
    <h3>Agregar registro a {esc(info['nombre'])}</h3>
    <div id="formNuevo">{campos_nuevo}</div>
    <div style="display:flex;gap:8px;margin-top:18px">
      <button class="btn btn-p" onclick="guardarNuevo()">Guardar</button>
      <button class="btn btn-o" onclick="cerrarNuevo()">Cancelar</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}
initEditor("{nombre_tabla}","{pk_col}");
</script>
</body></html>""", mimetype="text/html")


@app.route('/api/update_cell', methods=['POST'])
def api_update_cell():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    col    = d.get('col')
    val    = d.get('val', '')
    if tabla not in TABLAS_DISPONIBLES or not col or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).update({col: val if val != "" else None}) \
            .eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] {tabla}.{col} de {pk_val} → {val!r}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[EDITOR] update: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/delete_row', methods=['POST'])
def api_delete_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    if tabla not in TABLAS_DISPONIBLES or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).delete().eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] borrado {tabla} pk={pk_val}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/add_row', methods=['POST'])
def api_add_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d     = request.get_json(force=True)
    tabla = d.get('tabla')
    datos = d.get('datos') or {}
    if tabla not in TABLAS_DISPONIBLES or not datos:
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).insert(datos).execute()
        logger.info(f"[EDITOR] insertado en {tabla}: {list(datos.keys())}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  AJUSTE RÁPIDO DE FECHAS — pon cualquier fecha a un folio, pasada o futura
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/fechas', methods=['GET', 'POST'])
def admin_fechas():
    if not session.get('admin'):
        return redirect(url_for('login'))

    msg = ""
    folio_buscar = request.args.get('folio', '').strip().upper()
    registro = None

    if request.method == 'POST':
        folio  = request.form.get('folio', '').strip().upper()
        accion = request.form.get('accion', '')
        f_exp  = request.form.get('fecha_expedicion', '').strip()
        f_ven  = request.form.get('fecha_vencimiento', '').strip()
        try:
            if accion == 'manual':
                parches = {}
                if f_exp: parches['fecha_expedicion'] = f_exp
                if f_ven: parches['fecha_vencimiento'] = f_ven
                if parches:
                    supabase.table("folios_registrados").update(parches).eq("folio", folio).execute()
                    msg = f"Fechas del folio {folio} actualizadas."
                else:
                    msg = "No mandaste ninguna fecha."
            elif accion == 'vencer':
                ayer = (datetime.now(TZ_MX) - timedelta(days=1)).date().isoformat()
                supabase.table("folios_registrados").update(
                    {"fecha_vencimiento": ayer}).eq("folio", folio).execute()
                msg = f"Folio {folio} marcado VENCIDO."
            elif accion == 'restaurar':
                hoy = datetime.now(TZ_MX)
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  hoy.date().isoformat(),
                    "fecha_vencimiento": (hoy + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} restaurado a {DIAS_PERMISO} días desde hoy."
            elif accion == 'retro':
                dias = int(request.form.get('dias_atras', '30') or 30)
                base = datetime.now(TZ_MX) - timedelta(days=dias)
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  base.date().isoformat(),
                    "fecha_vencimiento": (base + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} expedido {dias} días atrás."
        except Exception as e:
            msg = f"Error: {e}"
        folio_buscar = folio

    if folio_buscar:
        try:
            r = supabase.table("folios_registrados").select("*").eq("folio", folio_buscar).execute()
            registro = r.data[0] if r.data else None
            if not registro and not msg:
                msg = f"Folio {folio_buscar} no encontrado."
        except Exception as e:
            msg = f"Error: {e}"

    esc = _esc

    ficha = ""
    if registro:
        fe = str(registro.get('fecha_expedicion', ''))[:10]
        fv = str(registro.get('fecha_vencimiento', ''))[:10]
        ficha = f"""
        <div class="card">
          <div style="font-size:15px;font-weight:700;color:#8a5a00;margin-bottom:12px">
            Folio {esc(registro.get('folio',''))}
          </div>
          <div style="font-size:13px;line-height:2;margin-bottom:16px">
            <strong>Titular:</strong> {esc(registro.get('nombre',''))}<br>
            <strong>Vehículo:</strong> {esc(registro.get('marca',''))} {esc(registro.get('linea',''))} {esc(registro.get('anio',''))}<br>
            <strong>Expedición actual:</strong> {esc(fe)}<br>
            <strong>Vencimiento actual:</strong> {esc(fv)}<br>
            <a href="/consulta/{esc(registro.get('folio',''))}" target="_blank"
               style="color:#8a5a00">🔗 Ver consulta pública</a>
          </div>
          <form method="POST">
            <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
            <input type="hidden" name="accion" value="manual">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="campo"><label>Nueva fecha de expedición</label>
                <input type="date" name="fecha_expedicion" value="{esc(fe)}"></div>
              <div class="campo"><label>Nueva fecha de vencimiento</label>
                <input type="date" name="fecha_vencimiento" value="{esc(fv)}"></div>
            </div>
            <button class="btn btn-p" type="submit">Guardar estas fechas</button>
            <p style="font-size:12px;color:#777;margin-top:8px">
              Sin límite: puedes poner fechas de años atrás o muy adelante.
            </p>
          </form>
          <hr style="margin:20px 0;border:none;border-top:1px solid #eee">
          <div style="font-size:13px;font-weight:700;margin-bottom:10px">Atajos</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="vencer">
              <button class="btn" style="background:#b38b00;color:#fff">⏰ Marcar vencido</button>
            </form>
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="restaurar">
              <button class="btn" style="background:#1a6e2e;color:#fff">✅ Restaurar {DIAS_PERMISO} días</button>
            </form>
            <form method="POST" style="display:inline;display:flex;gap:6px;align-items:center">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="retro">
              <input type="text" name="dias_atras" value="30" style="width:70px">
              <button class="btn btn-o">📅 Días atrás</button>
            </form>
          </div>
        </div>"""

    msg_html = (f'<div class="nota" style="border-left-color:#8a5a00">{esc(msg)}</div>'
                if msg else "")

    return Response(_editor_head("Ajuste de Fechas") + f"""
<div class="bar">📅 Ajuste de Fechas — GUERRERO
  <span style="margin-left:auto">
    <a href="/admin/editor">Editor de Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Cambia las fechas de cualquier folio sin restricción: <strong>pasadas o
    futuras</strong>. Útil para permisos retroactivos o para pruebas.
  </div>
  {msg_html}
  <div class="card">
    <form method="GET">
      <div class="toolbar">
        <input type="text" name="folio" value="{esc(folio_buscar)}"
               placeholder="ZY4917" style="min-width:220px">
        <button class="btn btn-p" type="submit">Buscar folio</button>
      </div>
    </form>
  </div>
  {ficha}
</div>
<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}</script>
</body></html>""", mimetype="text/html")


# ═══════════════════════════════════════════════════════════════════════════
#  TIMERS DEL BOT (solo lectura)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/timers_bot')
def admin_timers_bot():
    if not session.get('admin'):
        return redirect(url_for('login'))

    try:
        import bot_guerrero   # import tardío: no depende del orden de arranque
        timers = bot_guerrero.snapshot_timers()
    except Exception as e:
        logger.error(f"[TIMERS BOT] {e}")
        timers = []

    esc = _esc
    filas = "".join(
        f"<tr><td>{esc(t['folio'])}</td><td>{esc(t['user_id'])}</td>"
        f"<td>{esc(t['restante'])}</td></tr>"
        for t in timers
    ) or ('<tr><td colspan="3" style="text-align:center;padding:26px;color:#999">'
          'No hay timers activos</td></tr>')

    return Response(_editor_head("Timers del Bot") + f"""
<div class="bar">⏱️ Timers del Bot — GUERRERO
  <span style="margin-left:auto"><a href="/admin/menu">← Menú</a> &nbsp;·&nbsp; <a href="/admin">Panel</a></span>
</div>
<div class="wrap">
  <div class="nota">
    Folios del bot que esperan pago (36 h). Es solo de lectura: para validar o
    detener uno usa los botones del propio bot en Telegram.
  </div>
  <div class="tabla-wrap">
    <table><thead><tr><th>Folio</th><th>Usuario Telegram</th><th>Tiempo restante</th></tr></thead>
    <tbody>{filas}</tbody></table>
  </div>
</div>
</body></html>""", mimetype="text/html")


# ═══════════════════════════════════════════════════════════════════════════
#  MENÚ DE ACCESOS RÁPIDOS (una sola definición)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/menu')
def admin_menu():
    """Menú de accesos rápidos — no depende de panel.html."""
    if not session.get('admin'):
        return redirect(url_for('login'))
    items = [
        ("🗄️ Editor de Tablas",    "/admin/editor",         "Edita cualquier celda de Supabase sin entrar a su sitio"),
        ("📅 Ajuste de Fechas",    "/admin/fechas",         "Pon cualquier fecha a un folio, pasada o futura"),
        ("🧪 Test Fechas (viejo)", "/admin/test_fechas",    "Tu herramienta de pruebas original"),
        ("⏱️ Timers del Bot",     "/admin/timers_bot",     "Ver los timers de 36h del bot de Telegram"),
        ("📋 Ver Registros",       "/ver_registros_admin",  "Tu vista de folios de siempre"),
        ("🏠 Panel principal",     "/admin",                "Volver al panel"),
    ]
    filas = "".join(
        f"""<a class="tabla-card" href="{url}"><h3>{titulo}</h3><p>{desc}</p></a>"""
        for titulo, url, desc in items
    )
    return Response(_editor_head("Menú Admin") + f"""
<div class="bar">🧭 Menú Admin — GUERRERO</div>
<div class="wrap">
  <div class="nota">Accesos nuevos que no están enlazados desde tu panel.html original.</div>
  <div class="tablas-grid">{filas}</div>
</div>
</body></html>""", mimetype="text/html")
