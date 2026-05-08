import base64
import io
import os
import re
import unicodedata
from datetime import datetime
from typing import Optional, Tuple

import cv2
import numpy as np
import pandas as pd
import qrcode
import requests
import streamlit as st
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from streamlit_gsheets import GSheetsConnection

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Concherie", page_icon="🏷️", layout="wide")

USERS = {
    "jc": {"password": "master", "role": "admin"},
    "ventas": {"password": "moira", "role": "ventas"},
    "info": {"password": "precio", "role": "info"},
}

INVENTORY_SHEET = "inventario"
REQUIRED_COLUMNS = [
    "numero",
    "codigo_interno",
    "marca",
    "codigo",
    "producto",
    "color",
    "talla",
    "precio",
    "foto_url",
    "fecha_actualizacion",
]

# ============================================================
# HELPERS
# ============================================================
def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalize_col_name(x):
    text = clean_text(x).lower().replace(" ", "_")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def slugify(text):
    text = clean_text(text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "archivo"


def normalize_numero(value):
    s = clean_text(value)
    if not s:
        return ""
    try:
        if re.fullmatch(r"\d+\.0", s):
            s = str(int(float(s)))
        elif re.fullmatch(r"\d+(\.\d+)?", s):
            # 66 -> 066, 1 -> 001, 066 -> 066
            s = str(int(float(s)))
    except Exception:
        pass
    if s.isdigit():
        return s.zfill(3)
    m = re.search(r"(\d{1,3})$", s)
    if m:
        return m.group(1).zfill(3)
    return s


def display_talla(talla):
    t = clean_text(talla)
    if not t or t.upper() in ["T0", "0", "SIN TALLA", "NAN", "NONE"]:
        return "Talla Única"
    return t


def money(v):
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "$0.00"


def parse_price(v):
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def ensure_inventory_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    df = df.copy()
    df.columns = [normalize_col_name(c) for c in df.columns]
    aliases = {
        "marca_maison": "marca",
        "maison": "marca",
        "brand": "marca",
        "designer": "marca",
        "disenador": "marca",
        "cod": "codigo",
        "código": "codigo",
        "codigo_modelo": "codigo",
        "modelo": "codigo",
        "articulo": "producto",
        "artículo": "producto",
        "descripcion": "producto",
        "descripción": "producto",
        "colour": "color",
        "colour_name": "color",
        "size": "talla",
        "tallas": "talla",
        "price": "precio",
        "photo": "foto_url",
    }
    df = df.rename(columns={c: aliases.get(c, c) for c in df.columns})

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col != "precio" else 0.0

    df["numero"] = df.apply(lambda r: normalize_numero(r.get("numero")) or normalize_numero(r.get("codigo_interno")), axis=1)
    df["marca"] = df["marca"].apply(clean_text).str.upper()
    df["codigo"] = df["codigo"].apply(clean_text).str.upper()
    df["producto"] = df["producto"].apply(clean_text).str.upper()
    df["color"] = df["color"].apply(clean_text).str.upper()
    df["talla"] = df["talla"].apply(lambda x: display_talla(x))
    df["precio"] = df["precio"].apply(parse_price)
    df["foto_url"] = df["foto_url"].apply(clean_text)

    def make_internal(r):
        existing = clean_text(r.get("codigo_interno"))
        if existing and existing.lower() not in ["nan", "none"]:
            # Normalize ending only if needed
            parts = existing.split("-")
            if parts:
                parts[-1] = normalize_numero(parts[-1])
                return "-".join(parts).upper()
        codigo = clean_text(r.get("codigo")).upper() or "SINCODIGO"
        color = clean_text(r.get("color")).upper() or "SINCOLOR"
        talla = clean_text(r.get("talla"))
        talla_code = "T0" if talla == "Talla Única" else talla.upper()
        numero = normalize_numero(r.get("numero"))
        return f"{codigo}-{color}-{talla_code}-{numero}".upper()

    df["codigo_interno"] = df.apply(make_internal, axis=1)
    df["fecha_actualizacion"] = df["fecha_actualizacion"].apply(clean_text)

    return df[REQUIRED_COLUMNS].sort_values("codigo_interno").reset_index(drop=True)


# ============================================================
# DATA
# ============================================================
def get_gsheets_conn():
    return st.connection("gsheets", type=GSheetsConnection)


def load_inventory() -> pd.DataFrame:
    try:
        conn = get_gsheets_conn()
        df = conn.read(worksheet=INVENTORY_SHEET, ttl=0)
        st.session_state.data_status = "Google Sheets"
        return ensure_inventory_schema(df)
    except Exception as e:
        st.session_state.data_status = f"Local / error Sheets: {str(e)[:120]}"
        if "inventario" not in st.session_state:
            st.session_state.inventario = pd.DataFrame(columns=REQUIRED_COLUMNS)
        return ensure_inventory_schema(st.session_state.inventario)


def save_inventory(df: pd.DataFrame):
    df = ensure_inventory_schema(df)
    st.session_state.inventario = df
    try:
        conn = get_gsheets_conn()
        conn.update(worksheet=INVENTORY_SHEET, data=df)
        st.session_state.data_status = "Google Sheets"
        return True, "Guardado en Google Sheets"
    except Exception as e:
        st.session_state.data_status = f"Local / error Sheets: {str(e)[:120]}"
        return False, str(e)


# ============================================================
# SUPABASE STORAGE
# ============================================================
def supabase_configured():
    return "supabase" in st.secrets and all(k in st.secrets["supabase"] for k in ["url", "key", "bucket"])


def supabase_upload_bytes(data: bytes, path: str, content_type: str) -> Optional[str]:
    if not supabase_configured():
        return None
    url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["key"]
    bucket = st.secrets["supabase"]["bucket"]
    api_url = f"{url}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    r = requests.post(api_url, headers=headers, data=data, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase error {r.status_code}: {r.text[:300]}")
    return f"{url}/storage/v1/object/public/{bucket}/{path}"


def compress_image(uploaded_file) -> Tuple[bytes, str]:
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((1400, 1400))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue(), "image/jpeg"


def upload_product_photo(uploaded_file, numero, producto):
    data, content_type = compress_image(uploaded_file)
    filename = f"{normalize_numero(numero)}_{slugify(producto)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = f"productos/{filename}"
    return supabase_upload_bytes(data, path, content_type)


# ============================================================
# QR / PDF
# ============================================================
def make_qr_image(payload: str, box_size=10):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def pil_to_reader(img):
    b = io.BytesIO()
    img.save(b, format="PNG")
    b.seek(0)
    return ImageReader(b)


