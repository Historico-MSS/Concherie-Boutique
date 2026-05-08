import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

st.set_page_config(page_title="Concherie", layout="wide")

st.title("Concherie")
st.subheader("Generador de notas de venta")

# =========================================================
# INVENTARIO DEMO
# Reemplazar luego por Google Sheets / Supabase
# =========================================================

inventario_demo = pd.DataFrame([
    {
        "codigo": 1001,
        "marca": "Zimmermann",
        "descripcion": "Vestido floral",
        "precio": 450,
        "estado": "Disponible"
    },
    {
        "codigo": 1002,
        "marca": "Missoni",
        "descripcion": "Top tejido",
        "precio": 320,
        "estado": "Disponible"
    },
    {
        "codigo": 1003,
        "marca": "Pucci",
        "descripcion": "Pantalón estampado",
        "precio": 390,
        "estado": "Disponible"
    },
    {
        "codigo": 1004,
        "marca": "Zimmermann",
        "descripcion": "Blusa seda",
        "precio": 280,
        "estado": "Reservada"
    },
])

# =========================================================
# FUNCIONES
# =========================================================


def buscar_pieza(codigo):
    resultado = inventario_demo[inventario_demo["codigo"] == codigo]

    if resultado.empty:
        return None

    return resultado.iloc[0]



def generar_pdf(cliente, fecha_emision, vendidos_df, wishlist_df, pagos_df, total_pagado):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    styles = getSampleStyleSheet()
    elementos = []

    # =====================================================
    # HEADER
    # =====================================================

    titulo = Paragraph(f"<b>CONCHERIE</b>", styles['Title'])
    elementos.append(titulo)
    elementos.append(Spacer(1, 0.2 * inch))

    cliente_text = Paragraph(f"<b>Cliente:</b> {cliente}", styles['BodyText'])
    fecha_text = Paragraph(f"<b>Fecha:</b> {fecha_emision}", styles['BodyText'])

    elementos.append(cliente_text)
    elementos.append(fecha_text)
    elementos.append(Spacer(1, 0.25 * inch))

    # =====================================================
    # PIEZAS VENDIDAS
    # =====================================================

    elementos.append(Paragraph("<b>Piezas vendidas</b>", styles['Heading2']))
    elementos.append(Spacer(1, 0.12 * inch))

    mostrar_descuento = vendidos_df["descuento_pct"].fillna(0).sum() > 0

    if mostrar_descuento:
        data = [[
            "Fecha",
            "Código",
            "Marca",
            "Descripción",
            "Precio",
            "Desc.",
            "Total"
        ]]
    else:
        data = [[
            "Fecha",
            "Código",
            "Marca",
            "Descripción",
            "Total"
        ]]

    subtotal = 0
    descuento_total = 0

    for _, row in vendidos_df.iterrows():
        subtotal += row["precio"]
        descuento_total += row["descuento_monto"]

        if mostrar_descuento:
            data.append([
                row["fecha"],
                str(row["codigo"]),
                row["marca"],
                row["descripcion"],
                f"${row['precio']:,.2f}",
                row["descuento_display"],
                f"${row['total']:,.2f}"
            ])
        else:
            data.append([
                row["fecha"],
                str(row["codigo"]),
                row["marca"],
                row["descripcion"],
                f"${row['total']:,.2f}"
            ])

    tabla = Table(data, repeatRows=1)

    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.black),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
    ]))

    elementos.append(tabla)
    elementos.append(Spacer(1, 0.25 * inch))

    total_final = subtotal - descuento_total
    saldo_pendiente = total_final - total_pagado

    resumen = [
        ["Subtotal", f"${subtotal:,.2f}"],
    ]

    if descuento_total > 0:
        resumen.append(["Descuento total", f"-${descuento_total:,.2f}"])

    resumen.extend([
        ["Total vendido", f"${total_final:,.2f}"],
        ["Pagado a la fecha", f"${total_pagado:,.2f}"],
        ["Saldo pendiente", f"${saldo_pendiente:,.2f}"]
    ])

    tabla_resumen = Table(resumen, colWidths=[3 * inch, 2 * inch])

    tabla_resumen.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, -1), colors.beige),
    ]))

    elementos.append(tabla_resumen)
    elementos.append(Spacer(1, 0.35 * inch))

    # =====================================================
    # PAGOS
    # =====================================================

    if not pagos_df.empty:
        elementos.append(Paragraph("<b>Pagos registrados</b>", styles['Heading2']))
        elementos.append(Spacer(1, 0.12 * inch))

        pagos_data = [["Fecha", "Forma de pago", "Monto"]]

        for _, row in pagos_df.iterrows():
            pagos_data.append([
                row["fecha_pago"],
                row["forma_pago"],
                f"${row['monto_pago']:,.2f}"
            ])

        pagos_table = Table(pagos_data)

        pagos_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))

        elementos.append(pagos_table)
        elementos.append(Spacer(1, 0.35 * inch))

    # =====================================================
    # WISH LIST
    # =====================================================

    if not wishlist_df.empty:
        elementos.append(Paragraph("<b>Wish List / Piezas reservadas</b>", styles['Heading2']))
        elementos.append(Spacer(1, 0.12 * inch))

        wishlist_data = [[
            "Fecha",
            "Código",
            "Marca",
            "Descripción",
            "Precio"
        ]]

        for _, row in wishlist_df.iterrows():
            wishlist_data.append([
                row["fecha"],
                str(row["codigo"]),
                row["marca"],
                row["descripcion"],
                f"${row['precio']:,.2f}"
            ])

        wishlist_table = Table(wishlist_data)

        wishlist_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D7C7A3')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F7F1E7')),
        ]))

        elementos.append(wishlist_table)
        elementos.append(Spacer(1, 0.18 * inch))

        nota = Paragraph(
            "Las piezas incluidas en el wish list se mantienen temporalmente reservadas para la cliente. "
            "Agradecemos confirmar la decisión dentro de un tiempo prudencial, para que en caso de no continuar "
            "con la compra puedan volver a estar disponibles para la venta.",
            styles['Italic']
        )

        elementos.append(nota)

    doc.build(elementos)

    pdf = buffer.getvalue()
    buffer.close()

    return pdf


