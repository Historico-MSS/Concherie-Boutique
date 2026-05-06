import os, re
from io import BytesIO
from datetime import datetime
from typing import List, Tuple
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Concherie Boutique", page_icon="🏷️", layout="wide")

USERS = {
    "jc": {"password": "master", "role": "admin"},
    "ventas": {"password": "moira", "role": "ventas"},
    "info": {"password": "precio", "role": "info"},
}

VALID_STATES = ["disponible", "reservado", "probando", "vendido", "mantenimiento"]
INV_COLS = ["codigo_numero","codigo_concha","codigo_interno","marca","producto","color","talla","precio","estado","ubicacion","cliente","descuento_pct","descuento_monto","pagado","foto_url","foto_drive_id","notas","fecha_creacion","fecha_actualizacion"]
CLIENT_COLS = ["cliente","telefono","email","notas","fecha_creacion"]
MOV_COLS = ["fecha","usuario","tipo","codigo_numero","codigo_interno","cliente","detalle"]
SALE_COLS = ["fecha","usuario","cliente","codigo_numero","codigo_interno","accion","precio","descuento_pct","descuento_monto","neto","pagado","saldo","nota"]
NOTE_COLS = ["fecha","usuario","cliente","numero_nota","total","pagado","saldo","drive_file_id","drive_url"]

# ---------- helpers ----------
def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def today(): return datetime.now().strftime("%Y-%m-%d")
def clean(x):
    if x is None: return ""
    s = str(x).strip()
    return "" if s.lower() in ["nan","none","null"] else s
def fnum(x, d=0.0):
    try: return float(x) if x not in [None, ""] else d
    except Exception: return d
def inum(x, d=0):
    try: return int(float(x)) if x not in [None, ""] else d
    except Exception: return d
def money(x): return f"${fnum(x):,.2f}"
def pct(x): return f"{fnum(x):.0f}%"
def token(s):
    s = clean(s).upper().replace("/","-").replace(" ","-")
    s = re.sub(r"[^A-Z0-9_-]+", "", s)
    return re.sub(r"-+", "-", s).strip("-")
def safe_name(s):
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", clean(s))
    return s.strip("_") or "archivo"
def set_page(p):
    st.session_state.page = p
    st.rerun()
def role(): return st.session_state.get("role", "")
def is_admin(): return role() == "admin"
def can_sell(): return role() in ["admin", "ventas"]
def can_price(): return role() in ["admin", "ventas", "info"]

def ensure(df, cols):
    if df is None or not isinstance(df, pd.DataFrame): df = pd.DataFrame(columns=cols)
    df = df.copy()
    for c in cols:
        if c not in df.columns: df[c] = ""
    return df[cols]
def inv_schema(df):
    df = ensure(df, INV_COLS)
    for c in ["precio","descuento_pct","descuento_monto","pagado"]: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in [x for x in INV_COLS if x not in ["precio","descuento_pct","descuento_monto","pagado"]]: df[c] = df[c].fillna("").astype(str)
    df.loc[df.estado.str.strip()=="", "estado"] = "disponible"
    df.loc[df.ubicacion.str.strip()=="", "ubicacion"] = "tienda"
    return df
def table_schema(df, cols, numeric=None):
    df = ensure(df, cols)
    numeric = numeric or []
    for c in numeric: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in [x for x in cols if x not in numeric]: df[c] = df[c].fillna("").astype(str)
    return df

# ---------- storage ----------
def gsheets_on():
    try: return "connections" in st.secrets and "gsheets" in st.secrets["connections"]
    except Exception: return False
@st.cache_resource
def gs_conn():
    from streamlit_gsheets import GSheetsConnection
    return st.connection("gsheets", type=GSheetsConnection)
def lpath(name):
    os.makedirs("data", exist_ok=True); return f"data/{name}.csv"
def load_table(name, cols):
    if gsheets_on():
        try:
            df = gs_conn().read(worksheet=name, ttl=0)
            return ensure(pd.DataFrame(df) if df is not None else pd.DataFrame(), cols)
        except Exception as e:
            st.session_state.storage_warning = f"No pude leer Google Sheets; usando local. {e}"
    if os.path.exists(lpath(name)):
        try: return ensure(pd.read_csv(lpath(name)), cols)
        except Exception: pass
    return pd.DataFrame(columns=cols)
def save_table(name, df):
    if gsheets_on():
        try:
            gs_conn().update(worksheet=name, data=df.copy())
            return True, "Guardado en Google Sheets"
        except Exception as e:
            st.session_state.storage_warning = f"No pude escribir en Google Sheets; guardé local. {e}"
    df.to_csv(lpath(name), index=False)
    return True, "Guardado localmente"
def load_all():
    if st.session_state.get("loaded"): return
    st.session_state.inventario = inv_schema(load_table("inventario", INV_COLS))
    st.session_state.clientes = table_schema(load_table("clientes", CLIENT_COLS), CLIENT_COLS)
    st.session_state.movimientos = table_schema(load_table("movimientos", MOV_COLS), MOV_COLS)
    st.session_state.ventas = table_schema(load_table("ventas", SALE_COLS), SALE_COLS, ["precio","descuento_pct","descuento_monto","neto","pagado","saldo"])
    st.session_state.notas = table_schema(load_table("notas", NOTE_COLS), NOTE_COLS, ["total","pagado","saldo"])
    st.session_state.loaded = True
