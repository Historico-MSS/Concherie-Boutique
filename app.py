import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO

st.set_page_config(page_title="Concherie Boutique", layout="wide")

# =========================
# USUARIOS
# =========================
USERS = {
    "jc": {"password": "master", "role": "admin"},
    "moira": {"password": "ventas", "role": "operadora"},
    "info": {"password": "precios", "role": "consulta"},
}


# =========================
# HELPERS
# =========================
def set_page(page):
    st.session_state.page = page
    st.rerun()


def money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"


def init_data():
    if "inventario" not in st.session_state:
        st.session_state.inventario = pd.DataFrame(
            columns=[
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
                "fecha_actualizacion",
            ]
        )

    if "clientes" not in st.session_state:
        st.session_state.clientes = pd.DataFrame(
            columns=["cliente", "telefono", "email", "notas"]
        )


def normalize_columns(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    renames = {
        "maison": "marca",
        "brand": "marca",
        "codigo base": "codigo",
        "código": "codigo",
        "codigo": "codigo",
        "producto": "producto",
        "descripcion": "producto",
        "descripción": "producto",
        "cantidad": "cantidad",
        "precio": "precio",
        "precio unitario": "precio",
        "talla": "talla",
    }

    df = df.rename(columns={c: renames.get(c, c) for c in df.columns})
    return df


def create_piece_inventory(uploaded_df):
    df = normalize_columns(uploaded_df)

    required = ["codigo", "producto", "cantidad", "precio"]
    missing = [c for c in required if c not in df.columns]

    if missing:
        raise ValueError(
            "No pude identificar columnas mínimas: código, producto, cantidad y precio."
        )

    if "marca" not in df.columns:
        df["marca"] = ""

    if "talla" not in df.columns:
        df["talla"] = ""

    rows = []

    for _, r in df.iterrows():
        codigo = str(r.get("codigo", "")).strip()
        producto = str(r.get("producto", "")).strip()
        marca = str(r.get("marca", "")).strip()
        talla = str(r.get("talla", "")).strip()

        if not codigo or codigo.lower() == "nan":
            continue

        try:
            cantidad = int(float(r.get("cantidad", 0)))
        except Exception:
            cantidad = 0

        try:
            precio = float(r.get("precio", 0))
        except Exception:
            precio = 0

        for i in range(1, cantidad + 1):
            codigo_unico = f"{codigo}-{i:02d}"

            rows.append(
                {
                    "marca": marca,
                    "codigo": codigo,
                    "codigo_unico": codigo_unico,
                    "producto": producto,
                    "talla": talla,
                    "precio": precio,
                    "estado": "disponible",
                    "ubicacion": "tienda",
                    "cliente": "",
                    "descuento": 0.0,
                    "pagado": 0.0,
                    "foto_url": "",
                    "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )

    return pd.DataFrame(rows)


def available_count(df, codigo):
    return len(df[(df["codigo"] == codigo) & (df["estado"] == "disponible")])


def get_role():
    return st.session_state.get("role", "")


def can_edit():
    return get_role() in ["admin", "operadora"]


def can_admin():
    return get_role() == "admin"


# =========================
# LOGIN
# =========================
def login():
    st.title("Concherie Boutique")
    st.subheader("Acceso")

    user = st.text_input("Usuario")
    pwd = st.text_input("Clave", type="password")

    if st.button("Entrar", use_container_width=True):
        if user in USERS and USERS[user]["password"] == pwd:
            st.session_state.user = user
            st.session_state.role = USERS[user]["role"]
            st.session_state.page = "home"
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos")


# =========================
# SIDEBAR
# =========================
def sidebar():
    st.sidebar.title("Tienda Concha")
    st.sidebar.write(f"Usuario: **{st.session_state.user}**")
    st.sidebar.write(f"Rol: **{st.session_state.role}**")

    if st.sidebar.button("Cerrar sesión"):
        for key in ["user", "role", "page", "selected_piece"]:
            st.session_state.pop(key, None)
        st.rerun()

    st.sidebar.markdown("---")

    if st.sidebar.button("Inicio"):
        set_page("home")

    if st.sidebar.button("Escanear QR"):
        set_page("scan")

    if st.sidebar.button("Inventario"):
        set_page("inventario")

    if can_edit():
        if st.sidebar.button("Carga inicial"):
            set_page("carga")

        if st.sidebar.button("Clientes"):
            set_page("clientes")

        if st.sidebar.button("Ventas / Reservas"):
            set_page("ventas")

    if st.sidebar.button("Reportes"):
        set_page("reportes")


# =========================
# HOME
# =========================
def home_page():
    st.title("Concherie Boutique")
    st.caption("Sistema de inventario, ventas, clientes y consulta por QR")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("🔳 Escanear QR", use_container_width=True):
            set_page("scan")

        if st.button("📦 Inventario", use_container_width=True):
            set_page("inventario")

    with col2:
        if can_edit():
            if st.button("🛍️ Ventas / Reservas", use_container_width=True):
                set_page("ventas")

            if st.button("👥 Clientes", use_container_width=True):
                set_page("clientes")
        else:
            st.info("Usuario de consulta: puedes escanear QR y ver precios.")


# =========================
# CARGA INICIAL
# =========================
def carga_page():
    st.title("Carga inicial / actualización masiva")

    if not can_edit():
        st.warning("No tienes permiso para cargar inventario.")
        return

    st.info(
        "Carga un Excel con columnas: marca/maison, codigo, producto, cantidad y precio. "
        "La app crea una fila por cada pieza."
    )

    uploaded = st.file_uploader("Subir Excel", type=["xlsx"])

    modo = st.radio(
        "¿Cómo guardar?",
        ["Reemplazar inventario completo", "Agregar al inventario existente"],
    )

    if uploaded:
        try:
            raw = pd.read_excel(uploaded)
            preview = normalize_columns(raw)

            st.subheader("Vista previa")
            st.dataframe(preview.head(20), use_container_width=True)

            nuevo = create_piece_inventory(raw)

            st.subheader("Piezas que se crearán")
            st.write(f"Total piezas: **{len(nuevo)}**")
            st.dataframe(nuevo.head(30), use_container_width=True)

            if st.button("Guardar inventario", type="primary"):
                if modo == "Reemplazar inventario completo":
                    st.session_state.inventario = nuevo
                else:
                    st.session_state.inventario = pd.concat(
                        [st.session_state.inventario, nuevo], ignore_index=True
                    )

                st.success("Inventario guardado correctamente.")
                st.rerun()

        except Exception as e:
            st.error(str(e))


# =========================
# INVENTARIO
# =========================
def inventario_page():
    st.title("Inventario")

    df = st.session_state.inventario

    if df.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    col1, col2, col3 = st.columns(3)
    filtro = col1.text_input("Buscar por código / producto / cliente")
    estado = col2.selectbox(
        "Estado",
        ["todos", "disponible", "reservado", "probando", "vendido"],
    )
    codigo = col3.text_input("Código modelo")

    view = df.copy()

    if filtro:
        f = filtro.lower()
        view = view[
            view.apply(
                lambda r: f
                in " ".join([str(v).lower() for v in r.values]),
                axis=1,
            )
        ]

    if estado != "todos":
        view = view[view["estado"] == estado]

    if codigo:
        view = view[view["codigo"].astype(str).str.contains(codigo, case=False, na=False)]

    st.dataframe(view, use_container_width=True)

    st.markdown("---")
    st.subheader("Editar pieza")

    pieza = st.selectbox("Selecciona pieza", df["codigo_unico"].tolist())

    if pieza:
        idx = df[df["codigo_unico"] == pieza].index[0]
        edit_piece_form(idx)


def edit_piece_form(idx):
    df = st.session_state.inventario
    row = df.loc[idx]

    with st.form(f"edit_{idx}"):
        col1, col2 = st.columns(2)

        with col1:
            marca = st.text_input("Marca", value=str(row["marca"]))
            codigo = st.text_input("Código modelo", value=str(row["codigo"]))
            codigo_unico = st.text_input("Código pieza", value=str(row["codigo_unico"]))
            producto = st.text_input("Producto", value=str(row["producto"]))
            talla = st.text_input("Talla", value=str(row.get("talla", "")))

        with col2:
            precio = st.number_input("Precio", value=float(row["precio"]), step=10.0)
            estado = st.selectbox(
                "Estado",
                ["disponible", "reservado", "probando", "vendido"],
                index=["disponible", "reservado", "probando", "vendido"].index(
                    row["estado"]
                    if row["estado"] in ["disponible", "reservado", "probando", "vendido"]
                    else "disponible"
                ),
            )
            ubicacion = st.text_input("Ubicación", value=str(row["ubicacion"]))
            cliente = st.text_input("Cliente", value=str(row["cliente"]))
            descuento = st.number_input(
                "Descuento de esta pieza", value=float(row["descuento"]), step=10.0
            )
            pagado = st.number_input(
                "Pagado por esta pieza", value=float(row["pagado"]), step=10.0
            )

        guardar = st.form_submit_button("Guardar cambios")

    if guardar:
        if not can_edit():
            st.warning("No tienes permiso para editar.")
            return

        df.at[idx, "marca"] = marca
        df.at[idx, "codigo"] = codigo
        df.at[idx, "codigo_unico"] = codigo_unico
        df.at[idx, "producto"] = producto
        df.at[idx, "talla"] = talla
        df.at[idx, "precio"] = precio
        df.at[idx, "estado"] = estado
        df.at[idx, "ubicacion"] = ubicacion
        df.at[idx, "cliente"] = cliente
        df.at[idx, "descuento"] = descuento
        df.at[idx, "pagado"] = pagado
        df.at[idx, "fecha_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        st.success("Pieza actualizada.")
        st.rerun()


# =========================
# ESCANEAR QR
# =========================
def scan_page():
    st.title("Escanear QR / consultar pieza")

    df = st.session_state.inventario

    if df.empty:
        st.warning("Todavía no hay inventario cargado.")
        return

    st.info(
        "Por ahora puedes pegar el código leído del QR. Luego activamos lectura directa con cámara."
    )

    codigo_leido = st.text_input("Código QR")

    if codigo_leido:
        codigo_leido = codigo_leido.strip()

        # Permite QR tipo MARCA|CODIGO
        if "|" in codigo_leido:
            codigo_leido = codigo_leido.split("|")[-1].strip()

        exact = df[df["codigo_unico"].astype(str) == codigo_leido]

        if exact.empty:
            modelo = df[df["codigo"].astype(str) == codigo_leido]
            if not modelo.empty:
                show_model_detail(codigo_leido)
            else:
                st.error("No encontré esa pieza o modelo.")
        else:
            idx = exact.index[0]
            show_piece_detail(idx)


def show_piece_detail(idx):
    df = st.session_state.inventario
    row = df.loc[idx]

    st.subheader(f"{row['codigo_unico']} — {row['producto']}")

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"**Marca:** {row['marca']}")
        st.write(f"**Código modelo:** {row['codigo']}")
        st.write(f"**Código pieza:** {row['codigo_unico']}")
        st.write(f"**Producto:** {row['producto']}")
        st.write(f"**Talla:** {row.get('talla', '') or 'Sin cargar'}")

    with col2:
        precio = float(row["precio"])
        descuento = float(row["descuento"])
        pagado = float(row["pagado"])
        neto = precio - descuento
        saldo = neto - pagado

        st.write(f"**Precio:** {money(precio)}")
        st.write(f"**Descuento:** {money(descuento)}")
        st.write(f"**Neto:** {money(neto)}")
        st.write(f"**Pagado:** {money(pagado)}")
        st.write(f"**Saldo:** {money(saldo)}")
        st.write(f"**Estado:** {row['estado']}")
        st.write(f"**Ubicación:** {row['ubicacion']}")
        st.write(f"**Cliente:** {row['cliente']}")

    if can_edit():
        st.markdown("---")
        edit_piece_form(idx)


def show_model_detail(codigo):
    df = st.session_state.inventario
    model = df[df["codigo"].astype(str) == codigo]

    first = model.iloc[0]
    disponibles = len(model[model["estado"] == "disponible"])

    st.subheader(f"Modelo {codigo}")
    st.write(f"**Marca:** {first['marca']}")
    st.write(f"**Producto:** {first['producto']}")
    st.write(f"**Precio:** {money(first['precio'])}")
    st.write(f"**Disponibles:** {disponibles}")

    tallas = model["talla"].fillna("").astype(str)
    tallas = tallas[tallas != ""]
    if len(tallas) > 0:
        st.write("**Tallas cargadas:**")
        st.write(tallas.value_counts())

    st.dataframe(model, use_container_width=True)


# =========================
# CLIENTES
# =========================
def clientes_page():
    st.title("Clientes")

    if not can_edit():
        st.warning("No tienes permiso para ver clientes.")
        return

    df = st.session_state.inventario

    clientes = sorted([c for c in df["cliente"].dropna().unique().tolist() if str(c).strip()])

    nuevo_cliente = st.text_input("Crear / buscar cliente")

    if nuevo_cliente and nuevo_cliente not in clientes:
        if st.button("Agregar cliente"):
            new_row = pd.DataFrame(
                [{
                    "cliente": nuevo_cliente,
                    "telefono": "",
                    "email": "",
                    "notas": "",
                }]
            )
            st.session_state.clientes = pd.concat(
                [st.session_state.clientes, new_row], ignore_index=True
            )
            st.success("Cliente agregado.")

    cliente = st.selectbox("Cliente", [""] + clientes)

    if cliente:
        show_cliente_profile(cliente)


def show_cliente_profile(cliente):
    df = st.session_state.inventario
    data = df[df["cliente"].astype(str) == cliente].copy()

    st.subheader(f"Perfil de cliente: {cliente}")

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
    total = data["neto"].sum()
    pagado = data["pagado"].sum()
    saldo = data["saldo"].sum()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Subtotal", money(subtotal))
    col2.metric("Descuentos", money(descuentos))
    col3.metric("Total neto", money(total))
    col4.metric("Pagado", money(pagado))
    col5.metric("Saldo", money(saldo))

    pdf_bytes = create_simple_invoice_pdf(cliente, data)

    st.download_button(
        "Descargar nota de cobro PDF",
        data=pdf_bytes,
        file_name=f"nota_cobro_{cliente}.pdf",
        mime="application/pdf",
    )


def create_simple_invoice_pdf(cliente, data):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        y = height - 0.7 * inch

        c.setFont("Helvetica-Bold", 16)
        c.drawString(0.7 * inch, y, "Concherie Boutique")
        y -= 0.3 * inch

        c.setFont("Helvetica", 11)
        c.drawString(0.7 * inch, y, f"Nota de cobro - Cliente: {cliente}")
        y -= 0.25 * inch
        c.drawString(0.7 * inch, y, f"Fecha: {datetime.now().strftime('%Y-%m-%d')}")
        y -= 0.45 * inch

        c.setFont("Helvetica-Bold", 9)
        headers = ["Pieza", "Producto", "Precio", "Desc.", "Neto", "Pagado", "Saldo"]
        xs = [0.7, 1.6, 3.4, 4.2, 4.9, 5.6, 6.4]

        for x, h in zip(xs, headers):
            c.drawString(x * inch, y, h)

        y -= 0.2 * inch
        c.setFont("Helvetica", 8)

        for _, r in data.iterrows():
            if y < 0.8 * inch:
                c.showPage()
                y = height - 0.7 * inch
                c.setFont("Helvetica", 8)

            precio = float(r["precio"])
            descuento = float(r["descuento"])
            neto = precio - descuento
            pagado = float(r["pagado"])
            saldo = neto - pagado

            values = [
                str(r["codigo_unico"]),
                str(r["producto"])[:22],
                money(precio),
                money(descuento),
                money(neto),
                money(pagado),
                money(saldo),
            ]

            for x, v in zip(xs, values):
                c.drawString(x * inch, y, v)

            y -= 0.18 * inch

        y -= 0.25 * inch
        subtotal = data["precio"].astype(float).sum()
        descuentos = data["descuento"].astype(float).sum()
        total = subtotal - descuentos
        pagado_total = data["pagado"].astype(float).sum()
        saldo_total = total - pagado_total

        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.7 * inch, y, f"Subtotal: {money(subtotal)}")
        y -= 0.2 * inch
        c.drawString(0.7 * inch, y, f"Descuentos: {money(descuentos)}")
        y -= 0.2 * inch
        c.drawString(0.7 * inch, y, f"Total neto: {money(total)}")
        y -= 0.2 * inch
        c.drawString(0.7 * inch, y, f"Pagado: {money(pagado_total)}")
        y -= 0.2 * inch
        c.drawString(0.7 * inch, y, f"Saldo pendiente: {money(saldo_total)}")

        c.save()
        buffer.seek(0)
        return buffer.getvalue()

    except Exception:
        txt = f"Nota de cobro - {cliente}\n\n"
        for _, r in data.iterrows():
            txt += f"{r['codigo_unico']} - {r['producto']} - {money(r['precio'])}\n"
        return txt.encode("utf-8")


# =========================
# VENTAS / RESERVAS
# =========================
def ventas_page():
    st.title("Ventas / Reservas")

    if not can_edit():
        st.warning("No tienes permiso para ventas.")
        return

    df = st.session_state.inventario

    if df.empty:
        st.warning("No hay inventario.")
        return

    pieza = st.selectbox("Pieza", df["codigo_unico"].tolist())

    if pieza:
        idx = df[df["codigo_unico"] == pieza].index[0]
        row = df.loc[idx]

        st.write(f"**Producto:** {row['producto']}")
        st.write(f"**Precio:** {money(row['precio'])}")

        with st.form("venta_form"):
            cliente = st.text_input("Cliente", value=str(row["cliente"]))
            estado = st.selectbox(
                "Acción / Estado",
                ["reservado", "probando", "vendido", "disponible"],
            )
            ubicacion = st.text_input("Ubicación", value=str(row["ubicacion"]))
            descuento = st.number_input(
                "Descuento de esta pieza",
                value=float(row["descuento"]),
                step=10.0,
            )
            pagado = st.number_input(
                "Pagado por esta pieza",
                value=float(row["pagado"]),
                step=10.0,
            )

            guardar = st.form_submit_button("Guardar")

        if guardar:
            df.at[idx, "cliente"] = cliente
            df.at[idx, "estado"] = estado
            df.at[idx, "ubicacion"] = ubicacion
            df.at[idx, "descuento"] = descuento
            df.at[idx, "pagado"] = pagado
            df.at[idx, "fecha_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M")

            st.success("Registro actualizado.")
            st.rerun()


# =========================
# REPORTES
# =========================
def reportes_page():
    st.title("Reportes")

    df = st.session_state.inventario

    if df.empty:
        st.warning("No hay datos.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Total piezas", len(df))
    col2.metric("Disponibles", len(df[df["estado"] == "disponible"]))
    col3.metric("Vendidas", len(df[df["estado"] == "vendido"]))

    st.subheader("Resumen por modelo")
    resumen = (
        df.groupby(["marca", "codigo", "producto"], dropna=False)
        .agg(
            piezas=("codigo_unico", "count"),
            disponibles=("estado", lambda x: (x == "disponible").sum()),
            vendidas=("estado", lambda x: (x == "vendido").sum()),
            precio=("precio", "first"),
        )
        .reset_index()
    )
    st.dataframe(resumen, use_container_width=True)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="inventario", index=False)
        resumen.to_excel(writer, sheet_name="resumen_modelos", index=False)

    st.download_button(
        "Descargar respaldo Excel",
        data=output.getvalue(),
        file_name="respaldo_concherie.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# =========================
# MAIN
# =========================
def main():
    init_data()

    if "user" not in st.session_state:
        login()
        return

    sidebar()

    page = st.session_state.get("page", "home")

    if page == "home":
        home_page()
    elif page == "carga":
        carga_page()
    elif page == "inventario":
        inventario_page()
    elif page == "scan":
        scan_page()
    elif page == "clientes":
        clientes_page()
    elif page == "ventas":
        ventas_page()
    elif page == "reportes":
        reportes_page()
    else:
        home_page()


if __name__ == "__main__":
    main()