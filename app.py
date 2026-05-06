import os
import re
import json
import base64
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse

import streamlit as st
import pandas as pd
from PIL import Image

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm, inch
from reportlab.lib.utils import ImageReader

import qrcode

# ============================================================
# CONFIG
# ============================================================

st.set_page_config(page_title="Concherie Boutique", page_icon="🧾", layout="wide")

USERS = {
    "jc": {"password": "master", "role": "admin", "label": "Administrador"},
    "ventas": {"password": "moira", "role": "ventas", "label": "Ventas"},
    "info": {"password": "precio", "role": "info", "label": "Consulta"},
}

VALID_STATES = ["disponible", "reservado", "probando", "vendido", "mantenimiento"]

INVENTORY_COLUMNS = [
    "numero", "marca", "codigo", "color", "codigo_interno", "producto", "talla",
    "precio", "estado", "ubicacion", "cliente", "descuento_pct", "descuento",
    "pagado", "foto_file_id", "foto_url", "notas", "fecha_actualizacion"
]

CLIENT_COLUMNS = ["cliente", "telefono", "email", "notas", "fecha_creacion"]
MOVEMENT_COLUMNS = ["fecha", "usuario", "tipo", "numero", "codigo_interno", "cliente", "detalle"]
VENTAS_COLUMNS = [
    "fecha", "usuario", "numero", "codigo_interno", "cliente", "accion",
    "precio", "descuento_pct", "descuento", "neto", "pagado", "saldo", "nota_url"
]
NOTAS_COLUMNS = ["fecha", "cliente", "nota", "total", "pagado", "saldo", "drive_file_id", "drive_url"]

ALL_TABLES = {
    "inventario": INVENTORY_COLUMNS,
    "clientes": CLIENT_COLUMNS,
    "movimientos": MOVEMENT_COLUMNS,
    "ventas": VENTAS_COLUMNS,
    "notas": NOTAS_COLUMNS,
}

# ============================================================
# BASICS
# ============================================================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def clean_text(value):
    if value is None:
        return ""
    txt = str(value).strip()
    if txt.lower() in ["nan", "none", "null"]:
        return ""
    return txt


def safe_float(value, default=0.0):
    try:
        if value is None or clean_text(value) == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None or clean_text(value) == "":
            return default
        return int(float(value))
    except Exception:
        return default


def money(value):
    return f"${safe_float(value):,.2f}"


def safe_filename(text):
    txt = clean_text(text)
    txt = re.sub(r"[^A-Za-z0-9_\-]+", "_", txt)
    return txt.strip("_") or "archivo"


def role():
    return st.session_state.get("role", "")


def is_admin():
    return role() == "admin"


def is_sales():
    return role() in ["admin", "ventas"]


def can_edit_inventory():
    return role() == "admin"


def set_page(page):
    st.session_state.page = page
    st.rerun()


def display_talla(talla):
    txt = clean_text(talla)
    if txt == "" or txt.lower() in ["no", "sin", "s/t", "na", "n/a", "0"]:
        return "Talla Única"
    if txt.lower() in ["tu", "t/u", "unica", "única", "talla unica", "talla única"]:
        return "Talla Única"
    if txt.upper().startswith("T"):
        return txt.upper()
    return f"T{txt}" if txt.isdigit() else txt.upper()


def ensure_columns(df, cols):
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=cols)
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def ensure_inventory(df):
    df = ensure_columns(df, INVENTORY_COLUMNS)
    df["precio"] = pd.to_numeric(df["precio"], errors="coerce").fillna(0.0)
    df["descuento_pct"] = pd.to_numeric(df["descuento_pct"], errors="coerce").fillna(0.0)
    df["descuento"] = pd.to_numeric(df["descuento"], errors="coerce").fillna(0.0)
    df["pagado"] = pd.to_numeric(df["pagado"], errors="coerce").fillna(0.0)
    for c in [c for c in INVENTORY_COLUMNS if c not in ["precio", "descuento_pct", "descuento", "pagado"]]:
        df[c] = df[c].fillna("").astype(str)
    df.loc[df["estado"].str.strip() == "", "estado"] = "disponible"
    df.loc[df["ubicacion"].str.strip() == "", "ubicacion"] = "tienda"
    return df


def ensure_generic(df, cols):
    df = ensure_columns(df, cols)
    for c in cols:
        df[c] = df[c].fillna("").astype(str)
    return df

# ============================================================
# GOOGLE AUTH / SHEETS / DRIVE
# ============================================================

def get_gsheets_secrets():
    if "connections" not in st.secrets or "gsheets" not in st.secrets["connections"]:
        return None
    return st.secrets["connections"]["gsheets"]


def get_drive_folder_id():
    try:
        return st.secrets["drive"]["folder_id"]
    except Exception:
        return ""


def spreadsheet_id_from_url(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else url


@st.cache_resource
def google_creds_and_services():
    gs = get_gsheets_secrets()
    if gs is None:
        return None, None, None
    info = {
        "type": gs.get("type", "service_account"),
        "project_id": gs.get("project_id"),
        "private_key_id": gs.get("private_key_id"),
        "private_key": gs.get("private_key"),
        "client_email": gs.get("client_email"),
        "client_id": gs.get("client_id"),
        "auth_uri": gs.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
        "token_uri": gs.get("token_uri", "https://oauth2.googleapis.com/token"),
        "auth_provider_x509_cert_url": gs.get("auth_provider_x509_cert_url", "https://www.googleapis.com/oauth2/v1/certs"),
        "client_x509_cert_url": gs.get("client_x509_cert_url"),
    }
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return creds, sheets, drive


def sheets_ready():
    try:
        creds, sheets, drive = google_creds_and_services()
        return sheets is not None
    except Exception:
        return False


def drive_ready():
    try:
        creds, sheets, drive = google_creds_and_services()
        return drive is not None and get_drive_folder_id() != ""
    except Exception:
        return False


def get_spreadsheet_id():
    gs = get_gsheets_secrets()
    if gs is None:
        return ""
    return spreadsheet_id_from_url(gs.get("spreadsheet", ""))


def get_sheet_titles():
    _, sheets, _ = google_creds_and_services()
    sid = get_spreadsheet_id()
    meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def ensure_worksheet(name):
    _, sheets, _ = google_creds_and_services()
    sid = get_spreadsheet_id()
    titles = get_sheet_titles()
    if name in titles:
        return
    body = {"requests": [{"addSheet": {"properties": {"title": name}}}]}
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body=body).execute()