def build_labels_pdf(df: pd.DataFrame) -> bytes:
    df = ensure_inventory_schema(df).sort_values("codigo_interno")
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    label_w = 8 * cm
    label_h = 5 * cm
    margin_x = (page_w - 2 * label_w) / 2
    top_margin = 0.8 * cm
    rows = 5
    cols = 2

    def draw_grid():
        c.setDash(2, 3)
        c.setStrokeColorRGB(0.45, 0.45, 0.45)
        c.setLineWidth(0.6)
        mid_x = margin_x + label_w
        c.line(mid_x, top_margin, mid_x, page_h - top_margin)
        for r in range(1, rows):
            y = page_h - top_margin - r * label_h
            c.line(margin_x, y, margin_x + 2 * label_w, y)
        c.setDash()

    draw_grid()
    idx = 0
    for _, row in df.iterrows():
        pos = idx % (rows * cols)
        if idx > 0 and pos == 0:
            c.showPage()
            draw_grid()
        col = pos % cols
        rr = pos // cols
        x = margin_x + col * label_w
        y_top = page_h - top_margin - rr * label_h
        y = y_top - label_h

        numero = normalize_numero(row["numero"])
        producto = clean_text(row["producto"])
        color = clean_text(row["color"])
        talla = display_talla(row["talla"])
        interno = clean_text(row["codigo_interno"])

        qr_img = make_qr_image(numero, box_size=8)
        qr_size = 3.55 * cm
        qr_x = x + 0.28 * cm
        qr_y = y + 0.65 * cm
        c.drawImage(pil_to_reader(qr_img), qr_x, qr_y, width=qr_size, height=qr_size, mask="auto")

        tx = x + 4.15 * cm
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(tx, y_top - 0.85 * cm, numero)

        c.setFont("Helvetica-Bold", 7.2)
        text_obj = c.beginText(tx, y_top - 1.35 * cm)
        text_obj.setLeading(8)
        # wrap product in max 2 lines
        prod_words = producto.split()
        lines = []
        line = ""
        for w in prod_words:
            test = f"{line} {w}".strip()
            if len(test) > 20 and line:
                lines.append(line)
                line = w
            else:
                line = test
        if line:
            lines.append(line)
        for l in lines[:2]:
            text_obj.textLine(l)
        c.drawText(text_obj)

        c.setFont("Helvetica", 7.5)
        c.drawString(tx, y_top - 2.25 * cm, color[:22])
        c.drawString(tx, y_top - 2.65 * cm, talla[:22])
        c.setFont("Helvetica", 5.2)
        c.drawRightString(x + label_w - 0.25 * cm, y + 0.25 * cm, interno[:38])
        idx += 1

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def build_catalog_pdf(df: pd.DataFrame, include_price=True, talla_filter="", group_by_brand=True) -> bytes:
    df = ensure_inventory_schema(df)
    if talla_filter.strip():
        tf = talla_filter.strip().upper()
        df = df[df["talla"].astype(str).str.upper().str.contains(tf, na=False)]

    if "marca" not in df.columns:
        df["marca"] = ""

    df["marca"] = df["marca"].apply(clean_text).replace("", "SIN MARCA")
    df = df.sort_values(["marca", "producto", "color", "talla", "numero"])

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    margin = 1.3 * cm
    card_w = (w - 2 * margin - 0.7 * cm) / 2
    card_h = 6.7 * cm
    gap = 0.7 * cm

    def header(subtitle="Catálogo disponible"):
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(margin, h - margin, "CONCHERIE BOUTIQUE")
        c.setFont("Helvetica", 10)
        c.drawString(margin, h - margin - 0.45 * cm, subtitle)

    def brand_header(brand_name):
        nonlocal y, col
        if col != 0:
            col = 0
            y -= card_h + 0.55 * cm
        if y < margin + card_h + 1.2 * cm:
            c.showPage()
            header()
            y = h - margin - 1.3 * cm
        c.setFillColorRGB(0.10, 0.10, 0.10)
        c.roundRect(margin, y - 0.65 * cm, w - 2 * margin, 0.62 * cm, 6, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin + 0.25 * cm, y - 0.43 * cm, clean_text(brand_name))
        y -= 1.05 * cm

    header()
    y = h - margin - 1.3 * cm
    col = 0
    current_brand = None

    for _, row in df.iterrows():
        brand = clean_text(row.get("marca")) or "SIN MARCA"

        if group_by_brand and brand != current_brand:
            current_brand = brand
            brand_header(brand)

        if y - card_h < margin:
            c.showPage()
            header()
            y = h - margin - 1.3 * cm
            col = 0
            if group_by_brand:
                brand_header(brand)

        x = margin + col * (card_w + gap)

        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(x, y - card_h, card_w, card_h, 8, stroke=1, fill=0)

        # Brand pill inside card
        c.setFillColorRGB(0.93, 0.90, 0.84)
        c.roundRect(x + 0.25 * cm, y - 0.58 * cm, card_w - 0.5 * cm, 0.38 * cm, 5, fill=1, stroke=0)
        c.setFillColorRGB(0.16, 0.16, 0.16)
        c.setFont("Helvetica-Bold", 7.3)
        c.drawString(x + 0.42 * cm, y - 0.45 * cm, brand[:32])

        # photo area
        photo_url = clean_text(row.get("foto_url"))
        if photo_url.startswith("http"):
            try:
                img_data = requests.get(photo_url, timeout=10).content
                img = Image.open(io.BytesIO(img_data)).convert("RGB")
                img.thumbnail((500, 500))
                c.drawImage(
                    pil_to_reader(img),
                    x + 0.3 * cm,
                    y - 4.0 * cm,
                    width=2.9 * cm,
                    height=2.9 * cm,
                    preserveAspectRatio=True,
                    anchor="c",
                )
            except Exception:
                c.setFillColorRGB(0.95, 0.95, 0.95)
                c.rect(x + 0.3 * cm, y - 4.0 * cm, 2.9 * cm, 2.9 * cm, fill=1, stroke=0)
        else:
            c.setFillColorRGB(0.96, 0.96, 0.96)
            c.rect(x + 0.3 * cm, y - 4.0 * cm, 2.9 * cm, 2.9 * cm, fill=1, stroke=0)
            c.setFillColorRGB(0.45, 0.45, 0.45)
            c.setFont("Helvetica", 8)
            c.drawCentredString(x + 1.75 * cm, y - 2.55 * cm, "Sin foto")

        tx = x + 3.55 * cm
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 9.2)
        c.drawString(tx, y - 1.03 * cm, f"{normalize_numero(row['numero'])} · {clean_text(row['producto'])[:24]}")
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(tx, y - 1.52 * cm, f"{clean_text(row['color'])} · {display_talla(row['talla'])}")
        c.setFont("Helvetica", 7)
        c.drawString(tx, y - 2.02 * cm, clean_text(row["codigo_interno"])[:30])

        if include_price:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(tx, y - 2.88 * cm, money(row["precio"]))

        col += 1
        if col == 2:
            col = 0
            y -= card_h + 0.55 * cm

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# NOTAS DE VENTA / INVOICE
# ============================================================
def safe_date(value):
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    try:
        parsed = pd.to_datetime(value)
        if not pd.isna(parsed):
            return parsed.strftime("%d/%m/%Y")
    except Exception:
        pass
    return clean_text(value)


def parse_discount(value):
    if pd.isna(value) or clean_text(value) == "":
        return 0.0
    if isinstance(value, str):
        s = value.replace("%", "").replace(",", ".").strip()
        try:
            return float(s)
        except Exception:
            return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def find_product_by_sale_code(df, value):
    numero = normalize_numero(value)
    if not numero:
        return None
    matches = df[df["numero"].astype(str).apply(normalize_numero) == numero]
    if matches.empty:
        return None
    return matches.iloc[0]


def normalize_header_label(value):
    label = clean_text(value).lower()
    label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    label = label.replace(" ", "_")
    return label


