import os
import re
import base64
from io import BytesIO
from datetime import datetime

import streamlit as st
import pandas as pd


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

st.set_page_config(
    page_title="Concherie Boutique",
    page_icon="🧾",
    layout="wide",
)

USERS = {
    "jc": {"password": "master", "role": "admin"},
    "moira": {"password": "ventas", "role": "operadora"},
    "info": {"password": "precios", "role": "consulta"},
}

INVENTORY_COLUMNS = [
    "marca",
    "codigo",
    "codigo_unico",
    "producto",
    "talla",
    "precio",
    "estado",
    "ubicacion",
    "cliente",
    "descuento",
    "pagado",
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
    "codigo_unico",
    "cliente",
    "detalle",
]

VALID_STATES = ["disponible", "reservado", "probando", "vendido", "mantenimiento"]


# ============================================================
# UTILIDADES BÁSICAS
# ============================================================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def clean_text(value):
    if value is None:
        return ""
    value = str(value).strip()
    if value.lower() in ["nan", "none", "null"]:
        return ""
    return value


def money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def set_page(page):
    st.session_state.page = page
    st.rerun()


def get_role():
    return st.session_state.get("role", "")


def can_edit():
    return get_role() in ["admin", "operadora"]


def can_admin():
    return get_role() == "admin"


def ensure_columns(df, columns):
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=columns)

    df = df.copy()

    for col in columns:
        if col not in df.columns:
            df[col] = ""

    df = df[columns]

    return df


def ensure_inventory_schema(df):
    df = ensure_columns(df, INVENTORY_COLUMNS)

    df["precio"] = pd.to_numeric(df["precio"], errors="coerce").fillna(0.0)
    df["descuento"] = pd.to_numeric(df["descuento"], errors="coerce").fillna(0.0)
    df["pagado"] = pd.to_numeric(df["pagado"], errors="coerce").fillna(0.0)

    for col in [
        "marca",
        "codigo",
        "codigo_unico",
        "producto",
        "talla",
        "estado",
        "ubicacion",
        "cliente",
        "foto_url",
        "notas",
        "fecha_actualizacion",
    ]:
        df[col] = df[col].fillna("").astype(str)

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


# ============================================================
# PERSISTENCIA: GOOGLE SHEETS SI ESTÁ CONFIGURADO, LOCAL SI NO
# ============================================================

def gsheets_configured():
    """
    La app intenta usar Google Sheets si existen secrets de Streamlit.

    Hojas esperadas:
    - inventario
    - clientes
    - movimientos

    Si no hay Google Sheets configurado, guarda en CSV local dentro de /data.
    """
    try:
        if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
            return True
    except Exception:
        return False
    return False


@st.cache_resource
def get_gsheets_connection():
    from streamlit_gsheets import GSheetsConnection
    return st.connection("gsheets", type=GSheetsConnection)


def local_path(table_name):
    os.makedirs("data", exist_ok=True)
    return os.path.join("data", f"{table_name}.csv")


def load_table(table_name, columns):
    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            df = conn.read(worksheet=table_name, ttl=0)
            if df is None:
                return pd.DataFrame(columns=columns)

            df = pd.DataFrame(df)

            if "Unnamed: 0" in df.columns:
                df = df.drop(columns=["Unnamed: 0"])

            return ensure_columns(df, columns)

        except Exception as e:
            st.session_state["storage_warning"] = (
                f"No pude leer Google Sheets; usando respaldo local. Detalle: {e}"
            )

    path = local_path(table_name)

    if os.path.exists(path):
        try:
            return ensure_columns(pd.read_csv(path), columns)
        except Exception:
            return pd.DataFrame(columns=columns)

    return pd.DataFrame(columns=columns)


def save_table(table_name, df):
    df = df.copy()

    if gsheets_configured():
        try:
            conn = get_gsheets_connection()
            conn.update(worksheet=table_name, data=df)
            return True, "Guardado en Google Sheets."

        except Exception as e:
            st.session_state["storage_warning"] = (
                f"No pude escribir en Google Sheets; guardé localmente. Detalle: {e}"
            )

    path = local_path(table_name)
    df.to_csv(path, index=False)
    return True, "Guardado localmente."


def load_all_data():
    if "data_loaded" in st.session_state:
        return

    inv = load_table("inventario", INVENTORY_COLUMNS)
    clients = load_table("clientes", CLIENT_COLUMNS)
    movements = load_table("movimientos", MOVEMENT_COLUMNS)

    st.session_state.inventario = ensure_inventory_schema(inv)
    st.session_state.clientes = ensure_client_schema(clients)
    st.session_state.movimientos = ensure_movement_schema(movements)
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


def log_event(tipo, codigo_unico="", cliente="", detalle=""):
    row = {
        "fecha": now_str(),
        "usuario": st.session_state.get("user", ""),
        "tipo": tipo,
        "codigo_unico": codigo_unico,
        "cliente": cliente,
        "detalle": detalle,
    }

    st.session_state.movimientos = pd.concat(
        [st.session_state.movimientos, pd.DataFrame([row])],
        ignore_index=True,
    )
    save_movements()


# ============================================================
# LOGIN
# ============================================================

