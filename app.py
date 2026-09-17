import os, sys, json, subprocess
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

if st.sidebar.text_input("Clave", type="password") != secreto("APP_PASSWORD", "cambiaesta"):
    st.stop()

st.sidebar.header("Franja agresor")
franja_alto = st.sidebar.slider("Alto de la franja (largo)", 0.20, 1.20, 0.62, 0.02)
franja_min = st.sidebar.slider("Ancho de cada bloque (min)", 1, 5, 1, 1)
fig_ancho = st.sidebar.slider("Ancho del gráfico", 10.0, 16.0, 12.2, 0.2)
fig_alto = st.sidebar.slider("Alto del gráfico", 10.0, 16.0, 13.6, 0.2)
st.sidebar.caption("Alto = franja más gorda. Min = bloques más anchos. Luego pulsa AYER/HOY.")

def correr(nombre, modo):
    script = HOME / nombre
    if not script.exists():
        st.error("Falta " + script.name); return
    box = st.empty(); box.info(f"{nombre} · {modo}")
    env = os.environ.copy()
    env["UW_API_KEY"] = secreto("UW_API_KEY", "")
    env["TELEGRAM_BOT_TOKEN"] = secreto("TELEGRAM_BOT_TOKEN", "")
    env["TELEGRAM_CHAT_ID"] = secreto("TELEGRAM_CHAT_ID", "")
    env["FLUJOS_DIR"] = str(CARPETA)
    env["MODO_FLUJO"] = modo
    env["MIN_PREMIUM"] = os.environ.get("MIN_PREMIUM", "120000")
    env["SOLO_0DTE"] = os.environ.get("SOLO_0DTE", "0")
    env["UMBRAL_BURBUJA"] = os.environ.get("UMBRAL_BURBUJA", "0")
    env["ALERTA_USD"] = os.environ.get("ALERTA_USD", "2000000")
    env["FRANJA_ALTO"] = str(franja_alto)
    env["FRANJA_MIN"] = str(franja_min)
    env["FIG_ANCHO"] = str(fig_ancho)
    env["FIG_ALTO"] = str(fig_alto)
    p = subprocess.run([sys.executable, "-u", str(script)],
                       capture_output=True, text=True, cwd=str(HOME), env=env)
    box.empty()
    st.success("Listo") if p.returncode == 0 else st.error("Error")
    if p.stdout: st.text_area("Salida", p.stdout[-3500:], height=150)
    if p.stderr: st.text_area("Avisos", p.stderr[-1000:], height=80)

st.title("Flujo Quant")
ny = datetime.now(ZoneInfo("America/New_York"))
abierto = ny.weekday() < 5 and ny.replace(hour=9, minute=30) <= ny <= ny.replace(hour=16, minute=0)
a,b,c,d = st.columns(4)
a.metric("NY", ny.strftime("%H:%M"))
b.metric("Colombia", datetime.now(ZoneInfo("America/Bogota")).strftime("%H:%M"))
c.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
d.metric("Franja", f"alto {franja_alto:.2f} · {franja_min} min")

t1, t2, t3 = st.tabs(["Intradía", "Swing", "Dashboard"])
with t1:
    f1,f2,f3,f4 = st.columns(4)
    min_p = f1.number_input("Prima mín $", 50000, 3000000, 120000, 10000)
    umb = f2.number_input("Burbuja $M (0=auto)", 0, 500, 0, 5)
    alerta = f3.number_input("Alerta Telegram $M", 0.5, 20.0, 2.0, 0.5)
    dte = f4.checkbox("Solo 0DTE/1DTE")
    os.environ["MIN_PREMIUM"] = str(min_p)
    os.environ["UMBRAL_BURBUJA"] = str(int(umb)*1_000_000)
    os.environ["ALERTA_USD"] = str(int(alerta*1_000_000))
    os.environ["SOLO_0DTE"] = "1" if dte else "0"
    if st.checkbox("Vivo 3 min (solo HOY)") and abierto:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=180000, key="vivo")
            correr("flujo2.py", "HOY")
        except Exception as e:
            st.error(e)
    x,y,z = st.columns(3)
    if x.button("Solo AYER", width="stretch"): correr("flujo2.py","AYER")
    if y.button("AYER + HOY", type="primary", width="stretch"): correr("flujo2.py","AMBOS")
    if z.button("Solo HOY", width="stretch"): correr("flujo2.py","HOY")

with t2:
    if st.button("Generar Swing", width="stretch"):
        correr("flujo_swing.py", "AMBOS") if (HOME/"flujo_swing.py").exists() else st.warning("Falta swing")

with t3:
    res = CARPETA / "resumen.json"
    if res.exists():
        data = json.loads(res.read_text())
        cols = st.columns(3)
        for i, row in enumerate(data[-12:]):
            with cols[i % 3]:
                st.markdown(f"**{row.get('ticker')} {row.get('modo')}**")
                st.caption(f"{row.get('regimen')} {row.get('sesgo')}")
                st.write(f"CW {row.get('cw') or '—'} · PW {row.get('pw') or '—'} · QF {row.get('qf') or '—'}")
                st.write(f"flow {float(row.get('flow_ratio') or 0):.2f}")
    pngs = sorted(CARPETA.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in pngs[:6]:
        if p.stat().st_size <= 3_500_000:
            st.image(str(p), caption=p.name, width="stretch")