def save_inv(): st.session_state.inventario = inv_schema(st.session_state.inventario); return save_table("inventario", st.session_state.inventario)
def save_clients(): return save_table("clientes", table_schema(st.session_state.clientes, CLIENT_COLS))
def save_mov(): return save_table("movimientos", table_schema(st.session_state.movimientos, MOV_COLS))
def save_sales(): return save_table("ventas", table_schema(st.session_state.ventas, SALE_COLS, ["precio","descuento_pct","descuento_monto","neto","pagado","saldo"]))
def save_notes(): return save_table("notas", table_schema(st.session_state.notas, NOTE_COLS, ["total","pagado","saldo"]))
def log(tipo, cn="", ci="", cliente="", detalle=""):
    row = {"fecha":now(),"usuario":st.session_state.get("user", ""),"tipo":tipo,"codigo_numero":cn,"codigo_interno":ci,"cliente":cliente,"detalle":detalle}
    st.session_state.movimientos = pd.concat([st.session_state.movimientos, pd.DataFrame([row])], ignore_index=True); save_mov()

# ---------- drive ----------
def drive_on():
    try: return "drive" in st.secrets and "folder_id" in st.secrets["drive"]
    except Exception: return False
@st.cache_resource
def drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = dict(st.secrets["connections"]["gsheets"])
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds)
def folder_id(): return st.secrets["drive"]["folder_id"]
def drive_folder(name, parent):
    svc = drive_service(); q = f"name='{name.replace(chr(39), chr(92)+chr(39))}' and mimeType='application/vnd.google-apps.folder' and '{parent}' in parents and trashed=false"
    res = svc.files().list(q=q, spaces="drive", fields="files(id,name)").execute().get("files", [])
    if res: return res[0]["id"]
    return svc.files().create(body={"name":name,"mimeType":"application/vnd.google-apps.folder","parents":[parent]}, fields="id").execute()["id"]
def public_file(fid):
    try: drive_service().permissions().create(fileId=fid, body={"type":"anyone","role":"reader"}, fields="id").execute()
    except Exception: pass
def upload_bytes(data, name, mime, parent):
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
    f = drive_service().files().create(body={"name":name,"parents":[parent]}, media_body=media, fields="id,webViewLink").execute()
    public_file(f["id"])
    return f["id"], f"https://drive.google.com/uc?id={f['id']}"
def upload_photo(data, code):
    root = folder_id(); fotos = drive_folder("Fotos", root); piezas = drive_folder("Piezas", fotos)
    return upload_bytes(data, f"{safe_name(code)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", "image/jpeg", piezas)
def upload_note(pdf, cliente, num):
    root = folder_id(); notas = drive_folder("Notas de venta", root); cf = drive_folder(safe_name(cliente), notas)
    return upload_bytes(pdf, f"Nota_{safe_name(cliente)}_{num}.pdf", "application/pdf", cf)

# ---------- auth/nav ----------
def login():
    st.title("Concherie Boutique")
    with st.form("login"):
        u = st.text_input("Usuario").strip().lower(); p = st.text_input("Clave", type="password")
        if st.form_submit_button("Entrar", use_container_width=True):
            if u in USERS and USERS[u]["password"] == p:
                st.session_state.user = u; st.session_state.role = USERS[u]["role"]; st.session_state.page = "home"; st.rerun()
            else: st.error("Usuario o clave incorrectos")
def logout():
    for k in ["user","role","page","selected_code","default_sale_action"]: st.session_state.pop(k, None)
    st.rerun()
