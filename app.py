import os
import re
from datetime import datetime, date
import gspread
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Market Signals Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------
# 1. UI REAL ESTATE OPTIMIZATION (CLEAN HEADER & ULTRA-COMPACT CARDS)
# ----------------------------------------------------------------------
st.markdown("""
<style>
    /* Global Container Padding */
    .block-container {
        padding-top: 2.8rem !important;
        padding-bottom: 0.5rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }

    /* Fixed Clean Header Title */
    .dashboard-title {
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: 0rem;
        margin-bottom: 0.3rem;
        color: #1E293B;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* Compact Top KPI Metrics */
    div[data-testid="stMetric"] {
        padding: 0px !important;
        margin-bottom: -0.4rem !important;
    }
    div[data-testid="stMetricLabel"] > div {
        font-size: 0.8rem !important;
        color: #64748B !important;
    }
    div[data-testid="stMetricValue"] > div {
        font-size: 1.35rem !important;
        font-weight: 700 !important;
        line-height: 1.1 !important;
    }

    /* Compact Tabs Bar */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px !important;
        margin-top: 0.3rem !important;
        margin-bottom: 0.3rem !important;
    }
    .stTabs [data-baseweb="tab"] {
        padding-top: 3px !important;
        padding-bottom: 5px !important;
        font-size: 0.92rem !important;
    }

    /* Ultra-Compact Single-Screen Signal Card */
    .signal-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 6px;
        padding: 8px 10px;
        margin-bottom: 8px;
        font-size: 0.80rem;
        line-height: 1.35;
        box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }
    .signal-card:hover {
        border-color: #94A3B8;
    }
    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid #F1F5F9;
        padding-bottom: 4px;
        margin-bottom: 5px;
    }
    .card-symbol {
        font-size: 0.96rem;
        font-weight: 700;
        color: #0F172A;
        text-decoration: none;
    }
    .card-symbol:hover {
        color: #2563EB;
        text-decoration: underline;
    }
    .badge-ready {
        background-color: #DCFCE7;
        color: #15803D;
        font-size: 0.68rem;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 4px;
    }
    .badge-wait {
        background-color: #FEF9C3;
        color: #A16207;
        font-size: 0.68rem;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 4px;
    }
    .card-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        column-gap: 8px;
        row-gap: 2px;
    }
    .card-label {
        color: #64748B;
        font-size: 0.74rem;
    }
    .card-val {
        font-weight: 600;
        color: #1E293B;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# 2. FORMATTING HELPERS (Indian Numbering, Date & Time Parsers)
# ----------------------------------------------------------------------
def format_indian_currency(val, decimals=2, prefix=""):
    """Formats numbers to Indian comma grouping (e.g. 12,087.00 or 1,075,937)."""
    if pd.isna(val) or val == "" or val is None:
        return "-"
    try:
        val = float(val)
    except (ValueError, TypeError):
        return str(val)

    is_negative = val < 0
    val = abs(val)

    if decimals > 0:
        formatted_dec = f"{val:.{decimals}f}"
        int_part, dec_part = formatted_dec.split(".")
        dec_part = "." + dec_part
    else:
        int_part = str(int(round(val)))
        dec_part = ""

    if len(int_part) <= 3:
        res = int_part
    else:
        last3 = int_part[-3:]
        remaining = int_part[:-3]
        chunks = []
        while len(remaining) > 2:
            chunks.append(remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            chunks.append(remaining)
        chunks.reverse()
        res = ",".join(chunks) + "," + last3

    sign = "-" if is_negative else ""
    return f"{sign}{prefix}{res}{dec_part}"

def clean_date_str(val):
    """Parses Google Sheets serial day counts or date strings into clean YYYY-MM-DD."""
    if pd.isna(val) or str(val).strip() == "":
        return datetime.now().strftime("%Y-%m-%d")
    val_str = str(val).strip()
    if val_str.isdigit():
        try:
            base_date = datetime(1899, 12, 30)
            return (base_date + pd.Timedelta(days=int(val_str))).strftime("%Y-%m-%d")
        except Exception:
            return val_str
    return val_str

def format_last_seen_time(val):
    """Converts Sheets day fractions (0.57407...) or raw strings to HH:MM format."""
    if pd.isna(val) or val == "" or val is None:
        return "-"
    val_str = str(val).strip()
    try:
        f_val = float(val_str)
        if 0.0 <= f_val < 1.0:
            total_seconds = int(round(f_val * 86400))
            hours = (total_seconds // 3600) % 24
            minutes = (total_seconds % 3600) // 60
            return f"{hours:02d}:{minutes:02d}"
    except ValueError:
        pass

    try:
        parts = val_str.split(":")
        if len(parts) >= 2:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    except Exception:
        pass

    return val_str

def extract_tv_url(formula_str):
    """Extracts raw link from '=HYPERLINK("url", "text")' formula."""
    if not isinstance(formula_str, str):
        return ""
    match = re.search(r'HYPERLINK\("([^"]+)"', formula_str, re.IGNORECASE)
    return match.group(1) if match else (formula_str if formula_str.startswith("http") else "")

# ----------------------------------------------------------------------
# 3. DATA INGESTION ENGINE
# ----------------------------------------------------------------------
@st.cache_data(ttl=15)
def load_sheet_data():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            gc = gspread.service_account_from_dict(creds_dict)
        else:
            gc = gspread.service_account(filename="service_account.json")

        sheet_name = st.secrets.get("GOOGLE_SHEET_NAME", "Market_Signals")
        sheet_id = st.secrets.get("GOOGLE_SHEET_ID", "")

        sh = gc.open_by_key(sheet_id).sheet1 if sheet_id else gc.open(sheet_name).sheet1
        records = sh.get_all_records(value_render_option="FORMULA")

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        col_map = {
            "TradingView Chart": "TradingView_URL",
            "LTP (₹)": "LTP",
            "Stop Loss (₹)": "Stop_Loss",
            "Risk (%)": "Risk_Pct",
            "52W High (₹)": "High_52W",
            "Dist 52WH (%)": "Dist_52WH",
            "R²": "R2",
            "Daily RSI": "RSI",
            "Turnover (₹Cr)": "Turnover_Cr",
            "Market Cap (₹Cr)": "Market_Cap_Cr",
            "Today's Volume": "Today_Volume",
            "1W Avg Volume": "Avg_1W_Volume",
            "Alert Count": "Alert_Count",
            "Last Seen": "Last_Seen",
            "52W High Date": "High_52W_Date"
        }
        df.rename(columns=col_map, inplace=True)

        if "Date" in df.columns:
            df["Date"] = df["Date"].apply(clean_date_str)
        else:
            df["Date"] = datetime.now().strftime("%Y-%m-%d")

        if "Last_Seen" in df.columns:
            df["Last_Seen"] = df["Last_Seen"].apply(format_last_seen_time)
        else:
            df["Last_Seen"] = "-"

        if "High_52W_Date" not in df.columns:
            df["High_52W_Date"] = "-"
        else:
            df["High_52W_Date"] = df["High_52W_Date"].astype(str).replace("", "-")

        if "TradingView_URL" in df.columns:
            df["TradingView_URL"] = df["TradingView_URL"].apply(extract_tv_url)
        else:
            df["TradingView_URL"] = ""

        numeric_cols = [
            "LTP", "Stop_Loss", "Risk_Pct", "High_52W", "Dist_52WH", 
            "R2", "RSI", "Turnover_Cr", "Market_Cap_Cr", "Today_Volume", 
            "Avg_1W_Volume", "Alert_Count"
        ]
        for c in numeric_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

        if "Alert_Count" not in df.columns:
            df["Alert_Count"] = 1

        return df
    except Exception as e:
        st.error(f"Error connecting to Google Sheet: {e}")
        return pd.DataFrame()

df_raw = load_sheet_data()

# ----------------------------------------------------------------------
# 4. SIDEBAR: DATE, FLAT STRATEGY CHECKBOXES, RISK & DIST 52WH SLIDERS
# ----------------------------------------------------------------------
st.sidebar.markdown("### 🔍 Signal Filters")

if df_raw.empty:
    st.warning("No data found in Google Sheet. Run your scan script first.")
    st.stop()

# Date selector
available_dates = []
for d_str in df_raw["Date"].dropna().unique():
    try:
        available_dates.append(datetime.strptime(str(d_str), "%Y-%m-%d").date())
    except ValueError:
        pass

default_date = max(available_dates) if available_dates else date.today()
min_date = min(available_dates) if available_dates else date(2020, 1, 1)
max_date = max(available_dates) if available_dates else date.today()

picked_date = st.sidebar.date_input(
    "Session Date",
    value=default_date,
    min_value=min_date,
    max_value=max_date,
    format="YYYY-MM-DD"
)

selected_date_str = picked_date.strftime("%Y-%m-%d")
df_day = df_raw[df_raw["Date"] == selected_date_str].copy()

# Flat Strategy Checkbox Multi-Selection
st.sidebar.markdown("**Strategy Selection**")
all_strats = sorted([str(s) for s in df_raw["Strategy"].dropna().unique()])

selected_strats = []
for strat in all_strats:
    if st.sidebar.checkbox(strat, value=True, key=f"chk_{strat}"):
        selected_strats.append(strat)

df_day = df_day[df_day["Strategy"].isin(selected_strats)]

# Slider 1: Risk Cutoff
max_risk = st.sidebar.slider("Max Risk (%)", min_value=0.5, max_value=6.0, value=5.5, step=0.1)
df_day = df_day[df_day["Risk_Pct"] <= max_risk]

# Slider 2: Dist 52WH Cutoff
dist_min = float(round(df_raw["Dist_52WH"].min() - 1, 0)) if not df_raw.empty else -30.0
dist_min = min(dist_min, -30.0)
min_dist_52wh = st.sidebar.slider(
    "Max Pullback from 52WH (%)", 
    min_value=dist_min, 
    max_value=0.0, 
    value=dist_min, 
    step=0.5,
    help="Filters out stocks that dropped more than this % from their 52-week high"
)
df_day = df_day[df_day["Dist_52WH"] >= min_dist_52wh]

# ----------------------------------------------------------------------
# 5. TOP HEADER & COMPACT METRICS BAR
# ----------------------------------------------------------------------
st.markdown("<div class=\"dashboard-title\">📊 Market Signals Hub</div>", unsafe_allow_html=True)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
active_setups = df_day[df_day["Action"].str.contains("Ready", case=False, na=False)]
kpi1.metric("Active Ready Setups", len(active_setups))
kpi2.metric("Swing Low Signals", len(df_day[df_day["Strategy"] == "Swing Low"]))
kpi3.metric("AVWAP Bounces", len(df_day[df_day["Strategy"] == "AVWAP Bounce"]))
kpi4.metric("Liquidity Sweeps", len(df_day[df_day["Strategy"] == "Liquidity Sweep"]))

# ----------------------------------------------------------------------
# 6. TABS (Signals Table, Overview Cards, Watchlist)
# ----------------------------------------------------------------------
tab_signals, tab_overview, tab_watchlist = st.tabs(["📋 Signals Table", "📌 Overview Cards", "⭐ Watchlist"])

# --- TAB 1: SIGNALS TABLE ---
with tab_signals:
    st.markdown(f"**All Signals ({len(df_day)})**")

    if df_day.empty:
        st.info(f"No signals match the filters for {selected_date_str}.")
    else:
        display_df = pd.DataFrame()
        display_df["Date"] = df_day["Date"].astype(str)
        display_df["Symbol"] = df_day["Symbol"].astype(str)
        display_df["Strategy"] = df_day["Strategy"].astype(str)
        display_df["Action"] = df_day["Action"].astype(str)
        display_df["Alert_Count"] = df_day["Alert_Count"].astype(int)
        display_df["Last_Seen"] = df_day["Last_Seen"].astype(str)

        # Formatted Indian numbers (xx,xx,xxx)
        display_df["LTP"] = df_day["LTP"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        display_df["Stop_Loss"] = df_day["Stop_Loss"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        display_df["Risk_Pct"] = df_day["Risk_Pct"].apply(lambda v: f"{v:.2f}%")
        display_df["High_52W"] = df_day["High_52W"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        display_df["High_52W_Date"] = df_day["High_52W_Date"].astype(str)
        display_df["Dist_52WH"] = df_day["Dist_52WH"].apply(lambda v: f"{v:.2f}%")
        display_df["R2"] = df_day["R2"].apply(lambda v: f"{v:.2f}")
        display_df["RSI"] = df_day["RSI"].apply(lambda v: f"{v:.1f}")
        display_df["Turnover_Cr"] = df_day["Turnover_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 1)} Cr")
        display_df["Market_Cap_Cr"] = df_day["Market_Cap_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 0)} Cr")
        display_df["Today_Volume"] = df_day["Today_Volume"].apply(lambda v: format_indian_currency(v, 0))
        display_df["Avg_1W_Volume"] = df_day["Avg_1W_Volume"].apply(lambda v: format_indian_currency(v, 0))
        display_df["TradingView_URL"] = df_day["TradingView_URL"]

        st.dataframe(
            display_df,
            column_config={
                "Date": st.column_config.TextColumn("Date", alignment="center"),
                "Symbol": st.column_config.TextColumn("Symbol", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", alignment="center"),
                "Action": st.column_config.TextColumn("Action", alignment="center"),
                "Alert_Count": st.column_config.NumberColumn("Alert Count", alignment="center"),
                "Last_Seen": st.column_config.TextColumn("Last Seen", alignment="center"),
                "LTP": st.column_config.TextColumn("LTP (₹)", alignment="center"),
                "Stop_Loss": st.column_config.TextColumn("Stop Loss", alignment="center"),
                "Risk_Pct": st.column_config.TextColumn("Risk (%)", alignment="center"),
                "High_52W": st.column_config.TextColumn("52W High", alignment="center"),
                "High_52W_Date": st.column_config.TextColumn("52W High Date", alignment="center"),
                "Dist_52WH": st.column_config.TextColumn("Dist 52WH", alignment="center"),
                "R2": st.column_config.TextColumn("R²", alignment="center"),
                "RSI": st.column_config.TextColumn("RSI", alignment="center"),
                "Turnover_Cr": st.column_config.TextColumn("Turnover", alignment="center"),
                "Market_Cap_Cr": st.column_config.TextColumn("MCap (₹Cr)", alignment="center"),
                "Today_Volume": st.column_config.TextColumn("Today Vol", alignment="center"),
                "Avg_1W_Volume": st.column_config.TextColumn("1W Avg Vol", alignment="center"),
                "TradingView_URL": st.column_config.LinkColumn("Chart", display_text="Open ↗", alignment="center")
            },
            hide_index=True,
            use_container_width=True,
            height=700
        )

# --- TAB 2: OVERVIEW (COMPACT 4-COLUMN CARDS WITH HYPERLINKED SYMBOLS) ---
with tab_overview:
    st.markdown(f"**Active Ready Setups ({len(active_setups)})**")
    if active_setups.empty:
        st.info(f"No active setups found matching filters for {selected_date_str}.")
    else:
        # 4-column compact grid fits more cards per screen
        cols_per_row = 4
        chunks = [active_setups.iloc[i:i + cols_per_row] for i in range(0, len(active_setups), cols_per_row)]
        for chunk in chunks:
            grid = st.columns(cols_per_row)
            for idx, (_, row) in enumerate(chunk.iterrows()):
                with grid[idx]:
                    badge_class = "badge-ready" if "Ready" in str(row['Action']) else "badge-wait"
                    
                    # Direct TradingView Hyperlink on the stock symbol
                    if row["TradingView_URL"]:
                        symbol_link = f"<a href=\"{row['TradingView_URL']}\" target=\"_blank\" class=\"card-symbol\">{row['Symbol']} ↗</a>"
                    else:
                        symbol_link = f"<span class=\"card-symbol\">{row['Symbol']}</span>"

                    card_html = f"""
                    <div class="signal-card">
                        <div class="card-header">
                            <div>{symbol_link} <span style="font-size:0.75rem; color:#64748B;">({row['Strategy']})</span></div>
                            <span class="{badge_class}">{row['Action']}</span>
                        </div>
                        <div class="card-grid">
                            <div><span class="card-label">LTP:</span> <span class="card-val">{format_indian_currency(row['LTP'], 2, '₹')}</span></div>
                            <div><span class="card-label">Risk:</span> <span class="card-val">{row['Risk_Pct']:.2f}%</span></div>
                            <div><span class="card-label">SL:</span> <span class="card-val">{format_indian_currency(row['Stop_Loss'], 2, '₹')}</span></div>
                            <div><span class="card-label">R² / RSI:</span> <span class="card-val">{row['R2']:.2f} | {row['RSI']:.0f}</span></div>
                            <div><span class="card-label">52WH:</span> <span class="card-val">{format_indian_currency(row['High_52W'], 1, '₹')}</span></div>
                            <div><span class="card-label">Dist:</span> <span class="card-val">{row['Dist_52WH']:.1f}%</span></div>
                            <div><span class="card-label">Vol:</span> <span class="card-val">{format_indian_currency(row['Today_Volume'], 0)}</span></div>
                            <div><span class="card-label">MCap:</span> <span class="card-val">{format_indian_currency(row['Market_Cap_Cr'], 0, '₹')} Cr</span></div>
                        </div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)

