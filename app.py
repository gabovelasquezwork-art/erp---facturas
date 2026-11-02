import io
import json
import re
import time
from datetime import date, datetime

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="ERP Facturas", page_icon="🧾", layout="wide")

# ============================== ESTILO ==============================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1.6rem; max-width: 1400px; }
.hero {
    background: linear-gradient(120deg, #4f46e5 0%, #7c3aed 55%, #db2777 100%);
    padding: 22px 28px; border-radius: 18px; color: white; margin-bottom: 18px;
}
.hero h1 { margin: 0; font-size: 1.7rem; font-weight: 700; color: white; }
.hero p { margin: 4px 0 0 0; opacity: .9; font-size: .95rem; }
[data-testid="stMetric"] {
    background: rgba(128,128,128,0.08); border: 1px solid rgba(128,128,128,0.18);
    padding: 14px 16px; border-radius: 14px;
}
[data-testid="stMetricLabel"] { opacity: .75; font-weight: 500; }
[data-testid="stMetricValue"] { font-weight: 700; }
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { border-radius: 10px; padding: 8px 16px; font-weight: 600; }
.stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ============================== SECRETOS ==============================
try:
    WEBHOOK_URL = st.secrets["WEBHOOK_URL"]
    WEBHOOK_TOKEN = st.secrets["WEBHOOK_TOKEN"]
    USERS = dict(st.secrets["users"])
except Exception as e:
    st.error(f"Falta configurar los secretos en Streamlit: {e}")
    st.stop()

CLASIFICACIONES = ["Mantenimiento", "Logística y Montaje", "Insumos Operativos", "Servicios", "Personal", "Otro"]
ESTATUS = ["No refacturar", "Refacturar", "Refacturar/herramientas", "Gasto personal"]
DESC_ESTATUS = ["Refacturar/herramientas", "Gasto personal"]   # se descuentan al trabajador
REFAC_ESTATUS = ["Refacturar", "Refacturar/herramientas"]      # van a la pestaña Avisos
TIPOS = ["Compra", "Devolución"]
SIN_ASIGNAR = "Sin asignar"

# ============================== LOGIN ==============================
st.session_state.setdefault("autenticado", False)
st.session_state.setdefault("usuario_actual", "")

if not st.session_state.autenticado:
    _, centro, _ = st.columns([1, 1.2, 1])
    with centro:
        st.markdown('<div class="hero"><h1>🧾 ERP Facturas</h1><p>Inicia sesión para continuar</p></div>',
                    unsafe_allow_html=True)
        with st.form("form_login"):
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar", type="primary", use_container_width=True):
                if u in USERS and USERS[u] == p:
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = u
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")
    st.stop()

USUARIO = st.session_state.usuario_actual


# ============================== UTILIDADES ==============================
def txt(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def num(v):
    try:
        if v is None or pd.isna(v):
            return 0.0
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def fecha_txt(v):
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return date.today().strftime("%Y-%m-%d")


def fecha_corta(v):
    try:
        if pd.isna(v):
            return "s/f"
        return pd.to_datetime(v).strftime("%d/%m/%Y")
    except Exception:
        return "s/f"


def numero_limpio(v):
    s = txt(v)
    return s[:-2] if s.endswith(".0") else s


def clave_num(v):
    return re.sub(r"[^a-z0-9]", "", str(v).lower()).lstrip("0")


def clave_txt(v):
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def seguro(s):
    """Evita que Google Sheets interprete un texto como fórmula."""
    s = txt(s)
    return " " + s if s[:1] in ("=", "+", "-", "@") else s


def leer_meta(v):
    s = str(v or "").strip()
    if s.startswith("{"):
        try:
            return json.loads(s)
        except Exception:
            pass
    return {}


COLUMNAS = ["id", "fecha", "numero_factura", "proveedor", "rif", "hojas", "tipo", "descripcion",
            "subtotal", "iva", "total", "clasificacion", "alojamiento", "estatus",
            "trabajador", "monto_descuento", "descontado", "avisado", "fecha_aviso",
            "creado_por", "creada", "versiones"]


@st.cache_data(ttl=300, show_spinner=False)
def cargar():
    """Lee el Sheet y devuelve la última versión de cada factura (sin las anuladas)."""
    r = requests.get(WEBHOOK_URL, params={"token": WEBHOOK_TOKEN}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "error desconocido"))

    regs, cuenta = {}, {}
    for orden, row in enumerate(data.get("rows", [])):
        meta = leer_meta(row.get("archivo"))
        rid = txt(row.get("id")) or f"LEG-{orden}"
        cuenta[rid] = cuenta.get(rid, 0) + 1
        ver = (int(num(meta.get("v"))), orden)
        if rid in regs and regs[rid][0] > ver:
            continue
        sub, iva = num(row.get("subtotal")), num(row.get("iva"))
        total = num(row.get("total")) if txt(row.get("total")) != "" else sub + iva
        estatus = txt(row.get("estatus")) or ESTATUS[0]
        avisado = bool(meta.get("av", False))
        if estatus == "Refacturada":          # estatus antiguo: ahora es "Refacturar" ya avisado
            estatus = "Refacturar"
            avisado = True
        if estatus == "Descuento personal":   # nombre antiguo: ahora es "Gasto personal"
            estatus = "Gasto personal"
        if estatus not in ESTATUS:
            estatus = ESTATUS[0]
        md = num(meta.get("desc"))
        if estatus in DESC_ESTATUS and md == 0:
            md = total
        regs[rid] = (ver, {
            "id": rid,
            "fecha": str(row.get("fecha") or "")[:10],
            "numero_factura": txt(meta.get("nf")) or numero_limpio(row.get("numero_factura")),
            "proveedor": txt(row.get("proveedor")),
            "rif": txt(row.get("rif")),
            "hojas": max(1, int(num(meta.get("hojas")) or 1)),
            "tipo": txt(meta.get("tipo")) or ("Devolución" if total < 0 else "Compra"),
            "descripcion": txt(row.get("descripcion")),
            "subtotal": sub, "iva": iva, "total": total,
            "clasificacion": txt(row.get("clasificacion")) or CLASIFICACIONES[0],
            "alojamiento": txt(row.get("alojamiento")),
            "estatus": estatus,
            "trabajador": txt(meta.get("trab")),
            "monto_descuento": md,
            "descontado": bool(meta.get("desc_ok", False)),
            "avisado": avisado,
            "fecha_aviso": txt(meta.get("fav")),
            "creado_por": txt(meta.get("cp")) or txt(row.get("cargado_por")),
            "creada": txt(meta.get("creada")) or str(row.get("fecha_carga") or "")[:10],
            "anulada": bool(meta.get("anulada", False)),
        })

    filas = []
    for rid, (_, rec) in regs.items():
        if rec.pop("anulada"):
            continue
        rec["versiones"] = cuenta[rid]
        filas.append(rec)

    df = pd.DataFrame(filas, columns=COLUMNAS)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["es_desc"] = df["estatus"].isin(DESC_ESTATUS)
    df["gasto_aloj"] = df["total"] - df["monto_descuento"].where(df["es_desc"], 0)
    # Monto neto a refacturar: en "Refacturar/herramientas" se resta la parte personal
    df["a_refacturar"] = df["total"] - df["monto_descuento"].where(df["estatus"] == "Refacturar/herramientas", 0)
    df["aloj_vista"] = df["alojamiento"].replace("", SIN_ASIGNAR)
    return df.sort_values("fecha", ascending=False, na_position="last").reset_index(drop=True)


def enviar(rec, anulada=False):
    """Guarda una versión de la factura (agrega una fila al Sheet)."""
    sub, iva = round(num(rec["subtotal"]), 2), round(num(rec["iva"]), 2)
    meta = {
        "v": int(time.time() * 1000),
        "nf": txt(rec["numero_factura"]),
        "hojas": max(1, int(num(rec["hojas"]) or 1)),
        "tipo": txt(rec["tipo"]) or TIPOS[0],
        "trab": txt(rec["trabajador"]),
        "desc": round(num(rec["monto_descuento"]), 2),
        "desc_ok": bool(rec["descontado"]),
        "av": bool(rec.get("avisado", False)),
        "fav": txt(rec.get("fecha_aviso", "")),
        "cp": txt(rec["creado_por"]) or USUARIO,
        "creada": txt(rec["creada"]) or date.today().strftime("%Y-%m-%d"),
        "anulada": bool(anulada),
    }
    payload = {
        "token": WEBHOOK_TOKEN,
        "id": rec["id"],
        "fecha_carga": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cargado_por": USUARIO,
        "archivo": json.dumps(meta, ensure_ascii=False),
        "fecha": fecha_txt(rec["fecha"]),
        "numero_factura": seguro(rec["numero_factura"]),
        "proveedor": seguro(rec["proveedor"]),
        "rif": seguro(rec["rif"]),
        "descripcion": seguro(rec["descripcion"]),
        "subtotal": sub, "iva": iva, "total": round(sub + iva, 2),
        "clasificacion": txt(rec["clasificacion"]),
        "alojamiento": seguro(rec["alojamiento"]),
        "estatus": txt(rec["estatus"]),
    }
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()
    return bool(r.json().get("ok", False))


def hay_duplicado(df, numero, proveedor, rif="", excluir=None):
    kn = clave_num(numero)
    if df is None or df.empty or not kn:
        return None
    kp, kr = clave_txt(proveedor), clave_txt(rif)
    for _, r in df.iterrows():
        if excluir and r["id"] == excluir:
            continue
        if clave_num(r["numero_factura"]) != kn:
            continue
        if (kp and clave_txt(r["proveedor"]) == kp) or (kr and clave_txt(r["rif"]) == kr):
            return r
    return None


def firma(r):
    return (fecha_txt(r["fecha"]), txt(r["numero_factura"]), txt(r["proveedor"]), txt(r["rif"]),
            int(num(r["hojas"]) or 1), txt(r["tipo"]), txt(r["descripcion"]),
            round(num(r["subtotal"]), 2), round(num(r["iva"]), 2), txt(r["clasificacion"]),
            txt(r["alojamiento"]), txt(r["estatus"]), txt(r["trabajador"]),
            round(num(r["monto_descuento"]), 2), bool(r["descontado"]), bool(r["avisado"]))


def aplicar_cambios(editado, columnas):
    """Compara la tabla editada con lo guardado y envía solo las filas que cambiaron."""
    cargar.clear()
    activos = cargar()
    base = activos.set_index("id")
    guardadas, anuladas, errores = 0, 0, []
    for _, fila in editado.iterrows():
        rid = fila["id"]
        if rid not in base.index:
            continue
        orig = base.loc[rid].to_dict()
        orig["id"] = rid
        nuevo = dict(orig)
        for c in columnas:
            nuevo[c] = fila[c]
        if "avisado" in columnas:
            if bool(nuevo["avisado"]) and not bool(orig["avisado"]):
                nuevo["fecha_aviso"] = date.today().strftime("%Y-%m-%d")
            elif not bool(nuevo["avisado"]):
                nuevo["fecha_aviso"] = ""
        anular = bool(fila["anular"]) if "anular" in fila.index else False
        if not anular and firma(orig) == firma(nuevo):
            continue
        etiqueta = txt(nuevo["numero_factura"]) or rid
        if not anular:
            if not txt(nuevo["numero_factura"]) or not txt(nuevo["proveedor"]):
                errores.append(f"{etiqueta}: faltan número o proveedor")
                continue
            if hay_duplicado(activos, nuevo["numero_factura"], nuevo["proveedor"], nuevo["rif"], excluir=rid) is not None:
                errores.append(f"{etiqueta}: ya existe otra factura igual")
                continue
        try:
            if enviar(nuevo, anulada=anular):
                anuladas += anular
                guardadas += not anular
            else:
                errores.append(f"{etiqueta}: el servidor rechazó el guardado")
        except Exception as e:
            errores.append(f"{etiqueta}: {e}")
    cargar.clear()
    return guardadas, anuladas, errores


def a_excel(df):
    out = df.copy()
    out["fecha"] = pd.to_datetime(out["fecha"]).dt.strftime("%Y-%m-%d")
    out["descontado"] = out["descontado"].map({True: "Sí", False: "No"})
    out["avisado"] = out["avisado"].map({True: "Sí", False: "No"})
    cols = [c for c in COLUMNAS if c not in ("versiones",)]
    por_aloj = (df.groupby("aloj_vista")
                .agg(Facturas=("id", "count"), Total_tarjeta=("total", "sum"), Gasto_alojamiento=("gasto_aloj", "sum"))
                .reset_index().rename(columns={"aloj_vista": "Alojamiento"}))
    dp = df[df["es_desc"]].copy()
    dp["trabajador"] = dp["trabajador"].replace("", SIN_ASIGNAR)
    pers = (dp.groupby("trabajador")
            .apply(lambda g: pd.Series({
                "Facturas": len(g),
                "Total a descontar": g["monto_descuento"].sum(),
                "Ya descontado": g.loc[g["descontado"], "monto_descuento"].sum(),
                "Pendiente": g.loc[~g["descontado"], "monto_descuento"].sum()}), include_groups=False)
            .reset_index().rename(columns={"trabajador": "Trabajador"})) if not dp.empty else \
        pd.DataFrame(columns=["Trabajador", "Facturas", "Total a descontar", "Ya descontado", "Pendiente"])
    rf = df[df["estatus"].isin(REFAC_ESTATUS)].copy()
    para_ref = pd.DataFrame({
        "Alojamiento": rf["aloj_vista"],
        "Fecha": pd.to_datetime(rf["fecha"]).dt.strftime("%Y-%m-%d"),
        "Nº Factura": rf["numero_factura"],
        "Proveedor": rf["proveedor"],
        "Estatus": rf["estatus"],
        "Descripción": rf["descripcion"],
        "Total factura": rf["total"],
        "A refacturar (neto)": rf["a_refacturar"],
        "Avisado": rf["avisado"].map({True: "Sí", False: "No"}),
        "Fecha de aviso": rf["fecha_aviso"],
    }).sort_values(["Alojamiento", "Fecha"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        out[cols].to_excel(w, sheet_name="Facturas", index=False)
        para_ref.to_excel(w, sheet_name="Para refacturar", index=False)
        por_aloj.to_excel(w, sheet_name="Por alojamiento", index=False)
        pers.to_excel(w, sheet_name="Personal", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "A2"
            for celda in ws[1]:
                celda.font = celda.font.copy(bold=True)
            for col in ws.columns:
                ancho = max(len(str(c.value or "")) for c in col) + 2
                ws.column_dimensions[col[0].column_letter].width = min(ancho, 45)
    return buf.getvalue()


def filtrar_periodo(df, opcion):
    hoy = pd.Timestamp.today().normalize()
    ini_mes = hoy.replace(day=1)
    if opcion == "Este mes":
        return df[df["fecha"] >= ini_mes]
    if opcion == "Mes anterior":
        return df[(df["fecha"] >= ini_mes - pd.DateOffset(months=1)) & (df["fecha"] < ini_mes)]
    if opcion == "Últimos 90 días":
        return df[df["fecha"] >= hoy - pd.Timedelta(days=90)]
    if opcion == "Este año":
        return df[df["fecha"].dt.year == hoy.year]
    return df


def dinero(x):
    return f"${x:,.2f}"


def combo(label, opciones, key, placeholder):
    """Lista desplegable donde también se puede escribir un valor nuevo."""
    try:
        v = st.selectbox(label, list(opciones), index=None, accept_new_options=True,
                         placeholder=placeholder, key=key)
    except TypeError:   # versión vieja de Streamlit: campo de texto simple
        v = st.text_input(label, key=key, placeholder=placeholder)
    return txt(v)


# ============================== BARRA LATERAL ==============================
st.sidebar.markdown(f"### 👤 {USUARIO}")
if st.sidebar.button("🔄 Actualizar datos", use_container_width=True):
    cargar.clear()
    st.rerun()
if st.sidebar.button("Cerrar sesión", use_container_width=True):
    st.session_state.autenticado = False
    st.session_state.usuario_actual = ""
    st.rerun()
st.sidebar.caption("Tus datos viven en Google Sheets: cerrar sesión no borra nada. "
                   "Si otra persona carga facturas, pulsa Actualizar datos para verlas.")

# ============================== CARGA DE DATOS ==============================
try:
    df = cargar()
except Exception as e:
    st.error(f"No se pudo leer la base de datos: {e}")
    st.stop()

alojamientos = sorted({a for a in df["alojamiento"] if a})
proveedores = sorted({p for p in df["proveedor"] if p})
trabajadores = sorted({t for t in df["trabajador"] if t})

st.markdown('<div class="hero"><h1>🧾 Control de Facturas y Gastos</h1>'
            '<p>Facturación, avisos de refacturación, KPIs y gastos personales del personal en un solo lugar</p></div>',
            unsafe_allow_html=True)

tab_panel, tab_nueva, tab_fact, tab_avisos, tab_est, tab_pers = st.tabs(
    ["📊 Panel", "➕ Nueva factura", "🗂️ Facturas", "📣 Avisos", "🚦 Estatus", "👷 Personal"])

# ============================== PANEL ==============================
with tab_panel:
    if df.empty:
        st.info("Aún no hay facturas. Registra la primera en la pestaña **Nueva factura**.")
    else:
        f1, f2 = st.columns([1, 2])
        periodo = f1.selectbox("Periodo", ["Todo", "Este mes", "Mes anterior", "Últimos 90 días", "Este año"])
        aloj_f = f2.multiselect("Alojamiento", alojamientos, placeholder="Todos los alojamientos")
        d = filtrar_periodo(df, periodo)
        if aloj_f:
            d = d[d["alojamiento"].isin(aloj_f)]

        hoy = pd.Timestamp.today()
        per_hoy = hoy.to_period("M")
        mes_act = df.loc[df["fecha"].dt.to_period("M") == per_hoy, "total"].sum()
        mes_ant = df.loc[df["fecha"].dt.to_period("M") == per_hoy - 1, "total"].sum()
        por_avisar = d[d["estatus"].isin(REFAC_ESTATUS) & (~d["avisado"])]

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Tarjeta del mes", dinero(mes_act), delta=f"{mes_act - mes_ant:+,.2f} vs mes anterior",
                  delta_color="inverse", help="Todas las facturas del mes, sin importar el estatus.")
        k2.metric("Gasto total (tarjeta)", dinero(d["total"].sum()))
        k3.metric("Gasto en alojamientos", dinero(d["gasto_aloj"].sum()),
                  help="No incluye lo que se descuenta al personal.")
        k4.metric("Por avisar (refacturar)", dinero(por_avisar["a_refacturar"].sum()),
                  delta=f"{len(por_avisar)} factura(s)", delta_color="off")
        k5.metric("Devoluciones", dinero(d.loc[d["tipo"] == "Devolución", "total"].abs().sum()))
        k6.metric("Por descontar al personal",
                  dinero(d.loc[d["es_desc"] & ~d["descontado"], "monto_descuento"].sum()))
        st.caption(f"{len(d)} factura(s) en el periodo seleccionado.")

        # Alertas
        if not por_avisar.empty:
            st.warning(f"📣 Tienes {len(por_avisar)} factura(s) pendientes de avisar para refacturar. "
                       f"Ve a la pestaña **Avisos**.")
        compras = df[df["tipo"] == "Compra"].copy()
        if not compras.empty:
            med = compras.groupby("proveedor")["total"].transform("median")
            cnt = compras.groupby("proveedor")["total"].transform("count")
            atip = compras[(cnt >= 3) & (compras["total"] > med * 2.5)]
            if not atip.empty:
                with st.expander(f"🔎 {len(atip)} factura(s) con monto atípico para su proveedor"):
                    st.dataframe(atip[["fecha", "numero_factura", "proveedor", "total"]],
                                 use_container_width=True, hide_index=True)

        st.markdown("&nbsp;")
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Gasto por alojamiento**")
            a = d.groupby("aloj_vista", as_index=False)["gasto_aloj"].sum()
            if a.empty:
                st.caption("Sin datos.")
            else:
                st.altair_chart(alt.Chart(a).mark_bar(cornerRadiusEnd=6, color="#6366f1").encode(
                    x=alt.X("gasto_aloj:Q", title=None),
                    y=alt.Y("aloj_vista:N", sort="-x", title=None),
                    tooltip=[alt.Tooltip("aloj_vista:N", title="Alojamiento"),
                             alt.Tooltip("gasto_aloj:Q", title="Gasto", format=",.2f")]), theme=None)
        with g2:
            st.markdown("**Tendencia mensual (tarjeta)**")
            m = d.dropna(subset=["fecha"]).copy()
            m["mes"] = m["fecha"].dt.to_period("M").astype(str)
            m = m.groupby("mes", as_index=False)["total"].sum()
            if m.empty:
                st.caption("Sin datos.")
            else:
                st.altair_chart(alt.Chart(m).mark_area(
                    line={"color": "#7c3aed"}, color=alt.Gradient(
                        gradient="linear", stops=[alt.GradientStop(color="#7c3aed", offset=0),
                                                  alt.GradientStop(color="rgba(124,58,237,0.05)", offset=1)],
                        x1=1, x2=1, y1=1, y2=0), point=True).encode(
                    x=alt.X("mes:N", title=None),
                    y=alt.Y("total:Q", title=None),
                    tooltip=[alt.Tooltip("mes:N", title="Mes"),
                             alt.Tooltip("total:Q", title="Total", format=",.2f")]), theme=None)

        g3, g4 = st.columns(2)
        with g3:
            st.markdown("**Gasto por clasificación**")
            c = d.groupby("clasificacion", as_index=False)["gasto_aloj"].sum()
            c = c[c["gasto_aloj"] > 0]
            if c.empty:
                st.caption("Sin datos.")
            else:
                st.altair_chart(alt.Chart(c).mark_arc(innerRadius=60).encode(
                    theta="gasto_aloj:Q",
                    color=alt.Color("clasificacion:N", title=None),
                    tooltip=[alt.Tooltip("clasificacion:N", title="Clasificación"),
                             alt.Tooltip("gasto_aloj:Q", title="Gasto", format=",.2f")]), theme=None)
        with g4:
            st.markdown("**Top proveedores**")
            p = d[d["tipo"] == "Compra"].groupby("proveedor", as_index=False)["total"].sum()
            p = p.sort_values("total", ascending=False).head(8)
            if p.empty:
                st.caption("Sin datos.")
            else:
                st.altair_chart(alt.Chart(p).mark_bar(cornerRadiusEnd=6, color="#db2777").encode(
                    x=alt.X("total:Q", title=None),
                    y=alt.Y("proveedor:N", sort="-x", title=None),
                    tooltip=[alt.Tooltip("proveedor:N", title="Proveedor"),
                             alt.Tooltip("total:Q", title="Total", format=",.2f")]), theme=None)

# ============================== NUEVA FACTURA ==============================
st.session_state.setdefault("nf_key", 0)
with tab_nueva:
    if st.session_state.get("aviso_guardado"):
        st.success(st.session_state.pop("aviso_guardado"))

    st.markdown("#### Registrar factura")
    st.caption("Escribe los datos y pulsa **Guardar**. Una factura con muchas hojas va en **una sola fila**: "
               "indica el número de hojas y describe todo lo comprado.")

    k = st.session_state.nf_key
    with st.form(f"form_nueva_{k}"):
        ca, cb, cc = st.columns(3)
        with ca:
            n_fecha = st.date_input("Fecha de emisión", value=date.today(), key=f"n_fecha_{k}")
            n_num = st.text_input("Número de factura", key=f"n_num_{k}")
            n_hojas = st.number_input("Nº de hojas", min_value=1, step=1, value=1, key=f"n_hojas_{k}")
            n_tipo = st.selectbox("Tipo", TIPOS, key=f"n_tipo_{k}")
        with cb:
            n_prov = combo("Proveedor", proveedores, f"n_prov_{k}", "Escribe o elige un proveedor")
            n_rif = st.text_input("RIF / Identificación", key=f"n_rif_{k}",
                                  help="Si lo dejas vacío y el proveedor ya existe, se completa solo.")
            n_aloj = combo("Alojamiento", alojamientos, f"n_aloj_{k}", "Escribe o elige un alojamiento")
            n_clas = st.selectbox("Clasificación del gasto", CLASIFICACIONES, key=f"n_clas_{k}")
        with cc:
            n_sub = st.number_input("Subtotal", min_value=0.0, step=1.0, format="%.2f", key=f"n_sub_{k}")
            n_ivamodo = st.selectbox("IVA", ["16%", "8%", "Exento (0%)", "Manual"], key=f"n_ivamodo_{k}")
            n_ivaman = st.number_input("Monto del IVA (solo si elegiste Manual)", min_value=0.0, step=1.0,
                                       format="%.2f", key=f"n_ivaman_{k}")
            n_est = st.selectbox("Estatus", ESTATUS, key=f"n_est_{k}")

        st.markdown("##### 👷 Trabajador (solo para Refacturar/herramientas y Gasto personal)")
        t1, t2 = st.columns(2)
        with t1:
            n_trab = combo("Trabajador", trabajadores, f"n_trab_{k}", "Escribe o elige un trabajador")
        with t2:
            n_mdesc = st.number_input("Monto personal a descontar (0 = toda la factura)", min_value=0.0,
                                      step=1.0, format="%.2f", key=f"n_mdesc_{k}")

        n_desc = st.text_area("Descripción (todo lo comprado o devuelto)", key=f"n_desc_{k}", height=110)
        guardar = st.form_submit_button("💾 Guardar factura", type="primary", use_container_width=True)

    if guardar:
        num_f = n_num.strip()
        es_desc = n_est in DESC_ESTATUS
        if not num_f or not n_prov:
            st.warning("El número de factura y el proveedor son obligatorios.")
        elif es_desc and not n_trab:
            st.warning("Indica el trabajador al que se le descontará.")
        else:
            cargar.clear()
            actual = cargar()
            rif = n_rif.strip()
            if not rif:
                prev = actual[(actual["proveedor"].str.lower() == n_prov.lower()) & (actual["rif"] != "")]
                if not prev.empty:
                    rif = prev.iloc[0]["rif"]
            dup = hay_duplicado(actual, num_f, n_prov, rif)
            if dup is not None:
                st.error(f"⛔ Factura repetida: ya existe la nº {dup['numero_factura']} de {dup['proveedor']} "
                         f"({fecha_corta(dup['fecha'])}). No se guardó.")
            else:
                pct = {"16%": 0.16, "8%": 0.08, "Exento (0%)": 0.0}
                iva = n_ivaman if n_ivamodo == "Manual" else round(n_sub * pct[n_ivamodo], 2)
                signo = -1 if n_tipo == "Devolución" else 1
                total = signo * round(n_sub + iva, 2)
                monto_desc = signo * (n_mdesc if n_mdesc > 0 else round(n_sub + iva, 2)) if es_desc else 0.0
                rec = {
                    "id": f"FAC-{int(time.time() * 1000)}",
                    "fecha": n_fecha, "numero_factura": num_f, "proveedor": n_prov, "rif": rif,
                    "hojas": n_hojas, "tipo": n_tipo, "descripcion": n_desc,
                    "subtotal": signo * n_sub, "iva": signo * iva, "clasificacion": n_clas,
                    "alojamiento": n_aloj, "estatus": n_est,
                    "trabajador": n_trab if es_desc else "", "monto_descuento": monto_desc,
                    "descontado": False, "avisado": False, "fecha_aviso": "",
                    "creado_por": USUARIO, "creada": date.today().strftime("%Y-%m-%d"),
                }
                try:
                    with st.spinner("Guardando..."):
                        ok = enviar(rec)
                    if ok:
                        cargar.clear()
                        st.session_state.nf_key += 1
                        st.session_state.aviso_guardado = (f"✅ Factura {num_f} guardada. "
                                                           f"Total: {dinero(total)} (IVA {dinero(signo * iva)}).")
                        st.rerun()
                    else:
                        st.error("El servidor rechazó el guardado (revisa que el token coincida).")
                except Exception as e:
                    st.error(f"Error de conexión: {e}")

# ============================== FACTURAS ==============================
st.session_state.setdefault("ed_fact", 0)
with tab_fact:
    if df.empty:
        st.info("Aún no hay facturas guardadas.")
    else:
        b1, b2, b3, b4 = st.columns([2, 1.3, 1.3, 1.2])
        buscar = b1.text_input("🔎 Buscar", placeholder="Número, proveedor, descripción...")
        est_f = b2.multiselect("Estatus", ESTATUS, key="f_est")
        aloj_ff = b3.multiselect("Alojamiento", alojamientos, key="f_aloj")
        tipo_f = b4.multiselect("Tipo", TIPOS, key="f_tipo")

        v = df.copy()
        if buscar:
            q = buscar.lower()
            v = v[v[["numero_factura", "proveedor", "rif", "descripcion", "trabajador"]]
                  .astype(str).apply(lambda s: s.str.lower().str.contains(q, regex=False)).any(axis=1)]
        if est_f:
            v = v[v["estatus"].isin(est_f)]
        if aloj_ff:
            v = v[v["alojamiento"].isin(aloj_ff)]
        if tipo_f:
            v = v[v["tipo"].isin(tipo_f)]

        m1, m2, m3 = st.columns(3)
        m1.metric("Facturas", len(v))
        m2.metric("Total (tarjeta)", dinero(v["total"].sum()))
        m3.metric("Gasto en alojamientos", dinero(v["gasto_aloj"].sum()))

        st.caption("Edita cualquier celda y pulsa **Guardar cambios**. Marca **Anular** para retirar una factura.")
        vista = v[["id", "fecha", "numero_factura", "proveedor", "rif", "hojas", "tipo", "descripcion",
                   "subtotal", "iva", "total", "clasificacion", "alojamiento", "estatus",
                   "trabajador", "monto_descuento", "descontado", "avisado", "fecha_aviso",
                   "creado_por", "versiones"]].copy()
        vista.insert(0, "anular", False)
        editado = st.data_editor(
            vista, key=f"editor_fact_{st.session_state.ed_fact}", hide_index=True, use_container_width=True,
            num_rows="fixed", disabled=["id", "total", "fecha_aviso", "creado_por", "versiones"],
            column_config={
                "anular": st.column_config.CheckboxColumn("Anular"),
                "id": st.column_config.TextColumn("ID"),
                "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                "numero_factura": st.column_config.TextColumn("Nº Factura"),
                "proveedor": st.column_config.TextColumn("Proveedor"),
                "rif": st.column_config.TextColumn("RIF"),
                "hojas": st.column_config.NumberColumn("Hojas", min_value=1, step=1),
                "tipo": st.column_config.SelectboxColumn("Tipo", options=TIPOS),
                "descripcion": st.column_config.TextColumn("Descripción", width="large"),
                "subtotal": st.column_config.NumberColumn("Subtotal", format="%.2f"),
                "iva": st.column_config.NumberColumn("IVA", format="%.2f"),
                "total": st.column_config.NumberColumn("Total", format="%.2f"),
                "clasificacion": st.column_config.SelectboxColumn("Clasificación", options=CLASIFICACIONES),
                "alojamiento": st.column_config.TextColumn("Alojamiento"),
                "estatus": st.column_config.SelectboxColumn("Estatus", options=ESTATUS),
                "trabajador": st.column_config.TextColumn("Trabajador"),
                "monto_descuento": st.column_config.NumberColumn("Monto a descontar", format="%.2f"),
                "descontado": st.column_config.CheckboxColumn("Descontado"),
                "avisado": st.column_config.CheckboxColumn("Avisado"),
                "fecha_aviso": st.column_config.TextColumn("Fecha aviso"),
                "creado_por": st.column_config.TextColumn("Cargada por"),
                "versiones": st.column_config.NumberColumn("Versiones"),
            })

        s1, s2 = st.columns([1, 1])
        if s1.button("💾 Guardar cambios", type="primary", use_container_width=True):
            cols_edit = ["fecha", "numero_factura", "proveedor", "rif", "hojas", "tipo", "descripcion",
                         "subtotal", "iva", "clasificacion", "alojamiento", "estatus",
                         "trabajador", "monto_descuento", "descontado", "avisado"]
            with st.spinner("Guardando cambios..."):
                g, an, errs = aplicar_cambios(editado, cols_edit)
            if g or an:
                st.success(f"✅ {g} factura(s) actualizada(s), {an} anulada(s).")
            elif not errs:
                st.info("No hay cambios por guardar.")
            for e in errs:
                st.error(e)
            if g or an:
                st.session_state.ed_fact += 1
                time.sleep(1)
                st.rerun()
        s2.download_button("⬇️ Descargar Excel", a_excel(v), "facturas.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

# ============================== AVISOS ==============================
st.session_state.setdefault("ed_avisos", 0)
with tab_avisos:
    st.markdown("#### Avisos de refacturación")
    st.caption("Aquí están las facturas con estatus **Refacturar** y **Refacturar/herramientas**. "
               "Tú avisas; no las refacturas. En las de herramientas se muestra cuánto es personal "
               "(se descuenta al trabajador) y cuánto queda neto por refacturar. "
               "Marca **Avisado** cuando ya hayas informado y la app guarda la fecha.")
    rf = df[df["estatus"].isin(REFAC_ESTATUS)].copy()
    if rf.empty:
        st.info("No hay facturas para refacturar.")
    else:
        pend = rf[~rf["avisado"]].copy()
        hechas = rf[rf["avisado"]].copy()

        a1, a2, a3 = st.columns(3)
        a1.metric("Por avisar (neto)", dinero(pend["a_refacturar"].sum()), delta=f"{len(pend)} factura(s)",
                  delta_color="off")
        a2.metric("Ya avisadas (neto)", dinero(hechas["a_refacturar"].sum()), delta=f"{len(hechas)} factura(s)",
                  delta_color="off")
        a3.metric("Total neto para refacturar", dinero(rf["a_refacturar"].sum()))

        if pend.empty:
            st.success("🎉 No tienes avisos pendientes.")
        else:
            aloj_av = st.multiselect("Filtrar por alojamiento", sorted(pend["aloj_vista"].unique()),
                                     key="av_aloj", placeholder="Todos los alojamientos")
            pv = pend if not aloj_av else pend[pend["aloj_vista"].isin(aloj_av)]

            if SIN_ASIGNAR in set(pv["aloj_vista"]):
                st.warning("Hay facturas sin alojamiento. Escríbelo en la tabla antes de avisar.")

            vista_a = pv[["id", "avisado", "fecha", "numero_factura", "proveedor", "alojamiento", "estatus",
                          "descripcion", "total", "monto_descuento", "a_refacturar"]].copy()
            ed_a = st.data_editor(
                vista_a, key=f"editor_avisos_{st.session_state.ed_avisos}", hide_index=True,
                use_container_width=True, num_rows="fixed",
                disabled=["id", "fecha", "numero_factura", "proveedor", "estatus", "descripcion", "total",
                          "monto_descuento", "a_refacturar"],
                column_config={
                    "id": st.column_config.TextColumn("ID"),
                    "avisado": st.column_config.CheckboxColumn("Avisado"),
                    "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                    "numero_factura": st.column_config.TextColumn("Nº Factura"),
                    "proveedor": st.column_config.TextColumn("Proveedor"),
                    "alojamiento": st.column_config.TextColumn("Alojamiento"),
                    "estatus": st.column_config.TextColumn("Estatus"),
                    "descripcion": st.column_config.TextColumn("Descripción", width="large"),
                    "total": st.column_config.NumberColumn("Total factura", format="%.2f"),
                    "monto_descuento": st.column_config.NumberColumn("Personal (trabajador)", format="%.2f"),
                    "a_refacturar": st.column_config.NumberColumn("Neto a refacturar", format="%.2f"),
                })

            st.markdown("**📋 Mensaje listo para copiar**")
            lineas = [f"AVISO DE REFACTURACIÓN - {date.today():%d/%m/%Y}", ""]
            for aloj, g in pv.sort_values("fecha").groupby("aloj_vista"):
                lineas.append(f"🏠 {aloj} - Neto a refacturar: {dinero(g['a_refacturar'].sum())}")
                for _, r in g.iterrows():
                    lineas.append(f"  • Fact. {r['numero_factura']} | {r['proveedor']} | "
                                  f"{fecha_corta(r['fecha'])} | {dinero(r['total'])}")
                    if r["estatus"] == "Refacturar/herramientas" and r["monto_descuento"]:
                        lineas.append(f"    Herramientas personales (descuento al trabajador): "
                                      f"{dinero(r['monto_descuento'])} | Neto: {dinero(r['a_refacturar'])}")
                    if r["descripcion"]:
                        lineas.append(f"    {r['descripcion'][:120]}")
                lineas.append("")
            lineas.append(f"TOTAL NETO A REFACTURAR: {dinero(pv['a_refacturar'].sum())}")
            st.code("\n".join(lineas), language=None)

            w1, w2 = st.columns(2)
            if w1.button("✅ Guardar avisos marcados", type="primary", use_container_width=True):
                with st.spinner("Guardando..."):
                    g, _, errs = aplicar_cambios(ed_a, ["avisado", "alojamiento"])
                if g:
                    st.success(f"✅ {g} factura(s) actualizada(s).")
                elif not errs:
                    st.info("No hay cambios por guardar.")
                for e in errs:
                    st.error(e)
                if g:
                    st.session_state.ed_avisos += 1
                    time.sleep(1)
                    st.rerun()
            w2.download_button("⬇️ Descargar Excel", a_excel(rf), "para_refacturar.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)

        if not hechas.empty:
            with st.expander(f"📁 Ya avisadas ({len(hechas)})"):
                st.dataframe(hechas[["fecha_aviso", "fecha", "numero_factura", "proveedor", "aloj_vista",
                                     "estatus", "descripcion", "total", "a_refacturar"]]
                             .sort_values("fecha_aviso", ascending=False),
                             use_container_width=True, hide_index=True)

# ============================== ESTATUS ==============================
with tab_est:
    if df.empty:
        st.info("Aún no hay facturas guardadas.")
    else:
        cols = st.columns(len(ESTATUS))
        for col, e in zip(cols, ESTATUS):
            sub_e = df[df["estatus"] == e]
            col.metric(e, dinero(sub_e["total"].sum()), delta=f"{len(sub_e)} factura(s)", delta_color="off")

        modo = st.radio("Ver matriz en", ["Montos ($)", "Cantidad de facturas"], horizontal=True)
        if modo.startswith("Montos"):
            piv = df.pivot_table(index="aloj_vista", columns="estatus", values="total", aggfunc="sum", fill_value=0)
        else:
            piv = df.pivot_table(index="aloj_vista", columns="estatus", values="id", aggfunc="count", fill_value=0)
        piv = piv.reindex(columns=ESTATUS, fill_value=0)
        piv.index.name = "alojamiento"

        largo = piv.reset_index().melt(id_vars="alojamiento", var_name="estatus", value_name="valor")
        base = alt.Chart(largo).encode(x=alt.X("estatus:N", title=None, sort=ESTATUS),
                                       y=alt.Y("alojamiento:N", title=None))
        calor = base.mark_rect(cornerRadius=6).encode(
            color=alt.Color("valor:Q", scale=alt.Scale(scheme="purples"), legend=None),
            tooltip=["alojamiento", "estatus", alt.Tooltip("valor:Q", format=",.2f")])
        numeros = base.mark_text(fontSize=13, fontWeight="bold").encode(
            text=alt.Text("valor:Q", format=",.0f"),
            color=alt.condition(alt.datum.valor > largo["valor"].max() * 0.5,
                                alt.value("white"), alt.value("#333")))
        st.altair_chart((calor + numeros).properties(height=max(120, 46 * len(piv))), theme=None)
        st.dataframe(piv, use_container_width=True)

        st.markdown("##### Facturas por estatus")
        elegido = st.selectbox("Estatus", ESTATUS, key="est_det")
        det = df[df["estatus"] == elegido][["fecha", "numero_factura", "proveedor", "aloj_vista",
                                            "descripcion", "total", "trabajador"]]
        st.dataframe(det, use_container_width=True, hide_index=True)

# ============================== PERSONAL ==============================
st.session_state.setdefault("ed_pers", 0)
with tab_pers:
    dp = df[df["es_desc"]].copy()
    st.markdown("#### Gastos personales del personal")
    st.caption("Aquí aparecen las facturas con estatus **Refacturar/herramientas** (herramientas personales) "
               "o **Gasto personal** (comida u otras compras personales). Todas salen con la tarjeta de la "
               "empresa, por eso siguen apareciendo también en **Facturas**, y las de herramientas en **Avisos**. "
               "Aquí se ve lo que se le descuenta a cada trabajador.")
    if dp.empty:
        st.info("No hay compras personales registradas.")
    else:
        dp["trab_vista"] = dp["trabajador"].replace("", SIN_ASIGNAR)
        resumen = dp.groupby("trab_vista").apply(lambda g: pd.Series({
            "Facturas": len(g),
            "Total a descontar": g["monto_descuento"].sum(),
            "Ya descontado": g.loc[g["descontado"], "monto_descuento"].sum(),
            "Pendiente": g.loc[~g["descontado"], "monto_descuento"].sum(),
        }), include_groups=False).reset_index().rename(columns={"trab_vista": "Trabajador"})

        p1, p2, p3 = st.columns(3)
        p1.metric("Total a descontar", dinero(resumen["Total a descontar"].sum()))
        p2.metric("Ya descontado", dinero(resumen["Ya descontado"].sum()))
        p3.metric("Pendiente por descontar", dinero(resumen["Pendiente"].sum()))
        if SIN_ASIGNAR in resumen["Trabajador"].values:
            st.warning("Hay facturas personales sin trabajador asignado. Asígnalas en la tabla de abajo.")

        st.dataframe(resumen, use_container_width=True, hide_index=True,
                     column_config={c: st.column_config.NumberColumn(c, format="$%.2f")
                                    for c in ["Total a descontar", "Ya descontado", "Pendiente"]})

        filtro = st.selectbox("Ver trabajador", ["Todos"] + list(resumen["Trabajador"]))
        dd = dp if filtro == "Todos" else dp[dp["trab_vista"] == filtro]
        vista_p = dd[["id", "fecha", "numero_factura", "proveedor", "descripcion", "estatus",
                      "total", "trabajador", "monto_descuento", "descontado"]].copy()
        ed_p = st.data_editor(
            vista_p, key=f"editor_pers_{st.session_state.ed_pers}", hide_index=True,
            use_container_width=True, num_rows="fixed",
            disabled=["id", "fecha", "numero_factura", "proveedor", "descripcion", "estatus", "total"],
            column_config={
                "id": st.column_config.TextColumn("ID"),
                "fecha": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
                "numero_factura": st.column_config.TextColumn("Nº Factura"),
                "proveedor": st.column_config.TextColumn("Proveedor"),
                "descripcion": st.column_config.TextColumn("Descripción", width="large"),
                "estatus": st.column_config.TextColumn("Estatus"),
                "total": st.column_config.NumberColumn("Total factura", format="%.2f"),
                "trabajador": st.column_config.TextColumn("Trabajador"),
                "monto_descuento": st.column_config.NumberColumn("Monto a descontar", format="%.2f"),
                "descontado": st.column_config.CheckboxColumn("Descontado"),
            })
        q1, q2 = st.columns(2)
        if q1.button("💾 Guardar cambios de personal", type="primary", use_container_width=True):
            with st.spinner("Guardando..."):
                g, _, errs = aplicar_cambios(ed_p, ["trabajador", "monto_descuento", "descontado"])
            if g:
                st.success(f"✅ {g} registro(s) actualizado(s).")
            elif not errs:
                st.info("No hay cambios por guardar.")
            for e in errs:
                st.error(e)
            if g:
                st.session_state.ed_pers += 1
                time.sleep(1)
                st.rerun()
        q2.download_button("⬇️ Descargar Excel de personal", a_excel(dd), "gastos_personal.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