def sidebar():
    st.sidebar.title("Concherie")
    st.sidebar.write(f"Usuario: **{st.session_state.get('user')}**")
    st.sidebar.success("Datos: Google Sheets" if gsheets_on() else "Datos: local")
    st.sidebar.success("Drive: activo" if drive_on() else "Drive: no activo")
    if st.session_state.get("storage_warning"): st.sidebar.info(st.session_state.storage_warning)
    st.sidebar.markdown("---")
    for label, page in [("🏠 Inicio","home"),("🔳 Escanear QR","scan"),("🔢 Buscar código","buscar")]:
        if st.sidebar.button(label, use_container_width=True): set_page(page)
    if can_sell():
        for label, page in [("🛍️ Venta / reserva","venta"),("👥 Clientes","clientes"),("📄 Catálogo","catalogo"),("📦 Disponibles","disponibles")]:
            if st.sidebar.button(label, use_container_width=True): set_page(page)
    if is_admin():
        for label, page in [("📥 Recepción inventario","recepcion"),("🏷️ Generar QR","qr"),("📦 Inventario completo","inventario"),("📊 Reportes","reportes"),("⚙️ Administración","admin")]:
            if st.sidebar.button(label, use_container_width=True): set_page(page)
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar sesión", use_container_width=True): logout()
def home():
    st.title("Concherie Boutique")
    r = role()
    if r == "info":
        c1,c2 = st.columns(2)
        if c1.button("🔳 Escanear QR", use_container_width=True): set_page("scan")
        if c2.button("🔢 Buscar código", use_container_width=True): set_page("buscar")
    elif r == "ventas":
        c1,c2 = st.columns(2)
        for label,page,col in [("🔳 Escanear QR","scan",c1),("🔢 Buscar código","buscar",c1),("🛍️ Registrar venta","venta",c1),("👥 Clientes","clientes",c2),("📄 Catálogo disponible","catalogo",c2),("📦 Ver disponibles","disponibles",c2)]:
            if col.button(label, use_container_width=True): set_page(page)
    else:
        c1,c2,c3 = st.columns(3)
        for label,page,col in [("🔳 Escanear QR","scan",c1),("🔢 Buscar código","buscar",c1),("🛍️ Ventas","venta",c1),("📥 Recepción","recepcion",c2),("🏷️ Generar QR","qr",c2),("📦 Inventario","inventario",c2),("👥 Clientes","clientes",c3),("📄 Catálogo","catalogo",c3),("📊 Reportes","reportes",c3),("⚙️ Admin","admin",c3)]:
            if col.button(label, use_container_width=True): set_page(page)
        df = st.session_state.inventario
        if not df.empty:
            a,b,c,d=st.columns(4); a.metric("Total",len(df)); b.metric("Disponibles",len(df[df.estado=='disponible'])); c.metric("Reserv/prob",len(df[df.estado.isin(['reservado','probando'])])); d.metric("Vendidas",len(df[df.estado=='vendido']))

# ---------- reception ----------
def norm_cols(df):
    df=df.copy(); df.columns=[str(c).strip().lower() for c in df.columns]
    mp={"maison":"marca","brand":"marca","codigo":"codigo_concha","código":"codigo_concha","sku":"codigo_concha","producto":"producto","descripcion":"producto","descripción":"producto","color":"color","cantidad":"cantidad","pedido":"cantidad","pedidas":"cantidad","llegaron":"llegaron","llego":"llegaron","llegó":"llegaron","recibido":"llegaron","precio":"precio","precio unitario":"precio","talla":"talla","tallas":"tallas","tallas2":"tallas2"}
    return df.rename(columns={c:mp.get(c,c) for c in df.columns})
def infer_color(r):
    if clean(r.get("color")): return token(r.get("color"))
    prod=token(r.get("producto")); colors=["SILVER","PURPLE","VIOLETA","SAND","OLIVE","BLUE","ROJA","RED","AMARILLA","YELLOW","ANIS","WHITE","LAVANDA","PINK","FUCSIA","ORCHIDEA","GREEN","LEMON","ORO","GOLD"]
    found=[c for c in colors if c in prod]
    return found[-1] if found else ""
def row_tallas(r,n):
    vals=[]
    for col in r.index:
        if str(col).lower().startswith("talla"):
            vals.append(clean(r.get(col)))
    toks=re.findall(r"\b(?:T)?\d{1,3}\b|\bXS\b|\bS\b|\bM\b|\bL\b|\bXL\b", " ".join(vals).upper())
    toks=[t if (t.startswith("T") or not t.isdigit()) else f"T{t}" for t in toks]
    toks += [""]*max(0,n-len(toks))
    return toks[:n]
def next_num(existing):
    nums=[int(x) for x in existing.codigo_numero.astype(str) if str(x).isdigit()] if not existing.empty and "codigo_numero" in existing.columns else []
    return max(nums)+1 if nums else 1
def internal_code(cc,color,talla,num):
    return "-".join([x for x in [token(cc),token(color),token(talla),num] if x])
def price_for(r, existing):
    fp=fnum(r.get("precio"),0)
    if fp>=100: return fp
    cc=clean(r.get("codigo_concha"))
    cand=existing[existing.codigo_concha.astype(str)==cc] if not existing.empty else pd.DataFrame()
    return fnum(cand.precio.iloc[0]) if not cand.empty else fp
def build_reception(raw, mode):
    df=norm_cols(raw); miss=[c for c in ["codigo_concha","producto","cantidad"] if c not in df.columns]
    if miss: raise ValueError("Faltan columnas: "+", ".join(miss))
    if "llegaron" not in df.columns: df["llegaron"]=df["cantidad"]
    if "marca" not in df.columns: df["marca"]=""
    existing = st.session_state.inventario
    cur = next_num(existing if mode=="Agregar" else pd.DataFrame(columns=INV_COLS))
    rows=[]; falt=[]
    for _,r in df.iterrows():
        cc=clean(r.get("codigo_concha")); prod=clean(r.get("producto")); marca=clean(r.get("marca")); color=infer_color(r); pedido=inum(r.get("cantidad")); lleg=inum(r.get("llegaron"),pedido)
        if not cc or not prod: continue
        lleg=max(0,lleg); faltan=max(0,pedido-lleg); price=price_for(r,existing); tallas=row_tallas(r,lleg)
        if faltan: falt.append({"marca":marca,"codigo_concha":cc,"producto":prod,"color":color,"pedido":pedido,"llegaron":lleg,"faltan":faltan})
        for i in range(lleg):
            num=f"{cur:03d}"; talla=tallas[i] if i<len(tallas) else ""; ci=internal_code(cc,color,talla,num)
            rows.append({"codigo_numero":num,"codigo_concha":cc,"codigo_interno":ci,"marca":marca,"producto":prod,"color":color,"talla":talla,"precio":price,"estado":"disponible","ubicacion":"tienda","cliente":"","descuento_pct":0,"descuento_monto":0,"pagado":0,"foto_url":"","foto_drive_id":"","notas":"","fecha_creacion":now(),"fecha_actualizacion":now()}); cur+=1
    return inv_schema(pd.DataFrame(rows)), pd.DataFrame(falt)