def read_sheet(name, columns):
    if not sheets_ready():
        return read_local(name, columns)
    try:
        ensure_worksheet(name)
        _, sheets, _ = google_creds_and_services()
        sid = get_spreadsheet_id()
        result = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"{name}!A:ZZ").execute()
        values = result.get("values", [])
        if not values:
            return pd.DataFrame(columns=columns)
        header = values[0]
        rows = values[1:]
        normalized_rows = []
        for row in rows:
            row = row + [""] * (len(header) - len(row))
            normalized_rows.append(row[:len(header)])
        df = pd.DataFrame(normalized_rows, columns=header)
        return ensure_columns(df, columns)
    except Exception as e:
        st.session_state["sheet_warning"] = f"No pude leer Google Sheets; usando local. {e}"
        return read_local(name, columns)


def write_sheet(name, df, columns):
    df = ensure_columns(df, columns)
    if not sheets_ready():
        write_local(name, df)
        return False
    try:
        ensure_worksheet(name)
        _, sheets, _ = google_creds_and_services()
        sid = get_spreadsheet_id()
        sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"{name}!A:ZZ", body={}).execute()
        values = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        body = {"values": values}
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{name}!A1",
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        return True
    except Exception as e:
        st.session_state["sheet_warning"] = f"No pude escribir Google Sheets; guardé local. {e}"
        write_local(name, df)
        return False


def local_path(name):
    os.makedirs("data", exist_ok=True)
    return os.path.join("data", f"{name}.csv")


def read_local(name, columns):
    path = local_path(name)
    if os.path.exists(path):
        try:
            return ensure_columns(pd.read_csv(path), columns)
        except Exception:
            pass
    return pd.DataFrame(columns=columns)


def write_local(name, df):
    os.makedirs("data", exist_ok=True)
    df.to_csv(local_path(name), index=False)


def load_all():
    if st.session_state.get("loaded"):
        return
    st.session_state.inventario = ensure_inventory(read_sheet("inventario", INVENTORY_COLUMNS))
    st.session_state.clientes = ensure_generic(read_sheet("clientes", CLIENT_COLUMNS), CLIENT_COLUMNS)
    st.session_state.movimientos = ensure_generic(read_sheet("movimientos", MOVEMENT_COLUMNS), MOVEMENT_COLUMNS)
    st.session_state.ventas = ensure_generic(read_sheet("ventas", VENTAS_COLUMNS), VENTAS_COLUMNS)
    st.session_state.notas = ensure_generic(read_sheet("notas", NOTAS_COLUMNS), NOTAS_COLUMNS)
    st.session_state.loaded = True


def save_table(name):
    if name == "inventario":
        st.session_state.inventario = ensure_inventory(st.session_state.inventario)
        write_sheet("inventario", st.session_state.inventario, INVENTORY_COLUMNS)
    elif name == "clientes":
        st.session_state.clientes = ensure_generic(st.session_state.clientes, CLIENT_COLUMNS)
        write_sheet("clientes", st.session_state.clientes, CLIENT_COLUMNS)
    elif name == "movimientos":
        st.session_state.movimientos = ensure_generic(st.session_state.movimientos, MOVEMENT_COLUMNS)
        write_sheet("movimientos", st.session_state.movimientos, MOVEMENT_COLUMNS)
    elif name == "ventas":
        st.session_state.ventas = ensure_generic(st.session_state.ventas, VENTAS_COLUMNS)
        write_sheet("ventas", st.session_state.ventas, VENTAS_COLUMNS)
    elif name == "notas":
        st.session_state.notas = ensure_generic(st.session_state.notas, NOTAS_COLUMNS)
        write_sheet("notas", st.session_state.notas, NOTAS_COLUMNS)


def log_event(tipo, numero="", codigo_interno="", cliente="", detalle=""):
    row = {
        "fecha": now_str(), "usuario": st.session_state.get("user", ""), "tipo": tipo,
        "numero": clean_text(numero), "codigo_interno": clean_text(codigo_interno),
        "cliente": clean_text(cliente), "detalle": clean_text(detalle)
    }
    st.session_state.movimientos = pd.concat([st.session_state.movimientos, pd.DataFrame([row])], ignore_index=True)
    save_table("movimientos")

# ============================================================
# DRIVE HELPERS
# ============================================================

def drive_find_child(parent_id, name, mime_type=None):
    if not drive_ready():
        return ""
    _, _, drive = google_creds_and_services()
    q = f"'{parent_id}' in parents and name = '{name.replace(chr(39), chr(92)+chr(39))}' and trashed = false"
    if mime_type:
        q += f" and mimeType = '{mime_type}'"
    res = drive.files().list(q=q, fields="files(id,name,mimeType)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else ""


def drive_create_folder(parent_id, name):
    existing = drive_find_child(parent_id, name, "application/vnd.google-apps.folder")
    if existing:
        return existing
    _, _, drive = google_creds_and_services()
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    file = drive.files().create(body=metadata, fields="id").execute()
    return file["id"]


def drive_upload_bytes(data, filename, mime_type, parent_id):
    if not drive_ready():
        return "", ""
    _, _, drive = google_creds_and_services()
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime_type, resumable=False)
    metadata = {"name": filename, "parents": [parent_id]}
    file = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    return file.get("id", ""), file.get("webViewLink", "")


