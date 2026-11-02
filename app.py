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

    st.title("🧾 Control de Facturas y Gastos con IA")
    st.write("Sube o fotografía tu factura para leerla automáticamente y sincronizarla con Google Sheets.")

    def enviar_a_google_sheets(datos_dict):
        """Envía los datos procesados al Webhook de Google Apps Script"""
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

    # --- SECCIÓN DE LECTURA AUTOMÁTICA CON GEMINI ---
    st.subheader("🤖 Extracción Automática de Factura")
    archivo_subido = st.file_uploader("Sube la imagen o PDF de la factura", type=["jpg", "jpeg", "png", "pdf"])

    # Inicializar variables de estado para el formulario
    if "datos_extraidos" not in st.session_state:
        st.session_state.datos_extraidos = {
            "numero_factura": "",
            "proveedor": "",
            "rif": "",
            "fecha": datetime.today(),
            "subtotal": 0.0,
            "iva": 0.0,
            "total": 0.0,
            "clasificacion": "Mantenimiento",
            "alojamiento": "General / Operaciones",
            "descripcion": ""
        }

    if archivo_subido is not None:
        st.image(archivo_subido, caption="Factura cargada", width=300)
        if st.button("🔍 Analizar Factura con IA"):
            with st.spinner("Leyendo factura y extrayendo datos..."):
                try:
                    imagen = Image.open(archivo_subido)
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    prompt = """
                    Analiza esta factura y extrae la siguiente información en formato JSON estricto:
                    - "numero_factura": número de factura (string)
                    - "proveedor": nombre del proveedor o comercio (string)
                    - "rif": RIF o identificación fiscal (string)
                    - "fecha": fecha de la factura en formato YYYY-MM-DD (string, si no se encuentra usa la actual)
                    - "subtotal": subtotal numérico (float)
                    - "iva": impuesto o IVA numérico (float)
                    - "total": monto total numérico (float)
                    - "descripcion": breve resumen de los items o servicios (string)
                    Responde únicamente con el objeto JSON, sin texto adicional ni bloques de código markdown si es posible, o limpio.
                    """
                    response = model.generate_content([prompt, imagen])
                    texto_respuesta = response.text.strip()
                    
                    # Limpiar formato markdown si Gemini lo incluye
                    if texto_respuesta.startswith("```json"):
                        texto_respuesta = texto_respuesta[7:-3].strip()
                    elif texto_respuesta.startswith("```"):
                        texto_respuesta = texto_respuesta[3:-3].strip()

                    datos_json = json.loads(texto_respuesta)
                    
                    # Guardar en session_state para rellenar el formulario
                    st.session_state.datos_extraidos["numero_factura"] = datos_json.get("numero_factura", "")
                    st.session_state.datos_extraidos["proveedor"] = datos_json.get("proveedor", "")
                    st.session_state.datos_extraidos["rif"] = datos_json.get("rif", "")
                    st.session_state.datos_extraidos["subtotal"] = float(datos_json.get("subtotal", 0.0))
                    st.session_state.datos_extraidos["iva"] = float(datos_json.get("iva", 0.0))
                    st.session_state.datos_extraidos["total"] = float(datos_json.get("total", 0.0))
                    st.session_state.datos_extraidos["descripcion"] = datos_json.get("descripcion", "")
                    
                    st.success("¡Factura leída con éxito! Revisa los campos abajo y guarda.")
                except Exception as e:
                    st.error(f"No se pudo procesar automáticamente la factura: {e}")

    # --- FORMULARIO DE REVISIÓN Y REGISTRO ---
    st.markdown("---")
    with st.form("form_factura_editable"):
        st.subheader("📝 Detalle y Registro de la Factura")
        
        col1, col2 = st.columns(2)
        
        with col1:
            numero_factura = st.text_input("Número de Factura", value=st.session_state.datos_extraidos["numero_factura"])
            proveedor = st.text_input("Proveedor", value=st.session_state.datos_extraidos["proveedor"])
            rif = st.text_input("RIF / Identificación", value=st.session_state.datos_extraidos["rif"])
            fecha_factura = st.date_input("Fecha de la Factura", value=datetime.today())
            
        with col2:
            alojamiento = st.selectbox("Alojamiento / Propiedad", [
                "General / Operaciones",
                "Propiedad España - Apto 1", 
                "Propiedad España - Apto 2", 
                "Propiedad España - Villa Principal"
            ])
            clasificacion = st.selectbox("Clasificación del Gasto", [
                "Mantenimiento", 
                "Logística y Montaje", 
                "Insumos Operativos", 
                "Servicios", 
                "Otro"
            ])
            subtotal = st.number_input("Subtotal", min_value=0.0, value=st.session_state.datos_extraidos["subtotal"], format="%.2f")
            iva = st.number_input("IVA / Impuestos", min_value=0.0, value=st.session_state.datos_extraidos["iva"], format="%.2f")
        
        total_calculado = subtotal + iva
        st.info(f"**Monto Total:** ${total_calculado:.2f}")
        
        descripcion = st.text_area("Descripción detallada", value=st.session_state.datos_extraidos["descripcion"])
        
        submitted = st.form_submit_button("Guardar Factura en Google Sheets")
        
        if submitted:
            if not numero_factura or not proveedor:
                st.warning("Por favor verifica el Número de Factura y el Proveedor.")
            else:
                # Estructura de datos lista para enviar al Webhook
                datos_factura = {
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
                    "cargado_por": st.session_state.usuario_actual,
                    "fecha_carga": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                
                with st.spinner("Sincronizando con Google Sheets..."):
                    if enviar_a_google_sheets(datos_factura):
                        st.success("¡Factura guardada correctamente en la hoja de cálculo!")
                    else:
                        st.error("Hubo un problema al enviar los datos a la nube.")
