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
# 1. UI REAL ESTATE OPTIMIZATION (COMPACT CARDS, DIALOG & CSS)
# ----------------------------------------------------------------------
st.markdown("""
<style>
    /* Global Container Padding */
    .block-container {
        padding-top: 2.2rem !important;
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
    .card-symbol-link {
        font-size: 0.95rem;
        font-weight: 700;
        color: #1D4ED8 !important;
        text-decoration: none !important;
        cursor: pointer !important;
        pointer-events: auto !important;
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

    /* Integrated Inline Star Button within card */
    .card-star-btn div[data-testid="stButton"] > button {
        padding: 0px 4px !important;
        height: 24px !important;
        min-height: 24px !important;
        font-size: 0.95rem !important;
        line-height: 1 !important;
        background: transparent !important;
        border: none !important;
        color: #F59E0B !important;
        box-shadow: none !important;
    }
    .card-star-btn div[data-testid="stButton"] > button:hover {
        background-color: #FEF3C7 !important;
        border-radius: 4px !important;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# 2. STATE INITIALIZATION (WATCHLIST & PERSISTENT NOTES)
# ----------------------------------------------------------------------
if "watchlist_symbols" not in st.session_state:
    st.session_state.watchlist_symbols = set()

if "signal_notes" not in st.session_state:
    st.session_state.signal_notes = {}

# ----------------------------------------------------------------------
# 3. FORMATTING HELPERS (Indian Numbering, Date, Time & TV URL)
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
# 4. NOTE DIALOG POPUP
# ----------------------------------------------------------------------
@st.dialog("📝 Signal Trade Note")
def open_note_modal(symbol, strategy, ltp):
    current_entry = st.session_state.signal_notes.get(symbol, {})
    current_note = current_entry.get("note", "")

    st.markdown(f"**Stock:** `{symbol}` | **Strategy:** `{strategy}` | **LTP:** `₹{ltp}`")
    new_note = st.text_area("Observations / Levels / Trade Plan:", value=current_note, height=140, placeholder="e.g. Volume dry-up noticed, enter if 15m candle closes above trigger level...")

    c1, c2 = st.columns([3, 1])
    with c1:
        if st.button("💾 Save Note", use_container_width=True, type="primary"):
            if new_note.strip():
                st.session_state.signal_notes[symbol] = {
                    "note": new_note.strip(),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "strategy": strategy,
                    "ltp": ltp
                }
            else:
                st.session_state.signal_notes.pop(symbol, None)
            st.rerun()
    with c2:
        if current_note and st.button("🗑️ Delete", use_container_width=True):
            st.session_state.signal_notes.pop(symbol, None)
            st.rerun()

# ----------------------------------------------------------------------
# 5. DATA INGESTION ENGINE
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
# 6. SIDEBAR: DATE, WATCHLIST FILTER, STRATEGIES, RISK & DIST SLIDERS
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

# Watchlist Dedicated Filter Checkbox
st.sidebar.markdown("---")
only_watchlist = st.sidebar.checkbox(
    f"⭐ Show Watchlist Only ({len(st.session_state.watchlist_symbols)})", 
    value=False,
    help="Filters all views exclusively to stocks currently bookmarked"
)
if only_watchlist:
    df_day = df_day[df_day["Symbol"].isin(st.session_state.watchlist_symbols)]

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
# 7. TOP HEADER & COMPACT METRICS BAR
# ----------------------------------------------------------------------
st.markdown("<div class=\"dashboard-title\">📊 Market Signals Hub</div>", unsafe_allow_html=True)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
active_setups = df_day[df_day["Action"].str.contains("Ready", case=False, na=False)]
kpi1.metric("Active Ready Setups", len(active_setups))
kpi2.metric("Swing Low Signals", len(df_day[df_day["Strategy"] == "Swing Low"]))
kpi3.metric("AVWAP Bounces", len(df_day[df_day["Strategy"] == "AVWAP Bounce"]))
kpi4.metric("Liquidity Sweeps", len(df_day[df_day["Strategy"] == "Liquidity Sweep"]))

# ----------------------------------------------------------------------
# 8. 4-TAB WORKSPACE (Signals Table, Overview Cards, Watchlist, Notes)
# ----------------------------------------------------------------------
tab_signals, tab_overview, tab_watchlist, tab_notes = st.tabs([
    f"📋 Signals Table ({len(df_day)})", 
    f"📌 Overview Cards ({len(active_setups)})", 
    f"⭐ Watchlist ({len(st.session_state.watchlist_symbols)})",
    f"📝 Notes ({len(st.session_state.signal_notes)})"
])

# --- TAB 1: SIGNALS TABLE (WATCHLIST STAR AS LAST COLUMN) ---
with tab_signals:
    if df_day.empty:
        st.info(f"No signals match the filters for {selected_date_str}.")
    else:
        st.caption("💡 *Select '⭐ Watch' in the last column to bookmark a stock | Use the Note launcher below for trade plans.*")

        table_df = pd.DataFrame()
        table_df["Date"] = df_day["Date"].astype(str)
        table_df["Symbol"] = df_day["Symbol"].astype(str)
        table_df["Strategy"] = df_day["Strategy"].astype(str)
        table_df["Action"] = df_day["Action"].astype(str)
        table_df["Alert_Count"] = df_day["Alert_Count"].astype(int)
        table_df["Last_Seen"] = df_day["Last_Seen"].astype(str)

        # Formatted Indian numbers
        table_df["LTP"] = df_day["LTP"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        table_df["Stop_Loss"] = df_day["Stop_Loss"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        table_df["Risk_Pct"] = df_day["Risk_Pct"].apply(lambda v: f"{v:.2f}%")
        table_df["High_52W"] = df_day["High_52W"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        table_df["High_52W_Date"] = df_day["High_52W_Date"].astype(str)
        table_df["Dist_52WH"] = df_day["Dist_52WH"].apply(lambda v: f"{v:.2f}%")
        table_df["R2"] = df_day["R2"].apply(lambda v: f"{v:.2f}")
        table_df["RSI"] = df_day["RSI"].apply(lambda v: f"{v:.1f}")
        table_df["Turnover_Cr"] = df_day["Turnover_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 1)} Cr")
        table_df["Market_Cap_Cr"] = df_day["Market_Cap_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 0)} Cr")
        table_df["Today_Volume"] = df_day["Today_Volume"].apply(lambda v: format_indian_currency(v, 0))
        table_df["Avg_1W_Volume"] = df_day["Avg_1W_Volume"].apply(lambda v: format_indian_currency(v, 0))
        table_df["TradingView_URL"] = df_day["TradingView_URL"]
        table_df["Has_Note"] = df_day["Symbol"].apply(lambda s: "📝 Yes" if s in st.session_state.signal_notes else "-")
        
        # Star selector positioned as the very last column
        table_df["Watchlist"] = df_day["Symbol"].apply(lambda s: "⭐ Watch" if s in st.session_state.watchlist_symbols else "☆ Unwatched")

        edited_table = st.data_editor(
            table_df,
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
                "TradingView_URL": st.column_config.LinkColumn("Chart", display_text="Open ↗", alignment="center"),
                "Has_Note": st.column_config.TextColumn("Note", alignment="center"),
                "Watchlist": st.column_config.SelectboxColumn(
                    "Watchlist",
                    options=["☆ Unwatched", "⭐ Watch"],
                    default="☆ Unwatched",
                    required=True,
                    alignment="center"
                )
            },
            disabled=[c for c in table_df.columns if c != "Watchlist"],
            hide_index=True,
            use_container_width=True,
            height=600,
            key="signals_data_editor"
        )

        # Synchronize Star toggles from the selectbox column
        updated_stars = set(edited_table[edited_table["Watchlist"] == "⭐ Watch"]["Symbol"])
        unstarred_in_current_view = set(edited_table[edited_table["Watchlist"] == "☆ Unwatched"]["Symbol"])
        st.session_state.watchlist_symbols.update(updated_stars)
        st.session_state.watchlist_symbols.difference_update(unstarred_in_current_view)

        # Quick Note Launcher
        st.markdown("---")
        n_col1, n_col2 = st.columns([3, 1])
        with n_col1:
            sym_list = sorted(list(df_day["Symbol"].unique()))
            chosen_sym = st.selectbox("Select Symbol to Add / Edit Note:", options=sym_list, key="table_note_picker")
        with n_col2:
            st.write("")
            st.write("")
            if st.button("📝 Open Note Modal", use_container_width=True):
                row_match = df_day[df_day["Symbol"] == chosen_sym].iloc[0]
                open_note_modal(chosen_sym, row_match["Strategy"], format_indian_currency(row_match["LTP"], 2))

# --- TAB 2: OVERVIEW CARDS ---
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

                with grid[idx]:
                    badge_class = "badge-ready" if "Ready" in str(row['Action']) else "badge-wait"
                    tv_url = row["TradingView_URL"]

                    h_c1, h_c2, h_c3 = st.columns([6, 3, 2])
                    with h_c1:
                        st.markdown(
                            f"<a href=\"{tv_url}\" target=\"_blank\" class=\"card-symbol-link\">{sym} ↗</a> "
                            f"<span style=\"font-size:0.75rem; color:#64748B;\">({row['Strategy']}){has_note_tag}</span>",
                            unsafe_allow_html=True
                        )
                    with h_c2:
                        st.markdown(f"<span class=\"{badge_class}\">{row['Action']}</span>", unsafe_allow_html=True)
                    with h_c3:
                        st.markdown("<div class=\"card-star-btn\">", unsafe_allow_html=True)
                        if st.button(star_icon, key=f"star_inline_{sym}", help="Toggle Watchlist Bookmark"):
                            if is_starred:
                                st.session_state.watchlist_symbols.discard(sym)
                            else:
                                st.session_state.watchlist_symbols.add(sym)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    card_html = f"""
                    <div class="signal-card" style="margin-top:-6px;">
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
        watch_display["Stop_Loss"] = bookmarked_df["Stop_Loss"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        watch_display["Risk_Pct"] = bookmarked_df["Risk_Pct"].apply(lambda v: f"{v:.2f}%")
        watch_display["52W High"] = bookmarked_df["High_52W"].apply(lambda v: format_indian_currency(v, 2, "₹"))
        watch_display["52W High Date"] = bookmarked_df["High_52W_Date"].astype(str)
        watch_display["Dist 52WH"] = bookmarked_df["Dist_52WH"].apply(lambda v: f"{v:.2f}%")
        watch_display["Today Vol"] = bookmarked_df["Today_Volume"].apply(lambda v: format_indian_currency(v, 0))
        watch_display["Market Cap"] = bookmarked_df["Market_Cap_Cr"].apply(lambda v: f"₹{format_indian_currency(v, 0)} Cr")
        watch_display["Chart"] = bookmarked_df["TradingView_URL"]

        st.dataframe(
            watch_display,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", alignment="center"),
                "Action": st.column_config.TextColumn("Action", alignment="center"),
                "LTP": st.column_config.TextColumn("LTP", alignment="center"),
                "Stop_Loss": st.column_config.TextColumn("Stop Loss", alignment="center"),
                "Risk_Pct": st.column_config.TextColumn("Risk", alignment="center"),
                "52W High": st.column_config.TextColumn("52W High", alignment="center"),
                "52W High Date": st.column_config.TextColumn("52WH Date", alignment="center"),
                "Dist 52WH": st.column_config.TextColumn("Dist 52WH", alignment="center"),
                "Today Vol": st.column_config.TextColumn("Today Vol", alignment="center"),
                "Market Cap": st.column_config.TextColumn("Market Cap", alignment="center"),
                "Chart": st.column_config.LinkColumn("TradingView Chart", display_text="Open ↗", alignment="center")
            },
            hide_index=True,
            use_container_width=True,
            height=450
        )

# --- TAB 4: SIGNAL NOTES DIRECTORY ---
with tab_notes:
    st.markdown(f"### 📝 Saved Trade Notes ({len(st.session_state.signal_notes)})")

    if not st.session_state.signal_notes:
        st.info("No trade notes saved yet. Use the note launcher on the Signals Table to attach notes to setups.")
    else:
        note_rows = []
        for sym, data in st.session_state.signal_notes.items():
            tv_link = sanitize_tv_url(sym)
            note_rows.append({
                "Symbol": sym,
                "Strategy": data.get("strategy", "-"),
                "LTP (₹)": data.get("ltp", "-"),
                "Note": data.get("note", ""),
                "Last Updated": data.get("updated_at", "-"),
                "Chart": tv_link
            })

        notes_df = pd.DataFrame(note_rows)

        st.dataframe(
            notes_df,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", alignment="center"),
                "Strategy": st.column_config.TextColumn("Strategy", alignment="center"),
                "LTP (₹)": st.column_config.TextColumn("LTP", alignment="center"),
                "Note": st.column_config.TextColumn("Observation / Trade Plan", width="large"),
                "Last Updated": st.column_config.TextColumn("Updated At", alignment="center"),
                "Chart": st.column_config.LinkColumn("TradingView", display_text="Open ↗", alignment="center")
            },
            hide_index=True,
            use_container_width=True,
            height=450
        )

        if st.button("🗑️ Clear All Notes", key="btn_clear_all_notes"):
            st.session_state.signal_notes.clear()
            st.rerun()
