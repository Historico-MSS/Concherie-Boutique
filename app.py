import os
import re
import json
import uuid
import base64
import mimetypes
from io import BytesIO
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

# Optional imports used inside functions:
# PIL, qrcode, reportlab, cv2

st.set_page_config(page_title="Concherie Boutique", page_icon="🧾", layout="wide")

# ============================================================
# USUARIOS
# ============================================================
USERS = {
    "jc": {"password": "master", "role": "admin", "name": "JC"},
    "ventas": {"password": "moira", "role": "ventas", "name": "Ventas"},
    "info": {"password": "precio", "role": "info", "name": "Info"},
}

# ============================================================
# CONSTANTES / ESQUEMAS
# ============================================================
INVENTORY_COLUMNS = [
    "numero",              # 001, 002, 003
    "codigo_concha",       # ISH01
    "codigo_interno",      # ISH01-SILVER-T48-001
    "producto",
    "color",
    "talla",
    "precio",
    "estado",              # disponible/reservado/probando/vendido/mantenimiento
    "ubicacion",
    "cliente",
    "descuento_pct",       # descuento por pieza en porcentaje
    "pagado",              # total pagado aplicado a esta pieza
    "foto_url",
    "notas",
    "fecha_actualizacion",
]

CLIENT_COLUMNS = [
    "cliente",
    "telefono",
    "email",
    "notas",
    "fecha_creacion",
]

MOVEMENT_COLUMNS = [
    "fecha",
    "usuario",
    "tipo",
    "numero",
    "codigo_interno",
    "cliente",
    "detalle",
]

NOTE_COLUMNS = [
    "fecha",
    "cliente",
    "numero_nota",
    "total_bruto",
    "descuentos",
    "total_neto",
    "pagado",
    "saldo",
    "archivo_url",
    "archivo_path",
    "usuario",
]

PAYMENT_COLUMNS = [
    "fecha",
    "cliente",
    "numero",
    "codigo_interno",
    "monto",
    "metodo",
    "nota",
    "soporte_url",
    "soporte_path",
    "usuario",
]

VALID_STATES = ["disponible", "reservado", "probando", "vendido", "mantenimiento"]
PAYMENT_METHODS = ["Efectivo", "Transferencia", "Otro"]

# ============================================================
# HELPERS
# ============================================================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_file_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_text(x):
    if x is None:
        return ""
    s = str(x).strip()
    if s.lower() in ["nan", "none", "null", "nat"]:
        return ""
    return s


def money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"


def safe_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def safe_int(x, default=0):
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except Exception:
        return default


def safe_filename(text):
    text = clean_text(text)
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "archivo"


def display_talla(talla):
    talla = clean_text(talla)
    if talla == "" or talla.upper() in ["T0", "0", "TU", "U", "UNICA", "ÚNICA", "ONE SIZE", "SIN TALLA"]:
        return "Talla Única"
    if talla.upper().startswith("T"):
        return talla.upper()
    return f"T{talla}"


def normalize_color(color):
    color = clean_text(color).upper()
    replacements = {
        "OLD PINK": "PINK",
        "LIGHT PINK": "PINK",
        "LIGHT BLUE": "BLUE",
        "LIGHT LAVANDA": "LAVANDA",
        "LAVENDER": "LAVANDA",
        "PURPURA": "PURPLE",
        "AMARILLO": "AMARILLA",
        "PEA/GREEN": "GREEN",
        "PEA GREEN": "GREEN",
    }
    return replacements.get(color, color)


def infer_color_from_product(producto):
    p = clean_text(producto).upper()
    candidates = [
        "OLD PINK", "LIGHT PINK", "LIGHT BLUE", "LIGHT LAVANDA", "LAVANDA",
        "SILVER", "PURPLE", "VIOLETA", "OLIVE", "ANIS", "WHITE", "PINK",
        "FUCSIA", "ORCHIDEA", "GREEN", "LEMON", "AMARILLA", "ROJA", "ORO", "BLUE",
        "SAND",
    ]
    for c in candidates:
        if c in p:
            return normalize_color(c)
    # Sometimes producto has slash-color at the end
    if "/" in p:
        return normalize_color(p.split("/")[-1].strip())
    return ""


