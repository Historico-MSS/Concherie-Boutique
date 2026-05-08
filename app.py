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
    df.columns = [clean_text(c).lower().replace(" ", "_") for c in df.columns]
    aliases = {
        "marca/maison": "marca",
        "maison": "marca",
        "cod": "codigo",
        "código": "codigo",
        "codigo_modelo": "codigo",
        "articulo": "producto",
        "artículo": "producto",
        "descripcion": "producto",
        "descripción": "producto",
        "colour": "color",
        "size": "talla",
        "price": "precio",
        "photo": "foto_url",
    }
    df = df.rename(columns={c: aliases.get(c, c) for c in df.columns})

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col != "precio" else 0.0

    df["numero"] = df.apply(lambda r: normalize_numero(r.get("numero")) or normalize_numero(r.get("codigo_interno")), axis=1)
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


def build_catalog_pdf(df: pd.DataFrame, include_price=True, talla_filter="") -> bytes:
    df = ensure_inventory_schema(df)
    if talla_filter.strip():
        tf = talla_filter.strip().upper()
        df = df[df["talla"].astype(str).str.upper().str.contains(tf, na=False)]
    df = df.sort_values(["producto", "color", "talla", "numero"])

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    margin = 1.3 * cm
    card_w = (w - 2 * margin - 0.7 * cm) / 2
    card_h = 6.4 * cm
    gap = 0.7 * cm

    def header():
        c.setFont("Helvetica-Bold", 20)
        c.drawString(margin, h - margin, "CONCHERIE BOUTIQUE")
        c.setFont("Helvetica", 10)
        c.drawString(margin, h - margin - 0.45 * cm, "Catálogo disponible")

    header()
    y = h - margin - 1.3 * cm
    col = 0

    for _, row in df.iterrows():
        if y - card_h < margin:
            c.showPage()
            header()
            y = h - margin - 1.3 * cm
            col = 0
        x = margin + col * (card_w + gap)

        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.roundRect(x, y - card_h, card_w, card_h, 8, stroke=1, fill=0)

        # photo area
        photo_url = clean_text(row.get("foto_url"))
        if photo_url.startswith("http"):
            try:
                img_data = requests.get(photo_url, timeout=10).content
                img = Image.open(io.BytesIO(img_data)).convert("RGB")
                img.thumbnail((500, 500))
                c.drawImage(pil_to_reader(img), x + 0.3 * cm, y - 3.6 * cm, width=2.9 * cm, height=2.9 * cm, preserveAspectRatio=True, anchor="c")
            except Exception:
                c.setFillColorRGB(0.95, 0.95, 0.95)
                c.rect(x + 0.3 * cm, y - 3.6 * cm, 2.9 * cm, 2.9 * cm, fill=1, stroke=0)
        else:
            c.setFillColorRGB(0.96, 0.96, 0.96)
            c.rect(x + 0.3 * cm, y - 3.6 * cm, 2.9 * cm, 2.9 * cm, fill=1, stroke=0)
            c.setFillColorRGB(0.45, 0.45, 0.45)
            c.setFont("Helvetica", 8)
            c.drawCentredString(x + 1.75 * cm, y - 2.15 * cm, "Sin foto")

        tx = x + 3.55 * cm
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(tx, y - 0.7 * cm, f"{normalize_numero(row['numero'])} · {clean_text(row['producto'])[:24]}")
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(tx, y - 1.2 * cm, f"{clean_text(row['color'])} · {display_talla(row['talla'])}")
        c.setFont("Helvetica", 7)
        c.drawString(tx, y - 1.7 * cm, clean_text(row["codigo_interno"])[:30])
        if include_price:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(tx, y - 2.55 * cm, money(row["precio"]))
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