def read_single_sheet_invoice(uploaded_file, sheet_name):
    """
    Lee un formato visual sencillo, como:
        Cliente
        Josefina Fernandez

                  vendido | descuento | apartado
                      78  |    15     |    99

    También intenta leer pagos si encuentra encabezados tipo:
        fecha_pago | forma_pago | monto_pago
    """
    raw = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=None)

    cliente = "Cliente"

    # Buscar celda que diga Cliente. Toma el valor debajo o a la derecha.
    for r in range(raw.shape[0]):
        for c in range(raw.shape[1]):
            if normalize_header_label(raw.iat[r, c]) == "cliente":
                below = raw.iat[r + 1, c] if r + 1 < raw.shape[0] else ""
                right = raw.iat[r, c + 1] if c + 1 < raw.shape[1] else ""
                cliente = clean_text(below) or clean_text(right) or "Cliente"
                break

    vendidos_rows = []
    wishlist_rows = []
    pagos_rows = []

    # Buscar encabezado de piezas.
    piezas_header_row = None
    header_map = {}
    for r in range(raw.shape[0]):
        labels = [normalize_header_label(raw.iat[r, c]) for c in range(raw.shape[1])]
        if "vendido" in labels or "apartado" in labels or "wishlist" in labels or "wish_list" in labels:
            piezas_header_row = r
            for c, label in enumerate(labels):
                if label in ["vendido", "vendidos", "codigo_vendido", "pieza_vendida"]:
                    header_map["vendido"] = c
                elif label in ["descuento", "descuento_pct", "desc"]:
                    header_map["descuento_pct"] = c
                elif label in ["apartado", "wishlist", "wish_list", "reservado", "reservada"]:
                    header_map["apartado"] = c
                elif label in ["fecha", "fecha_venta"]:
                    header_map["fecha"] = c
            break

    if piezas_header_row is not None:
        for r in range(piezas_header_row + 1, raw.shape[0]):
            vendido = raw.iat[r, header_map["vendido"]] if "vendido" in header_map else ""
            apartado = raw.iat[r, header_map["apartado"]] if "apartado" in header_map else ""

            # Si la fila ya parece ser otra sección, paramos.
            row_labels = [normalize_header_label(raw.iat[r, c]) for c in range(raw.shape[1])]
            if any(x in row_labels for x in ["fecha_pago", "forma_pago", "monto_pago", "monto"]):
                break

            descuento = raw.iat[r, header_map["descuento_pct"]] if "descuento_pct" in header_map else ""
            fecha = raw.iat[r, header_map["fecha"]] if "fecha" in header_map else datetime.now().strftime("%d/%m/%Y")

            if clean_text(vendido):
                vendidos_rows.append({
                    "fecha": fecha,
                    "codigo": vendido,
                    "descuento_pct": descuento,
                })

            if clean_text(apartado):
                wishlist_rows.append({
                    "fecha": fecha,
                    "codigo": apartado,
                })

    # Buscar encabezado de pagos, si existe en la misma hoja.
    pagos_header_row = None
    pagos_map = {}
    for r in range(raw.shape[0]):
        labels = [normalize_header_label(raw.iat[r, c]) for c in range(raw.shape[1])]
        has_pago_header = (
            "fecha_pago" in labels
            or "forma_pago" in labels
            or "monto_pago" in labels
            or ("monto" in labels and ("forma" in labels or "forma_de_pago" in labels))
        )
        if has_pago_header:
            pagos_header_row = r
            for c, label in enumerate(labels):
                if label in ["fecha_pago", "fecha"]:
                    pagos_map["fecha_pago"] = c
                elif label in ["forma_pago", "forma_de_pago", "forma"]:
                    pagos_map["forma_pago"] = c
                elif label in ["monto_pago", "monto", "abono", "pagado"]:
                    pagos_map["monto_pago"] = c
            break

    if pagos_header_row is not None:
        for r in range(pagos_header_row + 1, raw.shape[0]):
            monto = raw.iat[r, pagos_map["monto_pago"]] if "monto_pago" in pagos_map else ""
            if not clean_text(monto):
                continue
            pagos_rows.append({
                "fecha_pago": raw.iat[r, pagos_map["fecha_pago"]] if "fecha_pago" in pagos_map else "",
                "forma_pago": raw.iat[r, pagos_map["forma_pago"]] if "forma_pago" in pagos_map else "",
                "monto_pago": monto,
            })

    return (
        cliente,
        pd.DataFrame(vendidos_rows),
        pd.DataFrame(wishlist_rows),
        pd.DataFrame(pagos_rows),
    )


def read_invoice_excel(uploaded_file):
    xls = pd.ExcelFile(uploaded_file)

    # Formato antiguo por hojas separadas
    if any(name in xls.sheet_names for name in ["CLIENTE", "VENDIDOS", "WISHLIST", "PAGOS"]):
        def read_sheet(name):
            if name not in xls.sheet_names:
                return pd.DataFrame()
            data = pd.read_excel(uploaded_file, sheet_name=name)
            data.columns = [normalize_col_name(c) for c in data.columns]
            return data

        cliente_df = read_sheet("CLIENTE")
        vendidos_df = read_sheet("VENDIDOS")
        wishlist_df = read_sheet("WISHLIST")
        pagos_df = read_sheet("PAGOS")

        if cliente_df.empty or "cliente" not in cliente_df.columns:
            cliente = "Cliente"
        else:
            cliente = clean_text(cliente_df.iloc[0]["cliente"]) or "Cliente"

        return cliente, vendidos_df, wishlist_df, pagos_df

    # Formato nuevo visual en una sola hoja
    return read_single_sheet_invoice(uploaded_file, xls.sheet_names[0])


def process_invoice_data(df_inventory, vendidos_excel, wishlist_excel, pagos_excel):
    vendidos = []
    wishlist = []
    warnings = []

    if not vendidos_excel.empty:
        for _, row in vendidos_excel.iterrows():
            codigo = row.get("codigo", "")
            if clean_text(codigo) == "":
                continue

            pieza = find_product_by_sale_code(df_inventory, codigo)
            if pieza is None:
                warnings.append(f"No encontré la pieza vendida con código {codigo}.")
                continue

            descuento_pct = parse_discount(row.get("descuento_pct", 0))
            precio = parse_price(pieza["precio"])
            descuento_monto = precio * descuento_pct / 100
            total = precio - descuento_monto

            vendidos.append({
                "fecha": safe_date(row.get("fecha", "")),
                "codigo": normalize_numero(pieza["numero"]),
                "codigo_interno": clean_text(pieza["codigo_interno"]),
                "marca": clean_text(pieza.get("marca", "")),
                "producto": clean_text(pieza["producto"]),
                "color": clean_text(pieza["color"]),
                "talla": display_talla(pieza["talla"]),
                "precio": precio,
                "descuento_pct": descuento_pct,
                "descuento_monto": descuento_monto,
                "total": total,
            })

    if not wishlist_excel.empty:
        for _, row in wishlist_excel.iterrows():
            codigo = row.get("codigo", "")
            if clean_text(codigo) == "":
                continue

            pieza = find_product_by_sale_code(df_inventory, codigo)
            if pieza is None:
                warnings.append(f"No encontré la pieza wish list con código {codigo}.")
                continue

            wishlist.append({
                "fecha": safe_date(row.get("fecha", "")),
                "codigo": normalize_numero(pieza["numero"]),
                "codigo_interno": clean_text(pieza["codigo_interno"]),
                "marca": clean_text(pieza.get("marca", "")),
                "producto": clean_text(pieza["producto"]),
                "color": clean_text(pieza["color"]),
                "talla": display_talla(pieza["talla"]),
                "precio": parse_price(pieza["precio"]),
            })

    if pagos_excel.empty:
        pagos = pd.DataFrame(columns=["fecha_pago", "forma_pago", "monto_pago"])
    else:
        pagos = pagos_excel.copy()
        for col in ["fecha_pago", "forma_pago", "monto_pago"]:
            if col not in pagos.columns:
                pagos[col] = "" if col != "monto_pago" else 0
        pagos["fecha_pago"] = pagos["fecha_pago"].apply(safe_date)
        pagos["forma_pago"] = pagos["forma_pago"].apply(clean_text)
        pagos["monto_pago"] = pagos["monto_pago"].apply(parse_price)

    return pd.DataFrame(vendidos), pd.DataFrame(wishlist), pagos, warnings


