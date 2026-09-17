import streamlit as st
import pandas as pd
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Flujo Quant · Institucional", page_icon="📊", layout="wide")

HOME = Path(__file__).resolve().parent
CARPETA = HOME / "flujos"
CARPETA.mkdir(exist_ok=True)

st.markdown("""
<style>
    .stApp { background-color: #0b1220; color: #e8eef7; }
    .stButton>button { background-color: #1a2332; color: #e8eef7; border: 1px solid #2d3a4f; }
    .stButton>button:hover { border-color: #7eb6ff; }
    div[data-testid="stMetricValue"] { font-size: 1.3rem; }
</style>
""", unsafe_allow_html=True)

def correr(nombre, modo="AMBOS"):
    script = HOME / nombre
    if not script.exists():
        st.error(f"No encuentro {script}")
        return
    caja = st.empty()
    caja.info(f"Ejecutando {nombre} ({modo})… esto puede tardar 1-3 min")
    env = os.environ.copy()
    try:
        env["UW_API_KEY"] = st.secrets.get("UW_API_KEY", env.get("UW_API_KEY", ""))
    except Exception:
        pass
    env["FLUJOS_DIR"] = str(CARPETA)
    env["MODO_FLUJO"] = modo
    env["MIN_PREMIUM"] = os.environ.get("MIN_PREMIUM", "120000")
    env["SOLO_0DTE"] = os.environ.get("SOLO_0DTE", "0")
    env["UMBRAL_BURBUJA"] = os.environ.get("UMBRAL_BURBUJA", "30000000")

    p = subprocess.run(
        [sys.executable, "-u", str(script)],
        capture_output=True, text=True, cwd=str(HOME), env=env
    )
    caja.empty()
    if p.returncode == 0:
        st.success("Listo")
    else:
        st.error("Terminó con error")
    if p.stdout:
        st.text_area("Salida", p.stdout[-5000:], height=200)
    if p.stderr:
        st.text_area("Avisos", p.stderr[-2000:], height=120)

# Header
st.title("Flujo Quant · Terminal Institucional")
st.caption("GEX · Net Premium · Q-Delta · Unusual Flow")

ny = datetime.now(ZoneInfo("America/New_York"))
abierto = ny.weekday() < 5 and (
    ny.replace(hour=9, minute=30, second=0, microsecond=0)
    <= ny <=
    ny.replace(hour=16, minute=0, second=0, microsecond=0)
)
col1, col2, col3 = st.columns(3)
col1.metric("Nueva York", ny.strftime("%H:%M:%S"))
col2.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
col3.metric("Colombia", datetime.now(ZoneInfo("America/Bogota")).strftime("%H:%M"))

tab1, tab2, tab3 = st.tabs(["Intradía", "Swing", "Resultados"])

with tab1:
    st.subheader("Tape + GEX + Net Premium + Señales")

    # ===== FILTROS AVANZADOS =====
    with st.expander("Filtros Avanzados", expanded=False):
        colf1, colf2, colf3 = st.columns(3)
        with colf1:
            min_premium = st.number_input("Prima mínima ($)", min_value=50000, max_value=2000000, value=120000, step=10000)
        with colf2:
            solo_0dte = st.checkbox("Solo 0DTE / 1DTE", value=False)
        with colf3:
            umbral_burbuja = st.number_input("Umbral burbuja ($M)", min_value=5, max_value=500, value=30, step=5)

    os.environ["MIN_PREMIUM"] = str(min_premium)
    os.environ["SOLO_0DTE"] = "1" if solo_0dte else "0"
    os.environ["UMBRAL_BURBUJA"] = str(umbral_burbuja * 1_000_000)

    auto = st.checkbox("En vivo cada 3 minutos (solo HOY)", value=False)
    if auto:
        if not abierto:
            st.warning("Mercado cerrado. El vivo solo corre 9:30–16:00 NY.")
        else:
            st_autorefresh(interval=180_000, key="vivo")
            st.info("Vivo activo · deja esta pestaña abierta")
            correr("flujo2.py", "HOY")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Solo AYER", width="stretch"):
            correr("flujo2.py", "AYER")
    with c2:
        if st.button("AYER + HOY", type="primary", width="stretch"):
            correr("flujo2.py", "AMBOS")
    with c3:
        if st.button("Solo HOY", width="stretch"):
            correr("flujo2.py", "HOY")

with tab2:
    st.subheader("Modo Swing")
    if st.button("Generar Swing", width="stretch"):
        if (HOME / "flujo_swing.py").exists():
            correr("flujo_swing.py", "AMBOS")
        else:
            st.warning("flujo_swing.py no encontrado todavía")

with tab3:
    st.subheader("Gráficos generados")
    pngs = sorted(CARPETA.glob("*.png"), key=os.path.getmtime, reverse=True)
    if not pngs:
        st.info("Aún no hay gráficos. Genera primero desde Intradía.")
    else:
        for p in pngs[:15]:
            st.image(str(p), caption=p.name, width="stretch")

st.markdown("---")
st.caption("Uso personal / pocos usuarios. No publiques el link ni la API key.")