def product_without_color(producto, color):
    producto = clean_text(producto)
    color = clean_text(color)
    if not producto or not color:
        return producto
    cleaned = re.sub(re.escape(color), "", producto, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+/\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or producto


def ensure_columns(df, columns):
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=columns)
    df = df.copy()
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def ensure_inventory_schema(df):
    # Backwards compatibility with previous versions
    if df is None or df.empty:
        return pd.DataFrame(columns=INVENTORY_COLUMNS)
    df = df.copy()
    rename_map = {
        "codigo": "codigo_concha",
        "codigo_base": "codigo_concha",
        "codigo_unico": "codigo_interno",
        "marca": "marca_old",
        "descuento": "descuento_monto_old",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]
    if "numero" not in df.columns:
        # Try to infer from codigo_interno or codigo_unico
        src = df.get("codigo_interno", pd.Series([""] * len(df))).astype(str)
        df["numero"] = src.str.extract(r"(\d{3})$")[0].fillna("")
    if "codigo_concha" not in df.columns:
        if "codigo_interno" in df.columns:
            df["codigo_concha"] = df["codigo_interno"].astype(str).str.split("-").str[0]
        else:
            df["codigo_concha"] = ""
    if "color" not in df.columns:
        df["color"] = df.get("producto", "").apply(infer_color_from_product) if "producto" in df.columns else ""
    if "talla" not in df.columns:
        df["talla"] = ""
    if "descuento_pct" not in df.columns:
        # If there was an old fixed discount amount, convert to % when possible
        if "descuento_monto_old" in df.columns and "precio" in df.columns:
            df["descuento_pct"] = df.apply(
                lambda r: round((safe_float(r.get("descuento_monto_old")) / safe_float(r.get("precio"), 1)) * 100, 2)
                if safe_float(r.get("precio"), 0) > 0 else 0,
                axis=1,
            )
        else:
            df["descuento_pct"] = 0.0
    df = ensure_columns(df, INVENTORY_COLUMNS)
    df["numero"] = df["numero"].apply(lambda x: f"{safe_int(x):03d}" if clean_text(x) else "")
    for col in ["codigo_concha", "codigo_interno", "producto", "color", "talla", "estado", "ubicacion", "cliente", "foto_url", "notas", "fecha_actualizacion"]:
        df[col] = df[col].fillna("").astype(str)
    for col in ["precio", "descuento_pct", "pagado"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df.loc[df["estado"].str.strip() == "", "estado"] = "disponible"
    df.loc[df["ubicacion"].str.strip() == "", "ubicacion"] = "tienda"
    return df


def ensure_client_schema(df):
    df = ensure_columns(df, CLIENT_COLUMNS)
    for col in CLIENT_COLUMNS:
        df[col] = df[col].fillna("").astype(str)
    return df


def ensure_movement_schema(df):
    df = ensure_columns(df, MOVEMENT_COLUMNS)
    for col in MOVEMENT_COLUMNS:
        df[col] = df[col].fillna("").astype(str)
    return df


def ensure_note_schema(df):
    df = ensure_columns(df, NOTE_COLUMNS)
    for col in ["total_bruto", "descuentos", "total_neto", "pagado", "saldo"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in [c for c in NOTE_COLUMNS if c not in ["total_bruto", "descuentos", "total_neto", "pagado", "saldo"]]:
        df[col] = df[col].fillna("").astype(str)
    return df


def ensure_payment_schema(df):
    df = ensure_columns(df, PAYMENT_COLUMNS)
    df["monto"] = pd.to_numeric(df["monto"], errors="coerce").fillna(0.0)
    for col in [c for c in PAYMENT_COLUMNS if c != "monto"]:
        df[col] = df[col].fillna("").astype(str)
    return df


def calc_discount_amount(precio, descuento_pct):
    precio = safe_float(precio)
    descuento_pct = max(0.0, min(100.0, safe_float(descuento_pct)))
    return round(precio * descuento_pct / 100.0, 2)


def calc_net(precio, descuento_pct):
    return round(safe_float(precio) - calc_discount_amount(precio, descuento_pct), 2)


def calc_saldo(precio, descuento_pct, pagado):
    return round(calc_net(precio, descuento_pct) - safe_float(pagado), 2)


def role():
    return st.session_state.get("role", "")


def is_admin():
    return role() == "admin"


def is_sales():
    return role() in ["admin", "ventas"]


def set_page(p):
    st.session_state.page = p
    st.rerun()

# ============================================================
# GOOGLE SHEETS
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


def local_path(table):
    os.makedirs("data", exist_ok=True)
    return os.path.join("data", f"{table}.csv")


def load_table(table, columns, ensure_fn):
    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            df = conn.read(worksheet=table, ttl=0)
            if df is None:
                return ensure_fn(pd.DataFrame(columns=columns))
            df = pd.DataFrame(df)
            if "Unnamed: 0" in df.columns:
                df = df.drop(columns=["Unnamed: 0"])
            return ensure_fn(df)
        except Exception as e:
            st.session_state.storage_warning = f"No pude leer Google Sheets; usando local. {table}: {e}"
    path = local_path(table)
    if os.path.exists(path):
        try:
            return ensure_fn(pd.read_csv(path))
        except Exception:
            pass
    return ensure_fn(pd.DataFrame(columns=columns))


def save_table(table, df):
    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            conn.update(worksheet=table, data=df)
            return True, "Guardado en Google Sheets."
        except Exception as e:
            st.session_state.storage_warning = f"No pude guardar en Google Sheets; respaldo local. {table}: {e}"
    path = local_path(table)
    df.to_csv(path, index=False)
    return True, "Guardado localmente."


def load_all_data():
    if st.session_state.get("data_loaded"):
        return
    st.session_state.inventario = load_table("inventario", INVENTORY_COLUMNS, ensure_inventory_schema)
    st.session_state.clientes = load_table("clientes", CLIENT_COLUMNS, ensure_client_schema)
    st.session_state.movimientos = load_table("movimientos", MOVEMENT_COLUMNS, ensure_movement_schema)
    st.session_state.notas = load_table("notas", NOTE_COLUMNS, ensure_note_schema)
    st.session_state.pagos = load_table("pagos", PAYMENT_COLUMNS, ensure_payment_schema)
    st.session_state.data_loaded = True


def save_inventory():
    st.session_state.inventario = ensure_inventory_schema(st.session_state.inventario)
    return save_table("inventario", st.session_state.inventario)


def save_clients():
    st.session_state.clientes = ensure_client_schema(st.session_state.clientes)
    return save_table("clientes", st.session_state.clientes)


def save_movements():
    st.session_state.movimientos = ensure_movement_schema(st.session_state.movimientos)
    return save_table("movimientos", st.session_state.movimientos)


def save_notes():
    st.session_state.notas = ensure_note_schema(st.session_state.notas)
    return save_table("notas", st.session_state.notas)


def save_payments():
    st.session_state.pagos = ensure_payment_schema(st.session_state.pagos)
    return save_table("pagos", st.session_state.pagos)


def log_event(tipo, numero="", codigo_interno="", cliente="", detalle=""):
    row = {
        "fecha": now_str(),
        "usuario": st.session_state.get("user", ""),
        "tipo": tipo,
        "numero": clean_text(numero),
        "codigo_interno": clean_text(codigo_interno),
        "cliente": clean_text(cliente),
        "detalle": clean_text(detalle),
    }
    st.session_state.movimientos = pd.concat([st.session_state.movimientos, pd.DataFrame([row])], ignore_index=True)
    save_movements()

# ============================================================
# SUPABASE STORAGE
# ============================================================
def supabase_configured():
    try:
        return "supabase" in st.secrets and all(k in st.secrets["supabase"] for k in ["url", "key", "bucket"])
    except Exception:
        return False


def supabase_public_url(path):
    url = st.secrets["supabase"]["url"].rstrip("/")
    bucket = st.secrets["supabase"]["bucket"]
    return f"{url}/storage/v1/object/public/{bucket}/{path}"


def upload_to_supabase(path, content_bytes, content_type="application/octet-stream"):
    if not supabase_configured():
        raise RuntimeError("Supabase no está configurado en Secrets.")
    url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["key"]
    bucket = st.secrets["supabase"]["bucket"]
    endpoint = f"{url}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    resp = requests.post(endpoint, headers=headers, data=content_bytes, timeout=45)
    if resp.status_code not in [200, 201]:
        raise RuntimeError(f"Supabase upload falló ({resp.status_code}): {resp.text[:300]}")
    return supabase_public_url(path), path


def prepare_image_for_upload(uploaded_file, max_size=1400, quality=80):
    # Supports JPG/PNG compression; if HEIC unsupported, uploads original.
    raw = uploaded_file.getvalue()
    content_type = uploaded_file.type or mimetypes.guess_type(uploaded_file.name)[0] or "application/octet-stream"
    name = uploaded_file.name.lower()
    if name.endswith((".jpg", ".jpeg", ".png", ".heic", ".heif")):
        try:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except Exception:
                pass
            from PIL import Image
            img = Image.open(BytesIO(raw)).convert("RGB")
            w, h = img.size
            scale = min(max_size / max(w, h), 1.0)
            if scale < 1:
                img = img.resize((int(w * scale), int(h * scale)))
            out = BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue(), "image/jpeg", ".jpg"
        except Exception:
            ext = os.path.splitext(uploaded_file.name)[1] or ".jpg"
            return raw, content_type, ext
    return raw, content_type, os.path.splitext(uploaded_file.name)[1] or ""


def upload_piece_photo(uploaded_file, row):
    content, ctype, ext = prepare_image_for_upload(uploaded_file)
    numero = clean_text(row.get("numero")) or "sin_numero"
    interno = safe_filename(row.get("codigo_interno"))
    path = f"fotos/piezas/{numero}_{interno}_{today_file_stamp()}{ext}"
    return upload_to_supabase(path, content, ctype)


def upload_payment_support(uploaded_file, cliente, numero):
    content, ctype, ext = prepare_image_for_upload(uploaded_file)
    path = f"pagos/{safe_filename(cliente)}/pago_{clean_text(numero) or 'general'}_{today_file_stamp()}{ext}"
    return upload_to_supabase(path, content, ctype)


def upload_invoice_pdf(pdf_bytes, cliente, filename):
    path = f"notas/{safe_filename(cliente)}/{filename}"
    return upload_to_supabase(path, pdf_bytes, "application/pdf")

# ============================================================
# AUTH / NAVEGACIÓN
# ============================================================
def login_page():
    st.title("Concherie Boutique")
    st.caption("Acceso privado")
    with st.form("login"):
        user = st.text_input("Usuario").strip().lower()
        pwd = st.text_input("Clave", type="password")
        ok = st.form_submit_button("Entrar", use_container_width=True)
    if ok:
        if user in USERS and USERS[user]["password"] == pwd:
            st.session_state.user = user
            st.session_state.role = USERS[user]["role"]
            st.session_state.page = "home"
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos.")


def logout():
    for k in ["user", "role", "page", "selected_numero", "selected_cliente"]:
        st.session_state.pop(k, None)
    st.rerun()


def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user','')}**")
    st.sidebar.write(f"Rol: **{role()}**")
    if gsheets_configured():
        st.sidebar.success("Datos: Google Sheets")
    else:
        st.sidebar.warning("Datos: local")
    if supabase_configured():
        st.sidebar.success("Archivos: Supabase")
    else:
        st.sidebar.warning("Archivos: sin Supabase")
    if st.session_state.get("storage_warning"):
        st.sidebar.info(st.session_state.storage_warning)
    st.sidebar.markdown("---")
    if st.sidebar.button("🏠 Inicio", use_container_width=True): set_page("home")
    if st.sidebar.button("🔎 Buscar código", use_container_width=True): set_page("buscar")
    if st.sidebar.button("🔳 Escanear QR", use_container_width=True): set_page("scan")
    if is_sales():
        if st.sidebar.button("🛍️ Ventas / Reservas", use_container_width=True): set_page("ventas")
        if st.sidebar.button("👥 Clientes", use_container_width=True): set_page("clientes")
        if st.sidebar.button("📄 Catálogo", use_container_width=True): set_page("catalogo")
    if is_admin():
        if st.sidebar.button("📥 Cargar recepción", use_container_width=True): set_page("carga")
        if st.sidebar.button("🏷️ Generar QR", use_container_width=True): set_page("qr")
        if st.sidebar.button("📦 Inventario", use_container_width=True): set_page("inventario")
        if st.sidebar.button("📊 Reportes", use_container_width=True): set_page("reportes")
        if st.sidebar.button("⚙️ Admin", use_container_width=True): set_page("admin")
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar sesión", use_container_width=True): logout()


def home_page():
    st.title("Concherie Boutique")
    st.caption("Inventario, ventas, clientes, QR, catálogo y pagos.")
    r = role()
    if r == "info":
        col1, col2 = st.columns(2)
        if col1.button("🔳 Escanear QR", use_container_width=True): set_page("scan")
        if col2.button("🔎 Buscar código", use_container_width=True): set_page("buscar")
    elif r == "ventas":
        col1, col2 = st.columns(2)
        if col1.button("🔳 Escanear QR", use_container_width=True): set_page("scan")
        if col2.button("🔎 Buscar código", use_container_width=True): set_page("buscar")
        if col1.button("🛍️ Registrar venta", use_container_width=True): set_page("ventas")
        if col2.button("👥 Clientes", use_container_width=True): set_page("clientes")
        if col1.button("📄 Catálogo disponible", use_container_width=True): set_page("catalogo")
    else:
        col1, col2, col3 = st.columns(3)
        buttons = [
            (col1, "🔳 Escanear QR", "scan"),
            (col2, "🔎 Buscar código", "buscar"),
            (col3, "🏷️ Generar QR", "qr"),
            (col1, "📥 Cargar recepción", "carga"),
            (col2, "📦 Inventario", "inventario"),
            (col3, "🛍️ Ventas", "ventas"),
            (col1, "👥 Clientes", "clientes"),
            (col2, "📄 Catálogo", "catalogo"),
            (col3, "⚙️ Admin", "admin"),
        ]
        for col, label, page in buttons:
            if col.button(label, use_container_width=True): set_page(page)
    show_dashboard_metrics()


def show_dashboard_metrics():
    df = st.session_state.inventario
    if df.empty:
        st.info("No hay inventario cargado.")
        return
    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total piezas", len(df))
    c2.metric("Disponibles", len(df[df.estado == "disponible"]))
    c3.metric("Reservadas", len(df[df.estado == "reservado"]))
    c4.metric("Vendidas", len(df[df.estado == "vendido"]))

# ============================================================
# BÚSQUEDA / FICHA
# ============================================================
def find_piece(query):
    q = clean_text(query)
    if not q:
        return None
    qnum = f"{safe_int(q):03d}" if q.isdigit() else q
    df = st.session_state.inventario
    exact = df[df["numero"].astype(str).str.strip() == qnum]
    if not exact.empty:
        return exact.index[0]
    exact = df[df["codigo_interno"].astype(str).str.strip().str.upper() == q.upper()]
    if not exact.empty:
        return exact.index[0]
    exact = df[df["codigo_concha"].astype(str).str.strip().str.upper() == q.upper()]
    if not exact.empty:
        return exact.index[0]
    return None


def buscar_page():
    st.title("Buscar código")
    code = st.text_input("Código numérico o interno", placeholder="Ej: 066")
    if code:
        idx = find_piece(code)
        if idx is None:
            st.error("No encontré esa pieza.")
        else:
            show_piece_card(idx, allow_actions=is_sales())


def scan_page():
    st.title("Escanear QR")
    st.info("Si la cámara no lee el QR, usa Buscar código y escribe el número grande de la etiqueta.")
    camera_img = st.camera_input("Tomar foto del QR")
    uploaded_img = st.file_uploader("O subir foto del QR", type=["jpg", "jpeg", "png"])
    manual = st.text_input("También puedes pegar/escribir el código")
    if manual:
        idx = find_piece(manual)
        if idx is not None:
            show_piece_card(idx, allow_actions=is_sales())
        else:
            st.error("No encontré ese código.")
    img = camera_img or uploaded_img
    if img:
        text, err = decode_qr(img)
        if text:
            idx = find_piece(text)
            if idx is not None:
                show_piece_card(idx, allow_actions=is_sales())
            else:
                st.error(f"QR leído, pero no encontré la pieza: {text}")
        elif err:
            st.error(err)


def decode_qr(uploaded_file):
    try:
        import cv2
        import numpy as np
        from PIL import Image
        img = Image.open(uploaded_file).convert("RGB")
        arr = np.array(img)
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(arr)
        return clean_text(data), None if data else "No pude leer el QR en esa imagen."
    except Exception as e:
        return "", f"No pude procesar el QR: {e}"


def show_photo(url, width=None):
    url = clean_text(url)
    if url:
        st.image(url, use_container_width=True if width is None else False, width=width)
    else:
        st.info("Sin foto")


def show_piece_card(idx, allow_actions=False):
    df = st.session_state.inventario
    row = df.loc[idx]
    numero = row["numero"]
    precio = safe_float(row["precio"])
    descuento_pct = safe_float(row["descuento_pct"])
    neto = calc_net(precio, descuento_pct)
    pagado = safe_float(row["pagado"])
    saldo = calc_saldo(precio, descuento_pct, pagado)
    st.subheader(f"{numero} · {row['producto']}")
    col1, col2 = st.columns([1, 1.3])
    with col1:
        show_photo(row.get("foto_url", ""))
        if is_sales() and not clean_text(row.get("foto_url")):
            upload_photo_widget(idx)
    with col2:
        st.markdown(f"### Código: **{numero}**")
        st.write(f"**Código interno:** {row['codigo_interno']}")
        st.write(f"**Código Concha:** {row['codigo_concha']}")
        st.write(f"**Color:** {row['color']}")
        st.write(f"**Talla:** {display_talla(row['talla'])}")
        st.write(f"**Estado:** {row['estado']}")
        st.write(f"**Ubicación:** {row['ubicacion']}")
        st.write(f"**Cliente:** {clean_text(row['cliente']) or '-'}")
        st.metric("Precio", money(precio))
        if is_sales():
            st.write(f"Descuento: **{descuento_pct:.0f}%** ({money(calc_discount_amount(precio, descuento_pct))})")
            st.write(f"Neto: **{money(neto)}**")
            st.write(f"Pagado: **{money(pagado)}**")
            st.write(f"Saldo: **{money(saldo)}**")
    if allow_actions:
        st.markdown("---")
        quick_sale_form(idx)


def upload_photo_widget(idx):
    df = st.session_state.inventario
    row = df.loc[idx]
    up = st.file_uploader("📸 Agregar foto de esta pieza", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"photo_{idx}")
    if up is not None and st.button("Guardar foto", key=f"save_photo_{idx}", use_container_width=True):
        try:
            url, path = upload_piece_photo(up, row)
            df.at[idx, "foto_url"] = url
            df.at[idx, "fecha_actualizacion"] = now_str()
            st.session_state.inventario = ensure_inventory_schema(df)
            save_inventory()
            log_event("foto_pieza", numero=row["numero"], codigo_interno=row["codigo_interno"], detalle=path)
            st.success("Foto guardada.")
            st.rerun()
        except Exception as e:
            st.error(f"No pude guardar la foto en Supabase: {e}")

# ============================================================
# CLIENTES / PAGOS / VENTAS
# ============================================================
def client_names():
    names = []
    if not st.session_state.clientes.empty:
        names += st.session_state.clientes["cliente"].dropna().astype(str).str.strip().tolist()
    if not st.session_state.inventario.empty:
        names += st.session_state.inventario["cliente"].dropna().astype(str).str.strip().tolist()
    names = sorted(set([n for n in names if n]), key=lambda s: s.lower())
    return names


def ensure_client(cliente):
    cliente = clean_text(cliente)
    if not cliente:
        return
    clients = st.session_state.clientes
    mask = clients["cliente"].astype(str).str.lower() == cliente.lower()
    if not mask.any():
        row = {"cliente": cliente, "telefono": "", "email": "", "notas": "", "fecha_creacion": now_str()}
        st.session_state.clientes = pd.concat([clients, pd.DataFrame([row])], ignore_index=True)
        save_clients()


def select_or_new_client(key="client"):
    names = client_names()
    option = st.selectbox("Cliente", ["+ Nueva cliente"] + names, key=f"{key}_select")
    if option == "+ Nueva cliente":
        return st.text_input("Nombre nueva cliente", key=f"{key}_new")
    return option


def quick_sale_form(idx):
    st.subheader("Acción rápida")
    df = st.session_state.inventario
    row = df.loc[idx]
    with st.form(f"quick_sale_{idx}"):
        cliente = select_or_new_client(key=f"q_{idx}")
        action = st.selectbox("Acción", ["vendido", "reservado", "probando", "disponible"])
        descuento_pct = st.number_input("Descuento %", min_value=0.0, max_value=100.0, value=safe_float(row["descuento_pct"]), step=1.0)
        pagado = st.number_input("Pago recibido / pagado acumulado", min_value=0.0, value=safe_float(row["pagado"]), step=10.0)
        metodo = st.selectbox("Método de pago", PAYMENT_METHODS)
        nota_pago = st.text_input("Nota del pago" if metodo == "Otro" else "Referencia / nota opcional")
        soporte = st.file_uploader("Soporte de pago (foto/capture opcional)", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"support_quick_{idx}")
        submitted = st.form_submit_button("Guardar", use_container_width=True)
    if submitted:
        apply_sale_update(idx, cliente, action, descuento_pct, pagado, metodo, nota_pago, soporte)


def apply_sale_update(idx, cliente, estado, descuento_pct, pagado, metodo="", nota_pago="", soporte=None):
    df = st.session_state.inventario
    row = df.loc[idx]
    cliente = clean_text(cliente)
    if estado != "disponible" and not cliente:
        st.error("Debes seleccionar o crear una cliente.")
        return
    old_pagado = safe_float(row["pagado"])
    nuevo_pagado = safe_float(pagado)
    diff_pago = max(0.0, nuevo_pagado - old_pagado)
    soporte_url = ""
    soporte_path = ""
    if soporte is not None and diff_pago > 0:
        try:
            soporte_url, soporte_path = upload_payment_support(soporte, cliente, row["numero"])
        except Exception as e:
            st.warning(f"No pude guardar soporte de pago: {e}")
    df.at[idx, "cliente"] = cliente if estado != "disponible" else ""
    df.at[idx, "estado"] = estado
    df.at[idx, "ubicacion"] = "casa cliente" if estado == "probando" else ("tienda" if estado == "disponible" else clean_text(row["ubicacion"]) or "tienda")
    df.at[idx, "descuento_pct"] = descuento_pct
    df.at[idx, "pagado"] = nuevo_pagado
    df.at[idx, "fecha_actualizacion"] = now_str()
    st.session_state.inventario = ensure_inventory_schema(df)
    save_inventory()
    if cliente:
        ensure_client(cliente)
    if diff_pago > 0:
        add_payment_record(cliente, row["numero"], row["codigo_interno"], diff_pago, metodo, nota_pago, soporte_url, soporte_path)
    log_event(estado, numero=row["numero"], codigo_interno=row["codigo_interno"], cliente=cliente, detalle=f"desc {descuento_pct}%, pagado {nuevo_pagado}")
    st.success("Guardado.")
    st.rerun()


def add_payment_record(cliente, numero, codigo_interno, monto, metodo, nota, soporte_url="", soporte_path=""):
    row = {
        "fecha": now_str(),
        "cliente": clean_text(cliente),
        "numero": clean_text(numero),
        "codigo_interno": clean_text(codigo_interno),
        "monto": safe_float(monto),
        "metodo": clean_text(metodo),
        "nota": clean_text(nota),
        "soporte_url": clean_text(soporte_url),
        "soporte_path": clean_text(soporte_path),
        "usuario": st.session_state.get("user", ""),
    }
    st.session_state.pagos = pd.concat([st.session_state.pagos, pd.DataFrame([row])], ignore_index=True)
    save_payments()
    log_event("pago", numero=numero, codigo_interno=codigo_interno, cliente=cliente, detalle=f"Pago {money(monto)} {metodo}")


def ventas_page():
    st.title("Ventas / Reservas")
    code = st.text_input("Buscar código numérico", placeholder="Ej: 066")
    if code:
        idx = find_piece(code)
        if idx is None:
            st.error("No encontré esa pieza.")
        else:
            show_piece_card(idx, allow_actions=True)
    else:
        st.info("Busca por el código numérico grande de la etiqueta.")


def clientes_page():
    st.title("Clientes")
    if not is_sales():
        st.warning("No tienes permiso para clientes.")
        return
    with st.expander("Crear / editar cliente"):
        with st.form("new_client"):
            cliente = st.text_input("Nombre")
            telefono = st.text_input("Teléfono")
            email = st.text_input("Email")
            notas = st.text_area("Notas")
            ok = st.form_submit_button("Guardar cliente")
        if ok and cliente.strip():
            clients = st.session_state.clientes
            mask = clients["cliente"].astype(str).str.lower() == cliente.strip().lower()
            if mask.any():
                i = clients[mask].index[0]
                clients.at[i, "telefono"] = telefono
                clients.at[i, "email"] = email
                clients.at[i, "notas"] = notas
            else:
                row = {"cliente": cliente.strip(), "telefono": telefono.strip(), "email": email.strip(), "notas": notas.strip(), "fecha_creacion": now_str()}
                clients = pd.concat([clients, pd.DataFrame([row])], ignore_index=True)
            st.session_state.clientes = ensure_client_schema(clients)
            save_clients()
            st.success("Cliente guardado.")
            st.rerun()
    names = client_names()
    if not names:
        st.info("No hay clientes todavía.")
        return
    selected = st.selectbox("Selecciona cliente", names)
    if selected:
        show_client_profile(selected)


def show_client_profile(cliente):
    st.subheader(cliente)
    df = st.session_state.inventario
    items = df[df["cliente"].astype(str).str.lower() == cliente.lower()].copy()
    if items.empty:
        st.info("Sin piezas asociadas.")
    else:
        items["descuento_monto"] = items.apply(lambda r: calc_discount_amount(r["precio"], r["descuento_pct"]), axis=1)
        items["neto"] = items.apply(lambda r: calc_net(r["precio"], r["descuento_pct"]), axis=1)
        items["saldo"] = items.apply(lambda r: calc_saldo(r["precio"], r["descuento_pct"], r["pagado"]), axis=1)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total neto", money(items["neto"].sum()))
        c2.metric("Pagado", money(items["pagado"].sum()))
        c3.metric("Saldo", money(items["saldo"].sum()))
        c4.metric("Piezas", len(items))
        st.dataframe(items[["numero", "producto", "color", "talla", "estado", "precio", "descuento_pct", "neto", "pagado", "saldo"]], use_container_width=True)
        pdf = create_invoice_pdf(cliente, items)
        filename = f"nota_{safe_filename(cliente)}_{today_file_stamp()}.pdf"
        # One button: download + upload to Supabase + register note.
        st.download_button(
            "Descargar nota PDF",
            data=pdf,
            file_name=filename,
            mime="application/pdf",
            use_container_width=True,
            on_click=save_invoice_note,
            args=(cliente, items, pdf, filename),
        )
    st.markdown("---")
    st.subheader("Agregar pago / abono")
    add_payment_form(cliente, items)
    st.markdown("---")
    st.subheader("Pagos registrados")
    pagos = st.session_state.pagos[st.session_state.pagos["cliente"].astype(str).str.lower() == cliente.lower()].copy()
    if pagos.empty:
        st.info("Sin pagos registrados.")
    else:
        st.dataframe(pagos, use_container_width=True)
    st.subheader("Notas guardadas")
    notas = st.session_state.notas[st.session_state.notas["cliente"].astype(str).str.lower() == cliente.lower()].copy()
    if notas.empty:
        st.info("Sin notas guardadas.")
    else:
        st.dataframe(notas[["fecha", "numero_nota", "total_neto", "pagado", "saldo", "archivo_url"]], use_container_width=True)


def add_payment_form(cliente, items):
    if items.empty:
        st.info("No hay piezas asociadas para aplicar pago.")
        return
    with st.form(f"payment_{cliente}"):
        nums = items["numero"].tolist()
        numero = st.selectbox("Aplicar a pieza", nums)
        monto = st.number_input("Monto del abono", min_value=0.0, step=10.0)
        metodo = st.selectbox("Método", PAYMENT_METHODS)
        nota = st.text_input("Nota" if metodo == "Otro" else "Referencia / nota opcional")
        soporte = st.file_uploader("Foto / capture del pago", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"support_{cliente}")
        ok = st.form_submit_button("Registrar abono", use_container_width=True)
    if ok:
        if monto <= 0:
            st.error("El monto debe ser mayor que cero.")
            return
        idx = st.session_state.inventario[st.session_state.inventario["numero"] == numero].index[0]
        row = st.session_state.inventario.loc[idx]
        soporte_url = ""
        soporte_path = ""
        if soporte:
            try:
                soporte_url, soporte_path = upload_payment_support(soporte, cliente, numero)
            except Exception as e:
                st.warning(f"No pude subir el soporte: {e}")
        st.session_state.inventario.at[idx, "pagado"] = safe_float(row["pagado"]) + monto
        st.session_state.inventario.at[idx, "fecha_actualizacion"] = now_str()
        save_inventory()
        add_payment_record(cliente, numero, row["codigo_interno"], monto, metodo, nota, soporte_url, soporte_path)
        st.success("Abono registrado.")
        st.rerun()


def save_invoice_note(cliente, items, pdf_bytes, filename):
    total_bruto = items["precio"].astype(float).sum()
    descuentos = sum(calc_discount_amount(r["precio"], r["descuento_pct"]) for _, r in items.iterrows())
    total_neto = total_bruto - descuentos
    pagado = items["pagado"].astype(float).sum()
    saldo = total_neto - pagado
    url = ""
    path = ""
    try:
        url, path = upload_invoice_pdf(pdf_bytes, cliente, filename)
    except Exception as e:
        st.session_state["last_note_warning"] = f"La nota se descargó, pero no pude guardarla en Supabase: {e}"
    row = {
        "fecha": now_str(),
        "cliente": cliente,
        "numero_nota": filename.replace(".pdf", ""),
        "total_bruto": total_bruto,
        "descuentos": descuentos,
        "total_neto": total_neto,
        "pagado": pagado,
        "saldo": saldo,
        "archivo_url": url,
        "archivo_path": path,
        "usuario": st.session_state.get("user", ""),
    }
    st.session_state.notas = pd.concat([st.session_state.notas, pd.DataFrame([row])], ignore_index=True)
    save_notes()
    log_event("nota_pdf", cliente=cliente, detalle=f"Nota {filename} guardada: {url or 'sin URL'}")

# ============================================================
# PDFS: NOTA, ETIQUETAS, CATÁLOGO
# ============================================================
def create_invoice_pdf(cliente, items):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    y = h - 0.75 * inch
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.75 * inch, y, "CONCHERIE BOUTIQUE")
    y -= 0.28 * inch
    c.setFont("Helvetica-Oblique", 11)
    c.drawString(0.75 * inch, y, "Nota de venta")
    y -= 0.32 * inch
    c.setStrokeColor(colors.lightgrey)
    c.line(0.75 * inch, y, 7.75 * inch, y)
    y -= 0.35 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.75 * inch, y, f"Cliente: {cliente}")
    c.drawRightString(7.75 * inch, y, f"Fecha: {datetime.now().strftime('%Y-%m-%d')}")
    y -= 0.42 * inch
    total_bruto = 0
    total_desc = 0
    total_pagado = 0
    for _, r in items.iterrows():
        if y < 1.7 * inch:
            c.showPage(); y = h - 0.75 * inch
        precio = safe_float(r["precio"])
        desc_pct = safe_float(r["descuento_pct"])
        desc_amt = calc_discount_amount(precio, desc_pct)
        neto = precio - desc_amt
        pagado = safe_float(r["pagado"])
        saldo = neto - pagado
        total_bruto += precio; total_desc += desc_amt; total_pagado += pagado
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(0.75 * inch, y, f"{r['numero']} · {clean_text(r['producto'])}")
        y -= 0.20 * inch
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawString(0.75 * inch, y, f"{r['codigo_interno']} · {display_talla(r['talla'])}")
        y -= 0.30 * inch
        c.setFont("Helvetica", 9.3)
        c.drawString(0.95 * inch, y, "Precio")
        c.drawRightString(7.25 * inch, y, money(precio))
        y -= 0.19 * inch
        c.drawString(0.95 * inch, y, f"Descuento {desc_pct:.0f}%")
        c.drawRightString(7.25 * inch, y, f"-{money(desc_amt)}")
        y -= 0.19 * inch
        c.setFont("Helvetica-Bold", 9.6)
        c.drawString(0.95 * inch, y, "Neto")
        c.drawRightString(7.25 * inch, y, money(neto))
        y -= 0.19 * inch
        c.setFont("Helvetica", 9.3)
        c.drawString(0.95 * inch, y, "Pagado")
        c.drawRightString(7.25 * inch, y, money(pagado))
        y -= 0.19 * inch
        c.drawString(0.95 * inch, y, "Saldo pieza")
        c.drawRightString(7.25 * inch, y, money(saldo))
        y -= 0.28 * inch
        c.setStrokeColor(colors.lightgrey)
        c.line(0.75 * inch, y, 7.75 * inch, y)
        y -= 0.28 * inch
    total_neto = total_bruto - total_desc
    saldo_total = total_neto - total_pagado
    if y < 1.9 * inch:
        c.showPage(); y = h - 0.75 * inch
    c.setFont("Helvetica", 10)
    c.drawString(4.7 * inch, y, "Subtotal")
    c.drawRightString(7.25 * inch, y, money(total_bruto)); y -= 0.22 * inch
    c.drawString(4.7 * inch, y, "Descuentos")
    c.drawRightString(7.25 * inch, y, f"-{money(total_desc)}"); y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(4.7 * inch, y, "Total neto")
    c.drawRightString(7.25 * inch, y, money(total_neto)); y -= 0.22 * inch
    c.setFont("Helvetica", 10)
    c.drawString(4.7 * inch, y, "Pagado")
    c.drawRightString(7.25 * inch, y, money(total_pagado)); y -= 0.30 * inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(4.7 * inch, y, "SALDO")
    c.drawRightString(7.25 * inch, y, money(saldo_total))
    c.save(); buf.seek(0)
    return buf.getvalue()


