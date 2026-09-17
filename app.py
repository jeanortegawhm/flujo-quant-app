import streamlit as st
import pandas as pd
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh
HOME = Path(__file__).resolve().parent
CARPETA = HOME / "flujos"
CARPETA.mkdir(exist_ok=True)

st.set_page_config(page_title="Flujo Quant", layout="wide")
import os
clave = st.sidebar.text_input("Clave de acceso", type="password")
if clave != os.getenv("APP_PASSWORD", "cambiaesta"):
    st.warning("Escribe la clave en la barra izquierda")
    st.stop()
st.title("Flujo Quant")
st.caption("Intradía (Quantium) + Swing (OI / 15 sesiones)")

tab1, tab2, tab3 = st.tabs(["Intradía", "Swing", "Resultados"])

def correr(nombre, modo="AMBOS"):
    script = HOME / nombre
    if not script.exists():
        st.error(f"No encuentro {script}")
        return
    caja = st.empty()
    caja.info(f"Ejecutando {nombre} ({modo})…")
    env = os.environ.copy()
    try:
        env["UW_API_KEY"] = st.secrets.get("UW_API_KEY", env.get("UW_API_KEY", ""))
    except Exception:
        pass
    env["FLUJOS_DIR"] = str(HOME / "flujos")
    env["MODO_FLUJO"] = modo
    p = subprocess.run(
        [sys.executable, "-u", str(script)],
        capture_output=True,
        text=True,
        cwd=str(HOME),
        env=env,
    )
    caja.empty()
    if p.returncode == 0:
        st.success("Listo")
    else:
        st.error("Terminó con error")
    if p.stdout:
        st.text_area("Salida", p.stdout[-6000:], height=220)
    if p.stderr:
        st.text_area("Avisos", p.stderr[-3000:], height=140)

with tab1:
    st.subheader("Tape + GEX + Q-delta + señales del día")
    ny = datetime.now(ZoneInfo("America/New_York"))
    abierto = ny.weekday() < 5 and (
        ny.replace(hour=9, minute=30, second=0, microsecond=0)
        <= ny
        <= ny.replace(hour=16, minute=0, second=0, microsecond=0)
    )
    st.caption(f"NY {ny:%Y-%m-%d %H:%M:%S}  |  {'ABIERTO' if abierto else 'CERRADO'}")

    auto = st.checkbox("En vivo cada 3 minutos (solo HOY)")
    if auto:
        if not abierto:
            st.warning("Mercado cerrado. El vivo solo corre 9:30–16:00 NY.")
        else:
            st_autorefresh(interval=180_000, key="vivo")
            st.info("Vivo activo. Cada 3 min genera SOLO HOY.")
            correr("flujo2.py", "HOY")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Solo AYER"):
            correr("flujo2.py", "AYER")
    with c2:
        if st.button("AYER + HOY", type="primary"):
            correr("flujo2.py", "AMBOS")
    with c3:
        if st.button("Solo HOY"):
            correr("flujo2.py", "HOY")
with tab2:
    st.subheader("15 sesiones + PW/QF de OI")
    st.write("Usa flujo_swing.py.")
    if st.button("Generar swing", type="primary"):
        correr("flujo_swing.py")

with tab3:
    st.subheader("Gráficos")
    pngs = sorted(CARPETA.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not pngs:
        st.info("Aún no hay PNG. Genera intradía o swing.")
    else:
        filtro = st.selectbox("Mostrar", ["Todos", "Intradía (_Q_)", "Swing (_SWING_)"])
        vistos = []
        for img in pngs:
            n = img.name
            if filtro == "Intradía (_Q_)" and "_Q_" not in n:
                continue
            if filtro == "Swing (_SWING_)" and "_SWING_" not in n:
                continue
            vistos.append(img)
        cols = st.columns(2)
        for i, img in enumerate(vistos[:16]):
            cols[i % 2].image(str(img), caption=img.name, use_container_width=True)

    st.subheader("Señales")
    for nombre in ("senales.csv", "senales_swing.csv"):
        f = CARPETA / nombre
        if f.exists():
            st.write(nombre)
            try:
                st.dataframe(pd.read_csv(f).tail(40), use_container_width=True)
            except Exception as e:
                st.write(str(e))