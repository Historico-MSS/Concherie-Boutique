import os
import re
import uuid
import math
import base64
import mimetypes
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st
from PIL import Image

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Concherie Boutique", page_icon="🏷️", layout="wide")

USERS = {
    "jc": {"password": "master", "role": "admin", "label": "JC"},
    "ventas": {"password": "moira", "role": "ventas", "label": "Ventas"},
    "info": {"password": "precio", "role": "info", "label": "Info"},
}

VALID_STATES = ["disponible", "apartado", "vendido", "probando", "mantenimiento"]
PAYMENT_METHODS = ["Efectivo", "Transferencia", "Otro"]

INVENTORY_COLUMNS = [
    "numero", "codigo", "codigo_interno", "producto", "color", "talla", "precio",
    "estado", "cliente_id", "nota_id", "fecha_apartado", "fecha_venta", "descuento_pct",
    "foto_url", "notas", "fecha_actualizacion"
]
CLIENT_COLUMNS = ["cliente_id", "nombre", "telefono", "email", "notas", "fecha_creacion"]
NOTES_COLUMNS = [
    "nota_id", "cliente_id", "cliente_nombre", "fecha_creacion", "estado",
    "total_venta", "total_apartado", "pagado", "saldo", "pdf_url", "fecha_actualizacion"
]
SALES_COLUMNS = [
    "linea_id", "nota_id", "cliente_id", "fecha", "tipo", "numero", "codigo", "codigo_interno",
    "producto", "color", "talla", "precio", "descuento_pct", "descuento_monto", "neto", "estado"
]
PAYMENTS_COLUMNS = [
    "pago_id", "nota_id", "cliente_id", "fecha", "monto", "metodo", "nota", "soporte_url", "usuario"
]
MOVEMENT_COLUMNS = ["fecha", "usuario", "tipo", "referencia", "detalle"]

# ============================================================
# HELPERS
# ============================================================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def clean(x):
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() in ["nan", "none", "null"] else s


def money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"


def fnum(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def inum(x, default=0):
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except Exception:
        return default


def display_talla(t):
    t = clean(t)
    if not t or t in ["T0", "0", "TU", "ÚNICA", "UNICA"]:
        return "Talla Única"
    return t if t.startswith("T") else f"T{t}"


def slug(text):
    text = clean(text)
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    return text.strip("_") or "archivo"


def calc_discount_amount(price, pct):
    price = fnum(price)
    pct = fnum(pct)
    return round(price * pct / 100, 2)


def calc_net(price, pct):
    return round(fnum(price) - calc_discount_amount(price, pct), 2)


def set_page(page):
    st.session_state.page = page
    st.rerun()


def role():
    return st.session_state.get("role", "")


def is_admin():
    return role() == "admin"


def can_sell():
    return role() in ["admin", "ventas"]


def ensure_cols(df, cols):
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=cols)
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def normalize_numeric(df):
    for c in ["precio", "descuento_pct", "total_venta", "total_apartado", "pagado", "saldo", "descuento_monto", "neto", "monto"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df

# ============================================================
# STORAGE: GOOGLE SHEETS
# ============================================================
def gsheets_configured():
    try:
        return "connections" in st.secrets and "gsheets" in st.secrets["connections"]
    except Exception:
        return False


@st.cache_resource
def get_gsheets_connection():
    from streamlit_gsheets import GSheetsConnection
    return st.connection("gsheets", type=GSheetsConnection)


def local_path(name):
    os.makedirs("data", exist_ok=True)
    return os.path.join("data", f"{name}.csv")


def load_table(name, cols):
    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            df = conn.read(worksheet=name, ttl=0)
            if df is None:
                df = pd.DataFrame(columns=cols)
            if "Unnamed: 0" in df.columns:
                df = df.drop(columns=["Unnamed: 0"])
            return normalize_numeric(ensure_cols(df, cols))
        except Exception as e:
            st.session_state.storage_warning = f"No pude leer Google Sheets; usando local. {name}: {e}"
    path = local_path(name)
    if os.path.exists(path):
        try:
            return normalize_numeric(ensure_cols(pd.read_csv(path), cols))
        except Exception:
            pass
    return pd.DataFrame(columns=cols)


def save_table(name, df, cols):
    df = normalize_numeric(ensure_cols(df, cols))
    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            conn.update(worksheet=name, data=df)
            return True, "Guardado en Google Sheets"
        except Exception as e:
            st.session_state.storage_warning = f"No pude guardar en Google Sheets; guardé local. {name}: {e}"
    df.to_csv(local_path(name), index=False)
    return True, "Guardado local"


def load_all():
    if st.session_state.get("loaded"):
        return
    st.session_state.inventario = load_table("inventario", INVENTORY_COLUMNS)
    st.session_state.clientes = load_table("clientes", CLIENT_COLUMNS)
    st.session_state.notas = load_table("notas", NOTES_COLUMNS)
    st.session_state.ventas = load_table("ventas", SALES_COLUMNS)
    st.session_state.pagos = load_table("pagos", PAYMENTS_COLUMNS)
    st.session_state.movimientos = load_table("movimientos", MOVEMENT_COLUMNS)
    st.session_state.loaded = True


def save_inventory(): return save_table("inventario", st.session_state.inventario, INVENTORY_COLUMNS)
def save_clients(): return save_table("clientes", st.session_state.clientes, CLIENT_COLUMNS)
def save_notes(): return save_table("notas", st.session_state.notas, NOTES_COLUMNS)
def save_sales(): return save_table("ventas", st.session_state.ventas, SALES_COLUMNS)
def save_payments(): return save_table("pagos", st.session_state.pagos, PAYMENTS_COLUMNS)
def save_movements(): return save_table("movimientos", st.session_state.movimientos, MOVEMENT_COLUMNS)


def log(tipo, referencia="", detalle=""):
    row = {"fecha": now_str(), "usuario": st.session_state.get("user", ""), "tipo": tipo, "referencia": referencia, "detalle": detalle}
    st.session_state.movimientos = pd.concat([st.session_state.movimientos, pd.DataFrame([row])], ignore_index=True)
    save_movements()

# ============================================================
# SUPABASE STORAGE
# ============================================================
def supabase_configured():
    try:
        return "supabase" in st.secrets and st.secrets["supabase"].get("url") and st.secrets["supabase"].get("key")
    except Exception:
        return False


def supabase_public_url(path):
    url = st.secrets["supabase"]["url"].rstrip("/")
    bucket = st.secrets["supabase"].get("bucket", "concherie-files")
    return f"{url}/storage/v1/object/public/{bucket}/{path}"


def upload_to_supabase(data_bytes, path, content_type="application/octet-stream"):
    if not supabase_configured():
        return "", "Supabase no está configurado"
    url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["key"]
    bucket = st.secrets["supabase"].get("bucket", "concherie-files")
    endpoint = f"{url}/storage/v1/object/{bucket}/{path}"
    headers = {"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": content_type, "x-upsert": "true"}
    try:
        r = requests.post(endpoint, headers=headers, data=data_bytes, timeout=30)
        if r.status_code not in [200, 201]:
            return "", f"Supabase error {r.status_code}: {r.text[:250]}"
        return supabase_public_url(path), ""
    except Exception as e:
        return "", str(e)


def compress_image(uploaded_file, max_size=1400, quality=80):
    img = Image.open(uploaded_file)
    img = img.convert("RGB")
    w, h = img.size
    scale = min(max_size / max(w, h), 1.0)
    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)))
    bio = BytesIO()
    img.save(bio, format="JPEG", quality=quality, optimize=True)
    return bio.getvalue(), "image/jpeg"

