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
# 1. COMPACT UI & ZERO-HORIZONTAL-SCROLL CSS
# ----------------------------------------------------------------------
st.markdown("""
<style>
    /* Drastically reduce container padding */
    .block-container {
        padding-top: 1.8rem !important;
        padding-bottom: 0.5rem !important;
        padding-left: 1.0rem !important;
        padding-right: 1.0rem !important;
        max-width: 100% !important;
    }
    .dashboard-title {
        font-size: 1.5rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
        color: #1E293B;
    }
    div[data-testid="stMetric"] {
        padding: 0px !important;
        margin-bottom: -0.5rem !important;
    }
    div[data-testid="stMetricLabel"] > div {
        font-size: 0.78rem !important;
        color: #64748B !important;
    }
    div[data-testid="stMetricValue"] > div {
        font-size: 1.25rem !important;
        font-weight: 700 !important;
        line-height: 1.1 !important;
    }
    
    /* Prevent table horizontal scroll by squeezing font and padding */
    div[data-testid="stDataFrame"] div[role="grid"] {
        font-size: 0.78rem !important;
    }
    div[data-testid="stDataFrame"] div[role="columnheader"] {
        font-size: 0.76rem !important;
        font-weight: 700 !important;
        padding: 2px 4px !important;
    }

    /* Compact Cards */
    .card-symbol-link {
        font-size: 0.95rem;
        font-weight: 700;
        color: #1D4ED8 !important;
        text-decoration: none !important;
    }
    .card-symbol-link:hover {
        text-decoration: underline !important;
        color: #1E40AF !important;
    }
    .badge-ready {
        background-color: #DCFCE7;
        color: #15803D;
        font-size: 0.68rem;
        font-weight: 600;
        padding: 1px 5px;
        border-radius: 4px;
        display: inline-block;
    }
    .badge-wait {
        background-color: #FEF9C3;
        color: #A16207;
        font-size: 0.68rem;
        font-weight: 600;
        padding: 1px 5px;
        border-radius: 4px;
        display: inline-block;
    }
    .card-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        column-gap: 6px;
        row-gap: 2px;
        font-size: 0.78rem;
        margin-top: 4px;
    }
    .card-label {
        color: #64748B;
        font-size: 0.72rem;
    }
    .card-val {
        font-weight: 600;
        color: #1E293B;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# 2. STATE INITIALIZATION (WATCHLIST, NOTES & PERSISTENT PREFERENCES)
# ----------------------------------------------------------------------
if "watchlist_symbols" not in st.session_state:
    st.session_state.watchlist_symbols = set()

if "signal_notes" not in st.session_state:
    st.session_state.signal_notes = {}

# Read initial settings from URL Query Params or default session state
q_params = st.query_params

if "pref_date" not in st.session_state:
    st.session_state.pref_date = q_params.get("date", None)
if "pref_only_watchlist" not in st.session_state:
    st.session_state.pref_only_watchlist = q_params.get("wl", "0") == "1"
if "pref_strats" not in st.session_state:
    saved_strats = q_params.get("strats", None)
    st.session_state.pref_strats = saved_strats.split(",") if saved_strats else None
if "pref_actions" not in st.session_state:
    saved_acts = q_params.get("acts", None)
    st.session_state.pref_actions = saved_acts.split(",") if saved_acts else None
if "pref_risk" not in st.session_state:
    try:
        st.session_state.pref_risk = float(q_params.get("risk", 5.5))
    except ValueError:
        st.session_state.pref_risk = 5.5
if "pref_dist" not in st.session_state:
    try:
        st.session_state.pref_dist = float(q_params.get("dist", -30.0))
    except ValueError:
        st.session_state.pref_dist = -30.0

# ----------------------------------------------------------------------
# 3. FORMATTING HELPERS
# ----------------------------------------------------------------------
def format_indian_currency(val, decimals=2, prefix=""):
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
    if pd.isna(val) or str(val).strip() in ["", "-", "N/A", "None"]:
        return "-"
    val_str = str(val).strip()
    
    numeric_candidate = None
    if val_str.isdigit():
        numeric_candidate = int(val_str)
    else:
        try:
            f = float(val_str)
            if f.is_integer() and f > 20000:
                numeric_candidate = int(f)
        except ValueError:
            pass

    if numeric_candidate is not None:
        try:
            base_date = datetime(1899, 12, 30)
            return (base_date + pd.Timedelta(days=numeric_candidate)).strftime("%Y-%m-%d")
        except Exception:
            return val_str

    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return val_str

def format_last_seen_time(val):
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

def sanitize_tv_url(symbol, formula_str=""):
    if isinstance(formula_str, str) and formula_str.strip():
        match = re.search(r'HYPERLINK\("([^"]+)"', formula_str, re.IGNORECASE)
        if match:
            return match.group(1)
        if formula_str.startswith("http"):
            return formula_str

    clean_sym = str(symbol).replace(".NS", "").replace("&", "_").replace("-", "_").strip().upper()
    return f"https://www.tradingview.com/chart/qQrGXVOL/?symbol=NSE:{clean_sym}&interval=D"

# ----------------------------------------------------------------------
# 4. DATA INGESTION ENGINE
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

        if "High_52W_Date" in df.columns:
            df["High_52W_Date"] = df["High_52W_Date"].apply(clean_date_str)
        else:
            df["High_52W_Date"] = "-"

        raw_urls = df["TradingView_URL"] if "TradingView_URL" in df.columns else [""] * len(df)
        df["TradingView_URL"] = [sanitize_tv_url(s, u) for s, u in zip(df["Symbol"], raw_urls)]

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
# 5. SIDEBAR: PERSISTENT SETTINGS ACROSS SESSIONS
# ----------------------------------------------------------------------
st.sidebar.markdown("### 🔍 Signal Filters")

if df_raw.empty:
    st.warning("No data found in Google Sheet. Run your scan script first.")
    st.stop()

available_dates = []
for d_str in df_raw["Date"].dropna().unique():
    try:
        available_dates.append(datetime.strptime(str(d_str), "%Y-%m-%d").date())
    except ValueError:
        pass

latest_date = max(available_dates) if available_dates else date.today()
min_date = min(available_dates) if available_dates else date(2020, 1, 1)
max_date = max(available_dates) if available_dates else date.today()

# Restore saved date
target_init_date = latest_date
if st.session_state.pref_date:
    try:
        parsed_p_date = datetime.strptime(st.session_state.pref_date, "%Y-%m-%d").date()
        if min_date <= parsed_p_date <= max_date:
            target_init_date = parsed_p_date
    except ValueError:
        pass

picked_date = st.sidebar.date_input(
    "Session Date",
    value=target_init_date,
    min_value=min_date,
    max_value=max_date,
    format="YYYY-MM-DD"
)

selected_date_str = picked_date.strftime("%Y-%m-%d")
st.session_state.pref_date = selected_date_str
df_day = df_raw[df_raw["Date"] == selected_date_str].copy()

st.sidebar.markdown("---")
only_watchlist = st.sidebar.checkbox(
    f"⭐ Show Watchlist Only ({len(st.session_state.watchlist_symbols)})", 
    value=st.session_state.pref_only_watchlist,
    help="Filters all views down to starred candidates only"
)
st.session_state.pref_only_watchlist = only_watchlist
if only_watchlist:
    df_day = df_day[df_day["Symbol"].isin(st.session_state.watchlist_symbols)]

# Section 1: Strategy Selection
st.sidebar.markdown("**Strategy Selection**")
all_strats = sorted([str(s) for s in df_raw["Strategy"].dropna().unique()])

selected_strats = []
for strat in all_strats:
    default_chk = True if st.session_state.pref_strats is None else (strat in st.session_state.pref_strats)
    if st.sidebar.checkbox(strat, value=default_chk, key=f"chk_strat_{strat}"):
        selected_strats.append(strat)

st.session_state.pref_strats = selected_strats
df_day = df_day[df_day["Strategy"].isin(selected_strats)]

# Section 2: Action Selection
st.sidebar.markdown("**Action Selection**")
all_actions = sorted([str(a) for a in df_raw["Action"].dropna().unique() if str(a).strip()])

selected_actions = []
for action_name in all_actions:
    default_act_chk = True if st.session_state.pref_actions is None else (action_name in st.session_state.pref_actions)
    if st.sidebar.checkbox(action_name, value=default_act_chk, key=f"chk_act_{action_name}"):
        selected_actions.append(action_name)

st.session_state.pref_actions = selected_actions
df_day = df_day[df_day["Action"].isin(selected_actions)]

st.sidebar.markdown("---")

# Sliders with persistence
max_risk = st.sidebar.slider(
    "Max Risk (%)", 
    min_value=0.5, 
    max_value=6.0, 
    value=st.session_state.pref_risk, 
    step=0.1
)
st.session_state.pref_risk = max_risk
df_day = df_day[df_day["Risk_Pct"] <= max_risk]

dist_min = float(round(df_raw["Dist_52WH"].min() - 1, 0)) if not df_raw.empty else -30.0
dist_min = min(dist_min, -30.0)
min_dist_52wh = st.sidebar.slider(
    "Max Pullback from 52WH (%)", 
    min_value=dist_min, 
    max_value=0.0, 
    value=st.session_state.pref_dist, 
    step=0.5
)
st.session_state.pref_dist = min_dist_52wh
df_day = df_day[df_day["Dist_52WH"] >= min_dist_52wh]

# Sync state to URL Query Parameters so refresh retains settings
st.query_params["date"] = selected_date_str
st.query_params["wl"] = "1" if only_watchlist else "0"
st.query_params["strats"] = ",".join(selected_strats)
st.query_params["acts"] = ",".join(selected_actions)
st.query_params["risk"] = str(max_risk)
st.query_params["dist"] = str(min_dist_52wh)

# ----------------------------------------------------------------------
# 6. TOP HEADER & KPI METRICS
# ----------------------------------------------------------------------
st.markdown("<div class=\"dashboard-title\">📊 Market Signals Hub</div>", unsafe_allow_html=True)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
active_setups = df_day[df_day["Action"].str.contains("Ready", case=False, na=False)]
kpi1.metric("Active Ready Setups", len(active_setups))
kpi2.metric("Swing Low Signals", len(df_day[df_day["Strategy"] == "Swing Low"]))
kpi3.metric("AVWAP Bounces", len(df_day[df_day["Strategy"] == "AVWAP Bounce"]))
kpi4.metric("Liquidity Sweeps", len(df_day[df_day["Strategy"] == "Liquidity Sweep"]))

# ----------------------------------------------------------------------
# 7. WORKSPACE TABS (CONDENSED TO PREVENT HORIZONTAL SCROLL)
# ----------------------------------------------------------------------
tab_signals, tab_overview, tab_watchlist, tab_notes = st.tabs([
    f"📋 Signals ({len(df_day)})", 
    f"📌 Cards ({len(active_setups)})", 
    f"⭐ Watchlist ({len(st.session_state.watchlist_symbols)})",
    f"📝 Notes ({len(st.session_state.signal_notes)})"
])

# --- TAB 1: SIGNALS TABLE (AUTO-FIT, ZERO HORIZONTAL SCROLL) ---
with tab_signals:
    if df_day.empty:
        st.info(f"No signals match the filters for {selected_date_str}.")
    else:
        st.caption("💡 *Edit '📝 Note' to save observations. Check '⭐' to add to Watchlist.*")

        table_df = pd.DataFrame()
        table_df["Date"] = df_day["Date"].astype(str)
        table_df["Symbol"] = df_day["Symbol"].astype(str)
        table_df["Strategy"] = df_day["Strategy"].astype(str)
        table_df["Action"] = df_day["Action"].astype(str)
        table_df["Hits"] = df_day["Alert_Count"].astype(int)
        table_df["Time"] = df_day["Last_Seen"].astype(str)

        table_df["LTP"] = df_day["LTP"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        table_df["SL"] = df_day["Stop_Loss"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        table_df["Risk%"] = df_day["Risk_Pct"].apply(lambda v: f"{v:.1f}%")
        table_df["52WH"] = df_day["High_52W"].apply(lambda v: format_indian_currency(v, 1, "₹"))
        table_df["52W Date"] = df_day["High_52W_Date"].astype(str)
        table_df["Dist%"] = df_day["Dist_52WH"].apply(lambda v: f"{v:.1f}%")
        table_df["R²"] = df_day["R2"].apply(lambda v: f"{v:.2f}")
        table_df["RSI"] = df_day["RSI"].apply(lambda v: f"{v:.0f}")
        table_df["Turnover"] = df_day["Turnover_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 1)}Cr")
        table_df["MCap"] = df_day["Market_Cap_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 0)}Cr")
        table_df["Vol"] = df_day["Today_Volume"].apply(lambda v: format_indian_currency(v, 0))
        table_df["1W Vol"] = df_day["Avg_1W_Volume"].apply(lambda v: format_indian_currency(v, 0))
        table_df["Chart"] = df_day["TradingView_URL"]
        table_df["📝 Note"] = df_day["Symbol"].apply(lambda s: st.session_state.signal_notes.get(s, {}).get("note", ""))
        table_df["⭐"] = df_day["Symbol"].apply(lambda s: s in st.session_state.watchlist_symbols)

        edited_table = st.data_editor(
            table_df,
            column_config={
                "Date": st.column_config.TextColumn("Date", width="small", alignment="center"),
                "Symbol": st.column_config.TextColumn("Symbol", width="small", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", width="small", alignment="center"),
                "Action": st.column_config.TextColumn("Action", width="small", alignment="center"),
                "Hits": st.column_config.NumberColumn("Hits", width="small", alignment="center"),
                "Time": st.column_config.TextColumn("Time", width="small", alignment="center"),
                "LTP": st.column_config.TextColumn("LTP", width="small", alignment="center"),
                "SL": st.column_config.TextColumn("SL", width="small", alignment="center"),
                "Risk%": st.column_config.TextColumn("Risk%", width="small", alignment="center"),
                "52WH": st.column_config.TextColumn("52WH", width="small", alignment="center"),
                "52W Date": st.column_config.TextColumn("52W Date", width="small", alignment="center"),
                "Dist%": st.column_config.TextColumn("Dist%", width="small", alignment="center"),
                "R²": st.column_config.TextColumn("R²", width="small", alignment="center"),
                "RSI": st.column_config.TextColumn("RSI", width="small", alignment="center"),
                "Turnover": st.column_config.TextColumn("Turnover", width="small", alignment="center"),
                "MCap": st.column_config.TextColumn("MCap", width="small", alignment="center"),
                "Vol": st.column_config.TextColumn("Vol", width="small", alignment="center"),
                "1W Vol": st.column_config.TextColumn("1W Vol", width="small", alignment="center"),
                "Chart": st.column_config.LinkColumn("Chart", width="small", display_text="Open ↗", alignment="center"),
                "📝 Note": st.column_config.TextColumn("📝 Note", width="medium"),
                "⭐": st.column_config.CheckboxColumn("⭐", width="small", default=False)
            },
            disabled=[c for c in table_df.columns if c not in ["⭐", "📝 Note"]],
            hide_index=True,
            use_container_width=True,
            height=660,
            key="signals_data_editor"
        )

        for _, r in edited_table.iterrows():
            sym = r["Symbol"]
            is_st = bool(r["⭐"])
            user_note = str(r["📝 Note"]).strip()

            if is_st:
                st.session_state.watchlist_symbols.add(sym)
            else:
                st.session_state.watchlist_symbols.discard(sym)

            if user_note:
                st.session_state.signal_notes[sym] = {
                    "note": user_note,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "strategy": r["Strategy"],
                    "ltp": r["LTP"]
                }
            elif sym in st.session_state.signal_notes and not user_note:
                st.session_state.signal_notes.pop(sym, None)

# --- TAB 2: OVERVIEW CARDS (CLEAN 4-COLUMN RESPONSIVE GRID) ---
with tab_overview:
    if active_setups.empty:
        st.info(f"No active setups found matching filters for {selected_date_str}.")
    else:
        cols_per_row = 4
        chunks = [active_setups.iloc[i:i + cols_per_row] for i in range(0, len(active_setups), cols_per_row)]
        
        for chunk in chunks:
            grid = st.columns(cols_per_row)
            for idx, (_, row) in enumerate(chunk.iterrows()):
                sym = row['Symbol']
                is_starred = sym in st.session_state.watchlist_symbols
                star_icon = "⭐" if is_starred else "☆"
                has_note_tag = " 📝" if sym in st.session_state.signal_notes else ""
                badge_class = "badge-ready" if "Ready" in str(row['Action']) else "badge-wait"
                tv_url = row["TradingView_URL"]

                with grid[idx]:
                    with st.container(border=True):
                        top_left, top_right = st.columns([5, 1])
                        with top_left:
                            st.markdown(
                                f"<a href=\"{tv_url}\" target=\"_blank\" class=\"card-symbol-link\">{sym} ↗</a> "
                                f"<span style=\"font-size:0.75rem; color:#64748B;\">({row['Strategy']}){has_note_tag}</span><br>"
                                f"<span class=\"{badge_class}\">{row['Action']}</span>",
                                unsafe_allow_html=True
                            )
                        with top_right:
                            if st.button(star_icon, key=f"card_star_{sym}", help="Click to star/unstar"):
                                if is_starred:
                                    st.session_state.watchlist_symbols.discard(sym)
                                else:
                                    st.session_state.watchlist_symbols.add(sym)
                                st.rerun()

                        st.markdown(f"""
                        <div class="card-grid">
                            <div><span class="card-label">LTP:</span> <span class="card-val">{format_indian_currency(row['LTP'], 2, '₹')}</span></div>
                            <div><span class="card-label">Risk:</span> <span class="card-val">{row['Risk_Pct']:.2f}%</span></div>
                            <div><span class="card-label">SL:</span> <span class="card-val">{format_indian_currency(row['Stop_Loss'], 2, '₹')}</span></div>
                            <div><span class="card-label">R²/RSI:</span> <span class="card-val">{row['R2']:.2f}|{row['RSI']:.0f}</span></div>
                            <div><span class="card-label">52WH:</span> <span class="card-val">{format_indian_currency(row['High_52W'], 1, '₹')}</span></div>
                            <div><span class="card-label">Dist:</span> <span class="card-val">{row['Dist_52WH']:.1f}%</span></div>
                            <div><span class="card-label">Vol:</span> <span class="card-val">{format_indian_currency(row['Today_Volume'], 0)}</span></div>
                            <div><span class="card-label">MCap:</span> <span class="card-val">{format_indian_currency(row['Market_Cap_Cr'], 0, '₹')}Cr</span></div>
                        </div>
                        """, unsafe_allow_html=True)

# --- TAB 3: WATCHLIST (ZERO HORIZONTAL SCROLL) ---
with tab_watchlist:
    st.markdown(f"### ⭐ Shortlisted Watchlist ({len(st.session_state.watchlist_symbols)})")
    
    bookmarked_df = df_day[df_day["Symbol"].isin(st.session_state.watchlist_symbols)].copy()

    if not st.session_state.watchlist_symbols:
        st.info("Your watchlist is currently empty. Star candidates from the **Signals Table** or **Overview Cards** to populate this list.")
    elif bookmarked_df.empty:
        st.warning(f"None of your {len(st.session_state.watchlist_symbols)} starred stocks have signals recorded for {selected_date_str}.")
        st.caption(f"Starred symbols: {', '.join(sorted(st.session_state.watchlist_symbols))}")
    else:
        if st.button("🗑️ Clear All Starred", key="btn_clear_wl"):
            st.session_state.watchlist_symbols.clear()
            st.rerun()

        watch_display = pd.DataFrame()
        watch_display["Symbol"] = bookmarked_df["Symbol"].astype(str)
        watch_display["Strategy"] = bookmarked_df["Strategy"].astype(str)
        watch_display["Action"] = bookmarked_df["Action"].astype(str)
        watch_display["LTP"] = bookmarked_df["LTP"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        watch_display["SL"] = bookmarked_df["Stop_Loss"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        watch_display["Risk%"] = bookmarked_df["Risk_Pct"].apply(lambda v: f"{v:.2f}%")
        watch_display["52WH"] = bookmarked_df["High_52W"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        watch_display["52W Date"] = bookmarked_df["High_52W_Date"].astype(str)
        watch_display["Dist%"] = bookmarked_df["Dist_52WH"].apply(lambda v: f"{v:.2f}%")
        watch_display["Vol"] = bookmarked_df["Today_Volume"].apply(lambda v: format_indian_currency(v, 0))
        watch_display["MCap"] = bookmarked_df["Market_Cap_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 0)}Cr")
        watch_display["Chart"] = bookmarked_df["TradingView_URL"]

        st.dataframe(
            watch_display,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", width="small", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", width="small", alignment="center"),
                "Action": st.column_config.TextColumn("Action", width="small", alignment="center"),
                "LTP": st.column_config.TextColumn("LTP", width="small", alignment="center"),
                "SL": st.column_config.TextColumn("SL", width="small", alignment="center"),
                "Risk%": st.column_config.TextColumn("Risk%", width="small", alignment="center"),
                "52WH": st.column_config.TextColumn("52WH", width="small", alignment="center"),
                "52W Date": st.column_config.TextColumn("52W Date", width="small", alignment="center"),
                "Dist%": st.column_config.TextColumn("Dist%", width="small", alignment="center"),
                "Vol": st.column_config.TextColumn("Vol", width="small", alignment="center"),
                "MCap": st.column_config.TextColumn("MCap", width="small", alignment="center"),
                "Chart": st.column_config.LinkColumn("Chart", width="small", display_text="Open ↗", alignment="center")
            },
            hide_index=True,
            use_container_width=True,
            height=450
        )

# --- TAB 4: SIGNAL NOTES DIRECTORY (FIT SCREEN) ---
with tab_notes:
    st.markdown(f"### 📝 Saved Trade Notes ({len(st.session_state.signal_notes)})")

    if not st.session_state.signal_notes:
        st.info("No trade notes saved yet. Type directly in the '📝 Note' column of the Signals Table to record trade plans.")
    else:
        note_rows = []
        for sym, data in st.session_state.signal_notes.items():
            tv_link = sanitize_tv_url(sym)
            note_rows.append({
                "Symbol": sym,
                "Strategy": data.get("strategy", "-"),
                "LTP": data.get("ltp", "-"),
                "Note": data.get("note", ""),
                "Updated At": data.get("updated_at", "-"),
                "Chart": tv_link
            })

        notes_df = pd.DataFrame(note_rows)

        st.dataframe(
            notes_df,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", width="small", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", width="small", alignment="center"),
                "LTP": st.column_config.TextColumn("LTP", width="small", alignment="center"),
                "Note": st.column_config.TextColumn("Observation / Trade Plan", width="large"),
                "Updated At": st.column_config.TextColumn("Updated At", width="small", alignment="center"),
                "Chart": st.column_config.LinkColumn("TradingView", width="small", display_text="Open ↗", alignment="center")
            },
            hide_index=True,
            use_container_width=True,
            height=450
        )

        if st.button("🗑️ Clear All Notes", key="btn_clear_all_notes"):
            st.session_state.signal_notes.clear()
            st.rerun()
