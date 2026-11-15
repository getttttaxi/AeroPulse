import base64
import json
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="AeroPulse — Динамическое ценообразование",
    layout="wide",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")

DEFAULT_USERS = {
    "expert": "reshis2026",
    "manager": "avia2026",
}

DEFAULT_ROUTES = pd.DataFrame([
    {"route": "MOW-AER", "name": "Москва – Сочи", "base_price": 9000, "cost_per_passenger": 4200, "price_sensitivity": 0.141, "price_min": 5400, "price_max": 18000},
    {"route": "MOW-LED", "name": "Москва – Санкт-Петербург", "base_price": 5500, "cost_per_passenger": 2600, "price_sensitivity": 0.294, "price_min": 3300, "price_max": 11000},
    {"route": "MOW-KGD", "name": "Москва – Калининград", "base_price": 7500, "cost_per_passenger": 3500, "price_sensitivity": 0.0957, "price_min": 4500, "price_max": 15000},
    {"route": "MOW-OVB", "name": "Москва – Новосибирск", "base_price": 12000, "cost_per_passenger": 6000, "price_sensitivity": 0.0312, "price_min": 7200, "price_max": 24000},
    {"route": "MOW-MRV", "name": "Москва – Минеральные Воды", "base_price": 8000, "cost_per_passenger": 3800, "price_sensitivity": 0.0653, "price_min": 4800, "price_max": 16000},
])

QUANTILES = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]


def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {**DEFAULT_USERS, **data}
        except Exception:
            pass
    return DEFAULT_USERS.copy()


def save_user(username, password):
    users = load_users()
    users[username] = password
    st.session_state["users_db"] = users
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def calc_optimal_tariff(base_dem, p_base, cost_pax, sens, seats, p_min, p_max):
    if base_dem <= 0 or sens <= 0:
        return float(p_base), 0.0
    p_unconstrained = (base_dem + sens * p_base + sens * cost_pax) / (2.0 * sens)
    p_cap = p_base + (base_dem - seats) / sens
    p_opt = max(p_unconstrained, p_cap)
    p_opt = min(max(p_opt, float(p_min)), float(p_max))
    sold = min(float(seats), max(0.0, base_dem - sens * (p_opt - p_base)))
    return round(p_opt, 2), round(sold, 2)