# ============================================================
# IDs AND BUSINESS LOGIC
# ============================================================
def next_numeric_code():
    inv = st.session_state.inventario
    nums = []
    for v in inv.get("numero", pd.Series(dtype=str)).astype(str):
        if v.strip().isdigit(): nums.append(int(v.strip()))
    n = max(nums) + 1 if nums else 1
    return f"{n:03d}"


def next_client_id():
    clients = st.session_state.clientes
    nums = []
    for v in clients.get("cliente_id", pd.Series(dtype=str)).astype(str):
        m = re.search(r"CL-(\d+)", v)
        if m: nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"CL-{n:03d}"


def next_note_id():
    notes = st.session_state.notas
    nums = []
    for v in notes.get("nota_id", pd.Series(dtype=str)).astype(str):
        m = re.search(r"NV-(\d+)", v)
        if m: nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"NV-{n:04d}"


def get_client_name(client_id):
    if not clean(client_id): return ""
    df = st.session_state.clientes
    row = df[df["cliente_id"].astype(str) == str(client_id)]
    return clean(row.iloc[0]["nombre"]) if not row.empty else ""


def ensure_client(nombre, telefono="", email="", notas=""):
    nombre = clean(nombre)
    if not nombre:
        return ""
    clients = st.session_state.clientes
    existing = clients[clients["nombre"].astype(str).str.lower() == nombre.lower()]
    if not existing.empty:
        cid = clean(existing.iloc[0]["cliente_id"])
        if not cid:
            cid = next_client_id()
            clients.loc[existing.index[0], "cliente_id"] = cid
            save_clients()
        return cid
    cid = next_client_id()
    row = {"cliente_id": cid, "nombre": nombre, "telefono": telefono, "email": email, "notas": notas, "fecha_creacion": now_str()}
    st.session_state.clientes = pd.concat([clients, pd.DataFrame([row])], ignore_index=True)
    save_clients()
    log("crear_cliente", cid, nombre)
    return cid


def active_note_for_client(client_id):
    notes = st.session_state.notas
    active = notes[(notes["cliente_id"].astype(str) == str(client_id)) & (notes["estado"].astype(str) == "abierta")]
    if not active.empty:
        return clean(active.iloc[0]["nota_id"])
    nid = next_note_id()
    cname = get_client_name(client_id)
    row = {"nota_id": nid, "cliente_id": client_id, "cliente_nombre": cname, "fecha_creacion": now_str(), "estado": "abierta", "total_venta": 0.0, "total_apartado": 0.0, "pagado": 0.0, "saldo": 0.0, "pdf_url": "", "fecha_actualizacion": now_str()}
    st.session_state.notas = pd.concat([notes, pd.DataFrame([row])], ignore_index=True)
    save_notes()
    log("crear_nota", nid, f"Cliente {client_id}")
    return nid


