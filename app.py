import os, sys, json, subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

st.set_page_config(page_title="Flujo Quant", page_icon="📊", layout="wide")
HOME = Path(__file__).resolve().parent
CARPETA = HOME / "flujos"
CARPETA.mkdir(exist_ok=True)

st.markdown("""
<style>
.stApp { background:#0b1220; color:#e8eef7; }
div[data-testid="stMetricValue"] { color:#e8eef7; }
</style>
""", unsafe_allow_html=True)

def secreto(n, d=""):
    try:
        return st.secrets.get(n, os.getenv(n, d))
    except Exception:
        return os.getenv(n, d)

if st.sidebar.text_input("Clave", type="password") != secreto("APP_PASSWORD", "cambiaesta"):
    st.stop()

def correr(nombre, modo):
    script = HOME / nombre
    if not script.exists():
        st.error("Falta " + script.name)
        return
    box = st.empty()
    box.info(f"{nombre} · {modo}")
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
    p = subprocess.run([sys.executable, "-u", str(script)],
                       capture_output=True, text=True, cwd=str(HOME), env=env)
    box.empty()
    st.success("Listo") if p.returncode == 0 else st.error("Error")
    if p.stdout:
        st.text_area("Salida", p.stdout[-3500:], height=160)
    if p.stderr:
        st.text_area("Avisos", p.stderr[-1200:], height=90)

st.title("Flujo Quant")
st.caption("GEX · Net premium · Q-Delta · Unusual flow")
ny = datetime.now(ZoneInfo("America/New_York"))
abierto = ny.weekday() < 5 and ny.replace(hour=9, minute=30) <= ny <= ny.replace(hour=16, minute=0)
c1, c2, c3, c4 = st.columns(4)
c1.metric("NY", ny.strftime("%H:%M"))
c2.metric("Colombia", datetime.now(ZoneInfo("America/Bogota")).strftime("%H:%M"))
c3.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
c4.metric("Cuota", "AYER si está cerrado")

t1, t2, t3 = st.tabs(["Intradía", "Swing", "Dashboard"])

with t1:
    f1, f2, f3, f4 = st.columns(4)
    min_p = f1.number_input("Prima mín $", 50000, 3000000, 120000, 10000)
    umb = f2.number_input("Burbuja $M (0=auto)", 0, 500, 0, 5)
    alerta = f3.number_input("Alerta Telegram $M", 0.5, 20.0, 2.0, 0.5)
    dte = f4.checkbox("Solo 0DTE/1DTE")
    os.environ["MIN_PREMIUM"] = str(min_p)
    os.environ["SOLO_0DTE"] = "1" if dte else "0"
    os.environ["UMBRAL_BURBUJA"] = str(int(umb) * 1_000_000)
    os.environ["ALERTA_USD"] = str(int(alerta * 1_000_000))
    auto = st.checkbox("En vivo cada 3 min (solo HOY)", False)
    if auto:
        if not abierto:
            st.warning("Vivo solo 9:30–16:00 NY")
        else:
            try:
                from streamlit_autorefresh import st_autorefresh
                st_autorefresh(interval=180000, key="vivo")
                correr("flujo2.py", "HOY")
            except Exception as e:
                st.error(e)
    a, b, c = st.columns(3)
    if a.button("Solo AYER", width="stretch"):
        correr("flujo2.py", "AYER")
    if b.button("AYER + HOY", type="primary", width="stretch"):
        correr("flujo2.py", "AMBOS")
    if c.button("Solo HOY", width="stretch"):
        correr("flujo2.py", "HOY")

with t2:
    if st.button("Generar Swing", width="stretch"):
        if (HOME / "flujo_swing.py").exists():
            correr("flujo_swing.py", "AMBOS")
        else:
            st.warning("Falta flujo_swing.py")

with t3:
    res = CARPETA / "resumen.json"
    if res.exists():
        data = json.loads(res.read_text(encoding="utf-8"))
        st.subheader("Resumen multi-ticker")
        cols = st.columns(3)
        for i, row in enumerate(data[-12:]):
            with cols[i % 3]:
                st.markdown(f"**{row.get('ticker')} {row.get('modo')}**")
                st.caption(row.get("fecha", ""))
                st.metric("Régimen", f"{row.get('regimen','')} {row.get('sesgo','')}")
                st.write(
                    f"CW {row.get('cw') or '—'} · PW {row.get('pw') or '—'} · QF {row.get('qf') or '—'}"
                )
                st.write(f"qΔ {row.get('qdelta',0):,.0f} · flow {float(row.get('flow_ratio') or 0):.2f}")
    pngs = sorted(CARPETA.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    st.subheader("Gráficos")
    if not pngs:
        st.info("Genera Intradía primero")
    else:
        for i in range(0, min(len(pngs), 6), 2):
            left, right = st.columns(2)
            for col, p in zip((left, right), pngs[i:i+2]):
                if p.stat().st_size > 3_500_000:
                    continue
                with col:
                    st.image(str(p), caption=p.name, width="content")