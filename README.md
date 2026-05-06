# Concherie App Simple

Versión simplificada para tienda:

- Google Sheets = inventario
- Supabase = fotos de productos
- QR por pieza
- catálogo PDF
- sin clientes, ventas, pagos, notas ni apartados

## Usuarios

- jc / master
- ventas / moira
- info / precio

## Google Sheets

Debe existir la pestaña:

- inventario

Columnas recomendadas:

numero, codigo_interno, codigo, producto, color, talla, precio, foto_url, fecha_actualizacion

## Supabase Secrets

```toml
[supabase]
url = "https://xxxxx.supabase.co"
key = "sb_secret_o_publishable_key"
bucket = "concherie-files"
```

Para subir fotos, el bucket debe permitir uploads con la key usada o usar una secret key en Streamlit Secrets.