def draw_wrapped_text(c, text, x, y, max_width, font_name="Helvetica", font_size=9, leading=11, max_lines=2):
    c.setFont(font_name, font_size)
    words = clean_text(text).split()
    lines = []
    line = ""
    for word in words:
        test = f"{line} {word}".strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)

    for line in lines[:max_lines]:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_invoice_pdf(cliente, vendidos_df, wishlist_df, pagos_df) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    margin = 1.45 * cm
    y = h - margin

    dark = (0.12, 0.12, 0.16)
    taupe = (0.66, 0.58, 0.46)
    champagne = (0.94, 0.90, 0.82)
    soft = (0.98, 0.97, 0.94)
    line = (0.82, 0.80, 0.76)

    def set_rgb(rgb):
        c.setFillColorRGB(rgb[0], rgb[1], rgb[2])

    def stroke_rgb(rgb):
        c.setStrokeColorRGB(rgb[0], rgb[1], rgb[2])

    def new_page_if_needed(required_space=2.5 * cm):
        nonlocal y
        if y < margin + required_space:
            footer()
            c.showPage()
            y = h - margin
            header(compact=True)

    def header(compact=False):
        nonlocal y
        set_rgb(dark)
        c.setFont("Helvetica-Bold", 26 if not compact else 20)
        c.drawString(margin, y, "MC")
        c.setFont("Helvetica", 11 if not compact else 9)
        c.drawString(margin, y - (0.48 * cm if not compact else 0.38 * cm), "Prêt-à-porter")

        c.setFont("Helvetica", 8)
        c.drawRightString(w - margin, y, "Nota de venta")
        c.drawRightString(w - margin, y - 0.38 * cm, datetime.now().strftime("%d/%m/%Y"))

        stroke_rgb(taupe)
        c.setLineWidth(0.7)
        c.line(margin, y - (0.78 * cm if not compact else 0.62 * cm), w - margin, y - (0.78 * cm if not compact else 0.62 * cm))
        y -= 1.15 * cm if not compact else 0.95 * cm

    def footer():
        c.setFont("Helvetica", 7.5)
        set_rgb((0.45, 0.45, 0.45))
        c.drawCentredString(w / 2, 0.9 * cm, "MC Prêt-à-porter")

    def section_title(title):
        nonlocal y
        new_page_if_needed(1.4 * cm)
        set_rgb(dark)
        c.setFont("Helvetica-Bold", 12.5)
        c.drawString(margin, y, title)
        y -= 0.35 * cm
        stroke_rgb(taupe)
        c.setLineWidth(0.4)
        c.line(margin, y, w - margin, y)
        y -= 0.35 * cm

    def table_header(headers, widths, fill_color=dark, text_color=(1, 1, 1)):
        nonlocal y
        new_page_if_needed(1.2 * cm)
        x = margin
        set_rgb(fill_color)
        c.roundRect(margin, y - 0.48 * cm, sum(widths), 0.52 * cm, 5, fill=1, stroke=0)
        set_rgb(text_color)
        c.setFont("Helvetica-Bold", 7.4)
        for header, width in zip(headers, widths):
            c.drawString(x + 0.10 * cm, y - 0.29 * cm, header)
            x += width
        y -= 0.58 * cm

    def table_row(values, widths, row_height=0.66 * cm):
        nonlocal y
        new_page_if_needed(row_height + 0.4 * cm)
        x = margin
        stroke_rgb(line)
        c.setLineWidth(0.25)
        c.line(margin, y - row_height + 0.08 * cm, margin + sum(widths), y - row_height + 0.08 * cm)
        set_rgb(dark)
        c.setFont("Helvetica", 7.4)
        for value, width in zip(values, widths):
            c.drawString(x + 0.10 * cm, y - 0.35 * cm, clean_text(value)[:42])
            x += width
        y -= row_height

    def draw_card_box(x, y_top, box_w, box_h, title, amount, accent=False):
        set_rgb(champagne if accent else soft)
        stroke_rgb((0.88, 0.85, 0.78))
        c.roundRect(x, y_top - box_h, box_w, box_h, 8, fill=1, stroke=1)
        set_rgb((0.36, 0.34, 0.31))
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 0.25 * cm, y_top - 0.38 * cm, title)
        set_rgb(dark)
        c.setFont("Helvetica-Bold", 12.5)
        c.drawString(x + 0.25 * cm, y_top - 0.92 * cm, amount)

    header()

    # Client block
    set_rgb(soft)
    stroke_rgb((0.90, 0.87, 0.80))
    c.roundRect(margin, y - 1.35 * cm, w - 2 * margin, 1.20 * cm, 10, fill=1, stroke=1)
    set_rgb((0.42, 0.38, 0.32))
    c.setFont("Helvetica", 8)
    c.drawString(margin + 0.35 * cm, y - 0.45 * cm, "CLIENTE")
    set_rgb(dark)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin + 0.35 * cm, y - 0.92 * cm, cliente)
    y -= 1.75 * cm

    # Totals
    subtotal = float(vendidos_df["precio"].sum()) if not vendidos_df.empty else 0.0
    descuento_total = float(vendidos_df["descuento_monto"].sum()) if not vendidos_df.empty and "descuento_monto" in vendidos_df.columns else 0.0
    total_vendido = float(vendidos_df["total"].sum()) if not vendidos_df.empty else 0.0
    total_pagado = float(pagos_df["monto_pago"].sum()) if not pagos_df.empty and "monto_pago" in pagos_df.columns else 0.0
    saldo = total_vendido - total_pagado

    card_gap = 0.35 * cm
    card_w = (w - 2 * margin - 2 * card_gap) / 3
    draw_card_box(margin, y, card_w, 1.15 * cm, "TOTAL VENDIDO", money(total_vendido), accent=True)
    draw_card_box(margin + card_w + card_gap, y, card_w, 1.15 * cm, "PAGADO A LA FECHA", money(total_pagado))
    draw_card_box(margin + 2 * (card_w + card_gap), y, card_w, 1.15 * cm, "SALDO PENDIENTE", money(saldo), accent=True)
    y -= 1.55 * cm

    # Vendidos
    section_title("Piezas vendidas")

    show_discount = False
    if not vendidos_df.empty and "descuento_pct" in vendidos_df.columns:
        show_discount = vendidos_df["descuento_pct"].fillna(0).sum() > 0

    if vendidos_df.empty:
        set_rgb((0.45, 0.45, 0.45))
        c.setFont("Helvetica", 9)
        c.drawString(margin, y, "No hay piezas vendidas registradas.")
        y -= 0.6 * cm
    else:
        if show_discount:
            widths = [1.45*cm, 1.35*cm, 6.05*cm, 1.75*cm, 2.15*cm, 2.0*cm]
            table_header(["Fecha", "Código", "Pieza", "Precio", "Descuento", "Total"], widths)
        else:
            widths = [1.65*cm, 1.35*cm, 8.15*cm, 2.0*cm, 2.0*cm]
            table_header(["Fecha", "Código", "Pieza", "Precio", "Total"], widths)

        for _, r in vendidos_df.iterrows():
            marca = clean_text(r.get("marca", ""))
            pieza_txt = f"{marca + ' · ' if marca else ''}{r['producto']} · {r['color']} · {r['talla']}"
            if show_discount:
                desc = ""
                if float(r["descuento_pct"]) > 0:
                    desc = f"{float(r['descuento_pct']):.0f}% / -{money(r['descuento_monto'])}"
                table_row([r["fecha"], r["codigo"], pieza_txt, money(r["precio"]), desc, money(r["total"])], widths)
            else:
                table_row([r["fecha"], r["codigo"], pieza_txt, money(r["precio"]), money(r["total"])], widths)

    y -= 0.25 * cm

    # Summary line
    if descuento_total > 0:
        new_page_if_needed(1.2 * cm)
        set_rgb((0.35, 0.35, 0.35))
        c.setFont("Helvetica", 8.5)
        c.drawRightString(w - margin - 3.8 * cm, y, "Subtotal")
        c.drawRightString(w - margin, y, money(subtotal))
        y -= 0.38 * cm
        c.drawRightString(w - margin - 3.8 * cm, y, "Descuento total")
        c.drawRightString(w - margin, y, f"-{money(descuento_total)}")
        y -= 0.50 * cm

    # Pagos
    if not pagos_df.empty:
        section_title("Pagos registrados")
        widths = [2.2*cm, 8.0*cm, 3.0*cm]
        table_header(["Fecha", "Forma de pago", "Monto"], widths, fill_color=taupe, text_color=(1, 1, 1))
        for _, r in pagos_df.iterrows():
            if clean_text(r.get("monto_pago", "")) == "":
                continue
            table_row([r["fecha_pago"], r["forma_pago"], money(r["monto_pago"])], widths)
        y -= 0.45 * cm

    # Wishlist
    if not wishlist_df.empty:
        section_title("Wish list / Piezas reservadas")
        widths = [1.65*cm, 1.35*cm, 9.0*cm, 2.4*cm]
        table_header(["Fecha", "Código", "Pieza", "Precio"], widths, fill_color=champagne, text_color=dark)

        for _, r in wishlist_df.iterrows():
            marca = clean_text(r.get("marca", ""))
            pieza_txt = f"{marca + ' · ' if marca else ''}{r['producto']} · {r['color']} · {r['talla']}"
            table_row([r["fecha"], r["codigo"], pieza_txt, money(r["precio"])], widths)

        y -= 0.35 * cm
        new_page_if_needed(1.5 * cm)

        set_rgb(soft)
        stroke_rgb((0.90, 0.87, 0.80))
        c.roundRect(margin, y - 1.22 * cm, w - 2 * margin, 1.05 * cm, 8, fill=1, stroke=1)
        set_rgb((0.30, 0.30, 0.30))
        c.setFont("Helvetica-Oblique", 8.2)
        note = (
            "Las piezas incluidas en el wish list se mantienen temporalmente reservadas para la cliente. "
            "Agradecemos confirmar la decisión dentro de un tiempo prudencial, para que en caso de no continuar "
            "con la compra puedan volver a estar disponibles para la venta."
        )
        draw_wrapped_text(c, note, margin + 0.30 * cm, y - 0.48 * cm, w - 2*margin - 0.6*cm, "Helvetica-Oblique", 8.2, 10, max_lines=3)
        y -= 1.42 * cm

    footer()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()