def recepcion_page():
    st.title("📥 Recepción de inventario")
    if not is_admin(): st.warning("Solo jc"); return
    up=st.file_uploader("Subir Excel", type=["xlsx"]); mode=st.radio("Modo",["Agregar","Reemplazar inventario completo"],horizontal=True)
    if up:
        raw=pd.read_excel(up); st.dataframe(norm_cols(raw).head(50), use_container_width=True)
        try:
            new,falt=build_reception(raw,mode); st.write(f"Piezas a crear: **{len(new)}**"); st.dataframe(new[["codigo_numero","codigo_interno","producto","color","talla","precio"]], use_container_width=True)
            if not falt.empty: st.subheader("Faltantes"); st.dataframe(falt, use_container_width=True)
            if st.button("Guardar recepción", type="primary", use_container_width=True):
                st.session_state.inventario = new if mode.startswith("Reemplazar") else inv_schema(pd.concat([st.session_state.inventario,new], ignore_index=True))
                save_inv(); log("recepcion", detalle=f"{len(new)} piezas"); st.success("Guardado"); st.rerun()
        except Exception as e: st.error(str(e))

# ---------- search/photo/scan ----------
def parse_qr(s): return clean(s).split("|")[-1].strip()
def find_idx(code):
    code=parse_qr(code).upper(); df=st.session_state.inventario
    for col in ["codigo_numero","codigo_interno"]:
        m=df[df[col].astype(str).str.upper()==code]
        if not m.empty: return m.index[0]
    return None
def compress_img(up):
    from PIL import Image, ImageOps
    im=Image.open(up); im=ImageOps.exif_transpose(im).convert("RGB"); w,h=im.size; scale=min(1400/max(w,h),1)
    if scale<1: im=im.resize((int(w*scale),int(h*scale)))
    b=BytesIO(); im.save(b,format="JPEG",quality=80,optimize=True); return b.getvalue()
def photo_uploader(idx):
    up=st.file_uploader("Tomar o subir foto", type=["jpg","jpeg","png","heic","heif"], key=f"photo_{idx}")
    if up and st.button("Guardar foto", key=f"save_photo_{idx}", use_container_width=True):
        if not drive_on(): st.error("Drive no activo"); return
        try:
            code=st.session_state.inventario.at[idx,"codigo_numero"]; fid,url=upload_photo(compress_img(up), code)
            st.session_state.inventario.at[idx,"foto_url"]=url; st.session_state.inventario.at[idx,"foto_drive_id"]=fid; st.session_state.inventario.at[idx,"fecha_actualizacion"]=now(); save_inv(); log("foto", code, st.session_state.inventario.at[idx,"codigo_interno"], detalle="Foto subida"); st.success("Foto guardada"); st.rerun()
        except Exception as e: st.error(f"No pude guardar foto: {e}")
def piece_card(idx, actions=True):
    r=st.session_state.inventario.loc[idx]
    st.subheader(f"{r.codigo_numero} · {r.producto}")
    a,b=st.columns([1,1.4])
    with a:
        if clean(r.foto_url): st.image(r.foto_url, use_container_width=True)
        else:
            st.info("Sin foto")
            if can_sell(): photo_uploader(idx)
    with b:
        st.markdown(f"## **{r.codigo_numero}**")
        st.write(f"**Código interno:** {r.codigo_interno}")
        st.write(f"**Código Concha:** {r.codigo_concha}")
        st.write(f"**Color:** {r.color or '-'}")
        st.write(f"**Talla:** {r.talla or '-'}")
        st.write(f"**Estado:** {r.estado}")
        st.write(f"**Ubicación:** {r.ubicacion}")
        if can_price(): st.metric("Precio", money(r.precio))
    if actions and can_sell():
        c1,c2,c3=st.columns(3)
        for label, act, col in [("🛍️ Vender","vendido",c1),("📌 Reservar","reservado",c2),("🏠 Probándose","probando",c3)]:
            if col.button(label, use_container_width=True): st.session_state.selected_code=r.codigo_numero; st.session_state.default_action=act; set_page("venta")
def buscar_page():
    st.title("🔢 Buscar código")
    code=st.text_input("Código", placeholder="001")
    if code:
        idx=find_idx(code); piece_card(idx) if idx is not None else st.error("No encontrado")