@st.cache_data
def load_full_network():
    forecast_path = os.path.join(BASE_DIR, "my_forecast.csv")
    routes_path = os.path.join(BASE_DIR, "routes.csv")
    raw_df = pd.read_csv(forecast_path)
    routes = pd.read_csv(routes_path) if os.path.exists(routes_path) else DEFAULT_ROUTES.copy()

    merged = raw_df.merge(routes, on="route", how="left")
    merged["date"] = pd.to_datetime(merged["date"])

    all_rows = []
    for rec in merged.to_dict("records"):
        parts = str(rec["name"]).replace("—", "–").split("–")
        city_a = parts.__getitem__(0).strip() if len(parts) > 1 else "Москва"
        city_b = parts.__getitem__(1).strip() if len(parts) > 1 else str(rec["name"]).strip()
        codes = str(rec["route"]).split("-")
        code_a, code_b = codes.__getitem__(0), codes.__getitem__(1)

        fwd = dict(rec)
        fwd["origin"] = city_a
        fwd["destination"] = city_b
        fwd["flight_type"] = "Прямой рейс"
        fwd["route_label"] = f"{city_a} – {city_b}"
        all_rows.append(fwd)

        rev = dict(rec)
        rev["route"] = f"{code_b}-{code_a}"
        rev["name"] = f"{city_b} – {city_a}"
        rev["origin"] = city_b
        rev["destination"] = city_a
        rev["flight_type"] = "Прямой обратный"
        rev["route_label"] = f"{city_b} – {city_a}"
        for q in QUANTILES:
            rev[f"{q}_base_demand"] = round(float(rec[f"{q}_base_demand"]) * 0.96, 2)
            p_o, s_o = calc_optimal_tariff(
                rev[f"{q}_base_demand"], rec["base_price"], rec["cost_per_passenger"],
                rec["price_sensitivity"], rec["seats"], rec["price_min"], rec["price_max"]
            )
            rev[f"{q}_price"] = p_o
            rev[f"{q}_price_demand"] = s_o
        all_rows.append(rev)

    base_df = pd.DataFrame(all_rows)
    for dt_val, grp in base_df.groupby("date"):
        legs = {r["origin"]: r for r in grp.to_dict("records") if r["destination"] == "Москва"}
        outs = {r["destination"]: r for r in grp.to_dict("records") if r["origin"] == "Москва"}
        for orig_c, leg1 in legs.items():
            for dest_c, leg2 in outs.items():
                if orig_c == dest_c:
                    continue
                c1 = str(leg1["route"]).split("-").__getitem__(0)
                c2 = str(leg2["route"]).split("-").__getitem__(1)
                tr_seats = int(min(leg1["seats"], leg2["seats"]))
                tr_base_p = float(leg1["base_price"]) + float(leg2["base_price"])
                tr_cost = float(leg1["cost_per_passenger"]) + float(leg2["cost_per_passenger"])
                tr_sens = round(min(float(leg1["price_sensitivity"]), float(leg2["price_sensitivity"])) * 0.75, 4)
                tr_pmin = float(leg1["price_min"]) + float(leg2["price_min"])
                tr_pmax = float(leg1["price_max"]) + float(leg2["price_max"])

                tr_row = {
                    "route": f"{c1}-{c2}",
                    "name": f"{orig_c} – {dest_c}",
                    "date": dt_val,
                    "seats": tr_seats,
                    "base_price": tr_base_p,
                    "cost_per_passenger": tr_cost,
                    "price_sensitivity": tr_sens,
                    "price_min": tr_pmin,
                    "price_max": tr_pmax,
                    "origin": orig_c,
                    "destination": dest_c,
                    "flight_type": "Транзит через Москву",
                    "route_label": f"{orig_c} – {dest_c}",
                }
                for q in QUANTILES:
                    b_dem = round(min(float(leg1[f"{q}_base_demand"]), float(leg2[f"{q}_base_demand"])) * 0.42, 2)
                    p_o, s_o = calc_optimal_tariff(b_dem, tr_base_p, tr_cost, tr_sens, tr_seats, tr_pmin, tr_pmax)
                    tr_row[f"{q}_base_demand"] = b_dem
                    tr_row[f"{q}_price"] = p_o
                    tr_row[f"{q}_price_demand"] = s_o
                all_rows.append(tr_row)

    return pd.DataFrame(all_rows)


if "users_db" not in st.session_state:
    st.session_state["users_db"] = load_users()
if "current_user" not in st.session_state:
    st.session_state["current_user"] = None
if "booked_extra" not in st.session_state:
    st.session_state["booked_extra"] = {}