def make_qr_png_bytes(text):
    import qrcode
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=1)
    qr.add_data(clean_text(text))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    out = BytesIO(); img.save(out, format="PNG"); out.seek(0)
    return out.getvalue()


def create_labels_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors
    data = data.sort_values("codigo_interno")
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    label_w = 8 * cm; label_h = 5 * cm; margin_x = 0.55 * cm; margin_y = 0.6 * cm
    cols = 2; rows = int((H - 2 * margin_y) // label_h)
    x_positions = [margin_x, margin_x + label_w]
    # common cut grid per page
    def draw_grid():
        c.setStrokeColor(colors.grey)
        c.setDash(2, 3)
        # vertical between columns
        c.line(margin_x + label_w, margin_y, margin_x + label_w, H - margin_y)
        # horizontal row cuts
        for r in range(1, rows):
            yline = H - margin_y - r * label_h
            c.line(margin_x, yline, margin_x + cols * label_w, yline)
        c.setDash()
    draw_grid()
    for i, (_, r) in enumerate(data.iterrows()):
        if i > 0 and i % (cols * rows) == 0:
            c.showPage(); draw_grid()
        pos = i % (cols * rows); col = pos % cols; row = pos // cols
        x = x_positions[col]; y = H - margin_y - (row + 1) * label_h
        qr_size = 4 * cm
        qr_bytes = make_qr_png_bytes(r["numero"])
        c.drawImage(ImageReader(BytesIO(qr_bytes)), x + 0.25*cm, y + 0.5*cm, qr_size, qr_size, mask="auto")
        tx = x + 4.55*cm; ty = y + 4.25*cm
        c.setFont("Helvetica-Bold", 24)
        c.drawString(tx, ty, clean_text(r["numero"]))
        c.setFont("Helvetica-Bold", 7.8)
        prod = clean_text(r["producto"])[:24]
        c.drawString(tx, ty - 0.55*cm, prod)
        c.setFont("Helvetica", 8.2)
        c.drawString(tx, ty - 1.05*cm, clean_text(r["color"])[:18])
        c.drawString(tx, ty - 1.55*cm, display_talla(r["talla"]))
        c.setFont("Helvetica", 6.8)
        c.drawString(tx, y + 0.55*cm, clean_text(r["codigo_interno"])[:26])
    c.save(); buf.seek(0)
    return buf.getvalue()


def qr_page():
    st.title("Generar etiquetas QR")
    if not is_admin():
        st.warning("Solo admin."); return
    df = st.session_state.inventario.copy()
    scope = st.selectbox("Etiquetas", ["disponible", "todo", "reservado", "probando", "vendido"])
    if scope != "todo":
        df = df[df["estado"] == scope]
    st.write(f"Etiquetas: **{len(df)}**")
    if not df.empty:
        st.dataframe(df[["numero", "codigo_interno", "producto", "color", "talla", "estado"]], use_container_width=True)
        st.download_button("Descargar PDF etiquetas", data=create_labels_pdf(df), file_name="etiquetas_concherie_5x8.pdf", mime="application/pdf", use_container_width=True)


def catalogo_page():
    st.title("Catálogo disponible")
    df = st.session_state.inventario
    avail = df[df["estado"] == "disponible"].copy()
    if avail.empty:
        st.info("No hay piezas disponibles."); return
    with_price = st.toggle("Incluir precio", value=True)
    tallas = sorted(set([display_talla(t) for t in avail["talla"].tolist()]))
    talla_filter = st.multiselect("Filtrar por talla", tallas)
    if talla_filter:
        avail = avail[avail["talla"].apply(display_talla).isin(talla_filter)]
    pdf = create_catalog_pdf(avail, with_price=with_price)
    st.download_button("Descargar catálogo PDF", data=pdf, file_name="catalogo_concherie.pdf", mime="application/pdf", use_container_width=True)


def create_catalog_pdf(data, with_price=True):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    c.setFont("Helvetica-Bold", 18); c.drawString(0.6*inch, H-0.7*inch, "CONCHERIE BOUTIQUE")
    c.setFont("Helvetica-Oblique", 10); c.drawString(0.6*inch, H-0.95*inch, "Catálogo disponible")
    card_w, card_h = 3.35*inch, 4.25*inch
    positions = [(0.55*inch, H-1.3*inch-card_h), (4.05*inch, H-1.3*inch-card_h), (0.55*inch, H-1.55*inch-2*card_h), (4.05*inch, H-1.55*inch-2*card_h)]
    groups = []
    for (codigo, producto, color), g in data.groupby(["codigo_concha", "producto", "color"], dropna=False):
        groups.append((codigo, producto, color, g))
    for i, (codigo, producto, color, g) in enumerate(groups):
        if i > 0 and i % 4 == 0:
            c.showPage()
        x, y = positions[i % 4]
        c.setStrokeColor(colors.lightgrey); c.roundRect(x, y, card_w, card_h, 8, stroke=1, fill=0)
        photo = g[g["foto_url"].astype(str) != ""]
        if not photo.empty:
            try:
                url = photo.iloc[0]["foto_url"]
                img_resp = requests.get(url, timeout=8)
                if img_resp.ok:
                    c.drawImage(ImageReader(BytesIO(img_resp.content)), x+0.18*inch, y+1.75*inch, card_w-0.36*inch, 2.25*inch, preserveAspectRatio=True, anchor="c", mask="auto")
            except Exception:
                pass
        else:
            c.setFont("Helvetica", 8); c.drawCentredString(x+card_w/2, y+2.8*inch, "Sin foto")
        c.setFont("Helvetica-Bold", 10); c.drawString(x+0.22*inch, y+1.38*inch, clean_text(producto)[:34])
        c.setFont("Helvetica-Oblique", 8.5); c.drawString(x+0.22*inch, y+1.15*inch, f"{codigo} · {color}")
        tallas = sorted(set([display_talla(t) for t in g["talla"].tolist()]))
        c.setFont("Helvetica", 8); c.drawString(x+0.22*inch, y+0.92*inch, f"Tallas: {', '.join(tallas)[:42]}")
        c.drawString(x+0.22*inch, y+0.72*inch, f"Disponibles: {len(g)}")
        if with_price:
            c.setFont("Helvetica-Bold", 13); c.drawString(x+0.22*inch, y+0.38*inch, money(g.iloc[0]["precio"]))
    c.save(); buf.seek(0); return buf.getvalue()

# ============================================================
# CARGA RECEPCIÓN / INVENTARIO
# ============================================================
def normalize_upload_columns(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    ren = {
        "código": "codigo_concha", "codigo": "codigo_concha", "codigo base": "codigo_concha", "codigo_concha": "codigo_concha",
        "producto": "producto", "descripcion": "producto", "descripción": "producto",
        "cantidad": "cantidad", "pedido": "cantidad", "pedidas": "cantidad",
        "llegaron": "llegaron", "llego": "llegaron", "llegó": "llegaron", "recibido": "llegaron",
        "precio unitario": "precio", "precio": "precio",
        "marca": "marca", "maison": "marca", "color": "color", "talla": "talla", "tallas": "tallas",
    }
    df = df.rename(columns={c: ren.get(c, c) for c in df.columns})
    return df


def extract_tallas_from_row(row, arrived):
    tallas = []
    for col in row.index:
        lc = str(col).lower()
        val = clean_text(row[col])
        if not val:
            continue
        if lc in ["talla", "tallas", "tallas2", "talla2", "size", "sizes"] or "talla" in lc:
            parts = re.split(r"[,;/\s]+", val)
            tallas += [p for p in parts if p]
    # If no sizes, use T0 for Talla Única
    if not tallas:
        tallas = ["T0"] * arrived
    # Normalize and pad/crop to number arrived
    norm = []
    for t in tallas:
        t = clean_text(t).upper().replace("TALLA", "").strip()
        if t in ["", "NO", "N/A", "NA", "-", "0"]:
            t = "T0"
        elif not t.startswith("T"):
            t = f"T{t}"
        norm.append(t)
    while len(norm) < arrived:
        norm.append("T0")
    return norm[:arrived]


def next_numbers(count):
    df = st.session_state.inventario
    maxnum = 0
    if not df.empty and "numero" in df.columns:
        maxnum = max([safe_int(n) for n in df["numero"].tolist()] + [0])
    return [f"{i:03d}" for i in range(maxnum+1, maxnum+count+1)]


def lookup_price(codigo_concha, producto, uploaded_price):
    # Treat tiny values as non-price when coming from reception sheet.
    uploaded_price = safe_float(uploaded_price)
    if uploaded_price > 100:
        return uploaded_price
    df = st.session_state.inventario
    if not df.empty:
        m = df[df["codigo_concha"].astype(str).str.upper() == clean_text(codigo_concha).upper()]
        if not m.empty:
            return safe_float(m.iloc[0]["precio"])
        m = df[df["producto"].astype(str).str.upper() == clean_text(producto).upper()]
        if not m.empty:
            return safe_float(m.iloc[0]["precio"])
    return uploaded_price


def create_inventory_from_reception(raw):
    df = normalize_upload_columns(raw)
    if "codigo_concha" not in df.columns or "producto" not in df.columns:
        raise ValueError("El Excel debe tener código y producto.")
    if "llegaron" not in df.columns and "cantidad" not in df.columns:
        raise ValueError("El Excel debe tener cantidad o llegaron.")
    rows = []
    total_arrived = sum([safe_int(v) for v in df.get("llegaron", df.get("cantidad")).tolist()])
    nums = next_numbers(total_arrived)
    ni = 0
    for _, r in df.iterrows():
        codigo = clean_text(r.get("codigo_concha"))
        producto = clean_text(r.get("producto"))
        if not codigo or not producto:
            continue
        arrived = safe_int(r.get("llegaron", r.get("cantidad", 0)))
        if arrived <= 0:
            continue
        color = normalize_color(clean_text(r.get("color")) or infer_color_from_product(producto))
        clean_prod = product_without_color(producto, color)
        precio = lookup_price(codigo, producto, r.get("precio", 0))
        tallas = extract_tallas_from_row(r, arrived)
        for t in tallas:
            numero = nums[ni]; ni += 1
            internal = f"{codigo}-{color or 'SIN_COLOR'}-{t}-{numero}"
            rows.append({
                "numero": numero, "codigo_concha": codigo, "codigo_interno": internal,
                "producto": clean_prod, "color": color, "talla": t, "precio": precio,
                "estado": "disponible", "ubicacion": "tienda", "cliente": "",
                "descuento_pct": 0.0, "pagado": 0.0, "foto_url": "", "notas": "",
                "fecha_actualizacion": now_str(),
            })
    return ensure_inventory_schema(pd.DataFrame(rows))


def carga_page():
    st.title("Cargar recepción / inventario")
    if not is_admin():
        st.warning("Solo admin."); return
    uploaded = st.file_uploader("Subir Excel de recepción", type=["xlsx"])
    mode = st.radio("Modo", ["Agregar al inventario", "Reemplazar inventario completo"])
    if uploaded:
        raw = pd.read_excel(uploaded)
        st.subheader("Vista previa")
        st.dataframe(normalize_upload_columns(raw).head(30), use_container_width=True)
        try:
            new = create_inventory_from_reception(raw)
            st.write(f"Piezas que se crearán: **{len(new)}**")
            st.dataframe(new.head(50), use_container_width=True)
            if st.button("Guardar inventario", type="primary", use_container_width=True):
                if mode == "Reemplazar inventario completo":
                    st.session_state.inventario = new
                else:
                    st.session_state.inventario = ensure_inventory_schema(pd.concat([st.session_state.inventario, new], ignore_index=True))
                save_inventory()
                log_event("carga_recepcion", detalle=f"{mode}. Piezas: {len(new)}")
                st.success("Inventario guardado.")
                st.rerun()
        except Exception as e:
            st.error(str(e))

# ============================================================
# ADMIN / INVENTARIO / REPORTES
# ============================================================
def inventario_page():
    st.title("Inventario completo")
    df = st.session_state.inventario
    q = st.text_input("Buscar")
    view = df.copy()
    if q:
        ql = q.lower()
        view = view[view.apply(lambda r: ql in " ".join([str(v).lower() for v in r.values]), axis=1)]
    st.dataframe(view, use_container_width=True)


def admin_page():
    st.title("Administración segura")
    if not is_admin():
        st.warning("Solo admin."); return
    st.error("Acciones delicadas. Descarga respaldo antes de ejecutar.")
    st.download_button("Descargar respaldo Excel", data=create_backup_excel(), file_name=f"respaldo_concherie_{today_file_stamp()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    st.subheader("Acción sobre pieza")
    code = st.text_input("Buscar código numérico", placeholder="066")
    idx = find_piece(code) if code else None
    if idx is not None:
        show_piece_card(idx, allow_actions=False)
        action = st.selectbox("Acción", ["Anular venta / reserva", "Marcar disponible", "Limpiar pagos y descuentos", "Eliminar pieza"])
        confirm_code = st.text_input("Confirma escribiendo el código numérico")
        admin_pwd = st.text_input("Clave admin", type="password")
        if st.button("Ejecutar acción", type="primary", use_container_width=True):
            row = st.session_state.inventario.loc[idx]
            if confirm_code.strip() != row["numero"] or admin_pwd != USERS["jc"]["password"]:
                st.error("Confirmación o clave incorrecta.")
                return
            df = st.session_state.inventario
            if action == "Anular venta / reserva":
                df.at[idx, "estado"] = "disponible"; df.at[idx, "ubicacion"] = "tienda"; df.at[idx, "cliente"] = ""; df.at[idx, "pagado"] = 0.0; df.at[idx, "descuento_pct"] = 0.0
            elif action == "Marcar disponible":
                df.at[idx, "estado"] = "disponible"; df.at[idx, "ubicacion"] = "tienda"
            elif action == "Limpiar pagos y descuentos":
                df.at[idx, "pagado"] = 0.0; df.at[idx, "descuento_pct"] = 0.0
            elif action == "Eliminar pieza":
                df = df.drop(index=idx).reset_index(drop=True)
            st.session_state.inventario = ensure_inventory_schema(df)
            save_inventory()
            log_event("admin_" + safe_filename(action), numero=row["numero"], codigo_interno=row["codigo_interno"], cliente=row["cliente"], detalle=action)
            st.success("Acción ejecutada.")
            st.rerun()
    elif code:
        st.error("No encontré esa pieza.")
    st.markdown("---")
    st.subheader("Reset historial de movimientos")
    if st.text_input("Para confirmar escribe BORRAR HISTORIAL") == "BORRAR HISTORIAL":
        if st.text_input("Clave admin para historial", type="password") == USERS["jc"]["password"]:
            if st.button("Resetear historial", type="primary"):
                st.session_state.movimientos = pd.DataFrame(columns=MOVEMENT_COLUMNS); save_movements(); st.success("Historial reseteado."); st.rerun()


def create_backup_excel():
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        st.session_state.inventario.to_excel(writer, sheet_name="inventario", index=False)
        st.session_state.clientes.to_excel(writer, sheet_name="clientes", index=False)
        st.session_state.pagos.to_excel(writer, sheet_name="pagos", index=False)
        st.session_state.notas.to_excel(writer, sheet_name="notas", index=False)
        st.session_state.movimientos.to_excel(writer, sheet_name="movimientos", index=False)
    return out.getvalue()


def reportes_page():
    st.title("Reportes")
    df = st.session_state.inventario
    st.dataframe(df, use_container_width=True)
    st.download_button("Descargar respaldo completo", data=create_backup_excel(), file_name="respaldo_concherie.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ============================================================
# MAIN
# ============================================================
def main():
    load_all_data()
    if "user" not in st.session_state:
        login_page(); return
    sidebar()
    if st.session_state.get("last_note_warning"):
        st.warning(st.session_state.pop("last_note_warning"))
    p = st.session_state.get("page", "home")
    if p == "home": home_page()
    elif p == "buscar": buscar_page()
    elif p == "scan": scan_page()
    elif p == "ventas": ventas_page()
    elif p == "clientes": clientes_page()
    elif p == "catalogo": catalogo_page()
    elif p == "carga": carga_page()
    elif p == "qr": qr_page()
    elif p == "inventario": inventario_page()
    elif p == "reportes": reportes_page()
    elif p == "admin": admin_page()
    else: home_page()

if __name__ == "__main__":
    main()