# =========================================================
# UI
# =========================================================

st.markdown("---")

st.header("Subir Excel de cliente")

st.info(
    "El Excel puede contener piezas vendidas, wish list y pagos registrados. "
    "La app generará automáticamente una nota de venta en PDF."
)

archivo = st.file_uploader(
    "Excel cliente",
    type=["xlsx", "xls"]
)

# =========================================================
# FORMATO ESPERADO
# =========================================================

with st.expander("Formato esperado del Excel"):
    st.markdown("""
### Hoja: VENDIDOS

| fecha | codigo | descuento_pct |
|---|---|---|
| 07/05/2026 | 1001 | |
| 07/05/2026 | 1002 | 10 |

---

### Hoja: WISHLIST

| fecha | codigo |
|---|---|
| 07/05/2026 | 1004 |

---

### Hoja: PAGOS

| fecha_pago | forma_pago | monto_pago |
|---|---|---|
| 07/05/2026 | Zelle | 200 |

---

### Hoja: CLIENTE

| cliente |
|---|
| María Pérez |
""")

# =========================================================
# PROCESAMIENTO
# =========================================================

if archivo:
    try:
        cliente_df = pd.read_excel(archivo, sheet_name="CLIENTE")
        vendidos_excel = pd.read_excel(archivo, sheet_name="VENDIDOS")

        try:
            wishlist_excel = pd.read_excel(archivo, sheet_name="WISHLIST")
        except:
            wishlist_excel = pd.DataFrame()

        try:
            pagos_excel = pd.read_excel(archivo, sheet_name="PAGOS")
        except:
            pagos_excel = pd.DataFrame()

        cliente = cliente_df.iloc[0]["cliente"]

        vendidos_procesados = []
        wishlist_procesados = []

        # =================================================
        # VENDIDOS
        # =================================================

        for _, row in vendidos_excel.iterrows():
            pieza = buscar_pieza(int(row["codigo"]))

            if pieza is None:
                st.warning(f"Código no encontrado: {row['codigo']}")
                continue

            descuento_pct = row.get("descuento_pct", 0)

            if pd.isna(descuento_pct):
                descuento_pct = 0

            descuento_monto = pieza["precio"] * (descuento_pct / 100)
            total = pieza["precio"] - descuento_monto

            vendidos_procesados.append({
                "fecha": row["fecha"],
                "codigo": pieza["codigo"],
                "marca": pieza["marca"],
                "descripcion": pieza["descripcion"],
                "precio": pieza["precio"],
                "descuento_pct": descuento_pct,
                "descuento_monto": descuento_monto,
                "descuento_display": (
                    f"{descuento_pct:.0f}% / ${descuento_monto:,.2f}"
                    if descuento_pct > 0 else ""
                ),
                "total": total
            })

        vendidos_df = pd.DataFrame(vendidos_procesados)

        # =================================================
        # WISHLIST
        # =================================================

        if not wishlist_excel.empty:
            for _, row in wishlist_excel.iterrows():
                pieza = buscar_pieza(int(row["codigo"]))

                if pieza is None:
                    continue

                wishlist_procesados.append({
                    "fecha": row["fecha"],
                    "codigo": pieza["codigo"],
                    "marca": pieza["marca"],
                    "descripcion": pieza["descripcion"],
                    "precio": pieza["precio"]
                })

        wishlist_df = pd.DataFrame(wishlist_procesados)

        # =================================================
        # PAGOS
        # =================================================

        if pagos_excel.empty:
            total_pagado = 0
        else:
            total_pagado = pagos_excel["monto_pago"].sum()

        fecha_emision = datetime.now().strftime("%d/%m/%Y")

        st.success("Nota procesada correctamente")

        st.subheader(cliente)

        if not vendidos_df.empty:
            st.markdown("### Piezas vendidas")
            st.dataframe(vendidos_df)

        if not wishlist_df.empty:
            st.markdown("### Wish list")
            st.dataframe(wishlist_df)

        if not pagos_excel.empty:
            st.markdown("### Pagos")
            st.dataframe(pagos_excel)

        pdf = generar_pdf(
            cliente,
            fecha_emision,
            vendidos_df,
            wishlist_df,
            pagos_excel,
            total_pagado
        )

        st.download_button(
            label="Descargar PDF",
            data=pdf,
            file_name=f"nota_{cliente.replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