def show_auth_screen():
    st.markdown("""
        <style>
        .stApp { background-color: #BDD5EA; }
        hr, div[data-testid="stDivider"] { display: none !important; }
        /* Заливаем карточку входа светлым фоном #F7F7FF */
        div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stColumn"]:nth-of-type(2) > div > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {
            background-color: #F7F7FF !important;
            border-radius: 14px !important;
            padding: 28px 32px !important;
            box-shadow: 0 10px 28px rgba(73, 88, 103, 0.22) !important;
            border: 2px solid #495867 !important;
        }
        /* Весь текст, вкладки («Вход в систему», «Регистрация») и подписи («Логин», «Пароль») — строго чёрные */
        .stApp label, .stApp label p, .stApp p, .stApp span,
        button[data-baseweb="tab"] p {
            color: #000000 !important;
            font-weight: 600 !important;
            font-size: 15px !important;
        }
        /* Поля ввода с белым текстом на тёмном фоне (или наоборот — чёткий контраст) */
        .stApp input {
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
        }
        /* Фирменные синие кнопки #577399 с белым текстом */
        .stApp button[kind="primary"],
        .stApp div[data-testid="stFormSubmitButton"] button {
            background-color: #577399 !important;
            border: 1px solid #495867 !important;
            color: #FFFFFF !important;
            font-weight: 700 !important;
        }
        .stApp button[kind="primary"] p,
        .stApp div[data-testid="stFormSubmitButton"] button p {
            color: #FFFFFF !important;
        }
        .stApp button[kind="secondary"] {
            background-color: #BDD5EA !important;
            border: 1px solid #495867 !important;
            color: #000000 !important;
            font-weight: 600 !important;
        }
    </style>
    <br>
    """, unsafe_allow_html=True)

    _, center_col, _ = st.columns([1, 1.4, 1])
    with center_col:
        with st.container(border=True):
            st.markdown(
                "<h2 style='text-align: center; margin-bottom: 4px; color: #000000; font-size: 28px;'>AeroPulse</h2>"
                "<p style='text-align: center; color: #000000; font-size: 15px; margin-top: 0;'>"
                "Система прогнозирования спроса и динамического ценообразования</p>",
                unsafe_allow_html=True,
            )
            tab_in, tab_up = st.tabs(["Вход в систему", "Регистрация"])
            with tab_in:
                with st.form("login_form", border=False):
                    login_user = st.text_input("Логин", placeholder="Введите логин (например, expert)")
                    login_pass = st.text_input("Пароль", type="password", placeholder="Введите пароль")
                    if st.form_submit_button("Войти в кабинет", type="primary", use_container_width=True):
                        users = load_users()
                        if login_user in users and users[login_user] == login_pass:
                            st.session_state["current_user"] = login_user
                            st.rerun()
                        else:
                            st.error("Неверный логин или пароль.")
            with tab_up:
                with st.form("register_form", border=False):
                    new_user = st.text_input("Новый логин", placeholder="Придумайте логин")
                    new_pass = st.text_input("Новый пароль", type="password", placeholder="Придумайте пароль")
                    new_pass2 = st.text_input("Подтверждение пароля", type="password", placeholder="Повторите пароль")
                    if st.form_submit_button("Создать аккаунт", type="primary", use_container_width=True):
                        users = load_users()
                        if not new_user.strip() or not new_pass:
                            st.warning("Заполните логин и пароль.")
                        elif new_user.strip() in users:
                            st.error("Такой логин уже занят.")
                        elif new_pass != new_pass2:
                            st.error("Пароли не совпадают.")
                        else:
                            save_user(new_user.strip(), new_pass)
                            st.session_state["current_user"] = new_user.strip()
                            st.rerun()
                st.markdown(
                    "<p style='text-align: center; color: #000000; font-size: 14px; margin: 12px 0 8px 0;'>"
                    "Демо-доступ для проверки (логин: <b>expert</b>, пароль: <b>reshis2026</b>):</p>",
                    unsafe_allow_html=True,
                )
                if st.button("Войти в демо-режим в один клик", type="secondary", use_container_width=True):
                    st.session_state["current_user"] = "expert"
                    st.rerun()

if st.session_state["current_user"] is None:
    show_auth_screen()
    st.stop()

