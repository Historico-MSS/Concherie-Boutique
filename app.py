from __future__ import annotations

import base64
import io
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import qrcode
import streamlit as st
from PIL import Image

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

try:
    from streamlit_gsheets import GSheetsConnection
except Exception:  # pragma: no cover
    GSheetsConnection = None

APP_TITLE = "Tienda Concha"
LOCAL_DATA_DIR = "data"

USERS = {
    "jc": {"password": "master", "role": "admin", "name": "JC"},
    "moira": {"password": "ventas", "role": "encargada", "name": "Moira"},
    "info": {"password": "precios", "role": "consulta", "name": "Consulta"},
}

ROLES = {
    "admin": {
        "ver": True, "editar": True, "vender": True, "clientes": True,
        "fotos": True, "carga_masiva": True, "borrar": True, "reportes": True,
    },
    "encargada": {
        "ver": True, "editar": True, "vender": True, "clientes": True,
        "fotos": True, "carga_masiva": False, "borrar": False, "reportes": True,
    },
    "consulta": {
        "ver": True, "editar": False, "vender": False, "clientes": False,
        "fotos": False, "carga_masiva": False, "borrar": False, "reportes": False,
    },
}

ESTADOS = ["disponible", "reservado", "probando en casa", "vendido", "mantenimiento"]
UBICACIONES = ["tienda", "casa cliente", "reservado en tienda", "fuera de tienda", "otro"]

INVENTARIO_COLUMNS = [
    "marca", "codigo_base", "codigo_unico", "qr_texto", "producto", "talla", "precio",
    "estado", "ubicacion", "cliente_id", "cliente_nombre", "fecha_estado",
    "monto_pagado", "saldo", "notas", "foto_principal", "fecha_creacion", "fecha_actualizacion",
]
CLIENTES_COLUMNS = ["cliente_id", "nombre", "telefono", "email", "notas", "fecha_creacion", "fecha_actualizacion"]
MOVIMIENTOS_COLUMNS = ["movimiento_id", "fecha", "usuario", "tipo", "codigo_unico", "detalle", "cliente_id", "monto", "precio", "saldo"]
FOTOS_COLUMNS = ["foto_id", "fecha", "usuario", "codigo_unico", "nombre_archivo", "foto_data_url"]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def can(permission: str) -> bool:
    role = st.session_state.get("role", "consulta")
    return bool(ROLES.get(role, {}).get(permission, False))


def normalize_col(name: str) -> str:
    return (
        str(name).strip().lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
        .replace(" ", "_")
    )


def empty_df(sheet: str) -> pd.DataFrame:
    mapping = {
        "inventario": INVENTARIO_COLUMNS,
        "clientes": CLIENTES_COLUMNS,
        "movimientos": MOVIMIENTOS_COLUMNS,
        "fotos": FOTOS_COLUMNS,
    }
    return pd.DataFrame(columns=mapping[sheet])


def storage_mode() -> str:
    # Por defecto trabajamos en modo local para evitar errores de escritura en Google Sheets
    # hasta que las credenciales queden configuradas.
    try:
        return str(st.secrets.get("storage_mode", "local")).lower().strip()
    except Exception:
        return "local"


@st.cache_resource(show_spinner=False)
def get_connection():
    if storage_mode() != "gsheets":
        return None
    if GSheetsConnection is None:
        return None
    try:
        return st.connection("gsheets", type=GSheetsConnection)
    except Exception:
        return None


def local_path(sheet: str) -> str:
    return f"{LOCAL_DATA_DIR}/{sheet}.csv"


def read_sheet(sheet: str) -> pd.DataFrame:
    conn = get_connection()
    if conn is not None:
        try:
            df = conn.read(worksheet=sheet, ttl=0)
            if df is None or df.empty:
                return empty_df(sheet)
            # Remove fully empty rows/cols that Google Sheets may return
            df = df.dropna(how="all").copy()
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception:
            pass
    try:
        return pd.read_csv(local_path(sheet))
    except Exception:
        return empty_df(sheet)


def write_sheet(sheet: str, df: pd.DataFrame) -> None:
    df = df.copy()
    conn = get_connection()
    if conn is not None:
        try:
            conn.update(worksheet=sheet, data=df)
            return
        except Exception as e:
            # Si Google Sheets no permite escritura todavía, guardamos en respaldo local
            # para que la app siga funcionando mientras configuramos credenciales.
            st.warning(f"No pude escribir en Google Sheets. Guardé en respaldo local. Detalle: {type(e).__name__}")
    import os
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    df.to_csv(local_path(sheet), index=False)


def ensure_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    df = df.copy()
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    df = df[columns]
    # Pandas/Streamlit Cloud puede inferir columnas vacías como numéricas.
    # Para poder guardar fotos en base64/data-url y textos largos, forzamos
    # las columnas no numéricas a dtype object/string antes de asignar valores.
    numeric_cols = {"precio", "monto_pagado", "saldo", "monto"}
    for col in df.columns:
        if col not in numeric_cols:
            df[col] = df[col].fillna("").astype("object")
    return df


