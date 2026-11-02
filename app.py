import streamlit as st
import requests
from datetime import datetime

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Control de Facturas - ERP", page_icon="🧾", layout="wide")

# Recuperar secretos
WEBHOOK_URL = st.secrets["WEBHOOK_URL"]
USERS = st.secrets.get("users", {"gabove": "1997011", "danigomez": "1998011"})

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
    st.write("Registra y sincroniza las facturas directamente con la base de datos en la nube.")

    def guardar_factura_en_google(datos_dict):
        """Envía los datos de la factura al Webhook de Google Sheets"""
        try:
            response = requests.post(WEBHOOK_URL, json=datos_dict)
            if response.status_code == 200:
                return True
            else:
                st.error(f"Error al sincronizar con la nube. Código: {response.status_code}")
                return False
        except Exception as e:
            st.error(f"Error de conexión: {e}")
            return False

    # --- FORMULARIO DE REGISTRO ---
    with st.form("form_factura", clear_on_submit=True):
        st.subheader("📝 Registrar Nueva Factura")
        
        col1, col2 = st.columns(2)
        
        with col1:
            numero_factura = st.text_input("Número de Factura")
            proveedor = st.text_input("Proveedor")
            rif = st.text_input("RIF / Identificación")
            fecha_factura = st.date_input("Fecha de la Factura", value=datetime.today())
            
        with col2:
            alojamiento = st.selectbox("Alojamiento / Propiedad", [
                "Seleccionar...", 
                "Propiedad España - Apto 1", 
                "Propiedad España - Apto 2", 
                "Propiedad España - Villa Principal", 
                "General / Operaciones"
            ])
            clasificacion = st.selectbox("Clasificación del Gasto", [
                "Mantenimiento", 
                "Logística y Montaje", 
                "Insumos Operativos", 
                "Servicios", 
                "Otro"
            ])
            subtotal = st.number_input("Subtotal", min_value=0.0, format="%.2f")
            iva = st.number_input("IVA / Impuestos", min_value=0.0, format="%.2f")
        
        total = subtotal + iva
        st.info(f"**Monto Total Calculado:** ${total:.2f}")
        
        descripcion = st.text_area("Descripción detallada del gasto o materiales")
        
        submitted = st.form_submit_button("Guardar Factura en la Nube")
        
        if submitted:
            if not numero_factura or not proveedor:
                st.warning("Por favor completa al menos el Número de Factura y el Proveedor.")
            elif alojamiento == "Seleccionar...":
                st.warning("Por favor selecciona un alojamiento o propiedad válido.")
            else:
                # Estructura del diccionario que recibirá Google Apps Script
                datos_factura = {
                    "id": f"FAC-{int(datetime.now().timestamp())}",
                    "archivo": "N/A",
                    "fecha": str(fecha_factura),
                    "numero_factura": numero_factura,
                    "proveedor": proveedor,
                    "rif": rif,
                    "descripcion": descripcion,
                    "subtotal": subtotal,
                    "iva": iva,
                    "total": total,
                    "clasificacion": clasificacion,
                    "alojamiento": alojamiento,
                    "cargado_por": st.session_state.usuario_actual,
                    "fecha_carga": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                
                with st.spinner("Guardando en Google Sheets..."):
                    if guardar_factura_en_google(datos_factura):
                        st.success("¡Factura registrada y sincronizada con éxito en Google Sheets!")
                    else:
                        st.error("No se pudo completar el registro.")