def recalc_note(nota_id):
    sales = st.session_state.ventas[st.session_state.ventas["nota_id"].astype(str) == str(nota_id)].copy()
    payments = st.session_state.pagos[st.session_state.pagos["nota_id"].astype(str) == str(nota_id)].copy()
    total_venta = sales[sales["tipo"] == "venta"]["neto"].astype(float).sum() if not sales.empty else 0.0
    total_apartado = sales[sales["tipo"] == "apartado"]["neto"].astype(float).sum() if not sales.empty else 0.0
    pagado = payments["monto"].astype(float).sum() if not payments.empty else 0.0
    saldo = total_venta - pagado
    notes = st.session_state.notas
    idxs = notes[notes["nota_id"].astype(str) == str(nota_id)].index
    if len(idxs):
        idx = idxs[0]
        notes.at[idx, "total_venta"] = round(total_venta, 2)
        notes.at[idx, "total_apartado"] = round(total_apartado, 2)
        notes.at[idx, "pagado"] = round(pagado, 2)
        notes.at[idx, "saldo"] = round(saldo, 2)
        notes.at[idx, "fecha_actualizacion"] = now_str()
        if saldo <= 0 and total_venta > 0:
            notes.at[idx, "estado"] = "pagada"
    st.session_state.notas = notes
    save_notes()


def find_piece(query):
    q = clean(query)
    inv = st.session_state.inventario
    if not q or inv.empty:
        return None, pd.DataFrame()
    # first by numeric code
    exact = inv[inv["numero"].astype(str).str.zfill(3) == q.zfill(3)] if q.isdigit() else pd.DataFrame()
    if not exact.empty:
        return exact.index[0], exact
    mask = inv.apply(lambda r: q.lower() in " ".join([str(v).lower() for v in r.values]), axis=1)
    res = inv[mask]
    if len(res) == 1:
        return res.index[0], res
    return None, res

# ============================================================
# PDFS AND QR
# ============================================================
def qr_png_bytes(text):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=2)
    qr.add_data(clean(text))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    bio = BytesIO()
    img.save(bio, "PNG")
    return bio.getvalue()


