import io
import json
import time
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image, ImageOps

st.set_page_config(page_title="Control de Facturas - ERP", page_icon="🧾", layout="wide")

# --- SECRETOS ---
try:
    WEBHOOK_URL = st.secrets["WEBHOOK_URL"]
    WEBHOOK_TOKEN = st.secrets["WEBHOOK_TOKEN"]
    USERS = dict(st.secrets["users"])
    client = genai.Client(
        api_key=st.secrets["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=90000),
    )
except Exception as e:
    st.error(f"Falta configurar los secretos en Streamlit: {e}")
    st.stop()

MODELO = "gemini-3.8-flash"
CLASIFICACIONES = ["Mantenimiento", "Logística y Montaje", "Insumos Operativos", "Servicios", "Otro"]
ESTATUS = ["No refacturar", "Refacturar", "Refacturar/herramientas"]
MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}

# --- LOGIN ---
st.session_state.setdefault("autenticado", False)
st.session_state.setdefault("usuario_actual", "")

if not st.session_state.autenticado:
    st.subheader("🔐 Iniciar Sesión en el ERP")
    with st.form("form_login"):
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Entrar"):
            if u in USERS and USERS[u] == p:
                st.session_state.autenticado = True
                st.session_state.usuario_actual = u
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")
    st.stop()

st.sidebar.title(f"👤 {st.session_state.usuario_actual}")
if st.sidebar.button("Cerrar Sesión"):
    st.session_state.autenticado = False
    st.session_state.usuario_actual = ""
    st.rerun()

# --- ESTADO ---
st.session_state.setdefault("lote", None)      # tabla de facturas escaneadas
st.session_state.setdefault("up_key", 0)       # para vaciar el cargador de archivos
st.session_state.setdefault("ed_key", 0)       # para reiniciar el editor de la tabla
st.session_state.setdefault("resumen", None)   # mensaje tras guardar


# --- FUNCIONES ---
def escanear(archivo):
    ext = archivo.name.rsplit(".", 1)[-1].lower()
    datos = archivo.getvalue()
    mime = MIME[ext]

    # Si es imagen, se reduce para que se procese más rápido
    if ext != "pdf":
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(datos))).convert("RGB")
        img.thumbnail((1800, 1800))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        datos = buf.getvalue()
        mime = "image/jpeg"

    prompt = """Analiza esta factura y extrae los datos en JSON con estas claves exactas:
- "numero_factura": string
- "proveedor": string (nombre del comercio o proveedor)
- "rif": string (RIF o identificación fiscal)
- "fecha": string en formato YYYY-MM-DD
- "subtotal": número
- "iva": número
- "total": número
- "descripcion": string con TODOS los productos o servicios comprados (con cantidades si aparecen).
  Si hay devoluciones o notas de crédito, indícalo en la descripción y usa montos negativos.
Si un dato no aparece, usa "" para texto y 0 para números."""

    contenido = [types.Part.from_bytes(data=datos, mime_type=mime), prompt]
    try:
        # Razonamiento reducido = respuesta mucho más rápida
        resp = client.models.generate_content(
            model=MODELO,
            contents=contenido,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level="low"),
            ),
        )
    except Exception as e:
        if "thinking" in str(e).lower():
            # Si el modelo no acepta ese ajuste, se intenta sin él
            resp = client.models.generate_content(
                model=MODELO,
                contents=contenido,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        else:
            raise
    return json.loads(resp.text)


@st.cache_data(ttl=60, show_spinner=False)
def cargar_historial():
    r = requests.get(WEBHOOK_URL, params={"token": WEBHOOK_TOKEN}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "error desconocido"))
    return pd.DataFrame(data["rows"])


def guardar(payload):
    payload["token"] = WEBHOOK_TOKEN
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()
    return r.json().get("ok", False)


def txt(v):
    return "" if v is None or pd.isna(v) else str(v).strip()


def num(v):
    try:
        return 0.0 if v is None or pd.isna(v) else float(v)
    except (ValueError, TypeError):
        return 0.0


def fecha_txt(v):
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return date.today().strftime("%Y-%m-%d")


def fecha_obj(v):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except ValueError:
        return date.today()


st.title("🧾 Control de Facturas y Gastos")
tab_cargar, tab_hist = st.tabs(["📤 Cargar facturas", "📚 Histórico"])

