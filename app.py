import streamlit as st
import requests
import json
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Control de Facturas - ERP", page_icon="🧾", layout="wide")

# --- CONFIGURACIÓN DE SECRETOS ---
try:
    WEBHOOK_URL = st.secrets["WEBHOOK_URL"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    USERS = st.secrets.get("users", {"gabove": "1997011", "danigomez": "1998011"})
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Error cargando los secretos en Streamlit: {e}")

# --- CONTROL DE SESIÓN Y LOGIN ---
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if "usuario_actual" not in st.session_state:
    st.session_state.usuario_actual = ""

def login():
    st.subheader("🔐 Iniciar Sesión en el ERP")
    with st.form("form_login"):
        usuario = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit_login = st.form_submit_button("Entrar")
        
        if submit_login:
            if usuario in USERS and USERS[usuario] == password:
                st.session_state.autenticado = True
                st.session_state.usuario_actual = usuario
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")

if not st.session_state.autenticado:
    login()
else:
    # --- INTERFAZ PRINCIPAL DEL ERP ---
    st.sidebar.title(f"👤 Bienvenido, {st.session_state.usuario_actual}")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.usuario_actual = ""
        st.rerun()

    st.title("🧾 Control de Facturas y Gastos")
    st.write("Sistema de digitalización, escaneo inteligente y registro automatizado en Google Sheets.")

    def enviar_a_google_sheets(datos_dict):
        """Envía los datos de la matriz al Webhook de Google Apps Script"""
        try:
            response = requests.post(WEBHOOK_URL, json=datos_dict)
            if response.status_code == 200:
                return True
            else:
                st.error(f"Error de sincronización con la nube. Código: {response.status_code}")
                return False
        except Exception as e:
            st.error(f"Error de conexión con el Webhook: {e}")
            return False

    # --- INICIALIZAR ESTADOS DE LA MATRIZ DE CAMPOS ---
    if "datos_factura" not in st.session_state:
        st.session_state.datos_factura = {
            "numero_factura": "",
            "proveedor": "",
            "rif": "",
            "fecha": str(datetime.today().strftime("%Y-%m-%d")),
            "subtotal": 0.0,
            "iva": 0.0,
            "total": 0.0,
            "clasificacion": "Mantenimiento",
            "alojamiento": "",
            "estatus": "No refacturar",
            "descripcion": ""
        }

    # --- SECCIÓN DE ESCANEO CON GEMINI ---
    st.subheader("📤 Carga y Escaneo de Factura")
    archivo_subido = st.file_uploader("Sube la imagen o PDF de la factura para procesarla", type=["jpg", "jpeg", "png", "pdf"])

    if archivo_subido is not None:
        col_img, col_btn = st.columns([1, 2])
        with col_img:
            st.image(archivo_subido, caption="Factura Cargada", width=250)
        with col_btn:
            st.info("El archivo está listo para ser escaneado por el sistema.")
            if st.button("🚀 Escanear Factura y Llenar Matriz", type="primary"):
                with st.spinner("Escaneando documento y extrayendo información..."):
                    try:
                        imagen = Image.open(archivo_subido)
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        prompt = """
                        Analiza esta factura en detalle y extrae la información exacta en formato JSON puro con estas claves:
                        - "numero_factura": número de la factura (string)
                        - "proveedor": nombre del comercio o proveedor (string)
                        - "rif": RIF o identificación fiscal (string)
                        - "fecha": fecha de la factura en formato YYYY-MM-DD (string)
                        - "subtotal": subtotal numérico (float)
                        - "iva": impuesto o IVA numérico (float)
                        - "total": monto total numérico (float)
                        - "descripcion": breve detalle de los conceptos o productos (string)
                        Devuelve exclusivamente el objeto JSON sin texto adicional.
                        """
                        response = model.generate_content([prompt, imagen])
                        texto_res = response.text.strip()
                        
                        if texto_res.startswith("```json"):
                            texto_res = texto_res[7:-3].strip()
                        elif texto_res.startswith("```"):
                            texto_res = texto_res[3:-3].strip()

                        resultado_json = json.loads(texto_res)
                        
                        # Actualizar la matriz de campos en session_state
                        st.session_state.datos_factura["numero_factura"] = resultado_json.get("numero_factura", "")
                        st.session_state.datos_factura["proveedor"] = resultado_json.get("proveedor", "")
                        st.session_state.datos_factura["rif"] = resultado_json.get("rif", "")
                        st.session_state.datos_factura["fecha"] = resultado_json.get("fecha", str(datetime.today().strftime("%Y-%m-%d")))
                        st.session_state.datos_factura["subtotal"] = float(resultado_json.get("subtotal", 0.0))
                        st.session_state.datos_factura["iva"] = float(resultado_json.get("iva", 0.0))
                        st.session_state.datos_factura["total"] = float(resultado_json.get("total", 0.0))
                        st.session_state.datos_factura["descripcion"] = resultado_json.get("descripcion", "")
                        
                        st.success("¡Factura escaneada con éxito! La matriz de campos se ha rellenado automáticamente.")
                    except Exception as e:
                        st.error(f"Error al escanear el documento con IA: {e}")

    # --- MATRIZ DE CAMPOS DETALLADOS ---
    st.markdown("---")
    st.subheader("📋 Matriz de Datos de la Factura")
    st.write("Verifica, ajusta o completa los campos antes de almacenarlos permanentemente.")

    with st.form("form_matriz_factura"):
        c1, c2, c3 = st.columns(3)
        
        with c1:
            numero_factura = st.text_input("Número de Factura", value=st.session_state.datos_factura["numero_factura"])
            proveedor = st.text_input("Proveedor", value=st.session_state.datos_factura["proveedor"])
            rif = st.text_input("RIF / Identificación", value=st.session_state.datos_factura["rif"])
            alojamiento = st.text_input("Alojamiento / Propiedad", value=st.session_state.datos_factura["alojamiento"], placeholder="Ej. Apto 3B, Villa Sol...")
            
        with c2:
            fecha_str = st.session_state.datos_factura["fecha"]
            try:
                val_fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            except:
                val_fecha = datetime.today().date()
            
            fecha_factura = st.date_input("Fecha de Emisión", value=val_fecha)
            
            clasificacion = st.selectbox("Clasificación del Gasto", [
                "Mantenimiento", 
                "Logística y Montaje", 
                "Insumos Operativos", 
                "Servicios", 
                "Otro"
            ], index=0)
            
            estatus = st.selectbox("Estatus de la Factura", [
                "No refacturar",
                "Refacturar",
                "Refacturar/herramientas"
            ], index=0)

        with c3:
            subtotal = st.number_input("Subtotal", min_value=0.0, value=float(st.session_state.datos_factura["subtotal"]), format="%.2f")
            iva = st.number_input("IVA / Impuestos", min_value=0.0, value=float(st.session_state.datos_factura["iva"]), format="%.2f")
            
            # Cálculo automático del total basado en los campos de la matriz
            total_calculado = subtotal + iva
            st.metric("Total Calculado", f"${total_calculado:.2f}")

        descripcion = st.text_area("Descripción o Concepto del Gasto", value=st.session_state.datos_factura["descripcion"])
        
        guardar_btn = st.form_submit_button("💾 Guardar Factura en Google Sheets", type="primary")
        
        if guardar_btn:
            if not numero_factura or not proveedor:
                st.warning("Por favor asegúrate de que el Número de Factura y el Proveedor no estén vacíos.")
            else:
                # Estructura final empaquetada para el Webhook
                payload = {
                    "id": f"FAC-{int(datetime.now().timestamp())}",
                    "archivo": archivo_subido.name if archivo_subido else "N/A",
                    "fecha": str(fecha_factura),
                    "numero_factura": numero_factura,
                    "proveedor": proveedor,
                    "rif": rif,
                    "descripcion": descripcion,
                    "subtotal": subtotal,
                    "iva": iva,
                    "total": total_calculado,
                    "clasificacion": clasificacion,
                    "alojamiento": alojamiento,
                    "estatus": estatus,
                    "cargado_por": st.session_state.usuario_actual,
                    "fecha_carga": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                
                with st.spinner("Guardando en la base de datos de Google Sheets..."):
                    if enviar_a_google_sheets(payload):
                        st.success("¡Factura registrada y guardada exitosamente en la nube!")
                    else:
                        st.error("No se pudo completar el almacenamiento en la nube.")