def decode_qr(up):
    try:
        import cv2, numpy as np
        from PIL import Image, ImageOps
        im=Image.open(up); im=ImageOps.exif_transpose(im).convert("RGB"); arr=cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        data,_,_=cv2.QRCodeDetector().detectAndDecode(arr)
        return data, "" if data else "No pude leer el QR. Escribe el código numérico."
    except Exception as e: return "", str(e)
def scan_page():
    st.title("🔳 Escanear QR")
    code=st.text_input("O escribe el código", placeholder="001")
    if code:
        idx=find_idx(code); piece_card(idx) if idx is not None else st.error("No encontrado")
    up=st.camera_input("Tomar foto del QR") or st.file_uploader("Subir foto QR", type=["jpg","jpeg","png","heic","heif"], key="qrfile")
    if up:
        data,err=decode_qr(up)
        if data:
            idx=find_idx(data); piece_card(idx) if idx is not None else st.error(f"Leí {data}, pero no existe")
        else: st.error(err)

# ---------- sales/clients/invoices ----------
def ensure_client(cliente):
    cliente=clean(cliente); df=st.session_state.clientes
    if cliente and df[df.cliente.astype(str).str.lower()==cliente.lower()].empty:
        st.session_state.clientes=pd.concat([df,pd.DataFrame([{"cliente":cliente,"telefono":"","email":"","notas":"","fecha_creacion":now()}])], ignore_index=True); save_clients()
def clients_list():
    names=[]
    if not st.session_state.clientes.empty: names += st.session_state.clientes.cliente.astype(str).str.strip().tolist()
    if not st.session_state.inventario.empty: names += st.session_state.inventario.cliente.astype(str).str.strip().tolist()
    return sorted(set([n for n in names if n]), key=str.lower)
def venta_page():
    st.title("🛍️ Venta / reserva")
    if not can_sell(): st.warning("Sin permiso"); return
    code=st.text_input("Código pieza", value=st.session_state.get("selected_code", ""), placeholder="001")
    idx=find_idx(code) if code else None
    if idx is None:
        if code: st.error("No encontrado")
        else: st.info("Escribe/escanea código")
        return
    piece_card(idx, actions=False); r=st.session_state.inventario.loc[idx]
    opts=clients_list(); choice=st.selectbox("Cliente", ["+ Nueva cliente"]+opts)
    cliente=st.text_input("Nueva cliente") if choice=="+ Nueva cliente" else choice
    default=st.session_state.get("default_action","vendido"); acts=["vendido","reservado","probando","disponible"]
    with st.form("venta"):
        accion=st.selectbox("Acción",acts,index=acts.index(default) if default in acts else 0)
        precio=st.number_input("Precio", value=fnum(r.precio), step=10.0)
        dp=st.number_input("Descuento (%)", value=fnum(r.descuento_pct), min_value=0.0, max_value=100.0, step=1.0)
        dm=round(precio*dp/100,2); neto=precio-dm
        pagado=st.number_input("Pagado", value=fnum(r.pagado), step=10.0); saldo=neto-pagado
        ubic=st.text_input("Ubicación", value="casa cliente" if accion=="probando" else ("tienda" if accion=="disponible" else r.ubicacion))
        notas=st.text_area("Notas", value=r.notas)
        st.info(f"Desc: {money(dm)} | Neto: {money(neto)} | Saldo: {money(saldo)}")
        if st.form_submit_button("Guardar", use_container_width=True):
            if accion in ["vendido","reservado","probando"] and not clean(cliente): st.error("Falta cliente"); return
            df=st.session_state.inventario
            for col,val in [("cliente",cliente),("estado",accion),("ubicacion",ubic),("precio",precio),("descuento_pct",dp),("descuento_monto",dm),("pagado",pagado),("notas",notas),("fecha_actualizacion",now())]: df.at[idx,col]=val
            st.session_state.inventario=inv_schema(df); save_inv(); ensure_client(cliente)
            sale={"fecha":now(),"usuario":st.session_state.user,"cliente":cliente,"codigo_numero":r.codigo_numero,"codigo_interno":r.codigo_interno,"accion":accion,"precio":precio,"descuento_pct":dp,"descuento_monto":dm,"neto":neto,"pagado":pagado,"saldo":saldo,"nota":notas}
            st.session_state.ventas=pd.concat([st.session_state.ventas,pd.DataFrame([sale])], ignore_index=True); save_sales(); log(accion,r.codigo_numero,r.codigo_interno,cliente,f"saldo {saldo}"); st.success("Guardado"); st.rerun()
