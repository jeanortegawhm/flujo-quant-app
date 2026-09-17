import os, sys, subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

st.set_page_config(page_title="Flujo Quant", page_icon="📊", layout="wide")

HOME = Path(__file__).resolve().parent
CARPETA = HOME / "flujos"
CARPETA.mkdir(exist_ok=True)

st.markdown("<style>.stApp{background:#0b1220;color:#e8eef7}</style>", unsafe_allow_html=True)

def secreto(n, d=""):
    try:
        return st.secrets.get(n, os.getenv(n, d))
    except Exception:
        return os.getenv(n, d)

if st.sidebar.text_input("Clave de acceso", type="password") != secreto("APP_PASSWORD", "cambiaesta"):
    st.sidebar.caption("Escribe la clave")
    st.stop()

def correr(nombre, modo="AMBOS"):
    script = HOME / nombre
    if not script.exists():
        st.error(f"No encuentro {script.name}")
        return
    box = st.empty()
    box.info(f"Ejecutando {nombre} ({modo})…")
    env = os.environ.copy()
    env["UW_API_KEY"] = secreto("UW_API_KEY", env.get("UW_API_KEY", ""))
    env["FLUJOS_DIR"] = str(CARPETA)
    env["MODO_FLUJO"] = modo
    env["MIN_PREMIUM"] = os.environ.get("MIN_PREMIUM", "120000")
    env["SOLO_0DTE"] = os.environ.get("SOLO_0DTE", "0")
    env["UMBRAL_BURBUJA"] = os.environ.get("UMBRAL_BURBUJA", "30000000")
    p = subprocess.run([sys.executable, "-u", str(script)],
                       capture_output=True, text=True, cwd=str(HOME), env=env)
    box.empty()
    st.success("Listo") if p.returncode == 0 else st.error("Error")
    if p.stdout: st.text_area("Salida", p.stdout[-4000:], height=180)
    if p.stderr: st.text_area("Avisos", p.stderr[-1500:], height=100)

st.title("Flujo Quant")
ny = datetime.now(ZoneInfo("America/New_York"))
abierto = ny.weekday() < 5 and ny.replace(hour=9, minute=30) <= ny <= ny.replace(hour=16, minute=0)
a, b, c = st.columns(3)
a.metric("NY", ny.strftime("%H:%M"))
b.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
c.metric("Colombia", datetime.now(ZoneInfo("America/Bogota")).strftime("%H:%M"))

t1, t2, t3 = st.tabs(["Intradía", "Swing", "Resultados"])

with t1:
    with st.expander("Filtros"):
        f1, f2, f3 = st.columns(3)
        min_p = f1.number_input("Prima mín $", 50000, 2000000, 120000, 10000)
        dte = f2.checkbox("Solo 0DTE/1DTE", False)
        umb = f3.number_input("Burbuja $M", 5, 500, 30, 5)
    os.environ["MIN_PREMIUM"] = str(min_p)
    os.environ["SOLO_0DTE"] = "1" if dte else "0"
    os.environ["UMBRAL_BURBUJA"] = str(int(umb) * 1_000_000)
    auto = st.checkbox("Vivo cada 3 min (HOY)", False)
    if auto:
        if not abierto:
            st.warning("Mercado cerrado (9:30–16:00 NY)")
        else:
            try:
                from streamlit_autorefresh import st_autorefresh
                st_autorefresh(interval=180000, key="vivo")
                correr("flujo2.py", "HOY")
            except Exception as e:
                st.error(e)
    x, y, z = st.columns(3)
    if x.button("Solo AYER", width="stretch"): correr("flujo2.py", "AYER")
    if y.button("AYER + HOY", type="primary", width="stretch"): correr("flujo2.py", "AMBOS")
    if z.button("Solo HOY", width="stretch"): correr("flujo2.py", "HOY")

with t2:
    if st.button("Generar Swing", width="stretch"):
        if (HOME / "flujo_swing.py").exists():
            correr("flujo_swing.py", "AMBOS")
        else:
            st.warning("Falta flujo_swing.py")

with t3:
    pngs = sorted(CARPETA.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    n = 0
    for p in pngs:
        if n >= 6: break
        if p.stat().st_size > 2_000_000: continue
        try:
            st.image(str(p), caption=p.name, width="stretch")
            n += 1
        except Exception:
            pass
    if n == 0:
        st.info("Sin gráficos (o son muy pesados). Corre Intradía.")