def invoice_page(df):
    st.title("Notas de venta")
    st.info("Sube el Excel de una cliente. La app generará una nota de venta en PDF con piezas vendidas, pagos y wish list.")

    uploaded = st.file_uploader("Excel de cliente", type=["xlsx", "xls"])

    with st.expander("Formato esperado del Excel"):
        st.markdown("""
Puedes usar el formato sencillo en una sola hoja:

|  |  | vendido | descuento |  | apartado |
|---|---|---:|---:|---|---:|
|  |  | 078 | 15 |  | 099 |
|  |  | 045 |  |  | 033 |

Arriba debe aparecer una celda que diga **Cliente** y debajo el nombre de la cliente.

También puedes agregar pagos en la misma hoja con columnas:

| fecha_pago | forma_pago | monto_pago |
|---|---|---:|
| 07/05/2026 | Zelle | 200 |
| 08/05/2026 | Efectivo | 150 |

La app también sigue aceptando el formato anterior por hojas separadas: CLIENTE, VENDIDOS, WISHLIST y PAGOS.
""")

    if not uploaded:
        return

    try:
        cliente, vendidos_excel, wishlist_excel, pagos_excel = read_invoice_excel(uploaded)
        vendidos_df, wishlist_df, pagos_df, warnings = process_invoice_data(df, vendidos_excel, wishlist_excel, pagos_excel)

        for warning in warnings:
            st.warning(warning)

        st.subheader(cliente)

        if not vendidos_df.empty:
            st.markdown("### Piezas vendidas")
            cols = ["fecha", "codigo", "marca", "producto", "color", "talla", "precio", "descuento_pct", "descuento_monto", "total"]
            st.dataframe(vendidos_df[cols], use_container_width=True)

        if not pagos_df.empty:
            st.markdown("### Pagos")
            st.dataframe(pagos_df[["fecha_pago", "forma_pago", "monto_pago"]], use_container_width=True)

        if not wishlist_df.empty:
            st.markdown("### Wish list")
            st.dataframe(wishlist_df[["fecha", "codigo", "marca", "producto", "color", "talla", "precio"]], use_container_width=True)

        pdf = build_invoice_pdf(cliente, vendidos_df, wishlist_df, pagos_df)
        st.download_button(
            "Descargar nota de venta PDF",
            data=pdf,
            file_name=f"nota_venta_{slugify(cliente)}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    except Exception as e:
        st.error(f"No pude procesar la nota de venta: {e}")

# ============================================================
# AUTH / NAV
# ============================================================
def login():
    st.title("Concherie")

    # Al estar dentro de un form, presionar Enter en la clave ejecuta "Entrar".
    with st.form("login_form"):
        u = st.text_input("Usuario")
        p = st.text_input("Clave", type="password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)

    if submitted:
        if u in USERS and USERS[u]["password"] == p:
            st.session_state.user = u
            st.session_state.role = USERS[u]["role"]
            st.session_state.page = "inicio"
            st.rerun()
        else:
            st.error("Usuario o clave incorrecta")


def can_admin():
    return st.session_state.get("role") == "admin"


def can_ventas():
    return st.session_state.get("role") in ["admin", "ventas"]


def set_page(p):
    st.session_state.page = p
    st.rerun()


def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user')}**")
    st.sidebar.success(f"Datos: {st.session_state.get('data_status', 'Google Sheets')}")
    if supabase_configured():
        st.sidebar.success("Fotos: Supabase")
    else:
        st.sidebar.warning("Fotos: Supabase no configurado")

    buttons = [("🏠 Inicio", "inicio"), ("🔎 Buscar código", "buscar"), ("◼ Escanear QR", "scan")]
    if can_ventas():
        buttons += [("📸 Fotos", "fotos"), ("📄 Catálogo", "catalogo"), ("🧾 Notas de venta", "notas")]
    if can_admin():
        buttons += [("📥 Cargar inventario", "cargar"), ("🏷️ Generar QR", "qr"), ("📦 Inventario", "inventario"), ("🧩 Reparar marcas", "reparar_marcas"), ("⚙️ Admin", "admin")]

    st.sidebar.divider()
    for label, page in buttons:
        if st.sidebar.button(label, use_container_width=True):
            set_page(page)
    st.sidebar.divider()
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        st.session_state.clear()
        st.rerun()


# ============================================================
# PRODUCT DISPLAY
# ============================================================
def find_product(df, query):
    q = normalize_numero(query)
    if not q:
        return None
    matches = df[df["numero"].astype(str).apply(normalize_numero) == q]
    if matches.empty:
        # also allow internal code search
        qq = clean_text(query).upper()
        matches = df[df["codigo_interno"].astype(str).str.upper().str.contains(qq, na=False)]
    if matches.empty:
        return None
    return matches.iloc[0]


def show_product(row):
    numero = normalize_numero(row["numero"])
    st.subheader(f"{numero} · {clean_text(row['producto'])}")
    col1, col2, col3 = st.columns([1.1, 1.4, 0.9])
    with col1:
        url = clean_text(row.get("foto_url"))
        if url.startswith("http"):
            st.image(url, use_container_width=True)
        else:
            st.info("Sin foto")
    with col2:
        st.write(f"**Código numérico:** {numero}")
        st.write(f"**Código interno:** {clean_text(row['codigo_interno'])}")
        st.write(f"**Marca:** {clean_text(row.get('marca', ''))}")
        st.write(f"**Modelo:** {clean_text(row['codigo'])}")
        st.write(f"**Color:** {clean_text(row['color'])}")
        st.write(f"**Talla:** {display_talla(row['talla'])}")
    with col3:
        st.metric("Precio", money(row["precio"]))


# ============================================================
# PAGES
# ============================================================
def home_page(df):
    st.title("Concherie Boutique")
    st.caption("Inventario, QR, fotos y catálogo.")
    cols = st.columns(3 if can_admin() else 2)
    with cols[0]:
        if st.button("◼ Escanear QR", use_container_width=True): set_page("scan")
        if st.button("🔎 Buscar código", use_container_width=True): set_page("buscar")
    with cols[1]:
        if can_ventas() and st.button("📸 Fotos", use_container_width=True): set_page("fotos")
        if can_ventas() and st.button("📄 Catálogo", use_container_width=True): set_page("catalogo")
        if can_ventas() and st.button("🧾 Notas de venta", use_container_width=True): set_page("notas")
    if can_admin():
        with cols[2]:
            if st.button("🏷️ Generar QR", use_container_width=True): set_page("qr")
            if st.button("📥 Cargar inventario", use_container_width=True): set_page("cargar")
            if st.button("🧩 Reparar marcas", use_container_width=True): set_page("reparar_marcas")
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total piezas", len(df))
    c2.metric("Con foto", int(df["foto_url"].astype(str).str.startswith("http").sum()) if not df.empty else 0)
    c3.metric("Sin foto", int((~df["foto_url"].astype(str).str.startswith("http")).sum()) if not df.empty else 0)


def search_page(df):
    st.title("Buscar código")
    q = st.text_input("Código numérico", placeholder="Ej: 066")
    if q:
        row = find_product(df, q)
        if row is None:
            st.error("No encontré esa pieza.")
        else:
            show_product(row)


def scan_page(df):
    st.title("Escanear QR")
    st.info("En iPhone, usa la cámara normal para leer el QR. Si prefieres, toma/sube una foto del QR aquí o escribe el código manualmente.")
    manual = st.text_input("Código manual", placeholder="Ej: 066")
    if manual:
        row = find_product(df, manual)
        if row is not None:
            show_product(row)
        else:
            st.error("No encontré esa pieza.")
        return
    uploaded = st.file_uploader("Subir foto del QR", type=["jpg", "jpeg", "png"])
    if uploaded:
        file_bytes = np.asarray(bytearray(uploaded.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        detector = cv2.QRCodeDetector()
        data, bbox, _ = detector.detectAndDecode(img)
        if data:
            st.success(f"QR leído: {data}")
            row = find_product(df, data)
            if row is not None:
                show_product(row)
            else:
                st.error("El QR se leyó, pero no encontré la pieza.")
        else:
            st.error("No pude leer el QR. Escribe el código numérico manualmente.")


def photos_page(df):
    st.title("Fotos de productos")
    if df.empty:
        st.info("No hay inventario.")
        return
    q = st.text_input("Buscar pieza por código", placeholder="Ej: 066")
    row = None
    if q:
        row = find_product(df, q)
    else:
        opts = [f"{normalize_numero(r.numero)} · {getattr(r, 'marca', '')} · {r.producto} · {r.color} · {display_talla(r.talla)}" for r in df.itertuples()]
        selected = st.selectbox("O selecciona pieza", opts)
        idx = opts.index(selected)
        row = df.iloc[idx]
    if row is None:
        st.warning("No encontré esa pieza.")
        return
    show_product(row)
    st.divider()
    uploaded = st.file_uploader("Agregar / cambiar foto", type=["jpg", "jpeg", "png", "heic", "heif"])
    if uploaded and st.button("Guardar foto", type="primary"):
        try:
            url = upload_product_photo(uploaded, row["numero"], row["producto"])
            if not url:
                st.error("Supabase no está configurado.")
                return
            df.loc[df["numero"].astype(str).apply(normalize_numero) == normalize_numero(row["numero"]), "foto_url"] = url
            df["fecha_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ok, msg = save_inventory(df)
            if ok:
                st.success("Foto guardada correctamente.")
                st.rerun()
            else:
                st.error(f"La foto subió, pero no pude guardar el link en Sheets: {msg}")
        except Exception as e:
            st.error(f"No pude guardar la foto: {e}")


def catalog_page(df):
    st.title("Catálogo disponible")
    if "marca" in df.columns:
        sin_marca_count = int(df["marca"].astype(str).str.strip().isin(["", "SIN MARCA", "nan", "None"]).sum())
        if sin_marca_count > 0:
            st.warning(f"Hay {sin_marca_count} piezas sin marca. Ve a Admin > Reparar marcas o vuelve a cargar los Excels originales para rellenarlas.")
    include_price = st.checkbox("Incluir precio", value=True)
    group_by_brand = st.checkbox("Separar catálogo por marca", value=True)
    talla_filter = st.text_input("Filtrar por talla opcional", placeholder="Ej: T40")
    preview = df.copy()
    if "marca" not in preview.columns:
        preview["marca"] = ""
    if talla_filter.strip():
        preview = preview[preview["talla"].astype(str).str.upper().str.contains(talla_filter.strip().upper(), na=False)]
    st.dataframe(preview[["numero", "marca", "producto", "color", "talla", "precio", "foto_url"]], use_container_width=True)
    pdf = build_catalog_pdf(df, include_price=include_price, talla_filter=talla_filter, group_by_brand=group_by_brand)
    st.download_button("Descargar catálogo PDF", pdf, file_name="catalogo_concherie_por_marca.pdf", mime="application/pdf", use_container_width=True)


def qr_page(df):
    st.title("Generar QR / etiquetas")
    st.caption("Formato 5x8 cm, QR grande, código numérico grande y líneas punteadas comunes para guillotina.")
    pdf = build_labels_pdf(df)
    st.download_button("Descargar etiquetas PDF", pdf, file_name="etiquetas_concherie_5x8.pdf", mime="application/pdf", use_container_width=True)


def inventory_page(df):
    st.title("Inventario")
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic")
    if st.button("Guardar cambios", type="primary"):
        ok, msg = save_inventory(edited)
        if ok:
            st.success("Inventario guardado.")
        else:
            st.error(msg)



def append_inventory(existing_df: pd.DataFrame, new_df: pd.DataFrame):
    existing_df = ensure_inventory_schema(existing_df)
    new_df = ensure_inventory_schema(new_df)

    if existing_df.empty:
        combined = new_df.copy()
        return ensure_inventory_schema(combined), [], len(new_df), 0

    existing_codes = set(existing_df["codigo_interno"].astype(str).str.upper())
    existing_nums = set(existing_df["numero"].astype(str).apply(normalize_numero))

    rows_to_add = []
    skipped = []
    brand_updates = 0

    for _, row in new_df.iterrows():
        codigo_interno = clean_text(row.get("codigo_interno")).upper()
        numero = normalize_numero(row.get("numero"))
        new_brand = clean_text(row.get("marca", "")).upper()

        duplicated = False
        reason = ""

        match_mask = pd.Series([False] * len(existing_df))

        if codigo_interno and codigo_interno in existing_codes:
            duplicated = True
            reason = f"código interno ya existe: {codigo_interno}"
            match_mask = existing_df["codigo_interno"].astype(str).str.upper() == codigo_interno
        elif numero and numero in existing_nums:
            duplicated = True
            reason = f"número ya existe: {numero}"
            match_mask = existing_df["numero"].astype(str).apply(normalize_numero) == numero

        if duplicated:
            # Antes solo omitía duplicados. Ahora, si la pieza ya existe pero no tiene marca,
            # rellena la marca sin tocar precio, código, foto ni demás campos.
            if new_brand:
                matched_indexes = existing_df.index[match_mask].tolist()
                for ix in matched_indexes:
                    current_brand = clean_text(existing_df.at[ix, "marca"]).upper()
                    if current_brand in ["", "SIN MARCA", "NAN", "NONE"]:
                        existing_df.at[ix, "marca"] = new_brand
                        brand_updates += 1

            skipped.append({
                "numero": numero,
                "codigo_interno": codigo_interno,
                "marca_detectada": new_brand,
                "motivo": reason,
            })
        else:
            rows_to_add.append(row)
            existing_codes.add(codigo_interno)
            existing_nums.add(numero)

    if rows_to_add:
        add_df = pd.DataFrame(rows_to_add)
        combined = pd.concat([existing_df, add_df], ignore_index=True)
    else:
        combined = existing_df.copy()

    return ensure_inventory_schema(combined), skipped, len(rows_to_add), brand_updates

def next_inventory_numbers(existing_df: pd.DataFrame, count: int):
    existing_df = ensure_inventory_schema(existing_df)
    used = set(existing_df["numero"].astype(str).apply(normalize_numero))
    max_num = 0
    for n in used:
        if clean_text(n).isdigit():
            max_num = max(max_num, int(n))
    nums = []
    candidate = max_num + 1
    while len(nums) < count:
        n = str(candidate).zfill(3)
        if n not in used:
            nums.append(n)
            used.add(n)
        candidate += 1
    return nums


def infer_color_from_producto(producto):
    p = clean_text(producto).upper()
    known_colors = [
        "NEGRO", "NEGRA", "BLACK", "SKIN", "AZUL", "BLUE", "ORQUIDEA", "MEADOW",
        "SULPHUR", "BLANCO", "BLANCA", "WHITE", "ROJO", "ROJA", "RED", "VERDE",
        "GREEN", "BEIGE", "CREMA", "CREAM", "DORADO", "GOLD", "PLATA", "SILVER",
        "MARRON", "BROWN", "CAMEL", "NAVY", "FUCSIA", "PINK", "ROSA"
    ]
    found = [color for color in known_colors if re.search(rf"\b{re.escape(color)}\b", p)]
    return " ".join(found[:2])


def prepare_new_merchandise_upload(raw_df: pd.DataFrame, existing_df: pd.DataFrame) -> pd.DataFrame:
    """
    Acepta:
    1) Inventario individualizado con numero/codigo_interno.
    2) Transcripción por modelo con marca, producto, precio, cantidad/llegaron
       y tallas en columnas Tallas, Tallas2, etc.

    También ignora automáticamente:
    - filas TOTAL
    - subtotales
    - filas vacías
    - líneas con precio 0 y sin código real
    """
    raw = raw_df.copy()
    raw.columns = [normalize_col_name(c) for c in raw.columns]

    aliases = {
        "marca_maison": "marca",
        "maison": "marca",
        "brand": "marca",
        "designer": "marca",
        "disenador": "marca",
        "modelo": "codigo",
        "descripcion": "producto",
        "articulo": "producto",
        "llego": "llegaron",
        "llegada": "llegaron",
        "pedido": "cantidad",
    }
    raw = raw.rename(columns={c: aliases.get(c, c) for c in raw.columns})

    has_piece_codes = "numero" in raw.columns or "codigo_interno" in raw.columns
    if has_piece_codes:
        cleaned = ensure_inventory_schema(raw)

        # eliminar basura/totales
        cleaned = cleaned[
            ~(
                cleaned["producto"].astype(str).str.upper().str.contains("TOTAL|SUBTOTAL", na=False)
                |
                (
                    (cleaned["codigo"].astype(str).str.strip() == "")
                    &
                    (cleaned["precio"].fillna(0).astype(float) <= 0)
                )
            )
        ]

        return ensure_inventory_schema(cleaned)

    quantity_col = None
    for candidate in ["llegaron", "cantidad", "qty", "unidades"]:
        if candidate in raw.columns:
            quantity_col = candidate
            break

    if not quantity_col:
        raw["cantidad"] = 1
        quantity_col = "cantidad"

    talla_cols = []
    for c in raw.columns:
        if c == "talla" or c.startswith("talla") or c.startswith("tallas"):
            talla_cols.append(c)

    total_pieces = 0
    expanded_plan = []

    for _, row in raw.iterrows():

        producto = clean_text(row.get("producto", ""))
        codigo = clean_text(row.get("codigo", ""))
        marca = clean_text(row.get("marca", ""))
        precio = parse_price(row.get("precio", 0))

        producto_upper = producto.upper().strip()
        codigo_upper = codigo.upper().strip()

        # =====================================================
        # IGNORAR FILAS BASURA / TOTALES
        # =====================================================

        # TOTAL / SUBTOTAL
        if any(word in producto_upper for word in ["TOTAL", "SUBTOTAL"]):
            continue

        if any(word in codigo_upper for word in ["TOTAL", "SUBTOTAL"]):
            continue

        # fila completamente vacía
        meaningful = any([
            producto,
            codigo,
            marca,
            precio > 0,
        ])

        if not meaningful:
            continue

        # precio 0 y sin codigo -> casi seguro basura
        if precio <= 0 and not codigo:
            continue

        q = row.get(quantity_col, 1)

        try:
            q = int(float(q))
        except Exception:
            q = 1

        q = max(q, 0)

        if q == 0:
            continue

        tallas = []

        for tc in talla_cols:
            val = clean_text(row.get(tc, ""))

            if not val or val.lower() in ["nan", "none"]:
                continue

            try:
                if re.fullmatch(r"\d+\.0", val):
                    val = str(int(float(val)))
            except Exception:
                pass

            tallas.append(
                f"T{val}" if val.isdigit() else val.upper()
            )

        if tallas:
            piece_tallas = tallas[:q] if q else tallas

            while len(piece_tallas) < q:
                piece_tallas.append("")
        else:
            piece_tallas = [""] * q

        for talla in piece_tallas:
            expanded_plan.append((row, talla))
            total_pieces += 1

    generated_numbers = next_inventory_numbers(existing_df, total_pieces)

    expanded_rows = []

    for idx, (row, talla) in enumerate(expanded_plan):

        numero = generated_numbers[idx]

        producto = clean_text(row.get("producto", ""))
        codigo = clean_text(row.get("codigo", ""))
        marca = clean_text(row.get("marca", ""))
        color = clean_text(row.get("color", "")) or infer_color_from_producto(producto)

        expanded_rows.append({
            "numero": numero,
            "marca": marca,
            "codigo": codigo,
            "producto": producto,
            "color": color,
            "talla": talla,
            "precio": row.get("precio", 0),
            "foto_url": row.get("foto_url", ""),
            "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    final_df = pd.DataFrame(expanded_rows)

    return ensure_inventory_schema(final_df)

def build_brand_map_from_excel(uploaded_file):
    """
    Lee un Excel de inventario/transcripción y devuelve mapa codigo -> marca.
    Sirve para reparar inventarios viejos que quedaron sin marca.
    """
    try:
        xls = pd.ExcelFile(uploaded_file)
        sheet = "inventario" if "inventario" in xls.sheet_names else xls.sheet_names[0]
        raw = pd.read_excel(uploaded_file, sheet_name=sheet)
    except Exception:
        return {}

    raw.columns = [normalize_col_name(c) for c in raw.columns]
    aliases = {
        "marca_maison": "marca",
        "maison": "marca",
        "brand": "marca",
        "designer": "marca",
        "disenador": "marca",
        "modelo": "codigo",
    }
    raw = raw.rename(columns={c: aliases.get(c, c) for c in raw.columns})

    if "marca" not in raw.columns or "codigo" not in raw.columns:
        return {}

    brand_map = {}
    for _, row in raw.iterrows():
        codigo = clean_text(row.get("codigo", "")).upper()
        marca = clean_text(row.get("marca", "")).upper()
        if codigo and marca and codigo != "TOTAL":
            brand_map[codigo] = marca

    return brand_map


def repair_inventory_brands(df: pd.DataFrame, uploaded_files):
    df = ensure_inventory_schema(df)
    if "marca" not in df.columns:
        df["marca"] = ""

    combined_map = {}
    for uploaded in uploaded_files:
        combined_map.update(build_brand_map_from_excel(uploaded))

    repaired = df.copy()
    updates = 0

    for idx, row in repaired.iterrows():
        current_brand = clean_text(row.get("marca", "")).upper()
        codigo = clean_text(row.get("codigo", "")).upper()
        codigo_interno = clean_text(row.get("codigo_interno", "")).upper()

        if current_brand and current_brand not in ["SIN MARCA", "NAN", "NONE"]:
            continue

        detected = ""

        # 1) Match exact model code column
        if codigo in combined_map:
            detected = combined_map[codigo]

        # 2) Match internal code prefix, e.g. S26C629-BLACK-T0-198 -> S26C629
        if not detected and codigo_interno:
            prefix = codigo_interno.split("-")[0].upper()
            if prefix in combined_map:
                detected = combined_map[prefix]

        if detected:
            repaired.at[idx, "marca"] = detected
            updates += 1

    return ensure_inventory_schema(repaired), updates, combined_map

def cargar_page(df):
    st.title("Cargar inventario")

    modo = st.radio(
        "¿Qué quieres hacer?",
        ["Agregar mercancía nueva", "Reemplazar inventario completo"],
        horizontal=True,
    )

    if modo == "Agregar mercancía nueva":
        st.info(
            "Esta opción agrega piezas nuevas al inventario actual. "
            "Si una pieza ya existe por número o código interno, no la duplica."
        )
        uploaded = st.file_uploader("Excel con mercancía nueva", type=["xlsx", "xls"], key="append_inventory_file")

        if uploaded:
            raw_new = pd.read_excel(uploaded)
            new = prepare_new_merchandise_upload(raw_new, df)

            st.markdown("### Vista previa de mercancía nueva")
            st.dataframe(new, use_container_width=True)

            sincodigo_count = int(new["codigo_interno"].astype(str).str.contains("SINCODIGO", na=False).sum())
            if sincodigo_count > 0:
                st.warning(
                    f"Hay {sincodigo_count} piezas sin código/modelo. "
                    "Revisa el Excel original porque podrían ser filas incompletas o resúmenes."
                )

            st.caption("Si el Excel venía por modelo con cantidad, aquí ya aparece expandido a una línea por pieza, con números únicos asignados automáticamente.")

            combined, skipped, added_count, brand_updates = append_inventory(df, new)

            c1, c2, c3 = st.columns(3)
            c1.metric("Piezas nuevas a agregar", added_count)
            c2.metric("Duplicadas / omitidas", len(skipped))
            c3.metric("Marcas a reparar", brand_updates)

            if skipped:
                st.warning("Estas piezas ya existen y no se agregarán nuevamente. Si tenían la marca vacía, la app sí la rellenará:")
                st.dataframe(pd.DataFrame(skipped), use_container_width=True)

            if st.button("Agregar al inventario", type="primary", use_container_width=True):
                ok, msg = save_inventory(combined)
                if ok:
                    st.success(f"Mercancía procesada correctamente. Se agregaron {added_count} piezas nuevas y se repararon {brand_updates} marcas.")
                    st.rerun()
                else:
                    st.error(msg)

    else:
        st.warning("Esto SÍ reemplaza el inventario actual. Usa solo si quieres borrar todo y recargar desde Excel.")
        uploaded = st.file_uploader("Excel inventario completo", type=["xlsx", "xls"], key="replace_inventory_file")

        if uploaded:
            new = pd.read_excel(uploaded)
            new = ensure_inventory_schema(new)
            st.dataframe(new, use_container_width=True)

            confirm = st.text_input("Para reemplazar inventario escribe REEMPLAZAR")
            if st.button("Reemplazar inventario completo", type="primary", use_container_width=True):
                if confirm == "REEMPLAZAR":
                    ok, msg = save_inventory(new)
                    if ok:
                        st.success("Inventario reemplazado.")
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.error("Confirmación incorrecta.")

def admin_page(df):
    st.title("Admin")
    st.subheader("Limpiar fotos")
    q = st.text_input("Código de pieza", placeholder="Ej: 066")
    if q:
        row = find_product(df, q)
        if row is not None:
            show_product(row)
            confirm = st.text_input("Para borrar la foto, escribe el código numérico")
            pwd = st.text_input("Clave admin", type="password")
            if st.button("Borrar foto", type="primary"):
                if confirm == normalize_numero(row["numero"]) and pwd == USERS["jc"]["password"]:
                    df.loc[df["numero"].astype(str).apply(normalize_numero) == normalize_numero(row["numero"]), "foto_url"] = ""
                    ok, msg = save_inventory(df)
                    if ok:
                        st.success("Foto borrada.")
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.error("Confirmación o clave incorrecta.")
        else:
            st.error("No encontré esa pieza.")


def reparar_marcas_page(df):
    st.title("Reparar marcas del inventario")
    st.info(
        "Usa esta opción si el inventario ya fue cargado antes y aparece como SIN MARCA. "
        "Sube los Excels originales de transcripción; la app usará el código/modelo para rellenar solo la columna marca, sin tocar precios, fotos ni números."
    )

    uploaded_files = st.file_uploader(
        "Excels originales con marca",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.caption("Ejemplo: Dice_Kayek_transcripcion.xlsx, Gianluca_Capannolo_transcripcion.xlsx, etc.")
        return

    repaired, updates, brand_map = repair_inventory_brands(df, uploaded_files)

    st.metric("Marcas detectadas por código/modelo", len(brand_map))
    st.metric("Piezas que se actualizarán", updates)

    if brand_map:
        preview_map = pd.DataFrame(
            [{"codigo": k, "marca": v} for k, v in sorted(brand_map.items())]
        )
        st.markdown("### Mapa detectado")
        st.dataframe(preview_map, use_container_width=True)

    if updates > 0:
        st.markdown("### Vista previa del inventario reparado")
        st.dataframe(repaired[["numero", "marca", "codigo", "producto", "color", "talla", "precio"]], use_container_width=True)

        if st.button("Guardar marcas reparadas", type="primary", use_container_width=True):
            ok, msg = save_inventory(repaired)
            if ok:
                st.success(f"Inventario actualizado. Se repararon {updates} piezas.")
                st.rerun()
            else:
                st.error(msg)
    else:
        st.warning("No encontré piezas para actualizar. Puede que ya tengan marca o que los códigos no coincidan.")

# ============================================================
# MAIN
# ============================================================
def main():
    if "user" not in st.session_state:
        login()
        return
    df = load_inventory()
    sidebar()
    page = st.session_state.get("page", "inicio")
    if page == "inicio": home_page(df)
    elif page == "buscar": search_page(df)
    elif page == "scan": scan_page(df)
    elif page == "fotos" and can_ventas(): photos_page(df)
    elif page == "catalogo" and can_ventas(): catalog_page(df)
    elif page == "notas" and can_ventas(): invoice_page(df)
    elif page == "qr" and can_admin(): qr_page(df)
    elif page == "inventario" and can_admin(): inventory_page(df)
    elif page == "cargar" and can_admin(): cargar_page(df)
    elif page == "admin" and can_admin(): admin_page(df)
    else:
        st.warning("No tienes permiso para esta sección.")

if __name__ == "__main__":
    main()