def create_labels_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader

    data = data.copy().sort_values("codigo_interno")
    bio = BytesIO(); c = canvas.Canvas(bio, pagesize=letter)
    W, H = letter
    label_w, label_h = 8*cm, 5*cm
    qr_size = 4*cm
    margin_x, margin_y = 0.4*cm, 0.6*cm
    cols = 2
    rows = int((H - margin_y*2) // label_h)
    per_page = cols * rows

    def draw_grid():
        c.setDash(2, 2); c.setLineWidth(0.35)
        # vertical cut line between columns
        x = margin_x + label_w
        c.line(x, margin_y, x, H-margin_y)
        # horizontal lines
        for r in range(1, rows):
            y = H - margin_y - r*label_h
            c.line(margin_x, y, margin_x + cols*label_w, y)
        c.setDash()

    draw_grid()
    for i, (_, r) in enumerate(data.iterrows()):
        if i > 0 and i % per_page == 0:
            c.showPage(); draw_grid()
        pos = i % per_page
        col = pos % cols; row = pos // cols
        x0 = margin_x + col*label_w
        y0 = H - margin_y - (row+1)*label_h
        qr = ImageReader(BytesIO(qr_png_bytes(str(r["numero"]).zfill(3))))
        c.drawImage(qr, x0+0.35*cm, y0+0.5*cm, qr_size, qr_size, preserveAspectRatio=True, mask='auto')
        tx = x0 + 4.7*cm
        c.setFont("Helvetica-Bold", 27)
        c.drawString(tx, y0 + 3.75*cm, str(r["numero"]).zfill(3))
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(tx, y0 + 3.20*cm, clean(r["producto"])[:24])
        c.setFont("Helvetica", 8.5)
        c.drawString(tx, y0 + 2.78*cm, clean(r["color"])[:18])
        c.drawString(tx, y0 + 2.38*cm, display_talla(r["talla"])[:18])
        c.setFont("Helvetica", 5.8)
        c.drawString(tx, y0 + 0.68*cm, clean(r["codigo_interno"])[:32])
    c.save(); bio.seek(0); return bio.getvalue()


def create_note_pdf(nota_id, disclaimer=True):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    notes = st.session_state.notas
    note = notes[notes["nota_id"].astype(str) == str(nota_id)]
    if note.empty:
        return b""
    n = note.iloc[0]
    cliente = clean(n["cliente_nombre"]) or get_client_name(n["cliente_id"])
    sales = st.session_state.ventas[st.session_state.ventas["nota_id"].astype(str) == str(nota_id)].copy()
    payments = st.session_state.pagos[st.session_state.pagos["nota_id"].astype(str) == str(nota_id)].copy()

    bio = BytesIO(); c = canvas.Canvas(bio, pagesize=letter); W,H = letter
    y = H - 0.65*inch
    c.setFont("Helvetica-Bold", 18); c.drawString(0.7*inch, y, "CONCHERIE BOUTIQUE")
    y -= 0.32*inch
    c.setFont("Helvetica", 10); c.drawString(0.7*inch, y, f"Nota de venta: {nota_id}")
    c.drawRightString(7.7*inch, y, f"Fecha: {today_str()}")
    y -= 0.25*inch
    c.setFont("Helvetica-Bold", 10); c.drawString(0.7*inch, y, f"Cliente: {cliente}")
    c.setFont("Helvetica", 9); c.drawRightString(7.7*inch, y, f"ID: {n['cliente_id']}")
    y -= 0.35*inch

    def section(title, df, y):
        if df.empty: return y
        c.setFont("Helvetica-Bold", 12); c.drawString(0.7*inch, y, title); y -= 0.22*inch
        c.setLineWidth(0.5); c.line(0.7*inch, y, 7.7*inch, y); y -= 0.22*inch
        for _, r in df.sort_values("fecha").iterrows():
            if y < 1.2*inch:
                c.showPage(); y = H - 0.7*inch
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(0.8*inch, y, f"{clean(r['numero']).zfill(3)} · {clean(r['producto'])[:42]}")
            c.setFont("Helvetica", 8.5)
            c.drawRightString(7.5*inch, y, money(r['neto']))
            y -= 0.18*inch
            c.drawString(0.8*inch, y, f"{clean(r['codigo_interno'])} · {display_talla(r['talla'])} · {clean(r['fecha'])[:10]}")
            if fnum(r.get('descuento_pct',0)):
                c.drawRightString(7.5*inch, y, f"Desc. {fnum(r['descuento_pct']):.0f}%")
            y -= 0.25*inch
        y -= 0.08*inch
        return y

    ventas = sales[sales["tipo"] == "venta"]
    apartados = sales[sales["tipo"] == "apartado"]
    y = section("VENTAS", ventas, y)
    y = section("APARTADOS", apartados, y)
    if disclaimer and not apartados.empty:
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawString(0.8*inch, y, "Nota: las piezas apartadas se mantienen reservadas por un máximo de 10 días.")
        y -= 0.35*inch

    if y < 1.8*inch:
        c.showPage(); y = H - 0.7*inch
    c.setFont("Helvetica-Bold", 10)
    c.drawString(4.7*inch, y, "Total ventas:"); c.drawRightString(7.5*inch, y, money(n['total_venta'])); y-=0.22*inch
    c.drawString(4.7*inch, y, "Total apartados:"); c.drawRightString(7.5*inch, y, money(n['total_apartado'])); y-=0.22*inch
    c.drawString(4.7*inch, y, "Pagado:"); c.drawRightString(7.5*inch, y, money(n['pagado'])); y-=0.28*inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(4.7*inch, y, "SALDO:"); c.drawRightString(7.5*inch, y, money(n['saldo']))
    c.save(); bio.seek(0); return bio.getvalue()

# ============================================================
# PAGES
# ============================================================
def login_page():
    st.title("Concherie Boutique")
    st.caption("Sistema de inventario, ventas y catálogo")
    with st.form("login"):
        u = st.text_input("Usuario").strip().lower()
        p = st.text_input("Clave", type="password")
        ok = st.form_submit_button("Entrar", use_container_width=True)
    if ok:
        if u in USERS and USERS[u]["password"] == p:
            st.session_state.user = u; st.session_state.role = USERS[u]["role"]; st.session_state.page = "home"; st.rerun()
        else:
            st.error("Usuario o clave incorrectos")


def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user','')}**")
    st.sidebar.success("Datos: Google Sheets" if gsheets_configured() else "Datos: local")
    st.sidebar.success("Archivos: Supabase" if supabase_configured() else "Archivos: no configurado")
    if st.session_state.get("storage_warning"):
        st.sidebar.info(st.session_state.storage_warning)
    buttons = [("🏠 Inicio","home"),("🔎 Buscar código","buscar")]
    if can_sell(): buttons += [("🛍️ Ventas","ventas"),("👥 Clientes","clientes"),("📄 Catálogo","catalogo")]
    if is_admin(): buttons += [("📥 Cargar recepción","carga"),("🏷️ Generar QR","qr"),("📊 Reportes","reportes"),("⚙️ Admin","admin")]
    for label, pg in buttons:
        if st.sidebar.button(label, use_container_width=True): set_page(pg)
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        for k in ["user","role","page"]: st.session_state.pop(k, None)
        st.rerun()


def home_page():
    r = role(); st.title("Concherie Boutique")
    if r == "info":
        st.button("🔎 Buscar / escanear código", use_container_width=True, on_click=lambda: set_page("buscar"))
    elif r == "ventas":
        c1,c2 = st.columns(2)
        with c1:
            st.button("🔎 Buscar código", use_container_width=True, on_click=lambda: set_page("buscar"))
            st.button("🛍️ Registrar venta/apartado", use_container_width=True, on_click=lambda: set_page("ventas"))
        with c2:
            st.button("👥 Clientes", use_container_width=True, on_click=lambda: set_page("clientes"))
            st.button("📄 Catálogo disponible", use_container_width=True, on_click=lambda: set_page("catalogo"))
    else:
        c1,c2,c3 = st.columns(3)
        with c1:
            st.button("🔎 Buscar código", use_container_width=True, on_click=lambda: set_page("buscar"))
            st.button("📥 Cargar recepción", use_container_width=True, on_click=lambda: set_page("carga"))
        with c2:
            st.button("🏷️ Generar QR", use_container_width=True, on_click=lambda: set_page("qr"))
            st.button("🛍️ Ventas", use_container_width=True, on_click=lambda: set_page("ventas"))
        with c3:
            st.button("👥 Clientes", use_container_width=True, on_click=lambda: set_page("clientes"))
            st.button("⚙️ Admin", use_container_width=True, on_click=lambda: set_page("admin"))
    inv = st.session_state.inventario
    if not inv.empty:
        c1,c2,c3 = st.columns(3)
        c1.metric("Disponibles", len(inv[inv.estado=="disponible"]))
        c2.metric("Apartadas", len(inv[inv.estado=="apartado"]))
        c3.metric("Vendidas", len(inv[inv.estado=="vendido"]))


def show_piece(idx):
    inv = st.session_state.inventario
    r = inv.loc[idx]
    col1,col2 = st.columns([1,2])
    with col1:
        url = clean(r.get("foto_url"))
        if url: st.image(url, use_container_width=True)
        else: st.info("Sin foto")
        if can_sell():
            up = st.file_uploader("Agregar foto", type=["jpg","jpeg","png","heic","heif"], key=f"photo_{idx}")
            if up and st.button("Guardar foto", key=f"savephoto_{idx}"):
                try:
                    data, ct = compress_image(up)
                    path = f"fotos/piezas/{clean(r['numero']).zfill(3)}_{uuid.uuid4().hex[:8]}.jpg"
                    url, err = upload_to_supabase(data, path, ct)
                    if err:
                        st.error(err)
                    else:
                        inv.at[idx,"foto_url"] = url; inv.at[idx,"fecha_actualizacion"] = now_str(); save_inventory(); st.success("Foto guardada"); st.rerun()
                except Exception as e: st.error(f"No pude procesar la foto: {e}")
    with col2:
        st.subheader(f"{clean(r['numero']).zfill(3)} · {r['producto']}")
        st.write(f"**Código interno:** {r['codigo_interno']}")
        st.write(f"**Color:** {r['color']}")
        st.write(f"**Talla:** {display_talla(r['talla'])}")
        st.write(f"**Precio:** {money(r['precio'])}")
        st.write(f"**Estado:** {r['estado']}")
        if clean(r.get("cliente_id")):
            cid = clean(r['cliente_id'])
            st.write(f"**Cliente ID:** {cid}")
            if r['estado'] == "apartado":
                fa = clean(r.get('fecha_apartado'))[:10]
                st.write(f"**Apartada desde:** {fa}")
                try:
                    days = (date.today() - datetime.strptime(fa, "%Y-%m-%d").date()).days
                    st.write(f"**Días apartada:** {days}")
                    if days > 10: st.warning("Apartado con más de 10 días")
                except Exception: pass


def buscar_page():
    st.title("Buscar / escanear pieza")
    q = st.text_input("Código numérico o interno", placeholder="Ej: 066")
    # camera QR optional
    if q:
        idx, res = find_piece(q)
        if idx is not None: show_piece(idx)
        elif res.empty: st.error("No encontré esa pieza")
        else:
            st.dataframe(res[["numero","codigo_interno","producto","color","talla","precio","estado"]], use_container_width=True)


def ventas_page():
    st.title("Ventas / apartados")
    if not can_sell(): st.warning("Sin permiso"); return
    q = st.text_input("Código de pieza", placeholder="Ej: 066")
    idx = None
    if q:
        idx, res = find_piece(q)
        if idx is None:
            if res.empty: st.error("No encontrada")
            else: st.dataframe(res[["numero","codigo_interno","producto","estado"]], use_container_width=True)
            return
        show_piece(idx)
    st.markdown("---")
    st.subheader("Registrar movimiento")
    clients = st.session_state.clientes.copy()
    opts = [""] + [f"{r['cliente_id']} · {r['nombre']}" for _,r in clients.sort_values('nombre').iterrows()]
    chosen = st.selectbox("Cliente", opts)
    new_client = st.text_input("Nueva cliente (si no existe)")
    tipo = st.radio("Tipo", ["venta", "apartado"], horizontal=True)
    descuento_pct = st.number_input("Descuento %", min_value=0.0, max_value=100.0, value=0.0, step=1.0)
    if st.button("Agregar a nota activa", type="primary", disabled=(idx is None)):
        if idx is None:
            st.error("Primero busca una pieza")
            return
        client_id = ""
        if new_client.strip(): client_id = ensure_client(new_client.strip())
        elif chosen: client_id = chosen.split(" · ")[0]
        if not client_id:
            st.error("Selecciona o crea una cliente")
            return
        inv = st.session_state.inventario; r = inv.loc[idx]
        if clean(r['estado']) not in ["disponible", "apartado"]:
            st.error(f"La pieza está en estado {r['estado']}")
            return
        nid = active_note_for_client(client_id)
        price = fnum(r['precio']); disc = calc_discount_amount(price, descuento_pct); net = price-disc
        line = {"linea_id": uuid.uuid4().hex, "nota_id": nid, "cliente_id": client_id, "fecha": now_str(), "tipo": tipo, "numero": clean(r['numero']).zfill(3), "codigo": clean(r['codigo']), "codigo_interno": clean(r['codigo_interno']), "producto": clean(r['producto']), "color": clean(r['color']), "talla": clean(r['talla']), "precio": price, "descuento_pct": descuento_pct, "descuento_monto": disc, "neto": net, "estado": "activo"}
        st.session_state.ventas = pd.concat([st.session_state.ventas, pd.DataFrame([line])], ignore_index=True)
        inv.at[idx, "estado"] = "vendido" if tipo=="venta" else "apartado"
        inv.at[idx, "cliente_id"] = client_id; inv.at[idx, "nota_id"] = nid
        if tipo == "venta": inv.at[idx,"fecha_venta"] = today_str()
        else: inv.at[idx,"fecha_apartado"] = today_str()
        inv.at[idx,"descuento_pct"] = descuento_pct; inv.at[idx,"fecha_actualizacion"] = now_str()
        st.session_state.inventario = inv
        save_sales(); save_inventory(); recalc_note(nid)
        log(f"registrar_{tipo}", clean(r['numero']).zfill(3), f"Nota {nid} cliente {client_id}")
        st.success(f"Agregado a nota {nid}")
        st.rerun()


def add_payment_widget(nota_id, client_id):
    st.subheader("Agregar abono")
    with st.form(f"payment_{nota_id}"):
        monto = st.number_input("Monto", min_value=0.0, step=10.0)
        metodo = st.selectbox("Método", PAYMENT_METHODS)
        nota = st.text_input("Nota / referencia")
        soporte = st.file_uploader("Foto / capture del pago", type=["jpg","jpeg","png","heic","heif"], key=f"soporte_{nota_id}")
        submitted = st.form_submit_button("Registrar abono", type="primary")
    if submitted:
        if monto <= 0:
            st.error("Monto inválido"); return
        # duplicate guard: same note, monto, method in last 2 minutes
        recent = st.session_state.pagos[(st.session_state.pagos["nota_id"].astype(str)==nota_id) & (st.session_state.pagos["monto"].astype(float)==float(monto)) & (st.session_state.pagos["metodo"].astype(str)==metodo)]
        if not recent.empty:
            last = clean(recent.iloc[-1]["fecha"])
            st.warning(f"Ya existe un abono igual en esta nota ({last}). Revisa antes de registrar otra vez.")
            return
        url = ""
        if soporte is not None:
            try:
                data, ct = compress_image(soporte)
                path = f"pagos/{client_id}/pago_{nota_id}_{uuid.uuid4().hex[:8]}.jpg"
                url, err = upload_to_supabase(data, path, ct)
                if err: st.warning(f"No pude subir soporte: {err}")
            except Exception as e: st.warning(f"No pude procesar soporte: {e}")
        row = {"pago_id": uuid.uuid4().hex, "nota_id": nota_id, "cliente_id": client_id, "fecha": now_str(), "monto": monto, "metodo": metodo, "nota": nota, "soporte_url": url, "usuario": st.session_state.get("user","")}
        st.session_state.pagos = pd.concat([st.session_state.pagos, pd.DataFrame([row])], ignore_index=True)
        save_payments(); recalc_note(nota_id); log("abono", nota_id, f"{money(monto)} {metodo}")
        st.success("Abono registrado")
        st.rerun()


def upload_note_pdf(nota_id):
    pdf = create_note_pdf(nota_id)
    notes = st.session_state.notas
    note = notes[notes["nota_id"].astype(str)==nota_id].iloc[0]
    cname = slug(clean(note["cliente_nombre"]) or get_client_name(note["cliente_id"]))
    fname = f"nota_{nota_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    path = f"notas/{cname}/{fname}"
    url, err = upload_to_supabase(pdf, path, "application/pdf")
    if not err:
        idx = notes[notes["nota_id"].astype(str)==nota_id].index[0]
        notes.at[idx,"pdf_url"] = url; notes.at[idx,"fecha_actualizacion"] = now_str()
        st.session_state.notas = notes; save_notes()
    return pdf, url, err


def clientes_page():
    st.title("Clientes")
    if not can_sell(): st.warning("Sin permiso"); return
    clients = st.session_state.clientes
    if clients.empty: st.info("Aún no hay clientes")
    with st.expander("Crear cliente"):
        nombre = st.text_input("Nombre")
        tel = st.text_input("Teléfono")
        email = st.text_input("Email")
        notas = st.text_area("Notas")
        if st.button("Guardar cliente") and nombre.strip():
            cid = ensure_client(nombre, tel, email, notas); st.success(f"Cliente {cid}"); st.rerun()
    opts = [""] + [f"{r['cliente_id']} · {r['nombre']}" for _,r in clients.sort_values('nombre').iterrows()]
    choice = st.selectbox("Cliente", opts)
    if not choice: return
    cid = choice.split(" · ")[0]
    cname = get_client_name(cid)
    st.subheader(f"{cid} · {cname}")
    notes = st.session_state.notas[st.session_state.notas["cliente_id"].astype(str)==cid].copy()
    if notes.empty:
        st.info("No tiene notas")
        if st.button("Crear nota activa"): active_note_for_client(cid); st.rerun()
        return
    st.dataframe(notes[["nota_id","fecha_creacion","estado","total_venta","total_apartado","pagado","saldo","pdf_url"]], use_container_width=True)
    active = notes[notes["estado"]=="abierta"]
    if active.empty:
        if st.button("Crear nueva nota activa"): active_note_for_client(cid); st.rerun()
        return
    nid = clean(active.iloc[0]["nota_id"])
    st.markdown(f"### Nota activa: {nid}")
    lines = st.session_state.ventas[st.session_state.ventas["nota_id"].astype(str)==nid]
    st.dataframe(lines[["fecha","tipo","numero","producto","talla","precio","descuento_pct","neto"]], use_container_width=True)
    add_payment_widget(nid, cid)
    st.markdown("---")
    if st.button("Descargar nota PDF y guardar", type="primary"):
        pdf, url, err = upload_note_pdf(nid)
        if err: st.warning(f"PDF descargado, pero no se guardó en Supabase: {err}")
        else: st.success("PDF guardado en Supabase")
        st.download_button("Descargar PDF", data=pdf, file_name=f"nota_{nid}_{slug(cname)}.pdf", mime="application/pdf", use_container_width=True)


def qr_page():
    st.title("Generar etiquetas QR")
    if not is_admin(): st.warning("Solo admin"); return
    inv = st.session_state.inventario
    if inv.empty: st.warning("Sin inventario"); return
    scope = st.selectbox("Alcance", ["Disponibles", "Todo", "Apartadas"])
    data = inv.copy()
    if scope == "Disponibles": data = data[data.estado=="disponible"]
    if scope == "Apartadas": data = data[data.estado=="apartado"]
    st.write(f"Etiquetas: {len(data)}")
    pdf = create_labels_pdf(data)
    st.download_button("Descargar etiquetas PDF", data=pdf, file_name="etiquetas_concherie_5x8.pdf", mime="application/pdf", use_container_width=True)


def catalogo_page():
    st.title("Catálogo disponible")
    inv = st.session_state.inventario
    data = inv[inv.estado=="disponible"].copy()
    if data.empty: st.info("No hay disponibles"); return
    show_price = st.checkbox("Incluir precio", value=False)
    talla_filter = st.text_input("Filtrar por talla (opcional)", placeholder="Ej: T40")
    if talla_filter: data = data[data["talla"].astype(str).str.contains(talla_filter, case=False, na=False)]
    st.dataframe(data[["numero","producto","color","talla","precio","foto_url"]], use_container_width=True)
    # simple elegant HTML-ish gallery in app
    for _, r in data.head(80).iterrows():
        c1,c2 = st.columns([1,2])
        with c1:
            if clean(r['foto_url']): st.image(r['foto_url'], use_container_width=True)
        with c2:
            st.markdown(f"**{r['numero']} · {r['producto']}**")
            st.caption(f"{r['color']} · {display_talla(r['talla'])}")
            if show_price: st.markdown(f"### {money(r['precio'])}")


def carga_page():
    st.title("Cargar recepción")
    if not is_admin(): st.warning("Solo admin"); return
    st.info("Excel esperado: marca/codigo/producto/cantidad/precio/llegaron/tallas. Se crean solo las piezas que llegaron.")
    up = st.file_uploader("Excel", type=["xlsx"])
    if not up: return
    df = pd.read_excel(up)
    df.columns = [str(c).strip().lower() for c in df.columns]
    st.dataframe(df.head(20), use_container_width=True)
    if st.button("Generar inventario desde llegaron", type="primary"):
        rows=[]
        for _, r in df.iterrows():
            codigo = clean(r.get("codigo") or r.get("código")); producto=clean(r.get("producto")); precio=fnum(r.get("precio")); llegaron=inum(r.get("llegaron"), inum(r.get("cantidad")))
            if not codigo or not producto or llegaron<=0: continue
            color = extract_color(producto)
            tallas = extract_tallas_from_row(r, llegaron)
            for i in range(llegaron):
                numero = next_numeric_code_for_rows(rows)
                talla = tallas[i] if i < len(tallas) else "T0"
                interno = f"{codigo}-{color}-{display_talla(talla).replace('Talla Única','T0')}-{numero}"
                rows.append({"numero": numero, "codigo": codigo, "codigo_interno": interno, "producto": producto, "color": color, "talla": talla, "precio": precio, "estado":"disponible", "cliente_id":"", "nota_id":"", "fecha_apartado":"", "fecha_venta":"", "descuento_pct":0.0, "foto_url":"", "notas":"", "fecha_actualizacion": now_str()})
        st.session_state.inventario = pd.DataFrame(rows, columns=INVENTORY_COLUMNS)
        save_inventory(); log("carga_recepcion", detalle=f"Piezas {len(rows)}")
        st.success(f"Inventario cargado: {len(rows)} piezas"); st.rerun()


def next_numeric_code_for_rows(rows):
    existing = [inum(x.get('numero')) for x in rows if clean(x.get('numero')).isdigit()]
    inv = st.session_state.inventario
    existing += [inum(x) for x in inv.get('numero', pd.Series(dtype=str)).astype(str) if clean(x).isdigit()]
    return f"{(max(existing)+1 if existing else 1):03d}"


def extract_color(producto):
    # last relevant color word from product text
    colors = ["SILVER","PURPLE","VIOLETA","OLIVE","BLUE","ROJA","AMARILLA","ANIS","WHITE","LAVANDA","PINK","OLD PINK","FUCSIA","ORCHIDEA","ORO","GREEN","LEMON","SAND"]
    p = clean(producto).upper()
    for col in sorted(colors, key=len, reverse=True):
        if col in p:
            return col.replace("OLD ", "")
    return "COLOR"


def extract_tallas_from_row(r, llegaron):
    vals=[]
    for k,v in r.items():
        if "talla" in str(k).lower() or str(k).lower().startswith("t"):
            s=clean(v)
            if s and s.lower() not in ["no", "nan"]:
                parts = re.split(r"[,/;\s]+", s)
                vals += [p if p.startswith("T") else f"T{p}" for p in parts if p]
    vals = vals[:llegaron]
    while len(vals)<llegaron: vals.append("T0")
    return vals


def reportes_page():
    st.title("Reportes")
    inv=st.session_state.inventario
    st.dataframe(inv, use_container_width=True)
    bio=BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        st.session_state.inventario.to_excel(writer, "inventario", index=False)
        st.session_state.clientes.to_excel(writer, "clientes", index=False)
        st.session_state.notas.to_excel(writer, "notas", index=False)
        st.session_state.ventas.to_excel(writer, "ventas", index=False)
        st.session_state.pagos.to_excel(writer, "pagos", index=False)
        st.session_state.movimientos.to_excel(writer, "movimientos", index=False)
    st.download_button("Descargar respaldo Excel", data=bio.getvalue(), file_name="respaldo_concherie.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def admin_page():
    st.title("Admin seguro")
    if not is_admin(): st.warning("Solo admin"); return
    q=st.text_input("Código numérico para anular/liberar", placeholder="066")
    if q:
        idx,res=find_piece(q)
        if idx is not None:
            show_piece(idx)
            action=st.selectbox("Acción", ["Liberar/anular venta o apartado", "Eliminar pieza"])
            conf=st.text_input("Confirma código numérico")
            pwd=st.text_input("Clave admin", type="password")
            if st.button("Ejecutar acción", type="primary"):
                if conf.zfill(3)!=clean(st.session_state.inventario.loc[idx,'numero']).zfill(3) or pwd!="master":
                    st.error("Confirmación o clave incorrecta")
                else:
                    inv=st.session_state.inventario; numero=clean(inv.loc[idx,'numero']).zfill(3)
                    if action.startswith("Liberar"):
                        inv.at[idx,"estado"]="disponible"; inv.at[idx,"cliente_id"]=""; inv.at[idx,"nota_id"]=""; inv.at[idx,"fecha_apartado"]=""; inv.at[idx,"fecha_venta"]=""; inv.at[idx,"descuento_pct"]=0.0
                        st.session_state.ventas.loc[st.session_state.ventas["numero"].astype(str).str.zfill(3)==numero,"estado"]="anulada"
                        save_sales()
                    else:
                        inv=inv.drop(index=idx).reset_index(drop=True)
                    st.session_state.inventario=inv; save_inventory(); log("admin_accion", numero, action); st.success("Ejecutado"); st.rerun()
        else: st.info("No hay coincidencia exacta")

# ============================================================
# MAIN
# ============================================================
def main():
    load_all()
    if "user" not in st.session_state:
        login_page(); return
    sidebar()
    page=st.session_state.get("page","home")
    if page=="home": home_page()
    elif page=="buscar": buscar_page()
    elif page=="ventas": ventas_page()
    elif page=="clientes": clientes_page()
    elif page=="qr": qr_page()
    elif page=="catalogo": catalogo_page()
    elif page=="carga": carga_page()
    elif page=="reportes": reportes_page()
    elif page=="admin": admin_page()
    else: home_page()

if __name__ == "__main__":
    main()
