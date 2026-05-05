import streamlit as st
import pandas as pd
import base64
from datetime import datetime

# ---------------------------

# CONFIG

# ---------------------------

st.set_page_config(page_title="Concherie Boutique", layout="wide")

USERS = {
"jc": {"password": "master", "role": "admin"},
"moira": {"password": "ventas", "role": "ventas"},
"info": {"password": "precios", "role": "consulta"},
}

# ---------------------------

# LOGIN

# ---------------------------

def login():
st.title("Concherie Boutique")

```
user = st.text_input("Usuario")
pwd = st.text_input("Clave", type="password")

if st.button("Entrar"):
    if user in USERS and USERS[user]["password"] == pwd:
        st.session_state.user = user
        st.session_state.role = USERS[user]["role"]
        st.session_state.page = "home"
        st.rerun()
    else:
        st.error("Credenciales incorrectas")
```

# ---------------------------

# INIT DATA

# ---------------------------

def init_data():
if "inventario" not in st.session_state:
st.session_state.inventario = pd.DataFrame(columns=[
"codigo_unico","codigo","producto","precio",
"estado","cliente","descuento","pagado","foto"
])

# ---------------------------

# HOME

# ---------------------------

def home():
st.title("Concherie Boutique")

```
col1, col2 = st.columns(2)

with col1:
    if st.button("🔳 Escanear QR", use_container_width=True):
        st.session_state.page = "scan"
        st.rerun()

    if st.button("📦 Inventario", use_container_width=True):
        st.session_state.page = "inventario"
        st.rerun()

with col2:
    if st.button("🛍️ Ventas", use_container_width=True):
        st.session_state.page = "ventas"
        st.rerun()

    if st.button("👥 Clientes", use_container_width=True):
        st.session_state.page = "clientes"
        st.rerun()
```

# ---------------------------

# INVENTARIO

# ---------------------------

def inventario():
st.button("⬅️ Volver", on_click=lambda: set_page("home"))

```
df = st.session_state.inventario

st.dataframe(df)
```

# ---------------------------

# SCAN QR

# ---------------------------

def scan():
st.button("⬅️ Volver", on_click=lambda: set_page("home"))

```
codigo = st.text_input("Simulación QR (pega código)")

if codigo:
    df = st.session_state.inventario
    pieza = df[df["codigo_unico"] == codigo]

    if not pieza.empty:
        st.session_state.selected = pieza.index[0]
        st.session_state.page = "pieza"
        st.rerun()
    else:
        st.error("No encontrada")
```

# ---------------------------

# PIEZA

# ---------------------------

def pieza():
st.button("⬅️ Volver", on_click=lambda: set_page("home"))

```
idx = st.session_state.selected
df = st.session_state.inventario

row = df.loc[idx]

st.write(row)

descuento = st.number_input("Descuento", value=float(row["descuento"]))
pagado = st.number_input("Pagado", value=float(row["pagado"]))

if st.button("Guardar cambios"):
    df.at[idx, "descuento"] = descuento
    df.at[idx, "pagado"] = pagado
    st.success("Actualizado")
```

# ---------------------------

# CLIENTES

# ---------------------------

def clientes():
st.button("⬅️ Volver", on_click=lambda: set_page("home"))

```
df = st.session_state.inventario

cliente = st.text_input("Buscar cliente")

if cliente:
    data = df[df["cliente"] == cliente]

    st.dataframe(data)

    total = (data["precio"] - data["descuento"]).sum()
    pagado = data["pagado"].sum()

    st.write(f"Total: {total}")
    st.write(f"Pagado: {pagado}")
    st.write(f"Saldo: {total - pagado}")
```

# ---------------------------

# HELPERS

# ---------------------------

def set_page(p):
st.session_state.page = p

# ---------------------------

# MAIN

# ---------------------------

def main():
if "user" not in st.session_state:
login()
return

```
init_data()

page = st.session_state.get("page","home")

if page == "home":
    home()
elif page == "inventario":
    inventario()
elif page == "scan":
    scan()
elif page == "pieza":
    pieza()
elif page == "clientes":
    clientes()
```

main()
