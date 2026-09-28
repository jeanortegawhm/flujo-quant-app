import os, glob, json, time, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
from streamlit_autorefresh import st_autorefresh

os.environ["FLUJOS_DIR"] = os.path.abspath("./flujos")
os.makedirs(os.environ["FLUJOS_DIR"], exist_ok=True)

from paneles import (
    last_price, gex_niveles, gex_strikes, oi_vol, darkpool,
    fig_gex, fig_oi, fig_dp, sesion_habil, ayer_habil, PARES,
)
from desks import flow_strike, greeks_net, oi_change, multi_leg, scanner, fig_flow_strike, fig_oi_chg

TZ = ZoneInfo("America/New_York")
TZ_COL = ZoneInfo("America/Bogota")
CARPETA = os.environ["FLUJOS_DIR"]
LIBROS = ["QQQ", "NDX", "SPY", "SPX", "DIA", "DJX", "GLD"]

def secret(k, default=""):
    try:
        return st.secrets.get(k, os.getenv(k, default))
    except Exception:
        return os.getenv(k, default)

os.environ["UW_API_KEY"] = secret("UW_API_KEY")
os.environ["TELEGRAM_BOT"] = secret("TELEGRAM_BOT")
os.environ["TELEGRAM_CHAT"] = secret("TELEGRAM_CHAT")
CLAVE = secret("APP_PASSWORD", "")

st.set_page_config(page_title="Flujo Quant", layout="wide")
st.markdown("""
<style>
.stApp { background:#0b1220; color:#e8eef7; }
h1,h2,h3 { color:#e8eef7; }
div[data-testid="stMetricValue"] { color:#e8eef7; }
</style>
""", unsafe_allow_html=True)

if CLAVE:
    if st.session_state.get("ok") != True:
        p = st.text_input("Clave", type="password")
        if st.button("Entrar") and p == CLAVE:
            st.session_state["ok"] = True
            st.rerun()
        elif p:
            st.error("Clave incorrecta")
        st.stop()

now = datetime.now(TZ)
col = datetime.now(TZ_COL)
abierto = now.weekday() < 5 and (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16

st.title("Flujo Quant")
st.caption("QQQ/NDX · SPY/SPX · DIA/DJX · GLD — 9:30–16:00 NY — precio UW — el print no es la dirección")
a, b, c, d = st.columns(4)
a.metric("NY", now.strftime("%H:%M"))
b.metric("Colombia", col.strftime("%H:%M"))
c.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
d.metric("Libros", "AYER" if now.weekday() >= 5 else "HOY")

dia_lib = st.sidebar.radio("Día libros / GEX / Desk", ["AYER", "HOY"], index=0 if now.weekday() >= 5 else 1)
FECHA_LIB = ayer_habil() if dia_lib == "AYER" else sesion_habil()
st.sidebar.caption(f"Fecha UW: {FECHA_LIB}")

t1, t2, t3, t4, t5, t6 = st.tabs(["Flujo", "Swing", "Dashboard", "Libros QQQ/NDX", "GEX · OI · DP", "Desk UW"])

def correr(script, extra_env=None, timeout=300):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        p = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           env=env, timeout=timeout, cwd=os.getcwd())
    except subprocess.TimeoutExpired:
        st.error(f"Timeout {timeout}s")
        return
    if p.stdout:
        st.code(p.stdout[-8000:], language="text")
    if p.stderr:
        st.code(p.stderr[-4000:], language="text")
    if p.returncode == 0:
        st.success("Listo")
    else:
        st.error(f"Error código {p.returncode}")

with t1:
    st.info("Flujo intradía. Un print grande no es la dirección del ETF.")
    modo = st.radio("Sesión", ["AYER", "AMBOS", "HOY"], horizontal=True, index=0)
    vivo = st.checkbox("En vivo cada 3 min (solo HOY y mercado abierto)")
    if st.button("Generar Flujo", type="primary"):
        correr("flujo2.py", {"MODO_FLUJO": modo})
    if vivo and modo == "HOY" and abierto:
        st_autorefresh(interval=180_000, key="vivo")
        correr("flujo2.py", {"MODO_FLUJO": "HOY"})