def clientes_page():
    st.title("👥 Clientes")
    if not can_sell(): st.warning("Sin permiso"); return
    with st.expander("Agregar cliente"):
        with st.form("cli"):
            c=st.text_input("Nombre"); t=st.text_input("Teléfono"); e=st.text_input("Email"); n=st.text_area("Notas")
            if st.form_submit_button("Guardar") and c:
                df=st.session_state.clientes; mask=df.cliente.astype(str).str.lower()==c.lower()
                if mask.any(): i=df[mask].index[0]; df.at[i,"telefono"]=t; df.at[i,"email"]=e; df.at[i,"notas"]=n
                else: df=pd.concat([df,pd.DataFrame([{"cliente":c,"telefono":t,"email":e,"notas":n,"fecha_creacion":now()}])], ignore_index=True)
                st.session_state.clientes=df; save_clients(); st.success("Cliente guardado"); st.rerun()
    opts=clients_list()
    if not opts: st.info("No hay clientes"); return
    cliente=st.selectbox("Cliente", opts); client_profile(cliente)
def invoice_pdf(cliente,data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    b=BytesIO(); c=canvas.Canvas(b,pagesize=letter); w,h=letter; y=h-.65*inch
    c.setFont("Helvetica-Bold",18); c.drawString(.7*inch,y,"CONCHERIE BOUTIQUE"); y-=.25*inch
    c.setFont("Helvetica-Oblique",11); c.drawString(.7*inch,y,"Nota de venta"); y-=.35*inch
    c.setFont("Helvetica",10); c.drawString(.7*inch,y,f"Cliente: {cliente}"); c.drawRightString(7.6*inch,y,f"Fecha: {today()}"); y-=.35*inch
    c.setStrokeColor(colors.lightgrey); c.line(.7*inch,y,7.6*inch,y); y-=.25*inch
    xs=[.7,1.25,3.45,4.05,4.85,5.55,6.25,7.0]; headers=["Cod.","Producto","Talla","Precio","Desc.","Neto","Pagado","Saldo"]
    c.setFont("Helvetica-Bold",8.5)
    for x,hdr in zip(xs,headers): c.drawString(x*inch,y,hdr)
    y-=.2*inch; c.setFont("Helvetica",8)
    for _,r in data.iterrows():
        if y<1.2*inch: c.showPage(); y=10.3*inch; c.setFont("Helvetica",8)
        vals=[r.codigo_numero, clean(r.producto)[:28], r.talla, money(r.precio), pct(r.descuento_pct), money(r.neto), money(r.pagado), money(r.saldo)]
        for x,v in zip(xs,vals): c.drawString(x*inch,y,str(v))
        y-=.18*inch
    y-=.25*inch; c.line(.7*inch,y,7.6*inch,y); y-=.3*inch
    sub=data.precio.sum(); desc=data.descuento_monto.sum(); total=data.neto.sum(); pag=data.pagado.sum(); saldo=data.saldo.sum(); sx=5.15*inch
    for label,val,bold in [("Subtotal",sub,False),("Descuentos",-desc,False),("Total neto",total,True),("Pagado",pag,False),("SALDO",saldo,True)]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 13 if label=="SALDO" else 10); c.drawString(sx,y,label+":"); c.drawRightString(7.6*inch,y,money(val)); y-=.25*inch
    c.save(); b.seek(0); return b.getvalue()
def client_profile(cliente):
    data=st.session_state.inventario[st.session_state.inventario.cliente.astype(str).str.lower()==cliente.lower()].copy()
    if data.empty: st.info("Sin piezas"); return
    data["neto"]=data.precio-data.descuento_monto; data["saldo"]=data.neto-data.pagado
    st.dataframe(data[["codigo_numero","codigo_interno","producto","color","talla","estado","precio","descuento_pct","neto","pagado","saldo"]], use_container_width=True)
    c1,c2,c3,c4=st.columns(4); c1.metric("Total",money(data.neto.sum())); c2.metric("Pagado",money(data.pagado.sum())); c3.metric("Saldo",money(data.saldo.sum())); c4.metric("Piezas",len(data))
    pdf=invoice_pdf(cliente,data); num=datetime.now().strftime("%Y%m%d_%H%M%S")
    a,b=st.columns(2); a.download_button("Descargar nota PDF",pdf,f"nota_{safe_name(cliente)}_{num}.pdf","application/pdf",use_container_width=True)
    if b.button("Guardar nota en Drive",use_container_width=True):
        if not drive_on(): st.error("Drive no activo")
        else:
            fid,url=upload_note(pdf,cliente,num); nr={"fecha":now(),"usuario":st.session_state.user,"cliente":cliente,"numero_nota":num,"total":data.neto.sum(),"pagado":data.pagado.sum(),"saldo":data.saldo.sum(),"drive_file_id":fid,"drive_url":url}
            st.session_state.notas=pd.concat([st.session_state.notas,pd.DataFrame([nr])], ignore_index=True); save_notes(); st.success("Nota guardada en Drive"); st.markdown(f"[Abrir nota]({url})")