df = load_full_network()
# Плашка профиля и выхода в правом верхнем углу
_, profile_col = st.columns([5.2, 2.8])
with profile_col:
    p_info_col, p_btn_col = st.columns([1.6, 1.4])
    with p_info_col:
        st.markdown(
            f"<div style='background-color: #F7F7FF; border: 1px solid #495867; border-radius: 8px; "
            f"padding: 7px 14px; text-align: center; font-size: 16px; color: #000000;'>"
            f"<b>{st.session_state['current_user']}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with p_btn_col:
        if st.button("Выйти из аккаунта", use_container_width=True):
            st.session_state["current_user"] = None
            st.rerun()


def get_banner_css():
    for fname in ("banner.jpg", "banner.jpeg", "banner.png"):
        fpath = os.path.join(BASE_DIR, fname)
        if os.path.exists(fpath):
            with open(fpath, "rb") as img_f:
                b64 = base64.b64encode(img_f.read()).decode("utf-8")
            ext = "png" if fname.endswith(".png") else "jpeg"
            return f"linear-gradient(rgba(18, 22, 28, 0.25), rgba(18, 22, 28, 0.35)), url('data:image/{ext};base64,{b64}')"
    return "linear-gradient(135deg, #577399 0%, #495867 100%)"


banner_bg = get_banner_css()

st.markdown(f"""
<style>
    html {{ scroll-behavior: smooth; }}
    .stApp {{
        background-color: #BDD5EA;
    }}
    section[data-testid="stSidebar"], button[data-testid="collapsedControl"],
    hr, div[data-testid="stDivider"] {{
        display: none !important;
    }}
    /* Весь базовый текст строго чёрный */
    .stApp h1, .stApp h2, .stApp h3, .stApp h4,
    .stApp p, .stApp label, .stApp li,
    div[data-testid="stMetricValue"], div[data-testid="stMetricLabel"],
    div[data-testid="stCaptionContainer"] {{
        color: #000000 !important;
    }}
    /* Основные кнопки интерфейса: цвет #577399 и белый текст */
    div[data-testid="stButton"] > button,
    div[data-testid="stFormSubmitButton"] > button,
    div[data-testid="stDownloadButton"] > button {{
        background-color: #577399 !important;
        border: 1px solid #495867 !important;
        color: #FFFFFF !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
    }}
    div[data-testid="stButton"] > button p,
    div[data-testid="stFormSubmitButton"] > button p,
    div[data-testid="stDownloadButton"] > button p {{
        color: #FFFFFF !important;
    }}
    div[data-testid="stButton"] > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover,
    div[data-testid="stDownloadButton"] > button:hover {{
        background-color: #495867 !important;
    }}
    div[data-testid="stButton"] > button:disabled {{
        background-color: #8FA3BE !important;
        color: #F7F7FF !important;
        opacity: 0.75 !important;
    }}
    /* Прозрачный фон у системных иконок графиков и полей ввода */
    div[data-testid="stPlotlyChart"] button,
    div[data-testid="stElementToolbar"] button,
    div[data-baseweb="select"] button,
    div[data-baseweb="select"] svg,
    div[data-testid="stDateInput"] button {{
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }}
    /* Поля выбора и ввода без красных рамок, цифры даты белые */
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="base-input"] {{
        border-color: #495867 !important;
        box-shadow: none !important;
    }}
    div[data-baseweb="select"] > div:focus-within,
    div[data-baseweb="input"] > div:focus-within,
    div[data-baseweb="base-input"]:focus-within {{
        border-color: #577399 !important;
        box-shadow: 0 0 0 1px #577399 !important;
    }}
    .stApp [data-baseweb="input"] input,
    .stApp [data-baseweb="base-input"] input,
    .stApp div[data-testid="stDateInput"] input {{
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        opacity: 1 !important;
        transition: color 0.15s ease !important;
    }}
    .stApp [data-baseweb="input"]:hover input,
    .stApp div[data-testid="stDateInput"]:hover input {{
        color: #BDD5EA !important;
        -webkit-text-fill-color: #BDD5EA !important;
    }}
    /* Теги фильтра «Тип рейсов» чуть темнее фона (#364559) с белым текстом */
    span[data-baseweb="tag"] {{
        background-color: #364559 !important;
        border: 1px solid #263140 !important;
        border-radius: 6px !important;
    }}
    span[data-baseweb="tag"] span,
    span[data-baseweb="tag"] svg {{
        background-color: transparent !important;
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
    }}
    /* Ярко-красные и ярко-зелёные бейджи под метриками */
    div[data-testid="stMetricDelta"] {{
        padding: 3px 10px !important;
        border-radius: 12px !important;
        width: fit-content !important;
    }}
    div[data-testid="stMetricDelta"]:has(svg[data-testid="stMetricDeltaIcon-Down"]) {{
        background-color: #E53935 !important;
    }}
    div[data-testid="stMetricDelta"]:has(svg[data-testid="stMetricDeltaIcon-Up"]) {{
        background-color: #2E7D32 !important;
    }}
    div[data-testid="stMetricDelta"] svg,
    div[data-testid="stMetricDelta"] div {{
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
        font-weight: 600 !important;
    }}
    /* Скругление краёв у таблиц и графиков */
    div[data-testid="stDataFrame"],
    div[data-testid="stPlotlyChart"] {{
        border-radius: 12px !important;
        overflow: hidden !important;
        border: 1px solid #495867 !important;
    }}
    /* Верхняя шапка */
    .top-navbar {{
        background-color: #F7F7FF;
        padding: 14px 28px;
        border-radius: 12px;
        border: 1px solid #495867;
        border-bottom: 3px solid #577399;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
    }}
    .brand-box {{
        font-size: 22px;
        font-weight: 700;
        color: #000000 !important;
    }}
    .nav-links a {{
        color: #000000 !important;
        text-decoration: none;
        margin-left: 20px;
        font-size: 14px;
        font-weight: 600;
    }}
    .nav-links a:hover {{
        color: #577399 !important;
        text-decoration: underline;
    }}
    /* Баннер с самолётом без острых углов и белой рамки */
    .hero-banner {{
        min-height: 82vh;
        background-image: {banner_bg};
        background-size: cover;
        background-position: center;
        padding: 48px 24px 40px 24px;
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid #495867;
        margin-top: 12px;
        margin-bottom: 28px;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        align-items: center;
    }}
    .hero-overlay-box {{
        background-color: transparent;
        padding: 12px 24px;
        text-align: center;
        max-width: 860px;
    }}
    .hero-overlay-box h1 {{
        color: #000000 !important;
        font-size: 28px;
        font-weight: 700;
        margin: 0 0 8px 0;
        line-height: 1.3;
    }}
    .hero-overlay-box p {{
        color: #000000 !important;
        font-size: 16px;
        font-weight: 500;
        margin: 0;
    }}
    /* Единый стиль рамок как у нижнего футера */
    .sirena-box {{
        background-color: #577399;
        border: 2px solid #495867;
        border-radius: 12px;
        padding: 18px 20px;
        margin-top: 16px;
        margin-bottom: 24px;
    }}
    .sirena-inner {{
        background-color: #F7F7FF;
        border: 1px solid #495867;
        border-radius: 8px;
        padding: 18px 22px;
        color: #000000 !important;
    }}
    /* Нижний футер */
    .sirena-footer {{
        background-color: #577399;
        border: 2px solid #495867;
        padding: 28px 28px 20px 28px;
        border-radius: 12px;
        margin-top: 28px;
    }}
    .footer-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 20px;
        margin-bottom: 20px;
    }}
    .footer-col {{
        background-color: #F7F7FF;
        border: 1px solid #495867;
        border-radius: 8px;
        padding: 16px 18px;
    }}
    .footer-col h4 {{
        color: #000000 !important;
        font-size: 15px;
        font-weight: 700;
        margin-bottom: 10px;
        border-bottom: 2px solid #577399;
        padding-bottom: 6px;
    }}
    .footer-col p, .footer-col li {{
        color: #000000 !important;
        font-size: 13px;
        line-height: 1.55;
        margin: 0 0 6px 0;
    }}
    .footer-col ul {{ padding-left: 16px; margin: 0; }}
    .footer-copy {{
        background-color: #F7F7FF;
        border: 1px solid #495867;
        border-radius: 8px;
        text-align: center;
        font-size: 12px;
        font-weight: 600;
        color: #000000 !important;
        padding: 10px;
    }}
</style>

<div class="top-navbar">
    <div class="brand-box">AeroPulse</div>
    <div class="nav-links">
        <a href="#calc-section">Расчёт рейса и 9 сценариев</a>
        <a href="#analytics-section">Аналитика за период</a>
        <a href="#routes-section">Сравнение маршрутов</a>
        <a href="#ai-footer">О системе и ИИ</a>
    </div>
</div>

<div class="hero-banner">
    <div class="hero-overlay-box">
        <h1>Интеллектуальная система прогнозирования спроса и динамического ценообразования</h1>
        <p>Аналитическая платформа управления доходами и тарифной сеткой маршрутной сети «AeroPulse»</p>
    </div>
</div>
""", unsafe_allow_html=True)

# === БЛОК 1: РАСЧЁТ РЕЙСА И 9 СЦЕНАРИЕВ ===
st.subheader(
    f"1. Расчёт спроса, продаж и распределения по 9 квантилям, "
    f"{df['origin'].nunique()} городам и {df['route_label'].nunique()} направлениям",
    anchor="calc-section",
)
col_in1, col_in2, col_in3, col_in4 = st.columns(4)
with col_in1:
    origins = sorted(df["origin"].unique().tolist())
    selected_origin = st.selectbox("Пункт отправления", origins, index=0)
with col_in2:
    destinations = sorted(df[df["origin"] == selected_origin]["destination"].unique().tolist())
    selected_dest = st.selectbox("Пункт назначения", destinations, index=0)

route_sub = df[(df["origin"] == selected_origin) & (df["destination"] == selected_dest)].sort_values("date").reset_index(drop=True)

with col_in3:
    avail_dates = route_sub["date"].dt.strftime("%Y-%m-%d").tolist()
    min_flight_date = route_sub["date"].min().date()
    max_flight_date = route_sub["date"].max().date()
    picked_date = st.date_input(
        "Дата вылета",
        value=min_flight_date,
        min_value=min_flight_date,
        max_value=max_flight_date,
        format="DD.MM.YYYY",
    )
    selected_date_str = picked_date.strftime("%Y-%m-%d")

sel_idx = avail_dates.index(selected_date_str)
flight_row = route_sub.iloc.__getitem__(sel_idx)

p_min = int(flight_row["price_min"])
p_max = int(flight_row["price_max"])
p_base = int(flight_row["base_price"])
rec_price = int(round(flight_row["0.5_price"]))
sens = float(flight_row["price_sensitivity"])
total_seats = int(flight_row["seats"])
cost_pax = float(flight_row["cost_per_passenger"])
f_type = flight_row["flight_type"]

flight_key = f"{selected_origin}_{selected_dest}_{selected_date_str}"
extra_booked = st.session_state["booked_extra"].get(flight_key, 0)
seats = max(0, total_seats - extra_booked)

if st.session_state.get("last_flight_key") != flight_key:
    st.session_state["last_flight_key"] = flight_key
    st.session_state["ticket_price_input"] = p_base


def apply_optimal_price():
    st.session_state["ticket_price_input"] = rec_price


with col_in4:
    ticket_price = st.number_input("Цена билета, ₽", min_value=1000, max_value=100000, step=100, key="ticket_price_input")
    st.button(f"Оптимальный тариф: {rec_price} ₽", on_click=apply_optimal_price, use_container_width=True)

st.caption(f"Тип перелёта: **{f_type}** | Базовый тариф: **{p_base} ₽** | Коридор тарифов: **{p_min} – {p_max} ₽**")

demand_median_clean = max(0.0, float(flight_row["0.5_base_demand"]) - sens * (ticket_price - p_base))
sold_tickets = min(seats, int(round(demand_median_clean)))
free_seats = max(0, seats - sold_tickets)
expected_profit = (ticket_price - cost_pax) * sold_tickets
opt_sold = min(seats, int(round(max(0.0, float(flight_row["0.5_base_demand"]) - sens * (rec_price - p_base)))))
profit_delta = expected_profit - (rec_price - cost_pax) * opt_sold

# Карточка показателей в едином стиле рамок футера
cal_slice = route_sub.iloc.__getitem__(slice(max(0, sel_idx - 3), min(len(route_sub), sel_idx + 4)))
min_cal_price = cal_slice["0.5_price"].min()
cards_list = []
for _, c_row in cal_slice.iterrows():
    c_price = int(round(c_row["0.5_price"]))
    is_curr = c_row["date"].strftime("%Y-%m-%d") == selected_date_str
    tag_text = "Выбрано" if is_curr else ("Выгодно" if c_price == int(round(min_cal_price)) else "Тариф")
    tag_bg = "#2E7D32" if (is_curr or c_price == int(round(min_cal_price))) else "#577399"
    p_str = f"{c_price:,} ₽".replace(",", " ")
    cards_list.append(
        f"<div style='min-width:90px;'>"
        f"<div style='font-size:12px; color:#000000;'>{c_row['date'].strftime('%d.%m')}</div>"
        f"<div style='font-size:18px; font-weight:700; color:#000000; margin:2px 0;'>{p_str}</div>"
        f"<span style='background:{tag_bg}; color:#FFFFFF; font-size:11px; font-weight:600; padding:2px 8px; border-radius:10px;'>{tag_text}</span>"
        f"</div>"
    )

delta_bg = "#2E7D32" if profit_delta >= 0 else "#E53935"
prof_str = f"{expected_profit:,.0f} ₽".replace(",", " ")
delta_str = f"{profit_delta:+,.0f} ₽ к оптимуму".replace(",", " ")

html_card = (
    f"<div style='padding:8px 0 16px 0;'>"
    f"<div style='display:flex; flex-wrap:wrap; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:16px;'>"
    f"<div><div style='font-size:13px; color:#000000;'>Доступно мест</div><div style='font-size:24px; font-weight:700; color:#000000;'>{seats} шт.</div></div>"
    f"<div><div style='font-size:13px; color:#000000;'>Желают купить</div><div style='font-size:24px; font-weight:700; color:#000000;'>{demand_median_clean:.1f} чел.</div></div>"
    f"<div><div style='font-size:13px; color:#000000;'>Купят билетов</div><div style='font-size:24px; font-weight:700; color:#000000;'>{sold_tickets} шт.</div></div>"
    f"<div><div style='font-size:13px; color:#000000;'>Свободных мест</div><div style='font-size:24px; font-weight:700; color:#000000;'>{free_seats} шт.</div></div>"
    f"<div><div style='font-size:13px; color:#000000;'>Ожидаемая прибыль</div><div style='font-size:24px; font-weight:700; color:#000000;'>{prof_str}</div>"
    f"<span style='background:{delta_bg}; color:#FFFFFF; font-size:11px; font-weight:600; padding:2px 9px; border-radius:10px;'>{delta_str}</span></div>"
    f"</div>"
    f"<div style='font-size:13px; font-weight:700; color:#000000; margin-bottom:8px;'>Календарь оптимальных цен на соседние даты:</div>"
    f"<div style='display:flex; flex-wrap:wrap; gap:28px;'>{''.join(cards_list)}</div>"
    f"</div>"
)
st.markdown(html_card, unsafe_allow_html=True)

b_col1, b_col2, b_col3 = st.columns(3)
with b_col1:
    load_ratio = (sold_tickets / seats) if seats > 0 else 1.0
    st.write(f"**Загрузка салона:** {load_ratio * 100:.1f}%")
    st.progress(min(1.0, load_ratio))
with b_col2:
    if st.button("Имитировать продажу 20 мест", use_container_width=True, disabled=(seats < 20)):
        st.session_state["booked_extra"][flight_key] = extra_booked + 20
        st.rerun()
with b_col3:
    if st.button("Сбросить бронь рейса", use_container_width=True, disabled=(extra_booked == 0)):
        st.session_state["booked_extra"][flight_key] = 0
        st.rerun()

scenario_labels = ["0.1 Пессим.", "0.2", "0.3", "0.4", "0.5 Медиана", "0.6", "0.7", "0.8", "0.9 Оптим."]
q_demands, bar_colors = [], []
for q in QUANTILES:
    want_q = max(0.0, float(flight_row[f"{q}_base_demand"]) - sens * (ticket_price - p_base))
    q_demands.append(round(want_q, 1))
    bar_colors.append("#ff7f0e" if q == "0.5" else ("#d62728" if want_q > seats else "#1f77b4"))

fig_q = go.Figure()
fig_q.add_trace(go.Bar(x=scenario_labels, y=q_demands, marker_color=bar_colors, text=[f"{v:.0f}" for v in q_demands], textposition="outside", name="Желают купить"))
fig_q.add_hline(y=seats, line_dash="dash", line_color="#d62728", line_width=3, annotation_text=f"Лимит кресел: {seats} шт.")
fig_q.update_layout(title=f"Распределение спроса по 9 квантилям: {selected_origin} → {selected_dest}", xaxis_title="Сценарий прогноза", yaxis_title="Пассажиры, чел.", height=400)
st.plotly_chart(fig_q, use_container_width=True)

# === БЛОК 2: АНАЛИТИКА ЗА ПЕРИОД ===
st.subheader("2. Динамика спроса и оптимальных цен за период", anchor="analytics-section")
f_col1, f_col2, f_col3 = st.columns(3)
with f_col1:
    sel_route_m = st.selectbox("Маршрут", ["Все маршруты"] + sorted(df["route_label"].unique().tolist()), index=1, key="m_route")
with f_col2:
    min_d, max_d = df["date"].min().date(), df["date"].max().date()
    d_range = st.date_input("Период", value=(min_d, max_d), min_value=min_d, max_value=max_d, format="DD.MM.YYYY", key="m_dates")
with f_col3:
    q_val = st.selectbox("Квантиль сценария", options=QUANTILES, index=4, key="m_quant")

s_d, e_d = (pd.to_datetime(d_range.__getitem__(0)), pd.to_datetime(d_range.__getitem__(1))) if isinstance(d_range, tuple) and len(d_range) == 2 else (pd.to_datetime(min_d), pd.to_datetime(max_d))
filt = df[(df["date"] >= s_d) & (df["date"] <= e_d)].copy()
if sel_route_m != "Все маршруты":
    filt = filt[filt["route_label"] == sel_route_m].copy()

dem_c, price_c = f"{q_val}_price_demand", f"{q_val}_price"
daily = filt.groupby("date", as_index=False).agg({"0.1_price_demand": "sum", "0.9_price_demand": "sum", dem_c: "sum", "seats": "sum", price_c: "mean"}) if sel_route_m == "Все маршруты" else filt.sort_values("date").copy()

c_left, c_right = st.columns(2)
with c_left:
    fig_d = go.Figure()
    fig_d.add_trace(go.Scatter(x=daily["date"], y=daily["0.9_price_demand"], mode="lines", line=dict(width=0), showlegend=False))
    fig_d.add_trace(go.Scatter(x=daily["date"], y=daily["0.1_price_demand"], mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(31,119,180,0.18)", name="Коридор 0.1–0.9"))
    fig_d.add_trace(go.Scatter(x=daily["date"], y=daily[dem_c], mode="lines+markers", name="Продажи по рек. цене", line=dict(color="#1f77b4", width=3)))
    fig_d.add_trace(go.Scatter(x=daily["date"], y=daily["seats"], mode="lines", name="Вместимость кресел", line=dict(color="#d62728", dash="dash")))
    fig_d.update_layout(title="Пассажиропоток и коридор квантилей", height=380, hovermode="x unified")
    st.plotly_chart(fig_d, use_container_width=True)

with c_right:
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=daily["date"], y=daily[price_c], mode="lines+markers", name="Оптимальная цена", line=dict(color="#2ca02c", width=3)))
    fig_p.update_layout(title="Динамика оптимальной цены билета, ₽", height=380, hovermode="x unified")
    st.plotly_chart(fig_p, use_container_width=True)

# === БЛОК 3: СРАВНЕНИЕ МАРШРУТОВ ===
st.subheader("3. Сводная статистика по всем направлениям сети", anchor="routes-section")
f_type_filter = st.multiselect("Тип рейсов", sorted(df["flight_type"].unique().tolist()), default=sorted(df["flight_type"].unique().tolist()))
df_sum = df[df["flight_type"].isin(f_type_filter)].copy()
df_sum["profit_05"] = (df_sum["0.5_price"] - df_sum["cost_per_passenger"]) * df_sum["0.5_price_demand"]
by_r = df_sum.groupby(["route", "name", "flight_type"], as_index=False).agg(total_seats=("seats", "sum"), sold=("0.5_price_demand", "sum"), avg_p=("0.5_price", "mean"), profit=("profit_05", "sum"))
by_r = df_sum.groupby(["route", "name", "flight_type"], as_index=False).agg(total_seats=("seats", "sum"), sold=("0.5_price_demand", "sum"), avg_p=("0.5_price", "mean"), profit=("profit_05", "sum"))
by_r["load_pct"] = (by_r["sold"] / by_r["total_seats"] * 100).round(1)
by_r["avg_p"] = by_r["avg_p"].round(0).astype(int)
by_r["profit_mln"] = (by_r["profit"] / 1_000_000).round(2)

view_r = by_r[["route", "name", "flight_type", "total_seats", "sold", "avg_p", "load_pct", "profit_mln"]].copy()
view_r.columns = ["Код", "Направление", "Тип перелёта", "Кресел", "Продано", "Средняя цена, ₽", "Загрузка, %", "Прибыль, млн ₽"]
st.dataframe(view_r, use_container_width=True, hide_index=True)
st.download_button("Скачать статистику в CSV", data=view_r.to_csv(index=False).encode("utf-8-sig"), file_name="network_summary.csv", mime="text/csv")

# === НИЖНИЙ СИНИЙ ФУТЕР С ИНФОРМАЦИЕЙ ОБ ИИ ===
st.markdown("""
<div id="ai-footer" class="sirena-footer">
    <div class="footer-grid">
        <div class="footer-col">
            <h4>О системе «AeroPulse»</h4>
            <p>Веб-платформа для автоматизации динамического ценообразования и управления загрузкой воздушных судов на прямых и транзитных маршрутах.</p>
            <p>Горизонт планирования: июнь – июль 2026 года (30 направлений между 6 городами сети).</p>
        </div>
        <div class="footer-col">
            <h4>Модели и инструменты ИИ</h4>
            <ul>
                <li><b>Google Gemini (LLM)</b> — проектирование архитектуры веб-сервиса, генерация интерфейса и проверка расчётных формул.</li>
                <li><b>AutoGluon TimeSeries (DirectTabular)</b> — квантильный прогноз спроса (0.1–0.9) с учётом праздничной сезонности.</li>
            </ul>
        </div>
        <div class="footer-col">
            <h4>Реализованный функционал</h4>
            <ul>
                <li>Расчёт спроса и продаж по медиане 0.5 с учётом эластичности и лимита мест.</li>
                <li>График распределения спроса по 9 квантилям и календарь тарифов на соседние даты.</li>
                <li>Регистрация и авторизация сотрудников с сохранением учётных записей.</li>
            </ul>
        </div>
        <div class="footer-col">
            <h4>Технологический стек</h4>
            <ul>
                <li>Backend и вычисления: Python, Pandas</li>
                <li>Интерфейс и графики: Streamlit, Plotly</li>
                <li>Среда разработки: PyCharm, Google Colab</li>
                <li>Хостинг: GitHub и Streamlit Cloud</li>
            </ul>
        </div>
    </div>
    <div class="footer-copy">
        2026 AeroPulse • Система управления доходами и тарифной сеткой. Все права защищены.
    </div>
</div>
""", unsafe_allow_html=True)