with t2:
    st.info("Qué ves. Swing de 8 días hábiles. Un print 0DTE no abre swing.")
    if st.button("Generar Swing", type="primary"):
        correr("flujo_swing.py", timeout=420)
    st.caption("Mira Salida: debe decir «velas QQQ N días». Luego ve a Dashboard.")

with t3:
    st.info("Dashboard. 2 de 3 pilares. QF fuera de rango = neutro.")
    path = os.path.join(CARPETA, "resumen.json")
    cards = []
    if os.path.exists(path):
        try:
            cards = json.loads(open(path).read())
        except Exception:
            cards = []
    if not cards:
        st.warning("Aún no hay resumen. Genera Flujo o Swing.")
    for r in cards:
        st.subheader(f"{r.get('ticker')}  ·  {r.get('modo')}  ·  {r.get('semaforo')}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ratio", f"{float(r.get('flow_ratio') or 0):.2f}")
        c2.metric("QF", r.get("qf"))
        c3.metric("CW", r.get("cw"))
        c4.metric("PW", r.get("pw"))
        if r.get("nota"):
            st.caption(r["nota"])
    pngs = sorted(glob.glob(os.path.join(CARPETA, "*.png")), key=os.path.getmtime, reverse=True)
    for p in pngs[:16]:
        st.image(p, caption=os.path.basename(p), width="stretch")

with t4:
    st.info("Libros. QQQ vs NDX, SPY vs SPX, DIA vs DJX. Zoom spot ±4%.")
    for izq, der in PARES:
        c1, c2 = st.columns(2)
        for col, tk in ((c1, izq), (c2, der)):
            with col:
                spot = last_price(tk)
                niv = gex_niveles(tk, FECHA_LIB, spot)
                df = gex_strikes(tk, FECHA_LIB, spot)
                st.pyplot(fig_gex(tk, df, niv, spot), width="stretch")
    st.subheader("GLD")
    spot = last_price("GLD")
    st.pyplot(fig_gex("GLD", gex_strikes("GLD", FECHA_LIB, spot),
                      gex_niveles("GLD", FECHA_LIB, spot), spot), width="stretch")

with t5:
    st.info("GEX · OI · dark pool. Sesión 9:30–16:00.")
    tk = st.selectbox("Libro", ["QQQ", "SPY", "DIA", "GLD", "SPX", "NDX"])
    spot = last_price(tk)
    st.metric("Spot UW", spot)
    st.pyplot(fig_gex(tk, gex_strikes(tk, FECHA_LIB, spot),
                      gex_niveles(tk, FECHA_LIB, spot), spot), width="stretch")
    st.pyplot(fig_oi(tk, oi_vol(tk, FECHA_LIB)), width="stretch")
    st.pyplot(fig_dp(tk, darkpool(tk, FECHA_LIB), spot), width="stretch")

with t6:
    st.info("Desk UW. Strike = dónde se sentó la prima. ΔOI = lo que se quedó. Vanna/charm = MM.")
    tk = st.selectbox("Libro desk", ["QQQ", "SPY", "SPX", "DIA", "GLD"], key="desk_tk")
    minm = st.number_input("Escáner min $M", 0.5, 10.0, 1.5, 0.25)
    spot = last_price(tk)
    g = greeks_net(tk, FECHA_LIB)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net γ", f"{g.get('net_gamma', 0):,.0f}")
    m2.metric("Net vanna", f"{g.get('net_vanna', 0):,.0f}")
    m3.metric("Net charm", f"{g.get('net_charm', 0):,.0f}")
    m4.metric("Spot UW", spot)
    fs = flow_strike(tk, FECHA_LIB, spot)
    st.pyplot(fig_flow_strike(tk, fs, spot), width="stretch")
    st.pyplot(fig_oi_chg(tk, oi_change(tk, FECHA_LIB)), width="stretch")
    st.subheader("Multi-leg")
    ml = multi_leg(tk, FECHA_LIB)
    if ml.empty:
        st.caption("Sin spreads en esa fecha.")
    else:
        st.dataframe(ml.head(25), width="stretch")
    st.subheader("Escáner mercado")
    sc = scanner(FECHA_LIB, min_prem=int(minm * 1_000_000))
    if sc.empty:
        st.caption("Sin alertas.")
    else:
        st.dataframe(sc.head(25), width="stretch")