# ---------- QR/catalog/reports/admin ----------
def qr_png(text):
    import qrcode
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=14,border=2); qr.add_data(clean(text)); qr.make(fit=True); img=qr.make_image(fill_color="black",back_color="white").convert("RGB"); b=BytesIO(); img.save(b,format="PNG"); return b.getvalue()
def labels_pdf(data):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    b=BytesIO(); c=canvas.Canvas(b,pagesize=letter); W,H=letter; lw,lh=8*cm,5*cm; mx,my=.6*cm,.8*cm; gx,gy=.25*cm,.25*cm; cols=max(1,int((W-2*mx)//(lw+gx))); rows=max(1,int((H-2*my)//(lh+gy)))
    for i,(_,r) in enumerate(data.iterrows()):
        if i and i%(cols*rows)==0: c.showPage()
        col=i%cols; row=(i//cols)%rows; x=mx+col*(lw+gx); y=H-my-(row+1)*lh-row*gy
        c.roundRect(x,y,lw,lh,5,stroke=1,fill=0); c.drawImage(ImageReader(BytesIO(qr_png(r.codigo_numero))),x+.35*cm,y+.5*cm,4*cm,4*cm,mask="auto")
        tx=x+4.65*cm; ty=y+3.65*cm; c.setFont("Helvetica-Bold",22); c.drawString(tx,ty,r.codigo_numero); c.setFont("Helvetica-Bold",11); c.drawString(tx,ty-.65*cm,clean(r.codigo_concha)[:18]); c.setFont("Helvetica",9.5); c.drawString(tx,ty-1.15*cm,clean(r.color)[:20]); c.setFont("Helvetica-Oblique",10); c.drawString(tx,ty-1.65*cm,clean(r.talla)[:15] or "Sin talla"); c.setFont("Helvetica",6.5); c.drawString(tx,y+.35*cm,clean(r.codigo_interno)[:32])
    c.save(); b.seek(0); return b.getvalue()
def qr_page():
    st.title("🏷️ Generar QR")
    if not is_admin(): st.warning("Solo jc"); return
    df=st.session_state.inventario; scope=st.selectbox("Piezas",["disponible","todo","por código concha"]); data=df.copy()
    if scope=="disponible": data=data[data.estado=="disponible"]
    elif scope=="por código concha":
        q=st.text_input("Código"); data=data[data.codigo_concha.astype(str).str.contains(q,case=False,na=False)] if q else data.iloc[0:0]
    data=data.sort_values(["codigo_interno","codigo_numero"]); st.write(f"Etiquetas: **{len(data)}**"); st.dataframe(data[["codigo_numero","codigo_interno","producto","color","talla"]], use_container_width=True)
    if not data.empty: st.download_button("Descargar etiquetas 5x8 cm",labels_pdf(data),"etiquetas_qr_5x8.pdf","application/pdf",use_container_width=True)
def catalog_pdf(data, price, order):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    import requests
    b=BytesIO(); c=canvas.Canvas(b,pagesize=letter); W,H=letter; data=data.sort_values(order if order in data.columns else "producto")
    pos=[(.55*inch,H-.55*inch-4.9*inch),(4.05*inch,H-.55*inch-4.9*inch),(.55*inch,H-5.65*inch-4.9*inch),(4.05*inch,H-5.65*inch-4.9*inch)]
    groups=list(data.groupby(["codigo_concha","producto","color"],dropna=False))
    for i,((cc,prod,color),g) in enumerate(groups):
        if i and i%4==0: c.showPage()
        x,y=pos[i%4]; cw,ch=3.35*inch,4.9*inch; c.roundRect(x,y,cw,ch,8,stroke=1,fill=0)
        imgurl=next((u for u in g.foto_url.astype(str) if clean(u)),""); ix,iy,iw,ih=x+.18*inch,y+2.2*inch,cw-.36*inch,2.45*inch
        if imgurl:
            try:
                r=requests.get(imgurl,timeout=8); c.drawImage(ImageReader(BytesIO(r.content)),ix,iy,iw,ih,preserveAspectRatio=True,anchor="c",mask="auto") if r.ok else (_ for _ in ()).throw(Exception())
            except Exception: c.setFont("Helvetica-Oblique",9); c.drawCentredString(x+cw/2,iy+ih/2,"Foto no disponible")
        else: c.setFont("Helvetica-Oblique",9); c.drawCentredString(x+cw/2,iy+ih/2,"Sin foto")
        c.setFont("Helvetica-Bold",10.5); c.drawString(x+.18*inch,y+1.85*inch,clean(cc)[:25]); c.setFont("Helvetica-Oblique",9.2); c.drawString(x+.18*inch,y+1.62*inch,clean(prod)[:40]); c.setFont("Helvetica",8.5); c.drawString(x+.18*inch,y+1.38*inch,f"Color: {clean(color) or '-'}"); tallas=", ".join(sorted(set([clean(t) for t in g.talla if clean(t)]))) or "sin cargar"; c.drawString(x+.18*inch,y+1.15*inch,f"Tallas disponibles: {tallas[:32]}"); c.drawString(x+.18*inch,y+.92*inch,f"Disponibles: {len(g)}")
        if price: c.setFont("Helvetica-Bold",15); c.drawString(x+.18*inch,y+.45*inch,money(g.precio.iloc[0]))
    c.save(); b.seek(0); return b.getvalue()
def catalogo_page():
    st.title("📄 Catálogo disponible")
    if not can_sell(): st.warning("Sin permiso"); return
    df=st.session_state.inventario; data=df[df.estado=="disponible"].copy(); a,b,c=st.columns(3); price=a.radio("Precio",["Con precio","Sin precio"],horizontal=True)=="Con precio"; talla=b.text_input("Filtrar talla"); order=c.selectbox("Orden",["producto","codigo_interno","talla"])
    if talla: data=data[data.talla.astype(str).str.upper().str.contains(talla.upper(),na=False)]
    if data.empty: st.info("No hay disponibles"); return
    st.dataframe(data[["codigo_numero","codigo_interno","producto","color","talla","precio","foto_url"]], use_container_width=True); st.download_button("Descargar catálogo PDF",catalog_pdf(data,price,order),"catalogo_disponible.pdf","application/pdf",use_container_width=True)
def disponibles_page(): st.title("📦 Disponibles"); st.dataframe(st.session_state.inventario[st.session_state.inventario.estado=="disponible"][["codigo_numero","codigo_interno","producto","color","talla","precio"]], use_container_width=True)
def inventario_page():
    st.title("📦 Inventario completo"); df=st.session_state.inventario; q=st.text_input("Buscar"); view=df[df.apply(lambda r: q.lower() in " ".join(map(lambda v:str(v).lower(),r.values)),axis=1)] if q else df; st.dataframe(view,use_container_width=True)
def reportes_page():
    st.title("📊 Reportes"); df=st.session_state.inventario; a,b,c,d=st.columns(4); a.metric("Total",len(df)); b.metric("Disponibles",len(df[df.estado=='disponible'])); c.metric("Reserv/prob",len(df[df.estado.isin(['reservado','probando'])])); d.metric("Vendidas",len(df[df.estado=='vendido'])); st.dataframe(st.session_state.ventas,use_container_width=True)
    out=BytesIO();
    with pd.ExcelWriter(out,engine="openpyxl") as w: st.session_state.inventario.to_excel(w,"inventario",index=False); st.session_state.clientes.to_excel(w,"clientes",index=False); st.session_state.ventas.to_excel(w,"ventas",index=False); st.session_state.movimientos.to_excel(w,"movimientos",index=False); st.session_state.notas.to_excel(w,"notas",index=False)
    st.download_button("Descargar respaldo Excel",out.getvalue(),"respaldo_concherie.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
def admin_page():
    st.title("⚙️ Administración")
    if not is_admin(): st.warning("Solo jc"); return
    df=st.session_state.inventario
    tab1,tab2=st.tabs(["Reversar/borrar pieza","Reset"])
    with tab1:
        if df.empty: st.info("No hay inventario"); return
        code=st.selectbox("Pieza",df.codigo_numero.tolist()); idx=df[df.codigo_numero==code].index[0]; piece_card(idx,False); act=st.selectbox("Acción",["Marcar disponible y limpiar","Borrar pieza"]); conf=st.text_input("Escribe el código para confirmar")
        if st.button("Ejecutar",type="primary"):
            if conf!=code: st.error("Confirmación incorrecta")
            elif act.startswith("Marcar"):
                for col,val in [("estado","disponible"),("ubicacion","tienda"),("cliente",""),("descuento_pct",0),("descuento_monto",0),("pagado",0)]: df.at[idx,col]=val
                df.at[idx,"fecha_actualizacion"]=now(); st.session_state.inventario=inv_schema(df); save_inv(); log("admin_reversar",code,df.at[idx,"codigo_interno"]); st.success("Listo"); st.rerun()
            else:
                ci=df.at[idx,"codigo_interno"]; st.session_state.inventario=inv_schema(df.drop(idx).reset_index(drop=True)); save_inv(); log("admin_borrar",code,ci); st.success("Borrado"); st.rerun()
    with tab2:
        if st.button("Resetear historial movimientos"): st.session_state.movimientos=pd.DataFrame(columns=MOV_COLS); save_mov(); st.success("Historial reseteado")
        conf=st.text_input("Para borrar todo escribe BORRAR TODO")
        if st.button("Borrar todo",type="primary"):
            if conf=="BORRAR TODO":
                st.session_state.inventario=pd.DataFrame(columns=INV_COLS); st.session_state.clientes=pd.DataFrame(columns=CLIENT_COLS); st.session_state.ventas=pd.DataFrame(columns=SALE_COLS); st.session_state.movimientos=pd.DataFrame(columns=MOV_COLS); st.session_state.notas=pd.DataFrame(columns=NOTE_COLS); save_inv(); save_clients(); save_sales(); save_mov(); save_notes(); st.success("Todo borrado"); st.rerun()
            else: st.error("Confirmación incorrecta")

def main():
    load_all()
    if "user" not in st.session_state: login(); return
    sidebar()
    pages={"home":home,"scan":scan_page,"buscar":buscar_page,"venta":venta_page,"clientes":clientes_page,"catalogo":catalogo_page,"recepcion":recepcion_page,"qr":qr_page,"inventario":inventario_page,"disponibles":disponibles_page,"reportes":reportes_page,"admin":admin_page}
    pages.get(st.session_state.get("page","home"),home)()
if __name__ == "__main__": main()