# =============== TAB CARGAR ===============
with tab_cargar:
    if st.session_state.resumen:
        tipo, msg = st.session_state.resumen
        (st.success if tipo == "ok" else st.warning)(msg)
        st.session_state.resumen = None

    archivos = st.file_uploader(
        "Sube una o varias facturas (JPG, PNG o PDF)",
        type=list(MIME.keys()),
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.up_key}",
    )

    if archivos:
        st.caption(f"{len(archivos)} archivo(s) listo(s) para escanear.")
        if st.button("🚀 Escanear todas las facturas", type="primary"):
            filas, errores = [], []
            barra = st.progress(0.0, text="Iniciando...")
            for i, a in enumerate(archivos):
                barra.progress(i / len(archivos), text=f"Escaneando {i + 1} de {len(archivos)}: {a.name}")
                try:
                    r = escanear(a)
                    filas.append({
                        "archivo": a.name,
                        "numero_factura": txt(r.get("numero_factura")),
                        "proveedor": txt(r.get("proveedor")),
                        "rif": txt(r.get("rif")),
                        "fecha": fecha_obj(r.get("fecha")),
                        "descripcion": txt(r.get("descripcion")),
                        "subtotal": num(r.get("subtotal")),
                        "iva": num(r.get("iva")),
                        "alojamiento": "",
                        "clasificacion": CLASIFICACIONES[0],
                        "estatus": ESTATUS[0],
                    })
                except Exception as e:
                    errores.append(f"{a.name}: {e}")
                    filas.append({
                        "archivo": a.name, "numero_factura": "", "proveedor": "", "rif": "",
                        "fecha": date.today(), "descripcion": "", "subtotal": 0.0, "iva": 0.0,
                        "alojamiento": "", "clasificacion": CLASIFICACIONES[0], "estatus": ESTATUS[0],
                    })
            barra.progress(1.0, text="Escaneo terminado")
            st.session_state.lote = pd.DataFrame(filas)
            st.session_state.ed_key += 1
            if errores:
                st.warning("No se pudieron leer estas facturas (llénalas a mano o bórralas de la tabla):\n\n"
                           + "\n\n".join(errores))
            else:
                st.success("Escaneo listo. Revisa la tabla antes de guardar.")

    lote = st.session_state.lote
    if lote is not None and len(lote) > 0:
        st.markdown("---")
        st.subheader("📋 Matriz de facturas escaneadas")
        st.write("Edita cualquier celda. Completa **Alojamiento** y **Estatus**. "
                 "Para borrar una fila, selecciónala a la izquierda y pulsa Suprimir.")

        editado = st.data_editor(
            lote,
            key=f"editor_{st.session_state.ed_key}",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            disabled=["archivo"],
            column_config={
                "archivo": st.column_config.TextColumn("Archivo"),
                "numero_factura": st.column_config.TextColumn("Nº Factura"),
                "proveedor": st.column_config.TextColumn("Proveedor"),
                "rif": st.column_config.TextColumn("RIF"),
                "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                "descripcion": st.column_config.TextColumn("Descripción", width="large"),
                "subtotal": st.column_config.NumberColumn("Subtotal", format="%.2f", step=0.01),
                "iva": st.column_config.NumberColumn("IVA", format="%.2f", step=0.01),
                "alojamiento": st.column_config.TextColumn("Alojamiento"),
                "clasificacion": st.column_config.SelectboxColumn("Clasificación", options=CLASIFICACIONES),
                "estatus": st.column_config.SelectboxColumn("Estatus", options=ESTATUS),
            },
        )

        total_lote = sum(num(s) + num(i) for s, i in zip(editado["subtotal"], editado["iva"]))
        m1, m2 = st.columns(2)
        m1.metric("Facturas en la tabla", len(editado))
        m2.metric("Total del lote", f"${total_lote:,.2f}")

        if st.button("💾 Guardar todas en Google Sheets", type="primary"):
            try:
                h = cargar_historial()
                existentes = set()
                if not h.empty:
                    existentes = set(zip(h["numero_factura"].astype(str).str.strip(),
                                         h["proveedor"].astype(str).str.strip().str.lower()))
            except Exception:
                existentes = set()

            guardadas, duplicadas, incompletas, fallidas = 0, [], [], []
            conservar = []
            base_id = int(time.time() * 1000)

            with st.spinner("Guardando..."):
                for n, (idx, row) in enumerate(editado.iterrows()):
                    numero, prov = txt(row["numero_factura"]), txt(row["proveedor"])
                    if not numero or not prov:
                        incompletas.append(txt(row["archivo"]) or f"fila {n + 1}")
                        conservar.append(idx)
                        continue
                    clave = (numero, prov.lower())
                    if clave in existentes:
                        duplicadas.append(f"{numero} ({prov})")
                        continue
                    payload = {
                        "id": f"FAC-{base_id}-{n}",
                        "fecha_carga": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "cargado_por": st.session_state.usuario_actual,
                        "archivo": txt(row["archivo"]) or "N/A",
                        "fecha": fecha_txt(row["fecha"]),
                        "numero_factura": numero,
                        "proveedor": prov,
                        "rif": txt(row["rif"]),
                        "descripcion": txt(row["descripcion"]),
                        "subtotal": num(row["subtotal"]),
                        "iva": num(row["iva"]),
                        "total": num(row["subtotal"]) + num(row["iva"]),
                        "clasificacion": txt(row["clasificacion"]) or CLASIFICACIONES[0],
                        "alojamiento": txt(row["alojamiento"]),
                        "estatus": txt(row["estatus"]) or ESTATUS[0],
                    }
                    try:
                        if guardar(payload):
                            guardadas += 1
                            existentes.add(clave)
                        else:
                            fallidas.append(numero)
                            conservar.append(idx)
                    except Exception:
                        fallidas.append(numero)
                        conservar.append(idx)

            cargar_historial.clear()

            partes = [f"✅ Guardadas: {guardadas}"]
            if duplicadas:
                partes.append(f"⚠️ Omitidas por duplicadas: {', '.join(duplicadas)}")
            if incompletas:
                partes.append(f"⚠️ Faltan Nº de factura o proveedor: {', '.join(incompletas)} (siguen en la tabla)")
            if fallidas:
                partes.append(f"❌ Error al guardar: {', '.join(fallidas)} (siguen en la tabla)")
            st.session_state.resumen = ("ok" if len(partes) == 1 else "warn", "\n\n".join(partes))

            if conservar:
                st.session_state.lote = editado.loc[conservar].reset_index(drop=True)
            else:
                st.session_state.lote = None
                st.session_state.up_key += 1   # vacía el cargador de archivos
            st.session_state.ed_key += 1
            st.rerun()