def drive_download_bytes(file_id):
    if not file_id or not drive_ready():
        return None
    try:
        _, _, drive = google_creds_and_services()
        req = drive.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return fh.getvalue()
    except Exception:
        return None


def get_root_subfolder(name):
    root = get_drive_folder_id()
    return drive_create_folder(root, name)


def upload_photo_to_drive(uploaded_file, numero):
    try:
        img = Image.open(uploaded_file).convert("RGB")
    except Exception:
        raise ValueError("No pude leer esa foto. En iPhone, intenta tomarla en formato JPG o 'Más compatible'.")
    max_size = 1400
    w, h = img.size
    scale = min(max_size / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=78, optimize=True)
    data = buf.getvalue()
    photos_folder = get_root_subfolder("Fotos")
    piezas_folder = drive_create_folder(photos_folder, "Piezas")
    filename = f"{safe_filename(numero)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    return drive_upload_bytes(data, filename, "image/jpeg", piezas_folder)


def upload_note_to_drive(cliente, pdf_bytes, total, saldo):
    notes_root = get_root_subfolder("Notas de venta")
    client_folder = drive_create_folder(notes_root, safe_filename(cliente))
    filename = f"Nota_{safe_filename(cliente)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    file_id, url = drive_upload_bytes(pdf_bytes, filename, "application/pdf", client_folder)
    row = {
        "fecha": now_str(), "cliente": cliente, "nota": filename, "total": str(total),
        "pagado": "", "saldo": str(saldo), "drive_file_id": file_id, "drive_url": url
    }
    st.session_state.notas = pd.concat([st.session_state.notas, pd.DataFrame([row])], ignore_index=True)
    save_table("notas")
    return file_id, url

# ============================================================
# CODES / PARSING
# ============================================================

COLOR_WORDS = [
    "LIGHT LAVANDA", "LIGHT LAVENDER", "LIGHT BLUE", "LIGHT PINK", "LIGHT SAND",
    "OLD PINK", "PEA/GREEN", "PEA GREEN", "LAME SILVER", "LAMÉ SILVER",
    "SILVER", "PURPLE", "VIOLETA", "VIOLET", "LAVANDA", "LAVENDER", "ANIS",
    "WHITE", "OLIVE", "ROJA", "ROJO", "RED", "ORO", "GOLD", "FUCSIA", "FUCHSIA",
    "LEMON", "ORCHIDEA", "AMARILLA", "YELLOW", "PINK", "BLUE", "GREEN"
]


def parse_color(producto):
    p = clean_text(producto).upper()
    # If slash, often color appears after slash
    if "/" in p:
        tail = p.split("/")[-1].strip()
        if tail:
            return tail.replace(" ", "-")
    for color in COLOR_WORDS:
        if color in p:
            return color.replace(" ", "-").replace("/", "-")
    parts = p.split()
    return parts[-1] if parts else ""


def normalize_code_part(text):
    txt = clean_text(text).upper()
    txt = re.sub(r"[^A-Z0-9]+", "-", txt)
    txt = re.sub(r"-+", "-", txt).strip("-")
    return txt


def next_numeric_code(existing_df):
    if existing_df.empty or "numero" not in existing_df.columns:
        return 1
    nums = []
    for v in existing_df["numero"].astype(str).tolist():
        m = re.match(r"^(\d+)$", v.strip())
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def make_internal_code(codigo, color, talla, numero):
    t = normalize_code_part(display_talla(talla).replace("Talla Única", "TU"))
    c = normalize_code_part(codigo)
    col = normalize_code_part(color) or "COLOR"
    return f"{c}-{col}-{t}-{numero}"


