import os
import re
from datetime import datetime, date
import gspread
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Market Signals Hub",
    page_icon="📊",
    layout="wide"
)

# ----------------------------------------------------------------------
# 1. FORMATTING HELPERS (Indian Numbering, Date & Time Parsers)
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
    
    # Try parsing numeric day fraction (e.g., 0.5740740740741)
    try:
        f_val = float(val_str)
        if 0.0 <= f_val < 1.0:
            total_seconds = int(round(f_val * 86400))
            hours = (total_seconds // 3600) % 24
            minutes = (total_seconds % 3600) // 60
            return f"{hours:02d}:{minutes:02d}"
    except ValueError:
        pass

    # Try parsing time strings like '13:46:40' or '13:46'
    try:
        parts = val_str.split(":")
        if len(parts) >= 2:
            h = int(parts[0])
            m = int(parts[1])
            return f"{h:02d}:{m:02d}"
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
# 2. DATA INGESTION (Streamlit Cloud Secrets)
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
            "Last Seen": "Last_Seen"
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
# 3. SIDEBAR: INTERACTIVE CALENDAR & FLAT STRATEGY CHECKBOXES
# ----------------------------------------------------------------------
st.title("📊 Market Signals Hub")

if df_raw.empty:
    st.warning("No data found in Google Sheet. Run your scan script first.")
    st.stop()

st.sidebar.header("🔍 Signal Filters")

# Convert available date strings to date objects
available_dates = []
for d_str in df_raw["Date"].dropna().unique():
    try:
        available_dates.append(datetime.strptime(str(d_str), "%Y-%m-%d").date())
    except ValueError:
        pass

if available_dates:
    default_date = max(available_dates)
    min_date = min(available_dates)
    max_date = max(available_dates)
else:
    default_date = date.today()
    min_date = date(2020, 1, 1)
    max_date = date.today()

# Popup calendar widget
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
st.sidebar.write("**Strategy Filter**")
all_strats = sorted([str(s) for s in df_raw["Strategy"].dropna().unique()])

selected_strats = []
for strat in all_strats:
    if st.sidebar.checkbox(strat, value=True, key=f"chk_{strat}"):
        selected_strats.append(strat)

df_day = df_day[df_day["Strategy"].isin(selected_strats)]

# Risk slider filter
max_risk = st.sidebar.slider("Max Risk (%)", min_value=0.5, max_value=6.0, value=5.5, step=0.1)
df_day = df_day[df_day["Risk_Pct"] <= max_risk]

# Top KPI Summary Cards
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
active_setups = df_day[df_day["Action"].str.contains("Ready", case=False, na=False)]
kpi1.metric("Active Ready Setups", len(active_setups))
kpi2.metric("Swing Low Signals", len(df_day[df_day["Strategy"] == "Swing Low"]))
kpi3.metric("AVWAP Bounces", len(df_day[df_day["Strategy"] == "AVWAP Bounce"]))
kpi4.metric("Liquidity Sweeps", len(df_day[df_day["Strategy"] == "Liquidity Sweep"]))

# ----------------------------------------------------------------------
# 4. TABS (Overview, Signals, Watchlist)
# ----------------------------------------------------------------------
tab_overview, tab_signals, tab_watchlist = st.tabs(["Overview", "Signals", "Watchlist"])

# --- TAB 1: OVERVIEW (CARD GRID) ---
with tab_overview:
    st.subheader(f"Active Ready Setups ({len(active_setups)})")
    if active_setups.empty:
        st.info(f"No active setups found matching filters for {selected_date_str}.")
    else:
        cols_per_row = 3
        chunks = [active_setups.iloc[i:i + cols_per_row] for i in range(0, len(active_setups), cols_per_row)]
        for chunk in chunks:
            grid = st.columns(cols_per_row)
            for idx, (_, row) in enumerate(chunk.iterrows()):
                with grid[idx]:
                    with st.container(border=True):
                        st.markdown(f"### {row['Symbol']}")
                        st.caption(f"{row['Strategy']} • :green[{row['Action']}]")
                        c1, c2 = st.columns(2)
                        c1.markdown(f"**LTP:** {format_indian_currency(row['LTP'], 2, '₹')}")
                        c1.markdown(f"**SL:** {format_indian_currency(row['Stop_Loss'], 2, '₹')}")
                        c1.markdown(f"**RSI:** {row['RSI']:.1f}")
                        c1.markdown(f"**Today Vol:** {format_indian_currency(row['Today_Volume'], 0)}")

                        c2.markdown(f"**Risk:** {row['Risk_Pct']:.2f}%")
                        c2.markdown(f"**R²:** {row['R2']:.2f}")
                        c2.markdown(f"**MCap:** {format_indian_currency(row['Market_Cap_Cr'], 0, '₹')} Cr")
                        c2.markdown(f"**1W Vol:** {format_indian_currency(row['Avg_1W_Volume'], 0)}")
                        if row["TradingView_URL"]:
                            st.link_button("TradingView ↗", row["TradingView_URL"], use_container_width=True)

# --- TAB 2: SIGNALS TABLE ---
with tab_signals:
    st.subheader(f"All Signals ({len(df_day)})")

    if df_day.empty:
        st.info(f"No signals recorded matching the criteria for {selected_date_str}.")
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
            height=650
        )

# --- TAB 3: WATCHLIST ---
with tab_watchlist:
    st.subheader("Priority Trigger Watchlist")
    watchlist_df = df_day[df_day["Action"].isin(["Retested & Ready", "Ready (ORB)", "Confirm Reclaim"])]
    if watchlist_df.empty:
        st.info(f"Watchlist is clear for {selected_date_str}.")
    else:
        for _, row in watchlist_df.iterrows():
            with st.container(border=True):
                r1, r2, r3, r4 = st.columns([2, 3, 3, 2])
                r1.markdown(f"**{row['Symbol']}**\n\n*{row['Strategy']}*")
                r2.markdown(f"**LTP:** {format_indian_currency(row['LTP'], 2, '₹')} | **Risk:** {row['Risk_Pct']:.2f}%\n\n**Action:** :green[{row['Action']}]")
                r3.markdown(f"**MCap:** {format_indian_currency(row['Market_Cap_Cr'], 0, '₹')} Cr\n\n**Vol:** {format_indian_currency(row['Today_Volume'], 0)}")
                if row["TradingView_URL"]:
                    r4.link_button("TradingView ↗", row["TradingView_URL"], use_container_width=True)