def load_all() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    inventario = ensure_columns(read_sheet("inventario"), INVENTARIO_COLUMNS)
    clientes = ensure_columns(read_sheet("clientes"), CLIENTES_COLUMNS)
    movimientos = ensure_columns(read_sheet("movimientos"), MOVIMIENTOS_COLUMNS)
    fotos = ensure_columns(read_sheet("fotos"), FOTOS_COLUMNS)

    for col in ["precio", "monto_pagado", "saldo"]:
        if col in inventario.columns:
            inventario[col] = pd.to_numeric(inventario[col], errors="coerce").fillna(0)
    if "monto" in movimientos.columns:
        movimientos["monto"] = pd.to_numeric(movimientos["monto"], errors="coerce").fillna(0)
    return inventario, clientes, movimientos, fotos


def append_movimiento(tipo: str, codigo_unico: str = "", detalle: str = "", cliente_id: str = "", monto: float = 0, precio: float = 0, saldo: float = 0) -> None:
    movimientos = ensure_columns(read_sheet("movimientos"), MOVIMIENTOS_COLUMNS)
    row = {
        "movimiento_id": str(uuid.uuid4()),
        "fecha": now_str(),
        "usuario": st.session_state.get("username", ""),
        "tipo": tipo,
        "codigo_unico": codigo_unico,
        "detalle": detalle,
        "cliente_id": cliente_id,
        "monto": monto,
        "precio": precio,
        "saldo": saldo,
    }
    movimientos = pd.concat([movimientos, pd.DataFrame([row])], ignore_index=True)
    write_sheet("movimientos", movimientos)


def make_qr_text(marca: str, codigo_unico: str) -> str:
    marca = str(marca).strip().upper()
    codigo_unico = str(codigo_unico).strip().upper()
    return f"{marca}|{codigo_unico}" if marca else codigo_unico


def excel_to_piece_inventory(uploaded_file) -> pd.DataFrame:
    raw = pd.read_excel(uploaded_file)
    raw = raw.dropna(how="all").copy()
    raw.columns = [normalize_col(c) for c in raw.columns]

    # Synonyms from different Excel versions
    col_marca = next((c for c in raw.columns if c in ["marca", "maison", "brand"]), None)
    col_codigo = next((c for c in raw.columns if c in ["codigo", "codigo_base", "codigo_concha", "code"]), None)
    col_producto = next((c for c in raw.columns if c in ["producto", "descripcion", "pieza", "item"]), None)
    col_cantidad = next((c for c in raw.columns if c in ["cantidad", "qty", "unidades"]), None)
    col_talla = next((c for c in raw.columns if c in ["talla", "size"]), None)
    col_precio = next((c for c in raw.columns if c in ["precio_unitario", "precio", "price"]), None)

    if not col_codigo or not col_producto or not col_cantidad or not col_precio:
        st.error("No pude identificar columnas mínimas: código, producto, cantidad y precio.")
        st.stop()

    rows = []
    counters: Dict[str, int] = {}
    for _, r in raw.iterrows():
        codigo = str(r.get(col_codigo, "")).strip().upper()
        if not codigo or codigo.lower() == "nan":
            continue
        producto = str(r.get(col_producto, "")).strip()
        marca = str(r.get(col_marca, "")).strip().upper() if col_marca else ""
        talla = str(r.get(col_talla, "")).strip().upper() if col_talla else ""
        if talla.lower() == "nan":
            talla = ""
        try:
            cantidad = int(float(r.get(col_cantidad, 0)))
        except Exception:
            cantidad = 0
        precio = pd.to_numeric(r.get(col_precio, 0), errors="coerce")
        precio = 0 if pd.isna(precio) else float(precio)

        for _i in range(max(cantidad, 0)):
            counters[codigo] = counters.get(codigo, 0) + 1
            codigo_unico = f"{codigo}-{counters[codigo]:02d}"
            rows.append({
                "marca": marca,
                "codigo_base": codigo,
                "codigo_unico": codigo_unico,
                "qr_texto": make_qr_text(marca, codigo_unico),
                "producto": producto,
                "talla": talla,
                "precio": precio,
                "estado": "disponible",
                "ubicacion": "tienda",
                "cliente_id": "",
                "cliente_nombre": "",
                "fecha_estado": now_str(),
                "monto_pagado": 0,
                "saldo": precio,
                "notas": "",
                "foto_principal": "",
                "fecha_creacion": now_str(),
                "fecha_actualizacion": now_str(),
            })
    return ensure_columns(pd.DataFrame(rows), INVENTARIO_COLUMNS)