# =============== TAB HISTÓRICO ===============
with tab_hist:
    col_a, col_b = st.columns([6, 1])
    with col_b:
        if st.button("🔄 Actualizar"):
            cargar_historial.clear()
    try:
        df = cargar_historial()
    except Exception as e:
        st.error(f"No se pudo leer el histórico: {e}")
        df = pd.DataFrame()

    if df.empty:
        st.info("Aún no hay facturas guardadas.")
    else:
        f1, f2, f3 = st.columns(3)
        est_sel = f1.multiselect("Estatus", ESTATUS)
        alo_sel = f2.multiselect("Alojamiento", sorted(x for x in df["alojamiento"].astype(str).unique() if x))
        prov_sel = f3.multiselect("Proveedor", sorted(df["proveedor"].astype(str).unique()))

        v = df.copy()
        if est_sel: v = v[v["estatus"].isin(est_sel)]
        if alo_sel: v = v[v["alojamiento"].isin(alo_sel)]
        if prov_sel: v = v[v["proveedor"].isin(prov_sel)]

        for c in ["subtotal", "iva", "total"]:
            v[c] = pd.to_numeric(v[c], errors="coerce").fillna(0)

        m1, m2, m3 = st.columns(3)
        m1.metric("Facturas", len(v))
        m2.metric("Total", f"${v['total'].sum():,.2f}")
        m3.metric("Por refacturar", f"${v[v['estatus'].str.startswith('Refacturar')]['total'].sum():,.2f}")

        st.dataframe(v.sort_values("fecha_carga", ascending=False), use_container_width=True, hide_index=True)
        st.download_button("⬇️ Descargar CSV", v.to_csv(index=False).encode("utf-8-sig"),
                           "facturas.csv", "text/csv")