def normalize_upload_columns(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    renames = {
        "maison": "marca", "marca": "marca", "brand": "marca",
        "codigo": "codigo", "código": "codigo", "codigo base": "codigo", "sku": "codigo",
        "producto": "producto", "descripcion": "producto", "descripción": "producto",
        "cantidad": "cantidad", "pedido": "cantidad", "pedidas": "cantidad",
        "precio": "precio", "precio unitario": "precio", "precio_unitario": "precio",
        "llegaron": "llegaron", "llego": "llegaron", "llegó": "llegaron", "recibido": "llegaron",
        "talla": "talla", "tallas": "talla", "size": "talla", "color": "color",
    }
    df = df.rename(columns={c: renames.get(c, c) for c in df.columns})
    return df


def extract_sizes_from_row(row, arrived):
    sizes = []
    # named columns containing talla/size
    for col, val in row.items():
        if "talla" in str(col).lower() or "size" in str(col).lower():
            txt = clean_text(val)
            if txt and txt.lower() not in ["no", "nan"]:
                pieces = re.split(r"[,;/\n]+", txt)
                sizes.extend([clean_text(p) for p in pieces if clean_text(p)])
    # If one talla col had a string like 48 44 40, split spaces only if multiple numbers
    exploded = []
    for s in sizes:
        nums = re.findall(r"\d+", s)
        if len(nums) > 1:
            exploded.extend(nums)
        else:
            exploded.append(s)
    sizes = exploded
    if not sizes:
        sizes = ["Talla Única"] * arrived
    if len(sizes) < arrived:
        sizes.extend([sizes[-1] if sizes else "Talla Única"] * (arrived - len(sizes)))
    return sizes[:arrived]


def create_inventory_from_reception(raw_df, replace=False):
    df = normalize_upload_columns(raw_df)
    required = ["codigo", "producto", "precio"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(missing)}")
    if "marca" not in df.columns:
        df["marca"] = ""
    if "color" not in df.columns:
        df["color"] = ""
    if "llegaron" not in df.columns:
        if "cantidad" in df.columns:
            df["llegaron"] = df["cantidad"]
        else:
            raise ValueError("Falta columna 'llegaron' o 'cantidad'.")
    base = pd.DataFrame(columns=INVENTORY_COLUMNS) if replace else st.session_state.inventario
    next_num = next_numeric_code(base)
    rows = []
    faltantes = []
    for _, r in df.iterrows():
        codigo = clean_text(r.get("codigo"))
        producto = clean_text(r.get("producto"))
        marca = clean_text(r.get("marca"))
        color = clean_text(r.get("color")) or parse_color(producto)
        pedido = safe_int(r.get("cantidad"), safe_int(r.get("llegaron"), 0))
        llegaron = safe_int(r.get("llegaron"), 0)
        precio = safe_float(r.get("precio"), 0.0)
        if not codigo or not producto or llegaron <= 0:
            if pedido > 0 and llegaron == 0:
                faltantes.append({"codigo": codigo, "producto": producto, "pedido": pedido, "llegaron": llegaron, "faltan": pedido-llegaron})
            continue
        if pedido > llegaron:
            faltantes.append({"codigo": codigo, "producto": producto, "pedido": pedido, "llegaron": llegaron, "faltan": pedido-llegaron})
        sizes = extract_sizes_from_row(r, llegaron)
        for size in sizes:
            numero = f"{next_num:03d}"
            codigo_interno = make_internal_code(codigo, color, size, numero)
            rows.append({
                "numero": numero, "marca": marca, "codigo": codigo, "color": color,
                "codigo_interno": codigo_interno, "producto": producto, "talla": display_talla(size),
                "precio": precio, "estado": "disponible", "ubicacion": "tienda", "cliente": "",
                "descuento_pct": 0.0, "descuento": 0.0, "pagado": 0.0,
                "foto_file_id": "", "foto_url": "", "notas": "", "fecha_actualizacion": now_str()
            })
            next_num += 1
    return ensure_inventory(pd.DataFrame(rows)), pd.DataFrame(faltantes)

# ============================================================
# LOGIN / NAV
# ============================================================

def login_page():
    st.title("Concherie Boutique")
    st.subheader("Acceso")
    with st.form("login"):
        u = st.text_input("Usuario").strip().lower()
        p = st.text_input("Clave", type="password")
        ok = st.form_submit_button("Entrar", use_container_width=True)
    if ok:
        if u in USERS and USERS[u]["password"] == p:
            st.session_state.user = u
            st.session_state.role = USERS[u]["role"]
            st.session_state.page = "home"
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos.")


def logout():
    for k in ["user", "role", "page", "selected_idx", "selected_model"]:
        st.session_state.pop(k, None)
    st.rerun()


def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user','')}**")
    st.sidebar.write(f"Rol: **{USERS.get(st.session_state.get('user',''),{}).get('label','')}**")
    st.sidebar.success("Datos: Google Sheets" if sheets_ready() else "Datos: local")
    st.sidebar.success("Drive: activo" if drive_ready() else "Drive: no configurado")
    if st.session_state.get("sheet_warning"):
        st.sidebar.info(st.session_state["sheet_warning"])
    st.sidebar.markdown("---")
    buttons = [("🏠 Inicio", "home"), ("◼️ Escanear QR", "scan"), ("🔢 Buscar código", "buscar")]
    if role() == "info":
        buttons += [("📦 Disponibles", "disponibles")]
    elif role() == "ventas":
        buttons += [("🛍️ Venta / reserva", "ventas"), ("👥 Clientes", "clientes"), ("📄 Catálogo", "catalogo"), ("📦 Disponibles", "disponibles")]
    elif role() == "admin":
        buttons += [("📥 Recepción", "recepcion"), ("🏷️ Generar QR", "qr"), ("📦 Inventario", "inventario"), ("🛍️ Ventas", "ventas"), ("👥 Clientes", "clientes"), ("📄 Catálogo", "catalogo"), ("📊 Reportes", "reportes"), ("⚙️ Admin", "admin")]
    for label, page in buttons:
        if st.sidebar.button(label, use_container_width=True):
            set_page(page)
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        logout()


def home_page():
    st.title("Concherie Boutique")
    if role() == "info":
        cols = st.columns(2)
        with cols[0]:
            if st.button("◼️ Escanear QR", use_container_width=True): set_page("scan")
            if st.button("🔢 Buscar código", use_container_width=True): set_page("buscar")
        with cols[1]:
            if st.button("📦 Inventario disponible", use_container_width=True): set_page("disponibles")
    elif role() == "ventas":
        cols = st.columns(2)
        with cols[0]:
            if st.button("◼️ Escanear QR", use_container_width=True): set_page("scan")
            if st.button("🔢 Buscar código", use_container_width=True): set_page("buscar")
            if st.button("🛍️ Registrar venta", use_container_width=True): set_page("ventas")
        with cols[1]:
            if st.button("👥 Clientes", use_container_width=True): set_page("clientes")
            if st.button("📄 Catálogo disponible", use_container_width=True): set_page("catalogo")
            if st.button("📦 Disponibles", use_container_width=True): set_page("disponibles")
    else:
        cols = st.columns(3)
        actions = [("◼️ Escanear QR","scan"),("🔢 Buscar código","buscar"),("📥 Recepción","recepcion"),("🏷️ Generar QR","qr"),("📦 Inventario","inventario"),("🛍️ Ventas","ventas"),("👥 Clientes","clientes"),("📄 Catálogo","catalogo"),("📊 Reportes","reportes"),("⚙️ Admin","admin")]
        for i,(label,page) in enumerate(actions):
            with cols[i%3]:
                if st.button(label, use_container_width=True): set_page(page)
    df = st.session_state.inventario
    if not df.empty:
        st.markdown("---")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total", len(df))
        c2.metric("Disponibles", len(df[df.estado=="disponible"]))
        c3.metric("Reservadas", len(df[df.estado=="reservado"]))
        c4.metric("Vendidas", len(df[df.estado=="vendido"]))

# ============================================================
# FIND / FICHA
# ============================================================

def find_piece(code):
    code = clean_text(code)
    if "|" in code:
        code = code.split("|")[-1].strip()
    df = st.session_state.inventario
    if df.empty:
        return None
    for col in ["numero", "codigo_interno", "codigo"]:
        exact = df[df[col].astype(str).str.upper() == code.upper()]
        if not exact.empty:
            return exact.index[0]
    return None


def buscar_page():
    st.title("Buscar código")
    code = st.text_input("Escribe el código", placeholder="Ej: 001")
    if code:
        idx = find_piece(code)
        if idx is None:
            st.error("No encontré ese código.")
        else:
            show_piece(idx)


def scan_page():
    st.title("Escanear QR")
    st.info("Si la cámara no lee el QR, escribe el código numérico grande en Buscar código.")
    camera = st.camera_input("Tomar foto del QR")
    upload = st.file_uploader("O subir foto del QR", type=["jpg","jpeg","png"])
    source = camera or upload
    if source:
        code, error = decode_qr(source)
        if code:
            idx = find_piece(code)
            if idx is not None:
                show_piece(idx)
            else:
                st.error(f"Leí {code}, pero no lo encontré en inventario.")
        else:
            st.error(error or "No pude leer el QR.")


def decode_qr(uploaded_file):
    try:
        import cv2
        import numpy as np
        img = Image.open(uploaded_file).convert("RGB")
        arr = np.array(img)
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(arr)
        return clean_text(data), None
    except Exception as e:
        return "", str(e)


def show_piece(idx):
    df = st.session_state.inventario
    r = df.loc[idx]
    st.subheader(f"{r['numero']} · {r['producto']}")
    c1,c2,c3 = st.columns([1.1,1.2,1])
    with c1:
        show_piece_photo(r)
    with c2:
        st.write(f"**Código numérico:** {r['numero']}")
        st.write(f"**Código interno:** {r['codigo_interno']}")
        st.write(f"**Modelo:** {r['codigo']}")
        st.write(f"**Color:** {r['color']}")
        st.write(f"**Talla:** {display_talla(r['talla'])}")
        st.write(f"**Estado:** {r['estado']}")
        st.write(f"**Ubicación:** {r['ubicacion']}")
    with c3:
        st.metric("Precio", money(r["precio"]))
        if is_sales():
            neto = safe_float(r.precio) - safe_float(r.descuento)
            st.metric("Neto", money(neto))
            st.metric("Saldo", money(neto - safe_float(r.pagado)))
    if is_sales():
        if clean_text(r.get("foto_file_id")) == "":
            st.warning("Esta pieza no tiene foto.")
        upload_photo_widget(idx)
        st.markdown("---")
        quick_action_form(idx)


def show_piece_photo(row):
    data = drive_download_bytes(clean_text(row.get("foto_file_id", "")))
    if data:
        st.image(data, use_container_width=True)
    else:
        st.info("Sin foto")


def upload_photo_widget(idx):
    up = st.file_uploader("📸 Agregar / cambiar foto", type=["jpg","jpeg","png","heic","heif"], key=f"photo_{idx}")
    if up and st.button("Guardar foto en Drive", key=f"save_photo_{idx}", use_container_width=True):
        try:
            df = st.session_state.inventario
            numero = df.at[idx, "numero"]
            fid, url = upload_photo_to_drive(up, numero)
            df.at[idx, "foto_file_id"] = fid
            df.at[idx, "foto_url"] = url
            df.at[idx, "fecha_actualizacion"] = now_str()
            st.session_state.inventario = ensure_inventory(df)
            save_table("inventario")
            log_event("foto", numero=numero, codigo_interno=df.at[idx,"codigo_interno"], detalle="Foto subida a Drive")
            st.success("Foto guardada.")
            st.rerun()
        except Exception as e:
            st.error(str(e))


def client_options():
    names = set(st.session_state.clientes["cliente"].dropna().astype(str).str.strip())
    inv_names = set(st.session_state.inventario["cliente"].dropna().astype(str).str.strip())
    names = sorted([n for n in names.union(inv_names) if n])
    return names


def ensure_client(cliente):
    cliente = clean_text(cliente)
    if not cliente:
        return
    clients = st.session_state.clientes
    mask = clients["cliente"].astype(str).str.lower() == cliente.lower()
    if not mask.any():
        row = {"cliente": cliente, "telefono":"", "email":"", "notas":"", "fecha_creacion": now_str()}
        st.session_state.clientes = pd.concat([clients, pd.DataFrame([row])], ignore_index=True)
        save_table("clientes")


def quick_action_form(idx):
    df = st.session_state.inventario
    r = df.loc[idx]
    with st.form(f"action_{idx}"):
        action = st.selectbox("Acción", ["vendido", "reservado", "probando", "disponible"])
        opts = ["+ Nueva cliente"] + client_options()
        selected = st.selectbox("Cliente", opts)
        new_client = ""
        if selected == "+ Nueva cliente":
            new_client = st.text_input("Nombre nueva cliente")
        cliente = new_client if selected == "+ Nueva cliente" else selected
        disc_pct = st.number_input("Descuento %", min_value=0.0, max_value=100.0, value=safe_float(r.descuento_pct), step=1.0)
        descuento = safe_float(r.precio) * disc_pct / 100
        pagado = st.number_input("Pago recibido", min_value=0.0, value=safe_float(r.pagado), step=10.0)
        neto = safe_float(r.precio) - descuento
        st.info(f"Neto: {money(neto)} · Saldo: {money(neto-pagado)}")
        guardar = st.form_submit_button("Guardar", use_container_width=True)
    if guardar:
        cliente = clean_text(cliente)
        df.at[idx,"estado"] = action
        df.at[idx,"cliente"] = cliente
        df.at[idx,"ubicacion"] = "casa cliente" if action == "probando" else ("tienda" if action == "disponible" else df.at[idx,"ubicacion"])
        df.at[idx,"descuento_pct"] = disc_pct
        df.at[idx,"descuento"] = descuento
        df.at[idx,"pagado"] = pagado
        df.at[idx,"fecha_actualizacion"] = now_str()
        st.session_state.inventario = ensure_inventory(df)
        save_table("inventario")
        if cliente: ensure_client(cliente)
        row = {"fecha": now_str(), "usuario": st.session_state.user, "numero": r.numero, "codigo_interno": r.codigo_interno, "cliente": cliente, "accion": action, "precio": str(r.precio), "descuento_pct": str(disc_pct), "descuento": str(descuento), "neto": str(neto), "pagado": str(pagado), "saldo": str(neto-pagado), "nota_url": ""}
        st.session_state.ventas = pd.concat([st.session_state.ventas, pd.DataFrame([row])], ignore_index=True)
        save_table("ventas")
        log_event(action, numero=r.numero, codigo_interno=r.codigo_interno, cliente=cliente, detalle=f"Neto {neto}, pagado {pagado}")
        st.success("Guardado.")
        st.rerun()

# ============================================================
# MAIN PAGES
# ============================================================

def disponibles_page():
    st.title("Inventario disponible")
    df = st.session_state.inventario
    if df.empty:
        st.info("No hay inventario.")
        return
    view = df[df.estado == "disponible"].copy()
    q = st.text_input("Filtrar")
    if q:
        s=q.lower(); view = view[view.apply(lambda r: s in " ".join(map(str,r.values)).lower(), axis=1)]
    st.dataframe(view[["numero","codigo_interno","producto","color","talla","precio","estado"]], use_container_width=True)


def inventario_page():
    st.title("Inventario completo")
    if not is_admin():
        disponibles_page(); return
    df = st.session_state.inventario
    st.dataframe(df, use_container_width=True)
    if not df.empty:
        idx = st.selectbox("Editar pieza", df.index, format_func=lambda i: f"{df.at[i,'numero']} · {df.at[i,'codigo_interno']}")
        show_piece(idx)


def recepcion_page():
    st.title("Recepción de inventario")
    if not is_admin():
        st.warning("Solo admin."); return
    st.write("Carga el Excel con lo pedido, lo que llegó y tallas. Se crean piezas solo por lo que llegó.")
    uploaded = st.file_uploader("Excel recepción", type=["xlsx"])
    replace = st.checkbox("Reemplazar inventario completo", value=False)
    if uploaded:
        raw = pd.read_excel(uploaded)
        st.dataframe(normalize_upload_columns(raw).head(30), use_container_width=True)
        try:
            new_df, faltantes = create_inventory_from_reception(raw, replace=replace)
            st.write(f"Piezas nuevas: **{len(new_df)}**")
            st.dataframe(new_df.head(50), use_container_width=True)
            if not faltantes.empty:
                st.warning("Hay faltantes")
                st.dataframe(faltantes, use_container_width=True)
            if st.button("Guardar recepción", type="primary", use_container_width=True):
                if replace:
                    st.session_state.inventario = new_df
                else:
                    st.session_state.inventario = ensure_inventory(pd.concat([st.session_state.inventario, new_df], ignore_index=True))
                save_table("inventario")
                log_event("recepcion", detalle=f"Piezas creadas: {len(new_df)}")
                st.success("Recepción guardada.")
                st.rerun()
        except Exception as e:
            st.error(str(e))


def ventas_page():
    st.title("Venta / reserva")
    if not is_sales(): st.warning("Sin permiso."); return
    code = st.text_input("Código numérico", placeholder="001")
    if code:
        idx = find_piece(code)
        if idx is None: st.error("No encontrado")
        else: show_piece(idx)


def clientes_page():
    st.title("Clientes")
    if not is_sales(): st.warning("Sin permiso."); return
    with st.expander("Agregar cliente"):
        with st.form("client_add"):
            name = st.text_input("Nombre")
            tel = st.text_input("Teléfono")
            email = st.text_input("Email")
            notas = st.text_area("Notas")
            ok = st.form_submit_button("Guardar")
        if ok and name:
            ensure_client(name)
            clients = st.session_state.clientes
            mask = clients.cliente.astype(str).str.lower()==name.lower()
            i = clients[mask].index[0]
            clients.at[i,"telefono"] = tel; clients.at[i,"email"] = email; clients.at[i,"notas"] = notas
            st.session_state.clientes = clients; save_table("clientes"); st.rerun()
    opts = client_options()
    if not opts: st.info("No hay clientes."); return
    cliente = st.selectbox("Cliente", opts)
    show_client(cliente)


def show_client(cliente):
    df = st.session_state.inventario
    data = df[df.cliente.astype(str).str.lower()==cliente.lower()].copy()
    st.subheader(cliente)
    if data.empty:
        st.info("Sin piezas asociadas."); return
    data["neto"] = data.precio.astype(float)-data.descuento.astype(float)
    data["saldo"] = data.neto-data.pagado.astype(float)
    st.dataframe(data[["numero","producto","talla","estado","precio","descuento_pct","neto","pagado","saldo"]], use_container_width=True)
    total = data.neto.sum(); pagado = data.pagado.sum(); saldo = data.saldo.sum()
    c1,c2,c3 = st.columns(3); c1.metric("Total", money(total)); c2.metric("Pagado", money(pagado)); c3.metric("Saldo", money(saldo))
    pdf = create_invoice_pdf(cliente, data)
    col1,col2 = st.columns(2)
    with col1:
        st.download_button("Descargar nota PDF", data=pdf, file_name=f"nota_{safe_filename(cliente)}.pdf", mime="application/pdf", use_container_width=True)
    with col2:
        if st.button("Guardar nota en Drive", use_container_width=True):
            fid,url = upload_note_to_drive(cliente, pdf, total, saldo)
            st.success("Nota guardada en Drive.")
            st.write(url)

# ============================================================
# PDF / QR
# ============================================================

def qr_png(text):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=2)
    qr.add_data(clean_text(text)); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf=BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()


