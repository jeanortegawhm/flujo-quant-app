import streamlit as st
import pandas as pd
import subprocess
from pathlib import Path

HOME = Path.home()
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

def correr(nombre):
    script = HOME / nombre
    if not script.exists():
        st.error(f"No encuentro {script}")
        st.info("Guárdalo en C:\\Users\\Jean Ortega")
        return
    caja = st.empty()
    caja.info(f"Ejecutando {nombre}… puede tardar 1–3 minutos")
    p = subprocess.run(
        ["python", "-u", str(script)],
        capture_output=True,
        text=True,
        cwd=str(HOME),
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
    st.write("Usa flujo2.py. Genera AYER y HOY.")
    st.warning("Si se queda en bucle, en PowerShell pulsa Ctrl+C.")
    if st.button("Generar intradía", type="primary"):
        correr("flujo2.py")

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