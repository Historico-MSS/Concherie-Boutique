# App Tienda Concha — Streamlit

Sistema simple para tienda/boutique con:

- Login por usuario
- Roles: admin, encargada, dueña, consulta
- Inventario por pieza individual
- Estados: disponible, reservado, probando en casa, vendido, mantenimiento
- Clientes
- Ventas y pagos parciales
- Fotos por pieza desde teléfono
- QR por pieza y PDF de etiquetas 2 cm x 2 cm
- Persistencia en Google Sheets o respaldo local CSV

## Usuarios iniciales

| Usuario | Clave | Rol |
|---|---|---|
| jc | master | admin |
| Moira | ventas | encargada |
| Concha | patrona | dueña |
| info | precios | consulta |

## Archivos para subir al repositorio

Sube todo esto:

```text
app.py
requirements.txt
README.md
.gitignore
.streamlit/secrets.toml.example
```

No subas `.streamlit/secrets.toml` real con claves privadas.

## Google Sheets recomendado

Crea un Google Sheet con estas pestañas:

```text
inventario
clientes
movimientos
fotos
```

La app puede crearlas/llenarlas al guardar, pero es más ordenado dejarlas creadas desde el principio.

## Streamlit Cloud

1. Sube estos archivos a GitHub.
2. En Streamlit Cloud crea una app desde ese repo.
3. En Settings > Secrets pega la configuración de Google Sheets.
4. Abre la app y entra con `jc / master`.
5. Carga el Excel del inventario desde la pestaña **Carga inicial**.

## Nota sobre fotos

Para mantenerlo simple, la app comprime las fotos y las guarda como texto Base64 en la hoja `fotos` de Google Sheets. Esto evita configurar Google Drive o Supabase al principio. Si luego hay muchas fotos, conviene migrarlas a Google Drive o Supabase Storage.