def read_invoice_excel(uploaded_file):
    xls = pd.ExcelFile(uploaded_file)

    def read_sheet(name):
        if name not in xls.sheet_names:
            return pd.DataFrame()
        data = pd.read_excel(uploaded_file, sheet_name=name)
        data.columns = [clean_text(c).lower().replace(" ", "_") for c in data.columns]
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
    margin = 1.5 * cm
    y = h - margin

    def new_page_if_needed(required_space=2.5 * cm):
        nonlocal y
        if y < margin + required_space:
            c.showPage()
            y = h - margin

    def section_title(title):
        nonlocal y
        new_page_if_needed(1.5 * cm)
        c.setFont("Helvetica-Bold", 13)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(margin, y, title)
        y -= 0.55 * cm

    def table_header(headers, widths, fill=(0.1, 0.1, 0.1), text_color=(1, 1, 1)):
        nonlocal y
        x = margin
        c.setFillColorRGB(*fill)
        c.rect(margin, y - 0.42 * cm, sum(widths), 0.52 * cm, fill=1, stroke=0)
        c.setFillColorRGB(*text_color)
        c.setFont("Helvetica-Bold", 7.8)
        for header, width in zip(headers, widths):
            c.drawString(x + 0.08 * cm, y - 0.25 * cm, header)
            x += width
        y -= 0.52 * cm

    def table_row(values, widths, row_height=0.62 * cm):
        nonlocal y
        new_page_if_needed(row_height + 0.5 * cm)
        x = margin
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 7.5)
        c.setStrokeColorRGB(0.82, 0.82, 0.82)
        c.line(margin, y - row_height + 0.08 * cm, margin + sum(widths), y - row_height + 0.08 * cm)
        for value, width in zip(values, widths):
            c.drawString(x + 0.08 * cm, y - 0.33 * cm, clean_text(value)[:36])
            x += width
        y -= row_height

    # Header
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin, y, "CONCHERIE")
    y -= 0.7 * cm
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, "Nota de venta")
    y -= 0.75 * cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Cliente:")
    c.setFont("Helvetica", 10)
    c.drawString(margin + 1.8 * cm, y, cliente)
    y -= 0.45 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Fecha:")
    c.setFont("Helvetica", 10)
    c.drawString(margin + 1.8 * cm, y, datetime.now().strftime("%d/%m/%Y"))
    y -= 0.9 * cm

    # Vendidos
    section_title("Piezas vendidas")

    show_discount = False
    if not vendidos_df.empty and "descuento_pct" in vendidos_df.columns:
        show_discount = vendidos_df["descuento_pct"].fillna(0).sum() > 0

    if vendidos_df.empty:
        c.setFont("Helvetica", 9)
        c.drawString(margin, y, "No hay piezas vendidas registradas.")
        y -= 0.6 * cm
    else:
        if show_discount:
            widths = [1.7*cm, 1.4*cm, 5.2*cm, 2.0*cm, 2.2*cm, 2.0*cm]
            table_header(["Fecha", "Código", "Pieza", "Precio", "Descuento", "Total"], widths)
        else:
            widths = [1.9*cm, 1.5*cm, 7.5*cm, 2.3*cm, 2.3*cm]
            table_header(["Fecha", "Código", "Pieza", "Precio", "Total"], widths)

        for _, r in vendidos_df.iterrows():
            pieza_txt = f"{r['producto']} · {r['color']} · {r['talla']}"
            if show_discount:
                desc = ""
                if float(r["descuento_pct"]) > 0:
                    desc = f"{float(r['descuento_pct']):.0f}% / -{money(r['descuento_monto'])}"
                table_row([
                    r["fecha"],
                    r["codigo"],
                    pieza_txt,
                    money(r["precio"]),
                    desc,
                    money(r["total"])
                ], widths)
            else:
                table_row([
                    r["fecha"],
                    r["codigo"],
                    pieza_txt,
                    money(r["precio"]),
                    money(r["total"])
                ], widths)

    subtotal = float(vendidos_df["precio"].sum()) if not vendidos_df.empty else 0.0
    descuento_total = float(vendidos_df["descuento_monto"].sum()) if not vendidos_df.empty and "descuento_monto" in vendidos_df.columns else 0.0
    total_vendido = float(vendidos_df["total"].sum()) if not vendidos_df.empty else 0.0
    total_pagado = float(pagos_df["monto_pago"].sum()) if not pagos_df.empty and "monto_pago" in pagos_df.columns else 0.0
    saldo = total_vendido - total_pagado

    y -= 0.3 * cm
    summary_x = w - margin - 7.0 * cm
    summary = [("Subtotal", subtotal)]
    if descuento_total > 0:
        summary.append(("Descuento total", -descuento_total))
    summary += [("Total vendido", total_vendido), ("Pagado a la fecha", total_pagado), ("Saldo pendiente", saldo)]

    for label, amount in summary:
        new_page_if_needed(0.45 * cm)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(summary_x, y, label)
        c.drawRightString(w - margin, y, money(amount))
        y -= 0.42 * cm

    y -= 0.4 * cm

    # Pagos
    if not pagos_df.empty:
        section_title("Pagos registrados")
        widths = [2.4*cm, 7.0*cm, 3.0*cm]
        table_header(["Fecha", "Forma de pago", "Monto"], widths, fill=(0.35, 0.35, 0.35))
        for _, r in pagos_df.iterrows():
            if clean_text(r.get("monto_pago", "")) == "":
                continue
            table_row([r["fecha_pago"], r["forma_pago"], money(r["monto_pago"])], widths)
        y -= 0.5 * cm

    # Wishlist
    if not wishlist_df.empty:
        section_title("Wish list / Piezas reservadas")
        widths = [1.8*cm, 1.5*cm, 8.0*cm, 2.6*cm]
        table_header(["Fecha", "Código", "Pieza", "Precio"], widths, fill=(0.78, 0.70, 0.56), text_color=(0, 0, 0))
        for _, r in wishlist_df.iterrows():
            pieza_txt = f"{r['producto']} · {r['color']} · {r['talla']}"
            table_row([r["fecha"], r["codigo"], pieza_txt, money(r["precio"])], widths)
        y -= 0.35 * cm

        new_page_if_needed(1.5 * cm)
        c.setFont("Helvetica-Oblique", 8.8)
        c.setFillColorRGB(0.25, 0.25, 0.25)
        note = (
            "Las piezas incluidas en el wish list se mantienen temporalmente reservadas para la cliente. "
            "Agradecemos confirmar la decisión dentro de un tiempo prudencial, para que en caso de no continuar "
            "con la compra puedan volver a estar disponibles para la venta."
        )
        y = draw_wrapped_text(c, note, margin, y, w - 2*margin, "Helvetica-Oblique", 8.8, 11, max_lines=4)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def invoice_page(df):
    st.title("Notas de venta")
    st.info("Sube el Excel de una cliente. La app generará una nota de venta en PDF con piezas vendidas, pagos y wish list.")

    uploaded = st.file_uploader("Excel de cliente", type=["xlsx", "xls"])

    with st.expander("Formato esperado del Excel"):
        st.markdown("""
El archivo debe tener estas hojas:

**CLIENTE**

| cliente |
|---|
| María Pérez |

**VENDIDOS**

| fecha | codigo | descuento_pct |
|---|---:|---:|
| 07/05/2026 | 066 | |
| 07/05/2026 | 067 | 10 |

**WISHLIST**

| fecha | codigo |
|---|---:|
| 07/05/2026 | 070 |

**PAGOS**

| fecha_pago | forma_pago | monto_pago |
|---|---|---:|
| 07/05/2026 | Zelle | 200 |
| 08/05/2026 | Efectivo | 150 |
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
            cols = ["fecha", "codigo", "producto", "color", "talla", "precio", "descuento_pct", "descuento_monto", "total"]
            st.dataframe(vendidos_df[cols], use_container_width=True)

        if not pagos_df.empty:
            st.markdown("### Pagos")
            st.dataframe(pagos_df[["fecha_pago", "forma_pago", "monto_pago"]], use_container_width=True)

        if not wishlist_df.empty:
            st.markdown("### Wish list")
            st.dataframe(wishlist_df[["fecha", "codigo", "producto", "color", "talla", "precio"]], use_container_width=True)

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
        buttons += [("📥 Cargar inventario", "cargar"), ("🏷️ Generar QR", "qr"), ("📦 Inventario", "inventario"), ("⚙️ Admin", "admin")]

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
        opts = [f"{normalize_numero(r.numero)} · {r.producto} · {r.color} · {display_talla(r.talla)}" for r in df.itertuples()]
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
    include_price = st.checkbox("Incluir precio", value=True)
    talla_filter = st.text_input("Filtrar por talla opcional", placeholder="Ej: T40")
    preview = df.copy()
    if talla_filter.strip():
        preview = preview[preview["talla"].astype(str).str.upper().str.contains(talla_filter.strip().upper(), na=False)]
    st.dataframe(preview[["numero", "producto", "color", "talla", "precio", "foto_url"]], use_container_width=True)
    pdf = build_catalog_pdf(df, include_price=include_price, talla_filter=talla_filter)
    st.download_button("Descargar catálogo PDF", pdf, file_name="catalogo_concherie.pdf", mime="application/pdf", use_container_width=True)


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
        return ensure_inventory_schema(combined), [], len(new_df)

    existing_codes = set(existing_df["codigo_interno"].astype(str).str.upper())
    existing_nums = set(existing_df["numero"].astype(str).apply(normalize_numero))

    rows_to_add = []
    skipped = []

    for _, row in new_df.iterrows():
        codigo_interno = clean_text(row.get("codigo_interno")).upper()
        numero = normalize_numero(row.get("numero"))

        duplicated = False
        reason = ""

        if codigo_interno and codigo_interno in existing_codes:
            duplicated = True
            reason = f"código interno ya existe: {codigo_interno}"
        elif numero and numero in existing_nums:
            duplicated = True
            reason = f"número ya existe: {numero}"

        if duplicated:
            skipped.append({
                "numero": numero,
                "codigo_interno": codigo_interno,
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

    return ensure_inventory_schema(combined), skipped, len(rows_to_add)


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
            new = pd.read_excel(uploaded)
            new = ensure_inventory_schema(new)

            st.markdown("### Vista previa de mercancía nueva")
            st.dataframe(new, use_container_width=True)

            combined, skipped, added_count = append_inventory(df, new)

            c1, c2 = st.columns(2)
            c1.metric("Piezas nuevas a agregar", added_count)
            c2.metric("Duplicadas / omitidas", len(skipped))

            if skipped:
                st.warning("Estas piezas ya existen y no se agregarán nuevamente:")
                st.dataframe(pd.DataFrame(skipped), use_container_width=True)

            if st.button("Agregar al inventario", type="primary", use_container_width=True):
                ok, msg = save_inventory(combined)
                if ok:
                    st.success(f"Mercancía agregada correctamente. Se agregaron {added_count} piezas nuevas.")
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