def qr_image_bytes(text: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(version=None, box_size=box_size, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def create_qr_pdf(inventario: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    label_w = 2.6 * cm       # includes QR + small margins
    label_h = 2.9 * cm       # QR 2 cm + text below
    qr_size = 2.0 * cm
    margin_x = 0.9 * cm
    margin_y = 0.9 * cm
    cols = int((page_w - 2 * margin_x) // label_w)
    rows = int((page_h - 2 * margin_y) // label_h)
    per_page = cols * rows

    data = inventario.copy()
    data = data[data["codigo_unico"].astype(str).str.strip() != ""]
    data = data.sort_values(["marca", "codigo_base", "codigo_unico"])

    for idx, (_, row) in enumerate(data.iterrows()):
        pos = idx % per_page
        if idx > 0 and pos == 0:
            c.showPage()
        col = pos % cols
        rownum = pos // cols
        x = margin_x + col * label_w + (label_w - qr_size) / 2
        y = page_h - margin_y - (rownum + 1) * label_h + 0.55 * cm

        png = qr_image_bytes(str(row.get("qr_texto", "")), box_size=8)
        img = Image.open(io.BytesIO(png))
        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        c.drawInlineImage(img, x, y + 0.35 * cm, width=qr_size, height=qr_size)

        code = str(row.get("codigo_unico", ""))
        brand = str(row.get("marca", ""))
        c.setFont("Helvetica-Bold", 5.8)
        c.drawCentredString(x + qr_size / 2, y + 0.18 * cm, code[:22])
        if brand:
            c.setFont("Helvetica", 4.8)
            c.drawCentredString(x + qr_size / 2, y + 0.02 * cm, brand[:22])
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def image_to_data_url(file, max_size: int = 900, quality: int = 65) -> str:
    img = Image.open(file).convert("RGB")
    img.thumbnail((max_size, max_size))
    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(bio.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def show_photo(data_url: str, caption: str = "") -> None:
    if isinstance(data_url, str) and data_url.startswith("data:image"):
        header, encoded = data_url.split(",", 1)
        st.image(base64.b64decode(encoded), caption=caption, use_container_width=True)


def login_screen() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🛍️", layout="wide")
    st.title("🛍️ Tienda Concha")
    st.subheader("Acceso")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Clave", type="password")
        submit = st.form_submit_button("Entrar")
    if submit:
        username_key = username.strip().lower()
        user = USERS.get(username_key)
        if user and user["password"] == password:
            st.session_state["logged_in"] = True
            st.session_state["username"] = user.get("name", username_key)
            st.session_state["role"] = user["role"]
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos")


def sidebar_user() -> None:
    st.sidebar.title("Tienda Concha")
    st.sidebar.write(f"Usuario: **{st.session_state.get('username', '')}**")
    st.sidebar.write(f"Rol: **{st.session_state.get('role', '')}**")
    if st.sidebar.button("Cerrar sesión"):
        st.session_state.clear()
        st.rerun()


def dashboard(inventario: pd.DataFrame) -> None:
    st.header("Resumen")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Piezas", len(inventario))
    c2.metric("Disponibles", int((inventario["estado"] == "disponible").sum()))
    c3.metric("Reservadas", int((inventario["estado"] == "reservado").sum()))
    c4.metric("Vendidas", int((inventario["estado"] == "vendido").sum()))

    st.subheader("Inventario")
    search = st.text_input("Buscar por código, producto, marca o cliente")
    df = inventario.copy()
    if search:
        s = search.lower()
        mask = df.astype(str).apply(lambda col: col.str.lower().str.contains(s, na=False)).any(axis=1)
        df = df[mask]
    st.dataframe(
        df[["marca", "codigo_unico", "producto", "talla", "precio", "estado", "ubicacion", "cliente_nombre"]],
        use_container_width=True,
        hide_index=True,
    )


def carga_inicial_tab() -> None:
    st.header("Carga inicial / actualización masiva")
    st.info("Carga el Excel con columnas de marca/maison, código, producto, cantidad y precio. La app crea una fila por cada pieza.")
    uploaded = st.file_uploader("Subir Excel", type=["xlsx", "xls"])
    if uploaded:
        new_inventory = excel_to_piece_inventory(uploaded)
        st.success(f"Se generaron {len(new_inventory)} piezas individuales.")
        st.dataframe(new_inventory.head(30), use_container_width=True, hide_index=True)
        mode = st.radio("¿Cómo guardar?", ["Reemplazar inventario completo", "Agregar al inventario existente"])
        if st.button("Guardar inventario", type="primary"):
            if mode.startswith("Agregar"):
                current = ensure_columns(read_sheet("inventario"), INVENTARIO_COLUMNS)
                combined = pd.concat([current, new_inventory], ignore_index=True)
                combined = combined.drop_duplicates(subset=["codigo_unico"], keep="last")
                write_sheet("inventario", combined)
                append_movimiento("carga masiva", detalle=f"Agregadas {len(new_inventory)} piezas desde Excel")
            else:
                write_sheet("inventario", new_inventory)
                append_movimiento("carga inicial", detalle=f"Inventario reemplazado con {len(new_inventory)} piezas")
            st.success("Inventario guardado.")
            st.rerun()


def inventario_tab(inventario: pd.DataFrame, clientes: pd.DataFrame) -> None:
    st.header("Inventario por pieza")
    if inventario.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    codigo = st.selectbox("Seleccionar pieza", inventario["codigo_unico"].astype(str).tolist())
    idx = inventario.index[inventario["codigo_unico"].astype(str) == str(codigo)][0]
    row = inventario.loc[idx]

    col1, col2 = st.columns([1, 1])
    with col1:
        st.write(f"**Marca:** {row['marca']}")
        st.write(f"**Producto:** {row['producto']}")
        talla_actual = str(row.get("talla", "")).strip()
        st.write(f"**Talla:** {talla_actual if talla_actual else '— pendiente —'}")
        st.write(f"**Precio:** ${float(row['precio']):,.2f}")
        st.write(f"**QR:** `{row['qr_texto']}`")
        st.image(qr_image_bytes(str(row["qr_texto"])), caption=str(row["codigo_unico"]), width=180)
    with col2:
        foto = str(row.get("foto_principal", ""))
        if foto:
            show_photo(foto, "Foto principal")
        else:
            st.caption("Sin foto principal todavía.")

    if not can("editar"):
        return

    st.subheader("Editar pieza")
    with st.form("edit_piece"):
        marca = st.text_input("Marca", value=str(row["marca"]))
        producto = st.text_input("Producto", value=str(row["producto"]))
        talla = st.text_input("Talla (opcional)", value=str(row.get("talla", "")))
        precio = st.number_input("Precio", min_value=0.0, step=10.0, value=float(row["precio"]))
        estado = st.selectbox("Estado", ESTADOS, index=ESTADOS.index(row["estado"]) if row["estado"] in ESTADOS else 0)
        ubicacion = st.selectbox("Ubicación", UBICACIONES, index=UBICACIONES.index(row["ubicacion"]) if row["ubicacion"] in UBICACIONES else 0)
        cliente_nombre = st.text_input("Cliente asociado", value=str(row.get("cliente_nombre", "")))
        monto_pagado = st.number_input("Monto pagado", min_value=0.0, step=10.0, value=float(row.get("monto_pagado", 0) or 0))
        notas = st.text_area("Notas", value=str(row.get("notas", "")))
        submitted = st.form_submit_button("Guardar cambios")

    if submitted:
        inventario.loc[idx, "marca"] = marca.strip().upper()
        inventario.loc[idx, "producto"] = producto.strip()
        inventario.loc[idx, "talla"] = talla.strip().upper()
        inventario.loc[idx, "precio"] = precio
        inventario.loc[idx, "estado"] = estado
        inventario.loc[idx, "ubicacion"] = ubicacion
        inventario.loc[idx, "cliente_nombre"] = cliente_nombre.strip()
        inventario.loc[idx, "monto_pagado"] = monto_pagado
        inventario.loc[idx, "saldo"] = max(precio - monto_pagado, 0)
        inventario.loc[idx, "notas"] = notas
        inventario.loc[idx, "qr_texto"] = make_qr_text(marca, codigo)
        inventario.loc[idx, "fecha_estado"] = now_str()
        inventario.loc[idx, "fecha_actualizacion"] = now_str()
        write_sheet("inventario", inventario)
        append_movimiento("edición pieza", codigo_unico=codigo, detalle=f"Estado: {estado}; ubicación: {ubicacion}; talla: {talla}", precio=precio, saldo=max(precio-monto_pagado, 0))
        st.success("Pieza actualizada.")
        st.rerun()


def clientes_tab(clientes: pd.DataFrame) -> None:
    st.header("Clientes")
    if not can("clientes"):
        st.warning("Este usuario no puede administrar clientes.")
        return

    with st.form("new_client"):
        st.subheader("Agregar cliente")
        nombre = st.text_input("Nombre")
        telefono = st.text_input("Teléfono")
        email = st.text_input("Email")
        notas = st.text_area("Notas")
        submit = st.form_submit_button("Guardar cliente")
    if submit and nombre.strip():
        row = {
            "cliente_id": str(uuid.uuid4())[:8],
            "nombre": nombre.strip(),
            "telefono": telefono.strip(),
            "email": email.strip(),
            "notas": notas.strip(),
            "fecha_creacion": now_str(),
            "fecha_actualizacion": now_str(),
        }
        clientes = pd.concat([clientes, pd.DataFrame([row])], ignore_index=True)
        write_sheet("clientes", clientes)
        append_movimiento("cliente", detalle=f"Cliente agregado: {nombre}", cliente_id=row["cliente_id"])
        st.success("Cliente guardado.")
        st.rerun()

    st.subheader("Listado")
    st.dataframe(clientes, use_container_width=True, hide_index=True)


def ventas_tab(inventario: pd.DataFrame, clientes: pd.DataFrame) -> None:
    st.header("Ventas / Reservas / Probándose")
    if not can("vender"):
        st.warning("Este usuario no puede registrar ventas o reservas.")
        return
    if inventario.empty:
        st.warning("Todavía no hay inventario.")
        return

    opciones = inventario[inventario["estado"].isin(["disponible", "reservado", "probando en casa"])]
    codigo = st.selectbox("Pieza", opciones["codigo_unico"].astype(str).tolist())
    idx = inventario.index[inventario["codigo_unico"].astype(str) == codigo][0]
    row = inventario.loc[idx]
    talla_txt = str(row.get("talla", "")).strip()
    talla_part = f" — Talla {talla_txt}" if talla_txt else ""
    st.write(f"**{row['producto']}** — {row['marca']}{talla_part} — ${float(row['precio']):,.2f}")

    cliente_names = [""] + clientes["nombre"].dropna().astype(str).tolist()
    with st.form("sale_form"):
        accion = st.selectbox("Acción", ["vender", "reservar", "probando en casa", "volver a disponible"])
        cliente_nombre = st.selectbox("Cliente existente", cliente_names)
        cliente_manual = st.text_input("O escribir cliente manual")
        monto_pagado = st.number_input("Monto pagado", min_value=0.0, step=10.0, value=float(row.get("monto_pagado", 0) or 0))
        notas = st.text_area("Notas")
        submit = st.form_submit_button("Registrar")

    if submit:
        cliente_final = cliente_manual.strip() or cliente_nombre.strip()
        precio = float(row["precio"])
        saldo = max(precio - monto_pagado, 0)
        if accion == "vender":
            estado = "vendido"
            ubicacion = "fuera de tienda"
        elif accion == "reservar":
            estado = "reservado"
            ubicacion = "reservado en tienda"
        elif accion == "probando en casa":
            estado = "probando en casa"
            ubicacion = "casa cliente"
        else:
            estado = "disponible"
            ubicacion = "tienda"
            cliente_final = ""
            monto_pagado = 0
            saldo = precio

        inventario.loc[idx, "estado"] = estado
        inventario.loc[idx, "ubicacion"] = ubicacion
        inventario.loc[idx, "cliente_nombre"] = cliente_final
        inventario.loc[idx, "monto_pagado"] = monto_pagado
        inventario.loc[idx, "saldo"] = saldo
        inventario.loc[idx, "notas"] = notas
        inventario.loc[idx, "fecha_estado"] = now_str()
        inventario.loc[idx, "fecha_actualizacion"] = now_str()
        write_sheet("inventario", inventario)
        append_movimiento(accion, codigo_unico=codigo, detalle=f"Cliente: {cliente_final}. {notas}", monto=monto_pagado, precio=precio, saldo=saldo)
        st.success("Movimiento registrado.")
        st.rerun()


def fotos_tab(inventario: pd.DataFrame, fotos: pd.DataFrame) -> None:
    st.header("Fotos de piezas")
    if not can("fotos"):
        st.warning("Este usuario no puede subir fotos.")
        return
    if inventario.empty:
        st.warning("Todavía no hay inventario.")
        return

    codigo = st.selectbox("Pieza para foto", inventario["codigo_unico"].astype(str).tolist(), key="foto_piece")
    uploaded = st.file_uploader("Tomar o subir foto", type=["jpg", "jpeg", "png"])
    if uploaded:
        data_url = image_to_data_url(uploaded)
        show_photo(data_url, "Vista previa")
        if st.button("Guardar foto"):
            foto_id = str(uuid.uuid4())[:8]
            row = {
                "foto_id": foto_id,
                "fecha": now_str(),
                "usuario": st.session_state.get("username", ""),
                "codigo_unico": codigo,
                "nombre_archivo": uploaded.name,
                "foto_data_url": data_url,
            }
            fotos = pd.concat([fotos, pd.DataFrame([row])], ignore_index=True)
            write_sheet("fotos", fotos)

            inv = ensure_columns(read_sheet("inventario"), INVENTARIO_COLUMNS)
            match = inv.index[inv["codigo_unico"].astype(str) == codigo]
            if len(match):
                row_idx = match[0]
                inv["foto_principal"] = inv["foto_principal"].astype("object")
                inv.at[row_idx, "foto_principal"] = data_url
                inv.at[row_idx, "fecha_actualizacion"] = now_str()
                write_sheet("inventario", inv)
            append_movimiento("foto", codigo_unico=codigo, detalle=f"Foto guardada: {uploaded.name}")
            st.success("Foto guardada.")
            st.rerun()

    st.subheader("Fotos guardadas de esta pieza")
    pieza_fotos = fotos[fotos["codigo_unico"].astype(str) == codigo]
    if pieza_fotos.empty:
        st.caption("Sin fotos todavía.")
    else:
        cols = st.columns(3)
        for i, (_, f) in enumerate(pieza_fotos.tail(6).iterrows()):
            with cols[i % 3]:
                show_photo(str(f["foto_data_url"]), str(f["fecha"]))


def decode_qr_from_image(uploaded_file) -> str:
    """Read QR text from a camera/uploaded image using OpenCV. Returns empty string if not found."""
    if uploaded_file is None or cv2 is None or np is None:
        return ""
    try:
        file_bytes = np.asarray(bytearray(uploaded_file.getvalue()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if img is None:
            return ""
        detector = cv2.QRCodeDetector()
        data, _points, _ = detector.detectAndDecode(img)
        return str(data).strip() if data else ""
    except Exception:
        return ""


def normalize_scanned_code(text: str) -> tuple[str, str]:
    """Return (brand, code). Accepts MARCA|CODIGO, plain CODIGO, and URLs with ?pieza=."""
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    if "pieza=" in raw:
        part = raw.split("pieza=", 1)[1]
        part = part.split("&", 1)[0].strip()
        return "", part.upper()
    if "|" in raw:
        brand, code = raw.split("|", 1)
        return brand.strip().upper(), code.strip().upper()
    return "", raw.strip().upper()


def pieza_card(row: pd.Series, show_internal: bool = False) -> None:
    """Compact product card for QR/search results."""
    c1, c2 = st.columns([1, 1.25])
    with c1:
        foto = str(row.get("foto_principal", ""))
        if foto:
            show_photo(foto, "Foto principal")
        else:
            st.caption("Sin foto cargada todavía.")
        st.image(qr_image_bytes(str(row.get("qr_texto", ""))), caption=str(row.get("codigo_unico", "")), width=150)
    with c2:
        st.subheader(str(row.get("producto", "")))
        st.write(f"**Marca:** {row.get('marca', '')}")
        st.write(f"**Código pieza:** `{row.get('codigo_unico', '')}`")
        talla = str(row.get("talla", "")).strip()
        st.write(f"**Talla:** {talla if talla else '— pendiente —'}")
        st.write(f"**Precio:** ${float(row.get('precio', 0) or 0):,.2f}")
        st.write(f"**Estado:** {row.get('estado', '')}")
        st.write(f"**Ubicación:** {row.get('ubicacion', '')}")
        cliente = str(row.get("cliente_nombre", "")).strip()
        if cliente and show_internal:
            st.write(f"**Cliente:** {cliente}")
        saldo = float(row.get("saldo", 0) or 0)
        pagado = float(row.get("monto_pagado", 0) or 0)
        if show_internal and (pagado or saldo):
            st.write(f"**Pagado:** ${pagado:,.2f}")
            st.write(f"**Saldo:** ${saldo:,.2f}")
        notas = str(row.get("notas", "")).strip()
        if notas and show_internal:
            st.caption(f"Notas: {notas}")


def consulta_qr_tab(inventario: pd.DataFrame) -> None:
    st.header("Leer QR / consultar precio")
    if inventario.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    st.caption("Escanea o sube una foto del QR. También puedes escribir el código manualmente si la cámara no lo lee.")

    scanned_text = ""
    tab1, tab2 = st.tabs(["Cámara / foto", "Código manual"])
    with tab1:
        camera_file = st.camera_input("Tomar foto del QR")
        uploaded_file = st.file_uploader("O subir imagen del QR", type=["jpg", "jpeg", "png"], key="qr_upload")
        qr_source = camera_file or uploaded_file
        if qr_source:
            scanned_text = decode_qr_from_image(qr_source)
            if scanned_text:
                st.success(f"QR leído: {scanned_text}")
            else:
                st.warning("No pude leer el QR en esa imagen. Prueba acercarlo, mejorar la luz o escribir el código manualmente.")
    with tab2:
        manual = st.text_input("Código o texto del QR", placeholder="Ej: MARCA|ISH01-01 o ISH01-01")
        if manual:
            scanned_text = manual

    brand, code = normalize_scanned_code(scanned_text)
    if not code:
        st.info("Cuando escanees o escribas un código, aquí aparecerá la pieza.")
        return

    df = inventario.copy()
    mask = df["codigo_unico"].astype(str).str.upper().str.strip() == code
    if brand:
        mask = mask & (df["marca"].astype(str).str.upper().str.strip() == brand)
    matches = df[mask]

    if matches.empty:
        # fallback: maybe QR is model code
        model_mask = df["codigo_base"].astype(str).str.upper().str.strip() == code
        if brand:
            model_mask = model_mask & (df["marca"].astype(str).str.upper().str.strip() == brand)
        model_matches = df[model_mask]
        if not model_matches.empty:
            st.subheader(f"Modelo {code}")
            disponibles = int((model_matches["estado"].astype(str) == "disponible").sum())
            precio = pd.to_numeric(model_matches["precio"], errors="coerce").dropna()
            precio_txt = f"${float(precio.mode().iloc[0] if not precio.mode().empty else precio.iloc[0]):,.2f}" if not precio.empty else "—"
            st.write(f"**Disponibles:** {disponibles}")
            st.write(f"**Precio:** {precio_txt}")
            st.dataframe(
                model_matches[["marca", "codigo_unico", "producto", "talla", "precio", "estado", "ubicacion"]],
                use_container_width=True,
                hide_index=True,
            )
            return
        st.error(f"No encontré una pieza o modelo con el código `{code}`.")
        return

    row = matches.iloc[0]
    pieza_card(row, show_internal=can("vender"))

    if can("vender"):
        st.divider()
        st.subheader("Acción rápida")
        idx = inventario.index[inventario["codigo_unico"].astype(str).str.upper().str.strip() == str(row["codigo_unico"]).upper().strip()][0]
        with st.form("quick_qr_action"):
            accion = st.selectbox("Acción", ["sin cambio", "reservar", "probando en casa", "vender", "volver a disponible"])
            cliente_nombre = st.text_input("Cliente", value=str(row.get("cliente_nombre", "")))
            monto_pagado = st.number_input("Monto pagado", min_value=0.0, step=10.0, value=float(row.get("monto_pagado", 0) or 0))
            submit = st.form_submit_button("Guardar acción")
        if submit and accion != "sin cambio":
            precio = float(row.get("precio", 0) or 0)
            saldo = max(precio - monto_pagado, 0)
            if accion == "vender":
                estado, ubicacion = "vendido", "fuera de tienda"
            elif accion == "reservar":
                estado, ubicacion = "reservado", "reservado en tienda"
            elif accion == "probando en casa":
                estado, ubicacion = "probando en casa", "casa cliente"
            else:
                estado, ubicacion = "disponible", "tienda"
                cliente_nombre, monto_pagado, saldo = "", 0, precio
            inventario.loc[idx, "estado"] = estado
            inventario.loc[idx, "ubicacion"] = ubicacion
            inventario.loc[idx, "cliente_nombre"] = cliente_nombre.strip()
            inventario.loc[idx, "monto_pagado"] = monto_pagado
            inventario.loc[idx, "saldo"] = saldo
            inventario.loc[idx, "fecha_estado"] = now_str()
            inventario.loc[idx, "fecha_actualizacion"] = now_str()
            write_sheet("inventario", inventario)
            append_movimiento(accion, codigo_unico=str(row["codigo_unico"]), detalle=f"QR - Cliente: {cliente_nombre}", monto=monto_pagado, precio=precio, saldo=saldo)
            st.success("Acción guardada.")
            st.rerun()


def qr_tab(inventario: pd.DataFrame) -> None:
    st.header("QR para imprimir")
    if inventario.empty:
        st.warning("Todavía no hay inventario.")
        return
    st.write("El PDF genera etiquetas con QR de 2 cm x 2 cm y el código de pieza debajo.")
    estado_filter = st.multiselect("Estados a incluir", ESTADOS, default=["disponible", "reservado", "probando en casa"])
    df = inventario[inventario["estado"].isin(estado_filter)].copy() if estado_filter else inventario.copy()
    st.write(f"Etiquetas a generar: **{len(df)}**")
    if st.button("Generar PDF de QR"):
        pdf = create_qr_pdf(df)
        st.download_button(
            "Descargar PDF de etiquetas",
            data=pdf,
            file_name=f"qr_tienda_concha_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
        )



def data_url_to_image_reader(data_url: str):
    try:
        if isinstance(data_url, str) and data_url.startswith("data:image"):
            encoded = data_url.split(",", 1)[1]
            return ImageReader(io.BytesIO(base64.b64decode(encoded)))
    except Exception:
        return None
    return None


def create_catalogo_modelos_pdf(inventario: pd.DataFrame) -> bytes:
    """Catálogo agrupado por modelo: foto, marca, código, producto, precio, disponibles, tallas y QR de modelo."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4
    margin = 1.1 * cm
    card_w = (page_w - 2 * margin - 0.8 * cm) / 2
    card_h = 8.0 * cm
    gap_x = 0.8 * cm
    gap_y = 0.7 * cm

    df = inventario.copy()
    if df.empty:
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(page_w/2, page_h/2, "Catálogo sin inventario")
        c.save(); buffer.seek(0); return buffer.getvalue()

    df["precio"] = pd.to_numeric(df["precio"], errors="coerce").fillna(0)
    grouped = []
    for (marca, codigo_base, producto), g in df.groupby(["marca", "codigo_base", "producto"], dropna=False):
        disponibles = int((g["estado"].astype(str) == "disponible").sum())
        if disponibles <= 0:
            continue
        fotos = g["foto_principal"].dropna().astype(str)
        foto = next((x for x in fotos if x.startswith("data:image")), "")
        precio = g["precio"].mode().iloc[0] if not g["precio"].mode().empty else g["precio"].iloc[0]
        tallas = g[g["estado"].astype(str) == "disponible"]["talla"].fillna("").astype(str).str.strip()
        tallas = tallas[tallas != ""]
        if not tallas.empty:
            tallas_texto = ", ".join([f"{t}: {n}" for t, n in tallas.value_counts().sort_index().items()])
        else:
            tallas_texto = "Tallas: pendiente"
        grouped.append({
            "marca": str(marca), "codigo_base": str(codigo_base), "producto": str(producto),
            "precio": float(precio), "disponibles": disponibles, "foto": foto, "tallas": tallas_texto,
            "qr_texto": make_qr_text(str(marca), str(codigo_base)),
        })

    grouped = sorted(grouped, key=lambda x: (x["marca"], x["codigo_base"]))

    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, page_h - margin, "Catálogo de Inventario")
    c.setFont("Helvetica", 9)
    c.drawRightString(page_w - margin, page_h - margin + 0.1*cm, datetime.now().strftime("%Y-%m-%d %H:%M"))

    start_y = page_h - margin - 1.0 * cm
    for i, item in enumerate(grouped):
        pos = i % 6
        if i > 0 and pos == 0:
            c.showPage()
            c.setFont("Helvetica-Bold", 18)
            c.drawString(margin, page_h - margin, "Catálogo de Inventario")
            c.setFont("Helvetica", 9)
            c.drawRightString(page_w - margin, page_h - margin + 0.1*cm, datetime.now().strftime("%Y-%m-%d %H:%M"))
        col = pos % 2
        row = pos // 2
        x = margin + col * (card_w + gap_x)
        y = start_y - row * (card_h + gap_y) - card_h

        c.roundRect(x, y, card_w, card_h, 8, stroke=1, fill=0)
        # Foto
        img_reader = data_url_to_image_reader(item["foto"])
        photo_x, photo_y = x + 0.35*cm, y + 2.7*cm
        photo_w, photo_h = card_w - 0.7*cm, 4.6*cm
        if img_reader:
            try:
                c.drawImage(img_reader, photo_x, photo_y, width=photo_w, height=photo_h, preserveAspectRatio=True, anchor="c")
            except Exception:
                c.setFont("Helvetica", 8); c.drawCentredString(x+card_w/2, photo_y+photo_h/2, "Foto no disponible")
        else:
            c.setFont("Helvetica", 8); c.drawCentredString(x+card_w/2, photo_y+photo_h/2, "Sin foto")

        # QR modelo
        qr_png = qr_image_bytes(item["qr_texto"], box_size=5)
        qr_img = Image.open(io.BytesIO(qr_png))
        qr_size = 1.45 * cm
        c.drawInlineImage(qr_img, x + card_w - qr_size - 0.25*cm, y + 0.3*cm, width=qr_size, height=qr_size)

        tx = x + 0.35*cm
        c.setFont("Helvetica-Bold", 9)
        c.drawString(tx, y + 2.25*cm, item["codigo_base"][:18])
        c.setFont("Helvetica-Bold", 8)
        c.drawString(tx, y + 1.9*cm, item["producto"][:28])
        c.setFont("Helvetica", 7)
        marca = item["marca"][:28]
        if marca:
            c.drawString(tx, y + 1.55*cm, marca)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(tx, y + 1.18*cm, f"${item['precio']:,.2f}")
        c.setFont("Helvetica", 7)
        c.drawString(tx, y + 0.82*cm, f"Disponibles: {item['disponibles']}")
        c.drawString(tx, y + 0.48*cm, item["tallas"][:42])

    c.save()
    buffer.seek(0)
    return buffer.getvalue()

def reportes_tab(inventario: pd.DataFrame, clientes: pd.DataFrame, movimientos: pd.DataFrame) -> None:
    st.header("Reportes y respaldo")
    if not can("reportes"):
        st.warning("Este usuario no puede descargar reportes.")
        return

    st.subheader("Inventario por estado")
    if not inventario.empty:
        st.dataframe(inventario.groupby("estado").agg(piezas=("codigo_unico", "count"), valor=("precio", "sum")).reset_index(), use_container_width=True, hide_index=True)

    st.subheader("Catálogo PDF por modelo")
    st.caption("Agrupa por modelo, muestra una foto representativa, QR de modelo, precio y cantidad disponible. Si luego cargas tallas, las mostrará automáticamente.")
    catalogo_pdf = create_catalogo_modelos_pdf(inventario)
    st.download_button(
        "Descargar catálogo PDF por modelo",
        data=catalogo_pdf,
        file_name=f"catalogo_concha_modelos_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf",
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        inventario.to_excel(writer, sheet_name="inventario", index=False)
        clientes.to_excel(writer, sheet_name="clientes", index=False)
        movimientos.to_excel(writer, sheet_name="movimientos", index=False)
    st.download_button(
        "Descargar respaldo Excel",
        data=output.getvalue(),
        file_name=f"respaldo_tienda_concha_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main() -> None:
    if not st.session_state.get("logged_in"):
        login_screen()
        return

    st.set_page_config(page_title=APP_TITLE, page_icon="🛍️", layout="wide")
    sidebar_user()
    inventario, clientes, movimientos, fotos = load_all()

    tabs = ["Resumen", "Leer QR", "Inventario", "Clientes", "Ventas/Reservas", "Fotos", "QR", "Reportes"]
    if can("carga_masiva"):
        tabs.insert(1, "Carga inicial")

    selected = st.sidebar.radio("Menú", tabs)

    if selected == "Resumen":
        dashboard(inventario)
    elif selected == "Leer QR":
        consulta_qr_tab(inventario)
    elif selected == "Carga inicial":
        carga_inicial_tab()
    elif selected == "Inventario":
        inventario_tab(inventario, clientes)
    elif selected == "Clientes":
        clientes_tab(clientes)
    elif selected == "Ventas/Reservas":
        ventas_tab(inventario, clientes)
    elif selected == "Fotos":
        fotos_tab(inventario, fotos)
    elif selected == "QR":
        qr_tab(inventario)
    elif selected == "Reportes":
        reportes_tab(inventario, clientes, movimientos)


if __name__ == "__main__":
    main()
