import re
import gspread
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Market Signals Hub",
    page_icon="📊",
    layout="wide"
)

# ----------------------------------------------------------------------
# DATA LOADER (CLOUD SECRETS)
# ----------------------------------------------------------------------
def extract_tv_url(formula_str):
    if not isinstance(formula_str, str):
        return ""
    match = re.search(r'HYPERLINK\("([^"]+)"', formula_str, re.IGNORECASE)
    return match.group(1) if match else (formula_str if formula_str.startswith("http") else "")

@st.cache_data(ttl=30)
def load_sheet_data():
    try:
        # Authenticate using Streamlit Cloud Secrets
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
        
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
            "1W Avg Volume": "Avg_1W_Volume"
        }
        df.rename(columns=col_map, inplace=True)
        
        if "TradingView_URL" in df.columns:
            df["TradingView_URL"] = df["TradingView_URL"].apply(extract_tv_url)
        else:
            df["TradingView_URL"] = ""

        numeric_cols = ["LTP", "Stop_Loss", "Risk_Pct", "High_52W", "Dist_52WH", "R2", "RSI", "Turnover_Cr", "Market_Cap_Cr", "Today_Volume", "Avg_1W_Volume"]
        for c in numeric_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

        return df
    except Exception as e:
        st.error(f"Error connecting to Google Sheet: {e}")
        return pd.DataFrame()

df_raw = load_sheet_data()

# ----------------------------------------------------------------------
# HEADER & FILTERS
# ----------------------------------------------------------------------
st.title("📊 Market Signals Hub")

if df_raw.empty:
    st.warning("No data retrieved from sheet. Ensure sheet is shared with the service account.")
    st.stop()

st.sidebar.header("🔍 Signal Filters")
dates = sorted(df_raw["Date"].dropna().unique(), reverse=True)
selected_date = st.sidebar.selectbox("Session Date", options=dates, index=0)

df_day = df_raw[df_raw["Date"] == selected_date].copy()

strat_options = ["All"] + list(df_day["Strategy"].dropna().unique())
selected_strat = st.sidebar.selectbox("Strategy", options=strat_options)
if selected_strat != "All":
    df_day = df_day[df_day["Strategy"] == selected_strat]

max_risk = st.sidebar.slider("Max Risk (%)", min_value=0.5, max_value=6.0, value=5.5, step=0.1)
df_day = df_day[df_day["Risk_Pct"] <= max_risk]

# Summary Metrics
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
active_setups = df_day[df_day["Action"].str.contains("Ready", case=False, na=False)]
kpi1.metric("Active Ready Setups", len(active_setups))
kpi2.metric("Swing Low Signals", len(df_day[df_day["Strategy"] == "Swing Low"]))
kpi3.metric("AVWAP Bounces", len(df_day[df_day["Strategy"] == "AVWAP Bounce"]))
kpi4.metric("Liquidity Sweeps", len(df_day[df_day["Strategy"] == "Liquidity Sweep"]))

# ----------------------------------------------------------------------
# 3 TABS (Overview, Signals, Watchlist)
# ----------------------------------------------------------------------
tab_overview, tab_signals, tab_watchlist = st.tabs(["Overview", "Signals", "Watchlist"])

with tab_overview:
    st.subheader(f"Active Ready Setups ({len(active_setups)})")
    if active_setups.empty:
        st.info("No active setups meet this filter.")
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
                        c1.markdown(f"**LTP:** ₹{row['LTP']:,.2f}")
                        c1.markdown(f"**SL:** ₹{row['Stop_Loss']:,.2f}")
                        c1.markdown(f"**RSI:** {row['RSI']}")
                        c1.markdown(f"**Vol:** {int(row['Today_Volume']):,}")
                        
                        c2.markdown(f"**Risk:** {row['Risk_Pct']}%")
                        c2.markdown(f"**R²:** {row['R2']}")
                        c2.markdown(f"**MCap:** ₹{row['Market_Cap_Cr']:,.0f} Cr")
                        c2.markdown(f"**1W Vol:** {int(row['Avg_1W_Volume']):,}")
                        if row["TradingView_URL"]:
                            st.link_button("TradingView ↗", row["TradingView_URL"], use_container_width=True)

with tab_signals:
    st.subheader(f"All Signals ({len(df_day)})")
    display_df = df_day[[
        "Symbol", "Strategy", "Action", "LTP", "Stop_Loss", "Risk_Pct",
        "High_52W", "Dist_52WH", "R2", "RSI", "Turnover_Cr", 
        "Market_Cap_Cr", "Today_Volume", "Avg_1W_Volume", "TradingView_URL"
    ]].copy()

    st.dataframe(
        display_df,
        column_config={
            "TradingView_URL": st.column_config.LinkColumn("Chart", display_text="Open ↗"),
            "LTP": st.column_config.NumberColumn("LTP (₹)", format="₹%.2f"),
            "Stop_Loss": st.column_config.NumberColumn("Stop Loss", format="₹%.2f"),
            "Risk_Pct": st.column_config.NumberColumn("Risk (%)", format="%.2f%%"),
            "Dist_52WH": st.column_config.NumberColumn("Dist 52WH", format="%.2f%%"),
            "Market_Cap_Cr": st.column_config.NumberColumn("MCap (₹Cr)", format="₹%d Cr"),
            "Turnover_Cr": st.column_config.NumberColumn("Turnover", format="₹%.1f Cr"),
            "Today_Volume": st.column_config.NumberColumn("Today Vol", format="%d"),
            "Avg_1W_Volume": st.column_config.NumberColumn("1W Avg Vol", format="%d")
        },
        hide_index=True,
        use_container_width=True,
        height=600
    )

with tab_watchlist:
    st.subheader("Watchlist Filter")
    watchlist_df = df_day[df_day["Action"].isin(["Retested & Ready", "Ready (ORB)", "Confirm Reclaim"])]
    if watchlist_df.empty:
        st.info("Watchlist is currently empty.")
    else:
        for _, row in watchlist_df.iterrows():
            with st.container(border=True):
                r1, r2, r3 = st.columns([3, 4, 3])
                r1.markdown(f"**{row['Symbol']}**\n\n*{row['Strategy']}*")
                r2.markdown(f"**LTP:** ₹{row['LTP']:,.2f} | **Risk:** {row['Risk_Pct']}%\n\n**MCap:** ₹{row['Market_Cap_Cr']:,.0f} Cr")
                if row["TradingView_URL"]:
                    r3.link_button("TradingView ↗", row["TradingView_URL"], use_container_width=True)
