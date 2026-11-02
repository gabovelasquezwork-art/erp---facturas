import json
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Control de Facturas - ERP", page_icon="🧾", layout="wide")

# --- SECRETOS (sin valores por defecto: si faltan, la app se detiene) ---
try:
    WEBHOOK_URL = st.secrets["WEBHOOK_URL"]
    WEBHOOK_TOKEN = st.secrets["WEBHOOK_TOKEN"]
    USERS = dict(st.secrets["users"])
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
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

# --- ESTADO DEL FORMULARIO ---
DEFAULTS = {
    "f_numero": "", "f_proveedor": "", "f_rif": "", "f_fecha": date.today(),
    "f_subtotal": 0.0, "f_iva": 0.0, "f_descripcion": "", "f_alojamiento": "",
    "f_clasificacion": CLASIFICACIONES[0], "f_estatus": ESTATUS[0],
}
if st.session_state.pop("reset_form", False):
    for k, v in DEFAULTS.items():
        st.session_state[k] = v
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)


# --- FUNCIONES ---
def escanear(archivo):
    ext = archivo.name.rsplit(".", 1)[-1].lower()
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
    resp = client.models.generate_content(
        model=MODELO,
        contents=[types.Part.from_bytes(data=archivo.getvalue(), mime_type=MIME[ext]), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
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


st.title("🧾 Control de Facturas y Gastos")
tab_cargar, tab_hist = st.tabs(["📤 Cargar factura", "📚 Histórico"])

# =============== TAB CARGAR ===============
with tab_cargar:
    archivo = st.file_uploader("Sube la factura (JPG, PNG o PDF)", type=list(MIME.keys()))

    if archivo is not None:
        c_img, c_btn = st.columns([1, 2])
        with c_img:
            if archivo.name.lower().endswith("pdf"):
                st.info(f"📄 {archivo.name}")
            else:
                st.image(archivo, width=250)
        with c_btn:
            if st.button("🚀 Escanear y llenar matriz", type="primary"):
                with st.spinner("Escaneando..."):
                    try:
                        r = escanear(archivo)
                        try:
                            fecha = datetime.strptime(str(r.get("fecha", "")), "%Y-%m-%d").date()
                        except ValueError:
                            fecha = date.today()
                        st.session_state.f_numero = str(r.get("numero_factura") or "")
                        st.session_state.f_proveedor = str(r.get("proveedor") or "")
                        st.session_state.f_rif = str(r.get("rif") or "")
                        st.session_state.f_fecha = fecha
                        st.session_state.f_subtotal = float(r.get("subtotal") or 0)
                        st.session_state.f_iva = float(r.get("iva") or 0)
                        st.session_state.f_descripcion = str(r.get("descripcion") or "")
                        st.success("Escaneo listo. Revisa los campos antes de guardar.")
                    except Exception as e:
                        st.error(f"Error al escanear: {e}")

    st.markdown("---")
    st.subheader("📋 Matriz de datos")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.text_input("Número de Factura", key="f_numero")
        st.text_input("Proveedor", key="f_proveedor")
        st.text_input("RIF / Identificación", key="f_rif")
        st.text_input("Alojamiento", key="f_alojamiento", placeholder="Ej. Apto 3B, Villa Sol...")
    with c2:
        st.date_input("Fecha de Emisión", key="f_fecha")
        st.selectbox("Clasificación del Gasto", CLASIFICACIONES, key="f_clasificacion")
        st.selectbox("Estatus", ESTATUS, key="f_estatus")
    with c3:
        st.number_input("Subtotal", key="f_subtotal", format="%.2f", step=1.0)
        st.number_input("IVA", key="f_iva", format="%.2f", step=1.0)
        total = st.session_state.f_subtotal + st.session_state.f_iva
        st.metric("Total", f"${total:,.2f}")

    st.text_area("Descripción (todo lo comprado o devuelto)", key="f_descripcion", height=120)

    if st.button("💾 Guardar en Google Sheets", type="primary"):
        ss = st.session_state
        if not ss.f_numero.strip() or not ss.f_proveedor.strip():
            st.warning("El Número de Factura y el Proveedor son obligatorios.")
        else:
            duplicada = False
            try:
                h = cargar_historial()
                if not h.empty:
                    duplicada = ((h["numero_factura"].astype(str) == ss.f_numero.strip()) &
                                 (h["proveedor"].astype(str).str.lower() == ss.f_proveedor.strip().lower())).any()
            except Exception:
                pass
            if duplicada:
                st.warning("⚠️ Ya existe una factura con ese número y proveedor. No se guardó.")
            else:
                payload = {
                    "id": f"FAC-{int(datetime.now().timestamp())}",
                    "fecha_carga": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cargado_por": ss.usuario_actual,
                    "archivo": archivo.name if archivo else "N/A",
                    "fecha": str(ss.f_fecha),
                    "numero_factura": ss.f_numero.strip(),
                    "proveedor": ss.f_proveedor.strip(),
                    "rif": ss.f_rif.strip(),
                    "descripcion": ss.f_descripcion,
                    "subtotal": ss.f_subtotal,
                    "iva": ss.f_iva,
                    "total": total,
                    "clasificacion": ss.f_clasificacion,
                    "alojamiento": ss.f_alojamiento.strip(),
                    "estatus": ss.f_estatus,
                }
                try:
                    with st.spinner("Guardando..."):
                        ok = guardar(payload)
                    if ok:
                        cargar_historial.clear()
                        st.session_state.reset_form = True
                        st.toast("Factura guardada ✅")
                        st.rerun()
                    else:
                        st.error("El servidor rechazó el guardado (revisa el token).")
                except Exception as e:
                    st.error(f"Error de conexión: {e}")

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