def login_page():
    st.title("Concherie Boutique")
    st.subheader("Acceso")

    with st.form("login_form"):
        username = st.text_input("Usuario").strip().lower()
        password = st.text_input("Clave", type="password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)

    if submitted:
        if username in USERS and USERS[username]["password"] == password:
            st.session_state.user = username
            st.session_state.role = USERS[username]["role"]
            st.session_state.page = "home"
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos.")


def logout():
    for key in [
        "user",
        "role",
        "page",
        "selected_piece",
        "selected_model",
        "qr_result",
    ]:
        st.session_state.pop(key, None)
    st.rerun()


# ============================================================
# NAVEGACIÓN
# ============================================================

def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user', '')}**")
    st.sidebar.write(f"Rol: **{st.session_state.get('role', '')}**")

    if gsheets_configured():
        st.sidebar.success("Persistencia: Google Sheets")
    else:
        st.sidebar.warning("Persistencia: local / temporal")

    if st.session_state.get("storage_warning"):
        st.sidebar.info(st.session_state["storage_warning"])

    st.sidebar.markdown("---")

    if st.sidebar.button("🏠 Inicio", use_container_width=True):
        set_page("home")

    if st.sidebar.button("🔳 Escanear QR", use_container_width=True):
        set_page("scan")

    if st.sidebar.button("📦 Inventario", use_container_width=True):
        set_page("inventario")

    if st.sidebar.button("🏷️ QR / Etiquetas", use_container_width=True):
        set_page("qr")

    if can_edit():
        if st.sidebar.button("📥 Cargar inventario", use_container_width=True):
            set_page("carga")

        if st.sidebar.button("🛍️ Ventas / Reservas", use_container_width=True):
            set_page("ventas")

        if st.sidebar.button("👥 Clientes", use_container_width=True):
            set_page("clientes")

    if st.sidebar.button("📊 Reportes", use_container_width=True):
        set_page("reportes")

    st.sidebar.markdown("---")

    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        logout()


def home_page():
    st.title("Concherie Boutique")
    st.caption("Inventario, ventas, QR, clientes, fotos y notas de cobro.")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔳 Escanear QR", use_container_width=True):
            set_page("scan")

        if st.button("📦 Inventario", use_container_width=True):
            set_page("inventario")

        if st.button("🏷️ QR / Etiquetas", use_container_width=True):
            set_page("qr")

    with col2:
        if can_edit():
            if st.button("🛍️ Ventas / Reservas", use_container_width=True):
                set_page("ventas")

            if st.button("👥 Clientes", use_container_width=True):
                set_page("clientes")

            if st.button("📥 Cargar inventario", use_container_width=True):
                set_page("carga")

        if st.button("📊 Reportes", use_container_width=True):
            set_page("reportes")

    st.markdown("---")

    df = st.session_state.inventario

    if df.empty:
        st.info("Todavía no hay inventario cargado.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total piezas", len(df))
    c2.metric("Disponibles", len(df[df["estado"] == "disponible"]))
    c3.metric("Reservadas", len(df[df["estado"] == "reservado"]))
    c4.metric("Vendidas", len(df[df["estado"] == "vendido"]))


# ============================================================
# CARGA DE INVENTARIO
# ============================================================

def normalize_upload_columns(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    renames = {
        "maison": "marca",
        "brand": "marca",
        "marca": "marca",
        "codigo base": "codigo",
        "código base": "codigo",
        "codigo": "codigo",
        "código": "codigo",
        "sku": "codigo",
        "producto": "producto",
        "descripcion": "producto",
        "descripción": "producto",
        "cantidad": "cantidad",
        "qty": "cantidad",
        "precio": "precio",
        "precio unitario": "precio",
        "precio_unitario": "precio",
        "unit price": "precio",
        "talla": "talla",
        "size": "talla",
    }

    df = df.rename(columns={c: renames.get(c, c) for c in df.columns})

    drop_cols = [
        c for c in df.columns
        if c in [
            "fuente",
            "total",
            "comentarios",
            "comentario",
            "notas_revision",
            "notas de revisión",
        ]
    ]

    if drop_cols:
        df = df.drop(columns=drop_cols)

    return df


def next_suffix_for_code(existing_df, codigo):
    if existing_df.empty or "codigo_unico" not in existing_df.columns:
        return 1

    pattern = re.compile(rf"^{re.escape(codigo)}-(\d+)$")
    max_n = 0

    for val in existing_df["codigo_unico"].astype(str).tolist():
        m = pattern.match(val.strip())
        if m:
            max_n = max(max_n, safe_int(m.group(1), 0))

    return max_n + 1


def create_piece_inventory_from_upload(uploaded_df, existing_df=None):
    df = normalize_upload_columns(uploaded_df)

    required = ["codigo", "producto", "cantidad", "precio"]
    missing = [c for c in required if c not in df.columns]

    if missing:
        raise ValueError(
            "El Excel debe tener estas columnas mínimas: codigo, producto, cantidad y precio. "
            f"Faltan: {', '.join(missing)}"
        )

    if "marca" not in df.columns:
        df["marca"] = ""

    if "talla" not in df.columns:
        df["talla"] = ""

    if existing_df is None:
        existing_df = pd.DataFrame(columns=INVENTORY_COLUMNS)

    counters = {}
    rows = []

    for _, r in df.iterrows():
        codigo = clean_text(r.get("codigo"))
        producto = clean_text(r.get("producto"))
        marca = clean_text(r.get("marca"))
        talla = clean_text(r.get("talla"))
        cantidad = safe_int(r.get("cantidad"), 0)
        precio = safe_float(r.get("precio"), 0.0)

        if not codigo or not producto or cantidad <= 0:
            continue

        if codigo not in counters:
            counters[codigo] = next_suffix_for_code(existing_df, codigo)

        for _ in range(cantidad):
            n = counters[codigo]
            counters[codigo] += 1

            rows.append(
                {
                    "marca": marca,
                    "codigo": codigo,
                    "codigo_unico": f"{codigo}-{n:02d}",
                    "producto": producto,
                    "talla": talla,
                    "precio": precio,
                    "estado": "disponible",
                    "ubicacion": "tienda",
                    "cliente": "",
                    "descuento": 0.0,
                    "pagado": 0.0,
                    "foto_url": "",
                    "notas": "",
                    "fecha_actualizacion": now_str(),
                }
            )

    return ensure_inventory_schema(pd.DataFrame(rows))


def carga_page():
    st.title("Cargar inventario")

    if not can_edit():
        st.warning("No tienes permiso para cargar inventario.")
        return

    st.write(
        "Sube un Excel con columnas: **marca**, **codigo**, **producto**, "
        "**cantidad**, **precio**. La columna **talla** es opcional."
    )

    uploaded = st.file_uploader("Subir Excel", type=["xlsx"])

    mode = st.radio(
        "Modo de carga",
        ["Agregar al inventario existente", "Reemplazar inventario completo"],
    )

    if uploaded:
        try:
            raw = pd.read_excel(uploaded)
            normalized = normalize_upload_columns(raw)

            st.subheader("Vista previa del archivo")
            st.dataframe(normalized.head(30), use_container_width=True)

            existing = (
                st.session_state.inventario
                if mode == "Agregar al inventario existente"
                else pd.DataFrame(columns=INVENTORY_COLUMNS)
            )

            new_inventory = create_piece_inventory_from_upload(raw, existing)

            st.subheader("Piezas que se crearán")
            st.write(f"Total piezas nuevas: **{len(new_inventory)}**")
            st.dataframe(new_inventory.head(50), use_container_width=True)

            if st.button("Guardar inventario", type="primary", use_container_width=True):
                if mode == "Reemplazar inventario completo":
                    st.session_state.inventario = new_inventory
                else:
                    st.session_state.inventario = ensure_inventory_schema(
                        pd.concat(
                            [st.session_state.inventario, new_inventory],
                            ignore_index=True,
                        )
                    )

                ok, msg = save_inventory()
                log_event(
                    "carga_inventario",
                    detalle=f"{mode}. Piezas: {len(new_inventory)}",
                )
                st.success(msg)
                st.rerun()

        except Exception as e:
            st.error(str(e))


# ============================================================
# FOTOS
# ============================================================

def file_to_compressed_data_url(uploaded_file, max_size=900, quality=78):
    from PIL import Image

    img = Image.open(uploaded_file)
    img = img.convert("RGB")

    w, h = img.size
    scale = min(max_size / max(w, h), 1.0)

    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


def image_bytes_from_data_url(data_url):
    data_url = clean_text(data_url)

    if not data_url.startswith("data:image"):
        return None

    try:
        encoded = data_url.split(",", 1)[1]
        return base64.b64decode(encoded)
    except Exception:
        return None


def show_photo(data_url, caption="Foto"):
    img_bytes = image_bytes_from_data_url(data_url)

    if img_bytes:
        st.image(img_bytes, caption=caption, use_container_width=True)
    else:
        st.info("Sin foto cargada.")


# ============================================================
# INVENTARIO Y EDICIÓN DE PIEZAS
# ============================================================

def inventory_filters(df):
    col1, col2, col3 = st.columns(3)

    query = col1.text_input("Buscar", placeholder="código, producto, cliente, talla...")
    state = col2.selectbox("Estado", ["todos"] + VALID_STATES)
    model_code = col3.text_input("Código modelo", placeholder="Ej: ISH01")

    view = df.copy()

    if query:
        q = query.lower().strip()
        view = view[
            view.apply(
                lambda r: q in " ".join([str(v).lower() for v in r.values]),
                axis=1,
            )
        ]

    if state != "todos":
        view = view[view["estado"] == state]

    if model_code:
        view = view[
            view["codigo"]
            .astype(str)
            .str.contains(model_code, case=False, na=False)
        ]

    return view


def inventario_page():
    st.title("Inventario")

    df = st.session_state.inventario

    if df.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    view = inventory_filters(df)

    st.caption(f"Mostrando {len(view)} de {len(df)} piezas.")

    st.dataframe(
        view[
            [
                "marca",
                "codigo",
                "codigo_unico",
                "producto",
                "talla",
                "precio",
                "estado",
                "ubicacion",
                "cliente",
                "descuento",
                "pagado",
            ]
        ],
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("Ficha / edición de pieza")

    selected = st.selectbox(
        "Selecciona una pieza",
        view["codigo_unico"].tolist()
        if not view.empty
        else df["codigo_unico"].tolist(),
    )

    if selected:
        idx = df[df["codigo_unico"] == selected].index[0]
        show_piece_detail(idx)


def edit_piece_form(idx, compact=False):
    if not can_edit():
        return

    df = st.session_state.inventario
    row = df.loc[idx]

    with st.form(f"edit_piece_{idx}_{compact}"):
        col1, col2 = st.columns(2)

        with col1:
            marca = st.text_input("Marca", value=clean_text(row["marca"]))
            codigo = st.text_input("Código modelo", value=clean_text(row["codigo"]))
            codigo_unico = st.text_input(
                "Código único / pieza",
                value=clean_text(row["codigo_unico"]),
            )
            producto = st.text_input("Producto", value=clean_text(row["producto"]))
            talla = st.text_input("Talla", value=clean_text(row["talla"]))

        with col2:
            precio = st.number_input(
                "Precio",
                value=safe_float(row["precio"]),
                step=10.0,
            )

            current_state = clean_text(row["estado"])
            if current_state not in VALID_STATES:
                current_state = "disponible"

            estado = st.selectbox(
                "Estado",
                VALID_STATES,
                index=VALID_STATES.index(current_state),
            )

            ubicacion = st.text_input("Ubicación", value=clean_text(row["ubicacion"]))
            cliente = st.text_input("Cliente", value=clean_text(row["cliente"]))

            descuento = st.number_input(
                "Descuento de esta pieza",
                value=safe_float(row["descuento"]),
                step=10.0,
            )

            pagado = st.number_input(
                "Pagado por esta pieza",
                value=safe_float(row["pagado"]),
                step=10.0,
            )

            notas = st.text_area("Notas", value=clean_text(row["notas"]))

        submitted = st.form_submit_button("Guardar cambios", use_container_width=True)

    if submitted:
        old_summary = f"{row['estado']} / {row['cliente']} / {row['precio']}"

        df.at[idx, "marca"] = marca.strip()
        df.at[idx, "codigo"] = codigo.strip()
        df.at[idx, "codigo_unico"] = codigo_unico.strip()
        df.at[idx, "producto"] = producto.strip()
        df.at[idx, "talla"] = talla.strip()
        df.at[idx, "precio"] = precio
        df.at[idx, "estado"] = estado
        df.at[idx, "ubicacion"] = ubicacion.strip()
        df.at[idx, "cliente"] = cliente.strip()
        df.at[idx, "descuento"] = descuento
        df.at[idx, "pagado"] = pagado
        df.at[idx, "notas"] = notas.strip()
        df.at[idx, "fecha_actualizacion"] = now_str()

        st.session_state.inventario = ensure_inventory_schema(df)
        ok, msg = save_inventory()

        log_event(
            "editar_pieza",
            codigo_unico=codigo_unico.strip(),
            cliente=cliente.strip(),
            detalle=f"Antes: {old_summary}. Ahora: {estado} / {cliente} / {precio}",
        )

        if cliente.strip():
            ensure_client_exists(cliente.strip())

        st.success(msg)
        st.rerun()

    uploaded_photo = st.file_uploader(
        "Subir/tomar foto de esta pieza",
        type=["jpg", "jpeg", "png"],
        key=f"photo_upload_{idx}_{compact}",
    )

    if uploaded_photo is not None:
        if st.button(
            "Guardar foto",
            key=f"save_photo_{idx}_{compact}",
            use_container_width=True,
        ):
            try:
                data_url = file_to_compressed_data_url(uploaded_photo)
                df.at[idx, "foto_url"] = data_url
                df.at[idx, "fecha_actualizacion"] = now_str()
                st.session_state.inventario = ensure_inventory_schema(df)
                ok, msg = save_inventory()

                log_event(
                    "foto_pieza",
                    codigo_unico=clean_text(df.at[idx, "codigo_unico"]),
                    detalle="Foto actualizada.",
                )

                st.success("Foto guardada.")
                st.rerun()

            except Exception as e:
                st.error(f"No pude guardar la foto: {e}")


def show_piece_detail(idx):
    df = st.session_state.inventario
    row = df.loc[idx]

    st.subheader(f"{row['codigo_unico']} — {row['producto']}")

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        show_photo(row.get("foto_url", ""), caption=row["codigo_unico"])

    with col2:
        st.write(f"**Marca:** {clean_text(row['marca']) or 'Sin marca'}")
        st.write(f"**Código modelo:** {row['codigo']}")
        st.write(f"**Código pieza:** {row['codigo_unico']}")
        st.write(f"**Producto:** {row['producto']}")
        st.write(f"**Talla:** {clean_text(row['talla']) or 'Sin cargar'}")
        st.write(f"**Estado:** {row['estado']}")
        st.write(f"**Ubicación:** {row['ubicacion']}")
        st.write(f"**Cliente:** {clean_text(row['cliente']) or 'Sin cliente'}")

    with col3:
        precio = safe_float(row["precio"])
        descuento = safe_float(row["descuento"])
        pagado = safe_float(row["pagado"])
        neto = precio - descuento
        saldo = neto - pagado

        st.metric("Precio", money(precio))
        st.metric("Descuento pieza", money(descuento))
        st.metric("Neto", money(neto))
        st.metric("Pagado", money(pagado))
        st.metric("Saldo", money(saldo))

        st.caption(f"QR: {qr_piece_text(row)}")

    if can_edit():
        st.markdown("---")
        edit_piece_form(idx)


# ============================================================
# VENTAS / RESERVAS / PROBÁNDOSE
# ============================================================

def ventas_page():
    st.title("Ventas / Reservas / Probándose")

    if not can_edit():
        st.warning("No tienes permiso para esta sección.")
        return

    df = st.session_state.inventario

    if df.empty:
        st.warning("No hay inventario cargado.")
        return

    col1, col2 = st.columns(2)
    only_available = col1.checkbox("Mostrar solo disponibles", value=False)
    search = col2.text_input("Buscar pieza/modelo/producto")

    view = df.copy()

    if only_available:
        view = view[view["estado"] == "disponible"]

    if search:
        s = search.lower().strip()
        view = view[
            view.apply(
                lambda r: s in " ".join([str(v).lower() for v in r.values]),
                axis=1,
            )
        ]

    if view.empty:
        st.info("No hay piezas con ese filtro.")
        return

    selected = st.selectbox("Selecciona pieza", view["codigo_unico"].tolist())
    idx = df[df["codigo_unico"] == selected].index[0]
    row = df.loc[idx]

    st.write(f"**Producto:** {row['producto']} — **Precio:** {money(row['precio'])}")

    with st.form("venta_form"):
        cliente = st.text_input("Cliente", value=clean_text(row["cliente"]))

        estado = st.selectbox(
            "Estado / acción",
            ["vendido", "reservado", "probando", "disponible"],
            index=0,
        )

        ubicacion_default = (
            "casa cliente"
            if estado == "probando"
            else clean_text(row["ubicacion"]) or "tienda"
        )

        ubicacion = st.text_input("Ubicación", value=ubicacion_default)

        colp1, colp2, colp3 = st.columns(3)

        precio = colp1.number_input(
            "Precio",
            value=safe_float(row["precio"]),
            step=10.0,
        )

        descuento = colp2.number_input(
            "Descuento de esta pieza",
            value=safe_float(row["descuento"]),
            step=10.0,
        )

        pagado = colp3.number_input(
            "Pagado por esta pieza",
            value=safe_float(row["pagado"]),
            step=10.0,
        )

        saldo = precio - descuento - pagado
        st.info(f"Neto: {money(precio - descuento)} | Saldo: {money(saldo)}")

        notas = st.text_area("Notas", value=clean_text(row["notas"]))

        submitted = st.form_submit_button("Guardar", use_container_width=True)

    if submitted:
        df.at[idx, "cliente"] = cliente.strip()
        df.at[idx, "estado"] = estado
        df.at[idx, "ubicacion"] = ubicacion.strip()
        df.at[idx, "precio"] = precio
        df.at[idx, "descuento"] = descuento
        df.at[idx, "pagado"] = pagado
        df.at[idx, "notas"] = notas.strip()
        df.at[idx, "fecha_actualizacion"] = now_str()

        st.session_state.inventario = ensure_inventory_schema(df)
        ok, msg = save_inventory()

        log_event(
            estado,
            codigo_unico=selected,
            cliente=cliente.strip(),
            detalle=f"Precio {precio}, descuento {descuento}, pagado {pagado}, saldo {saldo}",
        )

        if cliente.strip():
            ensure_client_exists(cliente.strip())

        st.success(msg)
        st.rerun()


# ============================================================
# CLIENTES Y NOTA DE COBRO
# ============================================================

def ensure_client_exists(cliente):
    cliente = clean_text(cliente)

    if not cliente:
        return

    clients = st.session_state.clientes
    existing = clients[clients["cliente"].astype(str).str.lower() == cliente.lower()]

    if existing.empty:
        row = {
            "cliente": cliente,
            "telefono": "",
            "email": "",
            "notas": "",
            "fecha_creacion": now_str(),
        }

        st.session_state.clientes = pd.concat(
            [clients, pd.DataFrame([row])],
            ignore_index=True,
        )

        save_clients()


def clientes_page():
    st.title("Clientes")

    if not can_edit():
        st.warning("No tienes permiso para ver clientes.")
        return

    df = st.session_state.inventario

    with st.expander("Agregar / editar datos de cliente", expanded=False):
        with st.form("client_form"):
            cliente = st.text_input("Nombre cliente")
            telefono = st.text_input("Teléfono")
            email = st.text_input("Email")
            notas = st.text_area("Notas")
            save_client = st.form_submit_button("Guardar cliente")

        if save_client and cliente.strip():
            clients = st.session_state.clientes
            mask = clients["cliente"].astype(str).str.lower() == cliente.strip().lower()

            if mask.any():
                idx = clients[mask].index[0]
                clients.at[idx, "telefono"] = telefono.strip()
                clients.at[idx, "email"] = email.strip()
                clients.at[idx, "notas"] = notas.strip()
            else:
                row = {
                    "cliente": cliente.strip(),
                    "telefono": telefono.strip(),
                    "email": email.strip(),
                    "notas": notas.strip(),
                    "fecha_creacion": now_str(),
                }
                clients = pd.concat([clients, pd.DataFrame([row])], ignore_index=True)

            st.session_state.clientes = ensure_client_schema(clients)
            ok, msg = save_clients()
            st.success(msg)
            st.rerun()

    names_from_inventory = df["cliente"].dropna().astype(str).str.strip()
    names_from_inventory = names_from_inventory[names_from_inventory != ""].tolist()

    names_from_clients = st.session_state.clientes["cliente"].dropna().astype(str).str.strip()
    names_from_clients = names_from_clients[names_from_clients != ""].tolist()

    all_clients = sorted(set(names_from_inventory + names_from_clients), key=lambda x: x.lower())

    if not all_clients:
        st.info("Todavía no hay clientes registrados ni piezas asignadas.")
        return

    selected_client = st.selectbox("Selecciona cliente", all_clients)

    if selected_client:
        show_client_profile(selected_client)


def show_client_profile(cliente):
    st.subheader(f"Perfil de cliente: {cliente}")

    clients = st.session_state.clientes
    client_info = clients[clients["cliente"].astype(str).str.lower() == cliente.lower()]

    if not client_info.empty:
        info = client_info.iloc[0]
        st.write(f"**Teléfono:** {clean_text(info['telefono']) or '-'}")
        st.write(f"**Email:** {clean_text(info['email']) or '-'}")
        st.write(f"**Notas cliente:** {clean_text(info['notas']) or '-'}")

    df = st.session_state.inventario
    data = df[df["cliente"].astype(str).str.lower() == cliente.lower()].copy()

    if data.empty:
        st.info("Este cliente no tiene piezas asociadas.")
        return

    data["neto"] = data["precio"].astype(float) - data["descuento"].astype(float)
    data["saldo"] = data["neto"] - data["pagado"].astype(float)

    st.dataframe(
        data[
            [
                "codigo_unico",
                "producto",
                "talla",
                "estado",
                "precio",
                "descuento",
                "neto",
                "pagado",
                "saldo",
            ]
        ],
        use_container_width=True,
    )

    subtotal = data["precio"].sum()
    descuentos = data["descuento"].sum()
    total_neto = data["neto"].sum()
    pagado_total = data["pagado"].sum()
    saldo_total = data["saldo"].sum()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Subtotal", money(subtotal))
    col2.metric("Descuentos", money(descuentos))
    col3.metric("Total neto", money(total_neto))
    col4.metric("Pagado", money(pagado_total))
    col5.metric("Saldo", money(saldo_total))

    pdf_bytes = create_invoice_pdf(cliente, data)

    st.download_button(
        "Descargar nota de cobro PDF",
        data=pdf_bytes,
        file_name=f"nota_cobro_{safe_filename(cliente)}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("Editar pieza de este cliente")

    piece = st.selectbox(
        "Pieza",
        data["codigo_unico"].tolist(),
        key=f"client_piece_{cliente}",
    )

    if piece:
        idx = df[df["codigo_unico"] == piece].index[0]
        edit_piece_form(idx, compact=True)


def safe_filename(text):
    text = clean_text(text)
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
    return text.strip("_") or "archivo"


def create_invoice_pdf(cliente, data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 0.7 * inch

    c.setFont("Helvetica-Bold", 17)
    c.drawString(0.7 * inch, y, "Concherie Boutique")
    y -= 0.28 * inch

    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.7 * inch, y, "Nota de cobro")
    y -= 0.25 * inch

    c.setFont("Helvetica", 10)
    c.drawString(0.7 * inch, y, f"Cliente: {cliente}")
    y -= 0.20 * inch
    c.drawString(0.7 * inch, y, f"Fecha: {datetime.now().strftime('%Y-%m-%d')}")
    y -= 0.42 * inch

    headers = ["Pieza", "Producto", "Talla", "Precio", "Desc.", "Neto", "Pagado", "Saldo"]
    xs = [0.45, 1.25, 3.0, 3.55, 4.35, 5.05, 5.75, 6.45]

    c.setFont("Helvetica-Bold", 8)
    for x, h in zip(xs, headers):
        c.drawString(x * inch, y, h)

    y -= 0.18 * inch
    c.line(0.45 * inch, y, 7.3 * inch, y)
    y -= 0.18 * inch

    c.setFont("Helvetica", 7.5)

    for _, r in data.iterrows():
        if y < 0.8 * inch:
            c.showPage()
            y = height - 0.7 * inch
            c.setFont("Helvetica", 7.5)

        precio = safe_float(r["precio"])
        descuento = safe_float(r["descuento"])
        neto = precio - descuento
        pagado = safe_float(r["pagado"])
        saldo = neto - pagado

        values = [
            clean_text(r["codigo_unico"])[:14],
            clean_text(r["producto"])[:25],
            clean_text(r["talla"])[:8],
            money(precio),
            money(descuento),
            money(neto),
            money(pagado),
            money(saldo),
        ]

        for x, value in zip(xs, values):
            c.drawString(x * inch, y, value)

        y -= 0.17 * inch

    y -= 0.25 * inch

    subtotal = data["precio"].astype(float).sum()
    descuentos = data["descuento"].astype(float).sum()
    total_neto = subtotal - descuentos
    pagado_total = data["pagado"].astype(float).sum()
    saldo_total = total_neto - pagado_total

    if y < 1.5 * inch:
        c.showPage()
        y = height - 0.7 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.7 * inch, y, f"Subtotal: {money(subtotal)}")
    y -= 0.2 * inch
    c.drawString(0.7 * inch, y, f"Descuentos por pieza: {money(descuentos)}")
    y -= 0.2 * inch
    c.drawString(0.7 * inch, y, f"Total neto: {money(total_neto)}")
    y -= 0.2 * inch
    c.drawString(0.7 * inch, y, f"Pagado: {money(pagado_total)}")
    y -= 0.2 * inch
    c.drawString(0.7 * inch, y, f"Saldo pendiente: {money(saldo_total)}")

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# QR: TEXTO, GENERACIÓN Y LECTOR
# ============================================================

def qr_piece_text(row):
    marca = clean_text(row.get("marca", ""))
    codigo_unico = clean_text(row.get("codigo_unico", ""))

    if marca:
        return f"{marca}|{codigo_unico}"

    return codigo_unico


def qr_model_text(row):
    marca = clean_text(row.get("marca", ""))
    codigo = clean_text(row.get("codigo", ""))

    if marca:
        return f"{marca}|{codigo}"

    return codigo


def parse_qr_code(text):
    text = clean_text(text)

    if "|" in text:
        return text.split("|")[-1].strip()

    return text


def make_qr_png_bytes(text):
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )

    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer.getvalue()


def qr_page():
    st.title("QR / Etiquetas")

    df = st.session_state.inventario

    if df.empty:
        st.warning("No hay inventario cargado.")
        return

    tab1, tab2, tab3 = st.tabs(["Etiquetas por pieza", "Catálogo por modelo", "Vista QR"])

    with tab1:
        st.subheader("Etiquetas QR de 2 cm x 2 cm")

        scope = st.selectbox(
            "Piezas para generar",
            [
                "disponible",
                "todo",
                "reservado",
                "probando",
                "vendido",
                "por código modelo",
            ],
        )

        selected_df = df.copy()

        if scope in VALID_STATES:
            selected_df = selected_df[selected_df["estado"] == scope]
        elif scope == "por código modelo":
            model = st.text_input("Código modelo")
            if model:
                selected_df = selected_df[
                    selected_df["codigo"]
                    .astype(str)
                    .str.contains(model, case=False, na=False)
                ]
            else:
                selected_df = selected_df.iloc[0:0]

        st.write(f"Etiquetas a generar: **{len(selected_df)}**")

        if not selected_df.empty:
            st.dataframe(
                selected_df[
                    ["marca", "codigo", "codigo_unico", "producto", "talla", "estado"]
                ],
                use_container_width=True,
            )

            pdf = create_qr_labels_pdf(selected_df)

            st.download_button(
                "Descargar etiquetas QR PDF",
                data=pdf,
                file_name="etiquetas_qr_concherie.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

    with tab2:
        st.subheader("Catálogo PDF por modelo con foto, QR y disponibles")

        available_only = st.checkbox("Mostrar solo modelos con disponibles", value=True)

        cat_pdf = create_catalog_pdf_by_model(df, available_only=available_only)

        st.download_button(
            "Descargar catálogo PDF",
            data=cat_pdf,
            file_name="catalogo_concherie_modelos.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with tab3:
        st.subheader("Ver QR de una pieza")

        selected = st.selectbox(
            "Pieza",
            df["codigo_unico"].tolist(),
            key="preview_qr_piece",
        )

        idx = df[df["codigo_unico"] == selected].index[0]
        row = df.loc[idx]
        text = qr_piece_text(row)

        st.write(f"Texto QR: `{text}`")
        st.image(make_qr_png_bytes(text), width=220)


def create_qr_labels_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    margin_x = 0.7 * cm
    margin_y = 0.7 * cm
    cell_w = 2.65 * cm
    cell_h = 2.85 * cm
    qr_size = 2.0 * cm

    cols = max(1, int((width - 2 * margin_x) // cell_w))
    rows = max(1, int((height - 2 * margin_y) // cell_h))

    count = 0

    for _, row in data.iterrows():
        if count > 0 and count % (cols * rows) == 0:
            c.showPage()

        col = count % cols
        rownum = (count // cols) % rows

        x = margin_x + col * cell_w
        y = height - margin_y - (rownum + 1) * cell_h

        qr_text = qr_piece_text(row)
        qr_bytes = make_qr_png_bytes(qr_text)
        qr_reader = ImageReader(BytesIO(qr_bytes))

        qr_x = x + (cell_w - qr_size) / 2
        qr_y = y + 0.52 * cm

        c.drawImage(
            qr_reader,
            qr_x,
            qr_y,
            qr_size,
            qr_size,
            preserveAspectRatio=True,
            mask="auto",
        )

        c.setFont("Helvetica-Bold", 6.3)

        label = clean_text(row["codigo_unico"])
        text_width = c.stringWidth(label, "Helvetica-Bold", 6.3)
        c.drawString(x + (cell_w - text_width) / 2, y + 0.25 * cm, label)

        count += 1

    c.save()
    buffer.seek(0)

    return buffer.getvalue()


def decode_qr_from_uploaded_file(uploaded_file):
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img = Image.open(uploaded_file).convert("RGB")
        arr = np.array(img)
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

        detector = cv2.QRCodeDetector()
        data, points, _ = detector.detectAndDecode(arr)

        if data:
            return data, None

        return "", "No pude leer el QR en esa imagen."

    except Exception as e:
        return "", f"No pude procesar la imagen: {e}"


def scan_page():
    st.title("Escanear QR / consultar precio")

    df = st.session_state.inventario

    if df.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    st.caption("Al leer el QR, la app abre la ficha de la pieza o del modelo.")

    manual = st.text_input(
        "Pegar código manualmente",
        placeholder="Ej: MARCA|ISH01-01 o ISH01-01",
    )

    if manual:
        open_qr_result(manual)

    st.markdown("---")
    st.subheader("Leer QR con cámara o foto")

    st.info(
        "En iPhone, usa la cámara trasera si el navegador te muestra selector. "
        "Streamlit no siempre permite forzar la cámara trasera desde Python."
    )

    camera_img = st.camera_input("Tomar foto del QR")
    uploaded_img = st.file_uploader("O subir foto del QR", type=["jpg", "jpeg", "png"])

    source = camera_img or uploaded_img

    if source is not None:
        decoded, error = decode_qr_from_uploaded_file(source)

        if decoded:
            open_qr_result(decoded)
        elif error:
            st.error(error)


def open_qr_result(raw_text):
    df = st.session_state.inventario
    code = parse_qr_code(raw_text)

    exact = df[df["codigo_unico"].astype(str).str.strip() == code]

    if not exact.empty:
        st.session_state.selected_piece = exact.index[0]
        st.session_state.page = "pieza"
        st.rerun()

    model = df[df["codigo"].astype(str).str.strip() == code]

    if not model.empty:
        st.session_state.selected_model = code
        st.session_state.page = "modelo"
        st.rerun()

    st.error(f"No encontré el código: {code}")


def piece_page():
    idx = st.session_state.get("selected_piece")

    if idx is None:
        st.warning("No hay pieza seleccionada.")
        return

    if st.button("⬅️ Volver a escanear"):
        set_page("scan")

    show_piece_detail(idx)


def model_page():
    code = st.session_state.get("selected_model", "")

    if not code:
        st.warning("No hay modelo seleccionado.")
        return

    if st.button("⬅️ Volver a escanear"):
        set_page("scan")

    df = st.session_state.inventario
    model = df[df["codigo"].astype(str) == code]

    if model.empty:
        st.error("Modelo no encontrado.")
        return

    first = model.iloc[0]
    disponibles = model[model["estado"] == "disponible"]

    st.title(f"Modelo {code}")
    st.subheader(first["producto"])

    col1, col2 = st.columns(2)

    with col1:
        photo_row = model[model["foto_url"].astype(str).str.startswith("data:image")]

        if not photo_row.empty:
            show_photo(photo_row.iloc[0]["foto_url"], caption=code)
        else:
            st.info("Este modelo todavía no tiene foto.")

    with col2:
        st.write(f"**Marca:** {clean_text(first['marca']) or 'Sin marca'}")
        st.write(f"**Producto:** {first['producto']}")
        st.write(f"**Precio:** {money(first['precio'])}")
        st.write(f"**Disponibles:** {len(disponibles)}")

        tallas = disponibles["talla"].fillna("").astype(str)
        tallas = tallas[tallas.str.strip() != ""]

        if not tallas.empty:
            st.write("**Disponibles por talla:**")
            st.write(tallas.value_counts())

    st.dataframe(
        model[
            [
                "codigo_unico",
                "talla",
                "estado",
                "ubicacion",
                "cliente",
                "precio",
                "descuento",
                "pagado",
            ]
        ],
        use_container_width=True,
    )


# ============================================================
# REPORTES Y PDFS
# ============================================================

def reportes_page():
    st.title("Reportes")

    df = st.session_state.inventario

    if df.empty:
        st.warning("No hay datos.")
        return

    disponibles_df = df[df["estado"] == "disponible"].copy()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total piezas", len(df))
    col2.metric("Disponibles", len(disponibles_df))
    col3.metric("Reservadas", len(df[df["estado"] == "reservado"]))
    col4.metric("Vendidas", len(df[df["estado"] == "vendido"]))

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "Disponibles",
            "Resumen por modelo",
            "Movimientos",
            "Respaldos",
        ]
    )

    with tab1:
        st.subheader("Inventario disponible por pieza")

        st.dataframe(
            disponibles_df[
                [
                    "marca",
                    "codigo",
                    "codigo_unico",
                    "producto",
                    "talla",
                    "precio",
                    "ubicacion",
                    "foto_url",
                ]
            ],
            use_container_width=True,
        )

        pdf = create_available_inventory_pdf(disponibles_df)

        st.download_button(
            "Descargar inventario disponible en PDF",
            data=pdf,
            file_name="inventario_disponible_concherie.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with tab2:
        st.subheader("Resumen disponible por modelo")

        resumen = build_model_summary(df)
        st.dataframe(resumen, use_container_width=True)

        pdf_modelos = create_model_summary_pdf(resumen)

        st.download_button(
            "Descargar resumen por modelo en PDF",
            data=pdf_modelos,
            file_name="resumen_modelos_concherie.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with tab3:
        st.subheader("Historial de movimientos")
        st.dataframe(st.session_state.movimientos, use_container_width=True)

    with tab4:
        st.subheader("Descargar respaldo Excel")

        output = BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="inventario", index=False)
            disponibles_df.to_excel(writer, sheet_name="disponibles", index=False)
            build_model_summary(df).to_excel(writer, sheet_name="resumen_modelos", index=False)
            st.session_state.clientes.to_excel(writer, sheet_name="clientes", index=False)
            st.session_state.movimientos.to_excel(writer, sheet_name="movimientos", index=False)

        st.download_button(
            "Descargar respaldo completo Excel",
            data=output.getvalue(),
            file_name="respaldo_concherie.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def build_model_summary(df):
    if df.empty:
        return pd.DataFrame(
            columns=[
                "marca",
                "codigo",
                "producto",
                "precio",
                "total_piezas",
                "disponibles",
                "reservadas",
                "probando",
                "vendidas",
            ]
        )

    grouped_rows = []

    for (marca, codigo, producto), g in df.groupby(
        ["marca", "codigo", "producto"],
        dropna=False,
    ):
        grouped_rows.append(
            {
                "marca": marca,
                "codigo": codigo,
                "producto": producto,
                "precio": safe_float(g["precio"].iloc[0]),
                "total_piezas": len(g),
                "disponibles": len(g[g["estado"] == "disponible"]),
                "reservadas": len(g[g["estado"] == "reservado"]),
                "probando": len(g[g["estado"] == "probando"]),
                "vendidas": len(g[g["estado"] == "vendido"]),
            }
        )

    return pd.DataFrame(grouped_rows).sort_values(["codigo", "producto"])


def create_available_inventory_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 0.65 * inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.6 * inch, y, "Inventario disponible - Concherie Boutique")
    y -= 0.28 * inch

    c.setFont("Helvetica", 9)
    c.drawString(
        0.6 * inch,
        y,
        f"Fecha: {datetime.now().strftime('%Y-%m-%d')} | Piezas disponibles: {len(data)}",
    )
    y -= 0.35 * inch

    headers = ["Marca", "Modelo", "Pieza", "Producto", "Talla", "Precio"]
    xs = [0.35, 1.05, 1.75, 2.7, 5.5, 6.2]

    c.setFont("Helvetica-Bold", 7.5)

    for x, h in zip(xs, headers):
        c.drawString(x * inch, y, h)

    y -= 0.18 * inch
    c.line(0.35 * inch, y, 7.5 * inch, y)
    y -= 0.15 * inch

    c.setFont("Helvetica", 7.2)

    for _, r in data.iterrows():
        if y < 0.55 * inch:
            c.showPage()
            y = height - 0.65 * inch
            c.setFont("Helvetica", 7.2)

        values = [
            clean_text(r.get("marca"))[:10],
            clean_text(r.get("codigo"))[:12],
            clean_text(r.get("codigo_unico"))[:14],
            clean_text(r.get("producto"))[:35],
            clean_text(r.get("talla"))[:8],
            money(r.get("precio")),
        ]

        for x, value in zip(xs, values):
            c.drawString(x * inch, y, value)

        y -= 0.15 * inch

    c.save()
    buffer.seek(0)

    return buffer.getvalue()


def create_model_summary_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 0.65 * inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.6 * inch, y, "Resumen por modelo - Concherie Boutique")
    y -= 0.28 * inch

    c.setFont("Helvetica", 9)
    c.drawString(0.6 * inch, y, f"Fecha: {datetime.now().strftime('%Y-%m-%d')}")
    y -= 0.35 * inch

    headers = ["Marca", "Modelo", "Producto", "Precio", "Total", "Disp.", "Res.", "Prob.", "Vend."]
    xs = [0.35, 1.05, 1.8, 4.2, 4.9, 5.35, 5.8, 6.25, 6.75]

    c.setFont("Helvetica-Bold", 7.5)

    for x, h in zip(xs, headers):
        c.drawString(x * inch, y, h)

    y -= 0.18 * inch
    c.line(0.35 * inch, y, 7.5 * inch, y)
    y -= 0.15 * inch

    c.setFont("Helvetica", 7.2)

    for _, r in data.iterrows():
        if y < 0.55 * inch:
            c.showPage()
            y = height - 0.65 * inch
            c.setFont("Helvetica", 7.2)

        values = [
            clean_text(r.get("marca"))[:10],
            clean_text(r.get("codigo"))[:12],
            clean_text(r.get("producto"))[:30],
            money(r.get("precio")),
            str(r.get("total_piezas", "")),
            str(r.get("disponibles", "")),
            str(r.get("reservadas", "")),
            str(r.get("probando", "")),
            str(r.get("vendidas", "")),
        ]

        for x, value in zip(xs, values):
            c.drawString(x * inch, y, value)

        y -= 0.15 * inch

    c.save()
    buffer.seek(0)

    return buffer.getvalue()


def create_catalog_pdf_by_model(df, available_only=True):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setTitle("Catálogo Concherie")

    card_w = 3.45 * inch
    card_h = 4.75 * inch
    margin_x = 0.45 * inch
    margin_y = 0.45 * inch
    gap_x = 0.25 * inch
    gap_y = 0.25 * inch

    positions = [
        (margin_x, height - margin_y - card_h),
        (margin_x + card_w + gap_x, height - margin_y - card_h),
        (margin_x, height - margin_y - 2 * card_h - gap_y),
        (margin_x + card_w + gap_x, height - margin_y - 2 * card_h - gap_y),
    ]

    groups = []

    for (marca, codigo, producto), g in df.groupby(
        ["marca", "codigo", "producto"],
        dropna=False,
    ):
        disponibles = g[g["estado"] == "disponible"]

        if available_only and disponibles.empty:
            continue

        photo_url = ""
        with_photo = g[g["foto_url"].astype(str).str.startswith("data:image")]

        if not with_photo.empty:
            photo_url = with_photo.iloc[0]["foto_url"]

        groups.append((marca, codigo, producto, g, disponibles, photo_url))

    if not groups:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(0.7 * inch, height - inch, "No hay modelos disponibles para catálogo.")
        c.save()
        buffer.seek(0)
        return buffer.getvalue()

    for i, (marca, codigo, producto, g, disponibles, photo_url) in enumerate(groups):
        if i > 0 and i % 4 == 0:
            c.showPage()

        x, y = positions[i % 4]

        c.roundRect(x, y, card_w, card_h, 8, stroke=1, fill=0)

        image_box_x = x + 0.18 * inch
        image_box_y = y + 2.35 * inch
        image_box_w = card_w - 0.36 * inch
        image_box_h = 2.25 * inch

        img_bytes = image_bytes_from_data_url(photo_url)

        if img_bytes:
            try:
                img_reader = ImageReader(BytesIO(img_bytes))
                c.drawImage(
                    img_reader,
                    image_box_x,
                    image_box_y,
                    image_box_w,
                    image_box_h,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception:
                c.setFont("Helvetica", 8)
                c.drawString(
                    image_box_x,
                    image_box_y + image_box_h / 2,
                    "Foto no disponible",
                )
        else:
            c.setFont("Helvetica", 8)
            c.drawString(image_box_x, image_box_y + image_box_h / 2, "Sin foto")

        try:
            qr_text = qr_model_text(g.iloc[0])
            qr_bytes = make_qr_png_bytes(qr_text)
            qr_reader = ImageReader(BytesIO(qr_bytes))

            c.drawImage(
                qr_reader,
                x + card_w - 0.95 * inch,
                y + 0.25 * inch,
                0.72 * inch,
                0.72 * inch,
                mask="auto",
            )
        except Exception:
            pass

        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 0.18 * inch, y + 2.05 * inch, clean_text(codigo)[:24])

        c.setFont("Helvetica", 8.5)
        c.drawString(x + 0.18 * inch, y + 1.83 * inch, clean_text(producto)[:42])

        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 0.18 * inch, y + 1.58 * inch, money(g["precio"].iloc[0]))

        c.setFont("Helvetica", 8.5)
        c.drawString(x + 0.18 * inch, y + 1.35 * inch, f"Disponibles: {len(disponibles)}")

        tallas = disponibles["talla"].fillna("").astype(str)
        tallas = tallas[tallas.str.strip() != ""]

        if not tallas.empty:
            talla_text = " | ".join([f"{t}: {n}" for t, n in tallas.value_counts().items()])
            c.drawString(x + 0.18 * inch, y + 1.12 * inch, f"Tallas: {talla_text[:36]}")
        else:
            c.drawString(x + 0.18 * inch, y + 1.12 * inch, "Tallas: sin cargar")

        c.setFont("Helvetica", 6.5)
        c.drawString(x + card_w - 1.05 * inch, y + 0.12 * inch, "QR modelo")

    c.save()
    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# MAIN
# ============================================================

def main():
    load_all_data()

    if "user" not in st.session_state:
        login_page()
        return

    sidebar()

    page = st.session_state.get("page", "home")

    if page == "home":
        home_page()
    elif page == "carga":
        carga_page()
    elif page == "inventario":
        inventario_page()
    elif page == "ventas":
        ventas_page()
    elif page == "clientes":
        clientes_page()
    elif page == "qr":
        qr_page()
    elif page == "scan":
        scan_page()
    elif page == "pieza":
        piece_page()
    elif page == "modelo":
        model_page()
    elif page == "reportes":
        reportes_page()
    else:
        home_page()


if __name__ == "__main__":
    main()