# --- TAB 3: WATCHLIST ---
with tab_watchlist:
    st.markdown("**Priority Trigger Watchlist**")
    watchlist_df = df_day[df_day["Action"].isin(["Retested & Ready", "Ready (ORB)", "Confirm Reclaim"])]
    if watchlist_df.empty:
        st.info(f"Watchlist is clear for {selected_date_str}.")
    else:
        for _, row in watchlist_df.iterrows():
            badge_class = "badge-ready" if "Ready" in str(row['Action']) else "badge-wait"
            symbol_link = f"<a href=\"{row['TradingView_URL']}\" target=\"_blank\" class=\"card-symbol\">{row['Symbol']} ↗</a>" if row["TradingView_URL"] else f"<span class=\"card-symbol\">{row['Symbol']}</span>"

            card_html = f"""
            <div class="signal-card" style="margin-bottom: 6px;">
                <div class="card-header">
                    <div>{symbol_link} <span style="color:#64748B;">({row['Strategy']})</span></div>
                    <span class="{badge_class}">{row['Action']}</span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.80rem;">
                    <span><b>LTP:</b> {format_indian_currency(row['LTP'], 2, '₹')} | <b>SL:</b> {format_indian_currency(row['Stop_Loss'], 2, '₹')} (<b>Risk:</b> {row['Risk_Pct']:.2f}%)</span>
                    <span><b>52WH:</b> {format_indian_currency(row['High_52W'], 1, '₹')} ({row['High_52W_Date']}) [<b>{row['Dist_52WH']:.1f}%</b>] | <b>MCap:</b> {format_indian_currency(row['Market_Cap_Cr'], 0, '₹')} Cr</span>
                </div>
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True)