def qr_page():
    st.title("Generar QR")
    if not is_admin(): st.warning("Solo admin."); return
    df = st.session_state.inventario.sort_values("codigo_interno")
    only = st.checkbox("Solo disponibles", value=False)
    if only: df = df[df.estado=="disponible"]
    st.write(f"Etiquetas: **{len(df)}**")
    pdf = create_qr_labels_pdf(df)
    st.download_button("Descargar etiquetas 5x8 cm", data=pdf, file_name="etiquetas_concherie_5x8.pdf", mime="application/pdf", use_container_width=True)


def create_qr_labels_pdf(df):
    buf=BytesIO(); c=canvas.Canvas(buf, pagesize=letter)
    width,height=letter
    label_w=8*cm; label_h=5*cm; margin_x=.5*cm; margin_y=.5*cm; gap=.25*cm
    cols=max(1,int((width-2*margin_x)//(label_w+gap))); rows=max(1,int((height-2*margin_y)//(label_h+gap)))
    for n,(_,r) in enumerate(df.iterrows()):
        if n>0 and n%(cols*rows)==0: c.showPage()
        col=n%cols; row=(n//cols)%rows
        x=margin_x+col*(label_w+gap); y=height-margin_y-(row+1)*label_h-row*gap
        c.roundRect(x,y,label_w,label_h,6,stroke=1,fill=0)
        qr_size=4*cm
        c.drawImage(ImageReader(BytesIO(qr_png(r.numero))), x+.35*cm, y+.5*cm, qr_size, qr_size, mask="auto")
        tx=x+4.75*cm
        c.setFont("Helvetica-Bold",26); c.drawString(tx,y+3.55*cm,str(r.numero))
        c.setFont("Helvetica-Bold",10); c.drawString(tx,y+2.75*cm,clean_text(r.codigo)[:18])
        c.setFont("Helvetica",9); c.drawString(tx,y+2.25*cm,clean_text(r.color)[:20])
        c.setFont("Helvetica",9); c.drawString(tx,y+1.75*cm,display_talla(r.talla)[:20])
        c.setFont("Helvetica-Oblique",7); c.drawString(tx,y+.65*cm,clean_text(r.codigo_interno)[:24])
    c.save(); buf.seek(0); return buf.getvalue()


def catalogo_page():
    st.title("Catálogo")
    if not is_sales(): st.warning("Sin permiso."); return
    df = st.session_state.inventario
    if df.empty: st.info("No hay inventario."); return
    con_precio = st.checkbox("Mostrar precio", value=True)
    talla_filter = st.text_input("Filtrar por talla (opcional)", placeholder="Ej: 44")
    available = df[df.estado=="disponible"].copy()
    if talla_filter:
        tf = talla_filter.lower().replace("t", "").strip()
        available = available[available.talla.astype(str).str.lower().str.replace("t", "", regex=False).str.contains(tf, na=False)]
    pdf=create_catalog_pdf(available, con_precio)
    st.download_button("Descargar catálogo PDF", data=pdf, file_name="catalogo_concherie.pdf", mime="application/pdf", use_container_width=True)


def create_catalog_pdf(df, con_precio=True):
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=letter); width,height=letter
    card_w=3.45*inch; card_h=4.7*inch; positions=[(.45*inch,height-.45*inch-card_h),(4.05*inch,height-.45*inch-card_h),(.45*inch,height-.7*inch-2*card_h),(4.05*inch,height-.7*inch-2*card_h)]
    if df.empty:
        c.setFont("Helvetica-Bold",16); c.drawString(.7*inch,height-inch,"No hay piezas disponibles."); c.save(); buf.seek(0); return buf.getvalue()
    for i,(_,r) in enumerate(df.sort_values(["codigo_interno"]).iterrows()):
        if i>0 and i%4==0: c.showPage()
        x,y=positions[i%4]
        c.roundRect(x,y,card_w,card_h,10,stroke=1,fill=0)
        img=drive_download_bytes(r.foto_file_id)
        if img:
            try: c.drawImage(ImageReader(BytesIO(img)),x+.18*inch,y+2.25*inch,card_w-.36*inch,2.25*inch,preserveAspectRatio=True,anchor="c",mask="auto")
            except Exception: pass
        else:
            c.setFont("Helvetica-Oblique",9); c.drawString(x+.25*inch,y+3.35*inch,"Sin foto")
        c.setFont("Helvetica-Bold",10); c.drawString(x+.22*inch,y+1.92*inch,clean_text(r.codigo_interno)[:34])
        c.setFont("Helvetica-Oblique",9); c.drawString(x+.22*inch,y+1.65*inch,clean_text(r.producto)[:38])
        c.setFont("Helvetica",8.5); c.drawString(x+.22*inch,y+1.38*inch,f"Color: {clean_text(r.color)}")
        c.drawString(x+.22*inch,y+1.13*inch,f"Talla: {display_talla(r.talla)}")
        # other sizes same model/color
        peers=df[(df.codigo==r.codigo)&(df.color==r.color)]
        sizes=sorted(set([display_talla(x) for x in peers.talla.tolist()]))
        c.drawString(x+.22*inch,y+.88*inch,f"Otras tallas: {', '.join(sizes)[:32]}")
        if con_precio:
            c.setFont("Helvetica-Bold",14); c.drawString(x+.22*inch,y+.45*inch,money(r.precio))
    c.save(); buf.seek(0); return buf.getvalue()


def create_invoice_pdf(cliente, data):
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=letter); width,height=letter; y=height-.75*inch
    c.setFont("Helvetica-Bold",20); c.drawString(.7*inch,y,"Concherie Boutique"); y-=.28*inch
    c.setFont("Helvetica-Oblique",12); c.drawString(.7*inch,y,"Nota de venta"); y-=.25*inch
    c.setFont("Helvetica",10); c.drawString(.7*inch,y,f"Cliente: {cliente}"); y-=.2*inch; c.drawString(.7*inch,y,f"Fecha: {today_str()}"); y-=.45*inch
    headers=["Código","Producto","Talla","Precio","Desc%","Neto","Pagado","Saldo"]; xs=[.45,1.1,2.85,3.45,4.15,4.8,5.55,6.35]
    c.setFont("Helvetica-Bold",8)
    for x,h in zip(xs,headers): c.drawString(x*inch,y,h)
    y-=.17*inch; c.line(.45*inch,y,7.3*inch,y); y-=.2*inch
    c.setFont("Helvetica",7.5)
    total=pagado=saldo=0
    for _,r in data.iterrows():
        if y<.8*inch: c.showPage(); y=height-.7*inch; c.setFont("Helvetica",7.5)
        precio=safe_float(r.precio); desc=safe_float(r.descuento); neto=precio-desc; pay=safe_float(r.pagado); sal=neto-pay
        total+=neto; pagado+=pay; saldo+=sal
        vals=[r.numero, clean_text(r.producto)[:24], display_talla(r.talla)[:8], money(precio), f"{safe_float(r.descuento_pct):.0f}%", money(neto), money(pay), money(sal)]
        for x,v in zip(xs,vals): c.drawString(x*inch,y,str(v))
        y-=.18*inch
    y-=.3*inch
    c.setFont("Helvetica-Bold",11); c.drawString(.7*inch,y,f"Total: {money(total)}"); y-=.23*inch; c.drawString(.7*inch,y,f"Pagado: {money(pagado)}"); y-=.23*inch
    c.setFont("Helvetica-Bold",14); c.drawString(.7*inch,y,f"Saldo pendiente: {money(saldo)}")
    c.save(); buf.seek(0); return buf.getvalue()

# ============================================================
# REPORTS / ADMIN
# ============================================================

def reportes_page():
    st.title("Reportes")
    df=st.session_state.inventario
    if df.empty: st.info("No hay datos."); return
    st.dataframe(df.groupby(["codigo","producto","color","talla","estado"]).size().reset_index(name="cantidad"), use_container_width=True)
    out=BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as w:
        for name, cols in ALL_TABLES.items():
            getattr(st.session_state,name).to_excel(w,sheet_name=name,index=False)
    st.download_button("Descargar respaldo Excel", data=out.getvalue(), file_name=f"respaldo_concherie_{today_str()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)


def admin_page():
    st.title("⚙️ Administración")
    if not is_admin(): st.warning("Solo admin."); return
    st.error("Zona delicada: las acciones destructivas requieren respaldo, frase exacta y clave admin.")
    tab1,tab2=st.tabs(["Reversar/borrar pieza","Reset seguro"])
    with tab1:
        df=st.session_state.inventario
        if df.empty: st.info("No hay inventario."); return
        idx=st.selectbox("Pieza", df.index, format_func=lambda i:f"{df.at[i,'numero']} · {df.at[i,'codigo_interno']} · {df.at[i,'estado']}")
        show_piece(idx)
        action=st.selectbox("Acción admin",["marcar disponible y limpiar venta","borrar pieza"])
        confirm=st.text_input("Escribe el número de la pieza para confirmar")
        pwd=st.text_input("Clave admin",type="password")
        if st.button("Ejecutar acción", type="primary"):
            if confirm != df.at[idx,"numero"] or pwd != USERS["jc"]["password"]:
                st.error("Confirmación o clave incorrecta.")
            else:
                numero=df.at[idx,"numero"]; ci=df.at[idx,"codigo_interno"]
                if action.startswith("marcar"):
                    for c,v in {"estado":"disponible","cliente":"","descuento_pct":0,"descuento":0,"pagado":0,"ubicacion":"tienda"}.items(): df.at[idx,c]=v
                    st.session_state.inventario=ensure_inventory(df); save_table("inventario"); log_event("admin_reversar", numero=numero, codigo_interno=ci)
                else:
                    st.session_state.inventario=ensure_inventory(df.drop(index=idx).reset_index(drop=True)); save_table("inventario"); log_event("admin_borrar_pieza", numero=numero, codigo_interno=ci)
                st.success("Hecho."); st.rerun()
    with tab2:
        reportes_page()
        st.warning("Para resetear TODO escribe BORRAR TODO y clave admin.")
        phrase=st.text_input("Frase exacta")
        pwd=st.text_input("Clave admin de nuevo",type="password",key="reset_pwd")
        chk=st.checkbox("Entiendo que esta acción es irreversible")
        if st.button("BORRAR TODO", type="primary"):
            if phrase=="BORRAR TODO" and pwd==USERS["jc"]["password"] and chk:
                st.session_state.inventario=pd.DataFrame(columns=INVENTORY_COLUMNS); st.session_state.clientes=pd.DataFrame(columns=CLIENT_COLUMNS); st.session_state.movimientos=pd.DataFrame(columns=MOVEMENT_COLUMNS); st.session_state.ventas=pd.DataFrame(columns=VENTAS_COLUMNS); st.session_state.notas=pd.DataFrame(columns=NOTAS_COLUMNS)
                for name in ALL_TABLES: save_table(name)
                st.success("Todo borrado."); st.rerun()
            else:
                st.error("No se ejecutó: falta confirmación, clave o checkbox.")

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
    elif page=="scan": scan_page()
    elif page=="buscar": buscar_page()
    elif page=="disponibles": disponibles_page()
    elif page=="recepcion": recepcion_page()
    elif page=="qr": qr_page()
    elif page=="inventario": inventario_page()
    elif page=="ventas": ventas_page()
    elif page=="clientes": clientes_page()
    elif page=="catalogo": catalogo_page()
    elif page=="reportes": reportes_page()
    elif page=="admin": admin_page()
    else: home_page()

if __name__ == "__main__":
    main()
