import json
import os
import gspread
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Market Signals Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Tight layout CSS to minimize white space while keeping top header & title clear
st.markdown(
    """
    <style>
      .block-container {
        padding-top: 2.25rem !important;
        padding-bottom: 0.55rem !important;
        padding-left: 1.0rem !important;
        padding-right: 1.0rem !important;
        max-width: 100% !important;
      }
      div[data-testid="stVerticalBlock"] > div {
        gap: 0.35rem !important;
      }
      div[data-testid="stMetric"] {
        padding: 0.1rem 0.25rem !important;
      }
      div[data-testid="stMetricValue"] {
        font-size: 1.55rem !important;
        line-height: 1.15 !important;
      }
      div[data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
      }
      section[data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 0.65rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
      }
      section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
        gap: 0.2rem !important;
      }
      section[data-testid="stSidebar"] .stCheckbox {
        margin-bottom: -0.35rem !important;
      }
      h2 {
        margin-top: 0.15rem !important;
        margin-bottom: 0.15rem !important;
        padding-top: 0.2rem !important;
        font-size: 1.45rem !important;
      }
      h3 {
        margin-top: 0.25rem !important;
        margin-bottom: 0.1rem !important;
        font-size: 0.95rem !important;
      }
      .stTabs [data-baseweb="tab-list"] {
        gap: 0.6rem !important;
        margin-bottom: 0.1rem !important;
      }
      .stTabs [data-baseweb="tab"] {
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

CSV_FILE = "signals_log.csv"
WATCHLIST_FILE = "watchlist_store.json"
NOTES_FILE = "notes_store.json"
FILTER_PREFS_FILE = "filter_prefs_store.json"
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Market_Signals")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
TRADINGVIEW_LAYOUT_ID = os.getenv("TRADINGVIEW_LAYOUT_ID", "qQrGXVOL")

ALL_STRATEGIES = [
    "AVWAP Bounce",
    "EMA20 Pullback",
    "Liquidity Sweep",
    "Swing Low",
    "VCP Contraction",
    "VDU 50-DMA Coil",
    "VDU Absorption Coil",
    "VDU Pre-Breakout",
]

BASE_TABLE_COLUMNS = [
    "Date",
    "Symbol",
    "Strategy",
    "Action",
    "Hits",
    "Time",
    "LTP",
    "SL",
    "Risk_Pct",
    "Dist_Pct",
    "52W_Date",
    "R2",
    "RSI",
    "Turnover",
    "MCap",
    "Vol",
    "1W_Vol",
    "Chart",
]

LIVE_STREAM_HIDDEN_COLUMNS = {
    "MACD",
    "Signal",
    "Hist",
    "Prev_Hist",
    "Cross_Below_0",
    "Green_Candle",
    "MACD_Hook_Up",
    "Hist_Inflection",
    "Verdict_Confidence",
    "Entry",
    "Exit",
    "Risk",
    "Target",
    "Qty",
    "Trade_Risk_Rs",
    "Target_Gain_Rs",
    "TradingView_URL",
}


# -----------------------------------------------------------------------------
# 1. PERSISTENT WATCHLIST, NOTES & FILTER PREFERENCES HELPERS
# -----------------------------------------------------------------------------
def load_json_store(filepath: str, default):
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_store(filepath: str, payload) -> None:
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _parse_csv_param(raw_val: str | None) -> set[str]:
    if not raw_val:
        return set()
    return {item.strip() for item in str(raw_val).split("||") if item.strip()}


def _encode_csv_param(items: list[str]) -> str:
    return "||".join(sorted(set(items)))


def load_unchecked_prefs(param_key: str, json_key: str) -> set[str]:
    if param_key in st.query_params:
        return _parse_csv_param(st.query_params.get(param_key))
    disk_prefs = load_json_store(FILTER_PREFS_FILE, {})
    return set(disk_prefs.get(json_key, []))


def persist_checkbox_group(
    group_kind: str, all_options: list[str], widget_prefix: str
) -> list[str]:
    unchecked: list[str] = []
    selected: list[str] = []
    for opt in all_options:
        is_checked = bool(st.session_state.get(f"{widget_prefix}{opt}", True))
        if is_checked:
            selected.append(opt)
        else:
            unchecked.append(opt)

    if group_kind == "strat":
        param_key, json_key = "off_strats", "unchecked_strategies"
    else:
        param_key, json_key = "off_acts", "unchecked_actions"

    encoded = _encode_csv_param(unchecked)
    if encoded:
        st.query_params[param_key] = encoded
    elif param_key in st.query_params:
        del st.query_params[param_key]

    disk_prefs = load_json_store(FILTER_PREFS_FILE, {})
    disk_prefs[json_key] = sorted(set(unchecked))
    save_json_store(FILTER_PREFS_FILE, disk_prefs)
    return selected


# -----------------------------------------------------------------------------
# 2. DATA ACCESS & NORMALIZATION
# -----------------------------------------------------------------------------
def _clean_num(series: pd.Series, default: float = 0.0) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace("₹", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(default)


def _normalize_signals(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=BASE_TABLE_COLUMNS)

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    rename_map = {
        "Alert Count": "Hits",
        "Alerts": "Hits",
        "Last Seen": "Time",
        "LTP (₹)": "LTP",
        "Stop Loss (₹)": "SL",
        "Stop_Loss": "SL",
        "Risk (%)": "Risk_Pct",
        "52W High (₹)": "52WH",
        "52W High Date": "52W_Date",
        "52W_High_Date": "52W_Date",
        "Dist 52WH (%)": "Dist_Pct",
        "Dist_52WH_Pct": "Dist_Pct",
        "R²": "R2",
        "Smoothness": "R2",
        "Smoothness(R²)": "R2",
        "Daily RSI": "RSI",
        "Turnover (₹Cr)": "Turnover",
        "Turnover_Cr": "Turnover",
        "Market Cap (₹Cr)": "MCap",
        "Today's Volume": "Vol",
        "1W Avg Volume": "1W_Vol",
    }
    df = df.rename(
        columns={k: v for k, v in rename_map.items() if k in df.columns}
    )

    for col in BASE_TABLE_COLUMNS:
        if col not in df.columns:
            if col in {
                "Date",
                "Symbol",
                "Strategy",
                "Action",
                "Time",
                "52W_Date",
                "Chart",
            }:
                df[col] = ""
            else:
                df[col] = 0.0

    for txt_col in ["Date", "Symbol", "Strategy", "Action", "Time", "52W_Date"]:
        df[txt_col] = df[txt_col].astype(str).str.strip()

    df["Hits"] = _clean_num(df["Hits"], default=1).astype(int)
    df["Vol"] = _clean_num(df["Vol"], default=0).astype(int)
    df["1W_Vol"] = _clean_num(df["1W_Vol"], default=0).astype(int)

    for num_col in [
        "LTP",
        "SL",
        "Risk_Pct",
        "Dist_Pct",
        "R2",
        "RSI",
        "Turnover",
        "MCap",
    ]:
        df[num_col] = _clean_num(df[num_col], default=0.0)

    if "52WH" in df.columns:
        w52 = _clean_num(df["52WH"], default=0.0)
        missing_dist = (df["Dist_Pct"] == 0.0) & (w52 > 0.0) & (df["LTP"] > 0.0)
        df.loc[missing_dist, "Dist_Pct"] = (
            (df.loc[missing_dist, "LTP"] - w52[missing_dist]) / w52[missing_dist]
        ) * 100.0

    df["Dist_Pct"] = df["Dist_Pct"].abs()

    df = df[df["Symbol"] != ""].copy()
    df["Chart"] = df["Symbol"].apply(
        lambda sym: f"https://www.tradingview.com/chart/{TRADINGVIEW_LAYOUT_ID}/?symbol=NSE:{sym}&interval=D"
    )
    return df


def _get_gspread_client(sa_file: str):
    if os.path.exists(sa_file):
        return gspread.service_account(filename=sa_file)
    try:
        if "gcp_service_account" in st.secrets:
            return gspread.service_account_from_dict(
                dict(st.secrets["gcp_service_account"])
            )
        if "service_account" in st.secrets:
            return gspread.service_account_from_dict(
                dict(st.secrets["service_account"])
            )
    except Exception:
        pass
    return None


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_sheet_df(
    sheet_name: str, sheet_id: str, sa_file: str
) -> pd.DataFrame | None:
    gc = _get_gspread_client(sa_file)
    if gc is None:
        return None
    sh = (
        gc.open_by_key(sheet_id.strip())
        if sheet_id and sheet_id.strip()
        else gc.open(sheet_name)
    )
    records = sh.sheet1.get_all_records()
    if not records:
        return pd.DataFrame(columns=BASE_TABLE_COLUMNS)
    return _normalize_signals(pd.DataFrame(records))


def load_signals() -> pd.DataFrame:
    try:
        sheet_df = _fetch_sheet_df(
            GOOGLE_SHEET_NAME, GOOGLE_SHEET_ID, SERVICE_ACCOUNT_FILE
        )
        if sheet_df is not None:
            return sheet_df
    except Exception as exc:
        st.sidebar.warning(f"Sheet read fallback: {exc}")

    if os.path.exists(CSV_FILE):
        return _normalize_signals(pd.read_csv(CSV_FILE))
    return pd.DataFrame(columns=BASE_TABLE_COLUMNS)


# -----------------------------------------------------------------------------
# 2A. LIVE STREAM DATA ACCESS ("Signals" TAB IN GOOGLE SHEETS)
# -----------------------------------------------------------------------------
def _parse_indian_cr_value(val) -> float:
    s = str(val).replace("₹", "").replace(",", "").replace("Cr", "").strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        return 0.0
    mult = 1.0
    if s.endswith(("L", "l")):
        mult = 100000.0
        s = s[:-1].strip()
    try:
        return float(s) * mult
    except Exception:
        return 0.0


def _build_tv_link(sym: str, raw_url: str = "") -> str:
    clean_url = str(raw_url or "").strip()
    if clean_url.startswith(("http://", "https://")):
        if "symbol=" in clean_url:
            return clean_url
        sep = "&" if "?" in clean_url else "?"
        return f"{clean_url}{sep}symbol=NSE:{sym}"
    return f"https://www.tradingview.com/chart/{TRADINGVIEW_LAYOUT_ID}/?symbol=NSE:{sym}&interval=D"


def _extract_symbol_from_link(val: str) -> str:
    s = str(val or "").strip()
    if "symbol=" in s:
        tail = s.split("symbol=", 1)[1].split("&", 1)[0]
        for prefix in ["NSE%3A", "NSE:", "NSE%3a", "nse:"]:
            if tail.startswith(prefix):
                tail = tail[len(prefix) :]
        return tail.strip()
    return s


def _normalize_live_stream(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Drop completely empty rows
    df = df.dropna(how="all")
    if df.empty:
        return pd.DataFrame()

    # Standardize common date column aliases to "Date"
    if "Date" not in df.columns:
        for alt in ["Signal Date", "Alert Date", "Timestamp", "Time"]:
            if alt in df.columns:
                df["Date"] = (
                    df[alt].astype(str).str.strip().str.split().str[0]
                )
                break
    if "Date" not in df.columns:
        df["Date"] = ""
    else:
        df["Date"] = df["Date"].astype(str).str.strip()

    # Standardize common category column aliases to "Category"
    if "Category" not in df.columns:
        for alt in ["Strategy", "Setup", "Signal Type", "Type"]:
            if alt in df.columns:
                df["Category"] = df[alt].astype(str).str.strip()
                break
    if "Category" not in df.columns:
        df["Category"] = "Uncategorized"
    else:
        df["Category"] = (
            df["Category"]
            .astype(str)
            .str.strip()
            .replace("", "Uncategorized")
        )

    # Standardize Symbol column and turn Symbol itself into a clickable TradingView link
    sym_col = next(
        (c for c in ["Symbol", "Ticker", "Stock"] if c in df.columns), None
    )
    url_col = next(
        (
            c
            for c in ["TradingView_URL", "TradingView", "Chart_URL", "Chart", "Link"]
            if c in df.columns
        ),
        None,
    )
    if sym_col:
        df["Symbol"] = df[sym_col].astype(str).str.strip()
        if sym_col != "Symbol":
            df = df.drop(columns=[sym_col])
        df = df[df["Symbol"] != ""].copy()

        url_series = (
            df[url_col].astype(str).str.strip()
            if url_col
            else pd.Series([""] * len(df), index=df.index)
        )
        df["Symbol"] = [
            _build_tv_link(s, u) for s, u in zip(df["Symbol"], url_series)
        ]

    # Compute same-day hits and across-dates hits before deduplicating
    if "Symbol" in df.columns and not df.empty:
        raw_sym_key = df["Symbol"].apply(_extract_symbol_from_link)
        day_counts = (
            df.assign(_sym=raw_sym_key)
            .groupby(["Date", "Category", "_sym"])["_sym"]
            .transform("count")
        )
        total_counts = (
            df.assign(_sym=raw_sym_key)
            .groupby(["Category", "_sym"])["_sym"]
            .transform("count")
        )
        df["Day_Hits"] = day_counts.astype(int)
        df["Total_Hits"] = total_counts.astype(int)

        # Keep one unique row per (Date, Category, Symbol), preferring latest time/row
        time_cols = [
            c for c in ["Date", "Time", "Timestamp"] if c in df.columns
        ]
        if time_cols:
            df = df.sort_values(by=time_cols, ascending=[True] * len(time_cols))
        df = df.assign(_sym=raw_sym_key).drop_duplicates(
            subset=["Date", "Category", "_sym"], keep="last"
        ).drop(columns=["_sym"])

    # Drop unwanted columns requested for Live Stream view
    drop_cols = [
        c
        for c in df.columns
        if c in LIVE_STREAM_HIDDEN_COLUMNS
        or c.lower() in {x.lower() for x in LIVE_STREAM_HIDDEN_COLUMNS}
    ]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    # Convert numeric-looking columns cleanly for proper sorting & formatting
    skip_numeric = {
        "Date",
        "Time",
        "Timestamp",
        "Last Seen",
        "Symbol",
        "Ticker",
        "Stock",
        "Category",
        "Strategy",
        "Action",
        "Notes",
        "Remarks",
        "52W_Date",
        "52W High Date",
        "High_52W_Date",
        "Sector",
        "Industry",
    }
    for col in df.columns:
        if col in skip_numeric:
            continue
        if "mcap" in col.lower() or "market cap" in col.lower():
            df[col] = df[col].apply(_parse_indian_cr_value)
            continue
        cleaned = (
            df[col]
            .astype(str)
            .str.replace("₹", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace("Cr", "", regex=False)
            .str.replace("x", "", regex=False)
            .str.strip()
        )
        numeric_vals = pd.to_numeric(cleaned, errors="coerce")
        non_empty_count = (df[col].astype(str).str.strip() != "").sum()
        if non_empty_count > 0 and numeric_vals.notna().sum() >= 0.7 * non_empty_count:
            df[col] = numeric_vals.fillna(0.0)

    return df


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_live_stream_df(
    sheet_name: str, sheet_id: str, sa_file: str
) -> pd.DataFrame | None:
    gc = _get_gspread_client(sa_file)
    if gc is None:
        return None
    sh = (
        gc.open_by_key(sheet_id.strip())
        if sheet_id and sheet_id.strip()
        else gc.open(sheet_name)
    )
    ws = sh.worksheet("Signals")
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()
    return _normalize_live_stream(pd.DataFrame(records))


def load_live_stream() -> tuple[pd.DataFrame, str | None]:
    try:
        live_df = _fetch_live_stream_df(
            GOOGLE_SHEET_NAME, GOOGLE_SHEET_ID, SERVICE_ACCOUNT_FILE
        )
        if live_df is not None:
            return live_df, None
        return pd.DataFrame(), "Google Sheets credentials not configured."
    except Exception as exc:
        return pd.DataFrame(), f"Unable to read 'Signals' tab: {exc}"


# -----------------------------------------------------------------------------
# 2B. COLUMN COLOR CONVENTIONS (TURNOVER, MCAP, VOL, 1W VOL)
# -----------------------------------------------------------------------------
def style_signal_columns(df: pd.DataFrame):
    def _style_row(row):
        styles = pd.Series("", index=row.index)

        turnover_val = float(row.get("Turnover", 0.0))
        if "Turnover" in styles.index and turnover_val >= 400.0:
            styles["Turnover"] = "background-color: #e0f2fe; color: #0f172a;"

        mcap_val = float(row.get("MCap", 0.0))
        if "MCap" in styles.index:
            if mcap_val >= 100000.0:
                styles["MCap"] = "background-color: #e0e7ff; color: #1e293b;"
            else:
                styles["MCap"] = "background-color: #dcfce7; color: #15803d;"

        vol_val = float(row.get("Vol", 0.0))
        w1_vol_val = float(row.get("1W_Vol", 0.0))
        if "Vol" in styles.index and vol_val >= 5000000.0:
            styles["Vol"] = "background-color: #eff6ff; color: #0f172a;"
        if "1W_Vol" in styles.index and (
            w1_vol_val >= 5000000.0
            or (w1_vol_val > 0 and vol_val >= 1.5 * w1_vol_val)
        ):
            styles["1W_Vol"] = "background-color: #dbeafe; color: #0f172a;"

        return styles

    return df.style.apply(_style_row, axis=1)


# -----------------------------------------------------------------------------
# 3. INITIALIZE SESSION STATE FOR WATCHLIST, NOTES & NOTE EDITOR
# -----------------------------------------------------------------------------
if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_json_store(WATCHLIST_FILE, [])

if "notes" not in st.session_state:
    st.session_state.notes = load_json_store(NOTES_FILE, {})

if "editing_note_for" not in st.session_state:
    st.session_state.editing_note_for = None

df_all = load_signals()
df_live_all, live_stream_err = load_live_stream()

# -----------------------------------------------------------------------------
# 4. SIDEBAR: SIGNAL FILTERS (PERSISTED CHECKBOXES)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🔍 Signal Filters")

    if not df_all.empty:
        date_period = st.selectbox(
            "Date Period",
            options=["Latest Session", "All Dates", "Custom Date"],
            index=0,
        )
        unique_dates = sorted(
            df_all["Date"].replace("", pd.NA).dropna().unique(), reverse=True
        )
        active_date_label = unique_dates[0] if unique_dates else ""

        if date_period == "Latest Session" and unique_dates:
            selected_dates = [unique_dates[0]]
        elif date_period == "Custom Date" and unique_dates:
            chosen = st.selectbox("Select Date", options=unique_dates, index=0)
            selected_dates = [chosen]
            active_date_label = chosen
        else:
            selected_dates = list(unique_dates)
            active_date_label = "All Dates"

        st.caption(f"Active Date: {active_date_label}")

        watchlist_only = st.checkbox(
            f"Show Watchlist Only ({len(st.session_state.watchlist)})",
            value=False,
        )

        st.markdown("### Strategy Selection")
        available_strats = list(
            dict.fromkeys(ALL_STRATEGIES + sorted(df_all["Strategy"].unique()))
        )
        saved_unchecked_strats = load_unchecked_prefs(
            "off_strats", "unchecked_strategies"
        )
        for strat in available_strats:
            w_key = f"chk_strat_{strat}"
            if w_key not in st.session_state:
                st.session_state[w_key] = strat not in saved_unchecked_strats
            st.checkbox(
                strat,
                key=w_key,
                on_change=persist_checkbox_group,
                args=("strat", available_strats, "chk_strat_"),
            )
        selected_strategies = [
            s for s in available_strats if st.session_state.get(f"chk_strat_{s}", True)
        ]

        st.markdown("### Action Selection")
        available_actions = sorted(
            df_all["Action"].replace("", pd.NA).dropna().unique()
        )
        saved_unchecked_acts = load_unchecked_prefs("off_acts", "unchecked_actions")
        for act in available_actions:
            w_key = f"chk_act_{act}"
            if w_key not in st.session_state:
                st.session_state[w_key] = act not in saved_unchecked_acts
            st.checkbox(
                act,
                key=w_key,
                on_change=persist_checkbox_group,
                args=("act", available_actions, "chk_act_"),
            )
        selected_actions = [
            a for a in available_actions if st.session_state.get(f"chk_act_{a}", True)
        ]

        st.markdown("### Smoothness Filter")
        min_r2 = st.slider(
            "Min Smoothness (R²)",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
        )
    else:
        st.info("No signals available.")
        selected_dates = []
        watchlist_only = False
        selected_strategies = []
        selected_actions = []
        min_r2 = 0.0

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# -----------------------------------------------------------------------------
# 5. APPLY FILTERS
# -----------------------------------------------------------------------------
df_view = df_all.copy()

if not df_view.empty:
    if selected_dates:
        df_view = df_view[df_view["Date"].isin(selected_dates)]
    if selected_strategies:
        df_view = df_view[df_view["Strategy"].isin(selected_strategies)]
    else:
        df_view = df_view.iloc[0:0]
    if selected_actions:
        df_view = df_view[df_view["Action"].isin(selected_actions)]
    else:
        df_view = df_view.iloc[0:0]
    df_view = df_view[df_view["R2"] >= min_r2]
    if watchlist_only:
        df_view = df_view[df_view["Symbol"].isin(st.session_state.watchlist)]

# -----------------------------------------------------------------------------
# 6. HEADER, KPI CARDS & TABS
# -----------------------------------------------------------------------------
st.markdown("## 📊 Market Signals Hub")

ready_mask = df_view["Action"].str.contains("Ready", case=False, na=False)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Active Ready Setups", int(ready_mask.sum()))
k2.metric("Swing Low Signals", int((df_view["Strategy"] == "Swing Low").sum()))
k3.metric("AVWAP Bounces", int((df_view["Strategy"] == "AVWAP Bounce").sum()))
k4.metric(
    "Liquidity Sweeps", int((df_view["Strategy"] == "Liquidity Sweep").sum())
)

tab_signals, tab_live_stream, tab_cards, tab_watchlist, tab_notes = st.tabs([
    f"Signals ({len(df_view)})",
    f"Live Stream ({len(df_live_all)})",
    f"Cards ({len(df_view)})",
    f"Watchlist ({len(st.session_state.watchlist)})",
    f"Notes ({len(st.session_state.notes)})",
])

with tab_signals:
    if df_view.empty:
        st.info("No signals match the active sidebar filters.")
    else:
        table_df = (
            df_view[BASE_TABLE_COLUMNS]
            .sort_values(by=["Date", "Hits", "R2"], ascending=[False, False, False])
            .copy()
        )
        table_df["Watchlist"] = table_df["Symbol"].isin(st.session_state.watchlist)
        table_df["Note"] = table_df["Symbol"].apply(
            lambda s: bool(str(st.session_state.notes.get(s, "")).strip())
        )

        styled_table = style_signal_columns(table_df)
        disabled_cols = [
            c for c in table_df.columns if c not in {"Watchlist", "Note"}
        ]

        edited_df = st.data_editor(
            styled_table,
            column_config={
                "Chart": st.column_config.LinkColumn("Chart", display_text="Open ↗"),
                "Hits": st.column_config.NumberColumn("Hits", format="%d"),
                "LTP": st.column_config.NumberColumn("LTP", format="₹%.2f"),
                "SL": st.column_config.NumberColumn("SL", format="₹%.2f"),
                "Risk_Pct": st.column_config.NumberColumn("Risk%", format="%.1f%%"),
                "Dist_Pct": st.column_config.NumberColumn(
                    "Dist to 52WH%", format="%.1f%%"
                ),
                "52W_Date": st.column_config.TextColumn("52W Date"),
                "R2": st.column_config.NumberColumn("R²", format="%.2f"),
                "RSI": st.column_config.NumberColumn("RSI", format="%.0f"),
                "Turnover": st.column_config.NumberColumn(
                    "Turnover", format="₹%.1f Cr"
                ),
                "MCap": st.column_config.NumberColumn("MCap", format="₹%,.0f Cr"),
                "Vol": st.column_config.NumberColumn("Vol", format="%d"),
                "1W_Vol": st.column_config.NumberColumn("1W Vol", format="%d"),
                "Watchlist": st.column_config.CheckboxColumn("Watchlist"),
                "Note": st.column_config.CheckboxColumn("Note"),
            },
            disabled=disabled_cols,
            hide_index=True,
            use_container_width=True,
            height=600,
            key="signals_data_editor",
        )

        updated_watchlist = list(st.session_state.watchlist)
        watchlist_changed = False
        for _, erow in edited_df.iterrows():
            sym = erow["Symbol"]
            checked_wl = bool(erow["Watchlist"])
            if checked_wl and sym not in updated_watchlist:
                updated_watchlist.append(sym)
                watchlist_changed = True
            elif not checked_wl and sym in updated_watchlist:
                updated_watchlist.remove(sym)
                watchlist_changed = True

        if watchlist_changed:
            st.session_state.watchlist = updated_watchlist
            save_json_store(WATCHLIST_FILE, st.session_state.watchlist)
            st.rerun()

        view_syms = sorted(table_df["Symbol"].unique())
        default_sym = (
            st.session_state.editing_note_for
            if st.session_state.editing_note_for in view_syms
            else (view_syms[0] if view_syms else "")
        )

        st.markdown("Select Symbol to view / edit trade plan note:")
        c_combo, c_btn = st.columns([5, 1])
        with c_combo:
            chosen_note_sym = st.selectbox(
                "Select Symbol to view / edit trade plan note:",
                options=view_syms,
                index=view_syms.index(default_sym) if default_sym in view_syms else 0,
                label_visibility="collapsed",
                key="quick_note_symbol_select",
            )
        with c_btn:
            if st.button(
                "Edit Note", use_container_width=True, key="btn_edit_note_below"
            ):
                st.session_state.editing_note_for = chosen_note_sym

        if chosen_note_sym and (
            st.session_state.editing_note_for == chosen_note_sym
            or st.session_state.notes.get(chosen_note_sym)
        ):
            with st.container(border=True):
                st.markdown(f"**Trade Plan Note — {chosen_note_sym}**")
                current_trade_note = st.session_state.notes.get(chosen_note_sym, "")
                draft_note = st.text_area(
                    f"Trade plan note for {chosen_note_sym}",
                    value=current_trade_note,
                    height=95,
                    label_visibility="collapsed",
                    key=f"inline_note_area_{chosen_note_sym}",
                )
                b_save, b_clear, b_close = st.columns([1, 1, 1])
                if b_save.button(
                    "Save Trade Note",
                    type="primary",
                    key=f"save_inline_note_{chosen_note_sym}",
                ):
                    if draft_note.strip():
                        st.session_state.notes[chosen_note_sym] = draft_note.strip()
                    else:
                        st.session_state.notes.pop(chosen_note_sym, None)
                    save_json_store(NOTES_FILE, st.session_state.notes)
                    st.success(f"Saved note for {chosen_note_sym}.")
                    st.rerun()
                if b_clear.button(
                    "Clear Note", key=f"clear_inline_note_{chosen_note_sym}"
                ):
                    st.session_state.notes.pop(chosen_note_sym, None)
                    save_json_store(NOTES_FILE, st.session_state.notes)
                    st.session_state.editing_note_for = None
                    st.rerun()
                if b_close.button(
                    "Close Editor", key=f"close_inline_note_{chosen_note_sym}"
                ):
                    st.session_state.editing_note_for = None
                    st.rerun()

with tab_live_stream:
    if live_stream_err:
        st.warning(live_stream_err)
    elif df_live_all.empty:
        st.info("No records found in the 'Signals' worksheet.")
    else:
        live_dates = sorted(
            df_live_all["Date"].replace("", pd.NA).dropna().unique(), reverse=True
        )
        live_categories = sorted(
            df_live_all["Category"].replace("", pd.NA).dropna().unique()
        )

        f_col1, f_col2, f_col3 = st.columns([1.4, 2.6, 1.0])
        with f_col1:
            date_options = ["All Dates"] + live_dates
            selected_live_date = st.selectbox(
                "Filter by Date",
                options=date_options,
                index=0,
                key="live_stream_date_filter",
            )
        with f_col2:
            selected_live_categories = st.multiselect(
                "Filter by Category",
                options=live_categories,
                default=live_categories,
                key="live_stream_category_filter",
            )
        with f_col3:
            st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
            if st.button(
                "🔄 Sync Live Stream",
                use_container_width=True,
                key="btn_refresh_live_stream",
            ):
                st.cache_data.clear()
                st.rerun()

        df_live_view = df_live_all.copy()
        if selected_live_date != "All Dates":
            df_live_view = df_live_view[df_live_view["Date"] == selected_live_date]
        if selected_live_categories:
            df_live_view = df_live_view[
                df_live_view["Category"].isin(selected_live_categories)
            ]
        else:
            df_live_view = df_live_view.iloc[0:0]

        sort_cols = [
            c for c in ["Date", "Time", "Timestamp"] if c in df_live_view.columns
        ]
        if sort_cols and not df_live_view.empty:
            df_live_view = df_live_view.sort_values(
                by=sort_cols, ascending=[False] * len(sort_cols)
            )

        live_sym_names = (
            df_live_view["Symbol"].apply(_extract_symbol_from_link)
            if "Symbol" in df_live_view.columns and not df_live_view.empty
            else pd.Series([], dtype=str)
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Unique Alerts Shown", len(df_live_view))
        m2.metric(
            "Unique Symbols",
            int(live_sym_names.nunique()) if not live_sym_names.empty else 0,
        )
        m3.metric(
            "Categories Shown",
            int(df_live_view["Category"].nunique())
            if not df_live_view.empty
            else 0,
        )

        if df_live_view.empty:
            st.info(
                "No live stream signals match the selected Date and Category filters."
            )
        else:
            preferred_after_2nd = [
                "Category",
                "Day_Hits",
                "Total_Hits",
                "Time",
                "Timestamp",
                "Action",
                "LTP",
                "Close",
                "Price",
                "Pivot",
                "Breakout",
                "SL",
                "Stop Loss",
                "Risk_Pct",
                "Risk (%)",
                "Vol",
                "Volume",
                "Vol_Ratio",
                "Vol_Ratio_20D",
                "RVOL",
                "RSI",
                "RSI_14",
                "Turnover",
                "MCap",
                "Mcap",
            ]
            lead_cols = [c for c in ["Date", "Symbol"] if c in df_live_view.columns]
            mid_cols = [
                c
                for c in preferred_after_2nd
                if c in df_live_view.columns and c not in lead_cols
            ]
            tail_cols = [
                c
                for c in df_live_view.columns
                if c not in lead_cols and c not in mid_cols
            ]
            display_live_df = df_live_view[lead_cols + mid_cols + tail_cols].copy()

            display_live_df["Watchlist"] = live_sym_names.isin(
                st.session_state.watchlist
            ).values
            display_live_df["Note"] = live_sym_names.apply(
                lambda s: bool(str(st.session_state.notes.get(s, "")).strip())
            ).values

            live_disabled_cols = [
                c for c in display_live_df.columns if c not in {"Watchlist", "Note"}
            ]

            live_col_config = {
                "Symbol": st.column_config.LinkColumn(
                    "Symbol",
                    display_text=r".*symbol=(?:NSE%3A|NSE:)?([^&]+).*",
                ),
                "Day_Hits": st.column_config.NumberColumn("Day Hits", format="%d"),
                "Total_Hits": st.column_config.NumberColumn("Total Hits", format="%d"),
                "Watchlist": st.column_config.CheckboxColumn("Watchlist"),
                "Note": st.column_config.CheckboxColumn("Note"),
            }
            for col in display_live_df.columns:
                cl = col.lower()
                if col in {"Symbol", "Day_Hits", "Total_Hits", "Watchlist", "Note"}:
                    continue
                elif any(
                    k in cl
                    for k in ["ltp", "price", "close", "pivot", "breakout", "sl", "stop"]
                ):
                    if "pct" not in cl and "%" not in cl:
                        live_col_config[col] = st.column_config.NumberColumn(
                            col, format="₹%.2f"
                        )
                elif "turnover" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="₹%.1f Cr"
                    )
                elif "mcap" in cl or "market cap" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="₹%,.0f Cr"
                    )
                elif "pct" in cl or "%" in cl or "change" in cl or "dist" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="%.2f%%"
                    )
                elif "rvol" in cl or "ratio" in cl or "mult" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="%.2fx"
                    )
                elif "rsi" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="%.1f"
                    )
                elif "vol" in cl:
                    live_col_config[col] = st.column_config.NumberColumn(
                        col, format="%,.0f"
                    )

            def _style_live_stream(df_in: pd.DataFrame):
                def _row_styler(row):
                    styles = pd.Series("", index=row.index)
                    if "Category" in styles.index:
                        styles["Category"] = (
                            "background-color: #eef2ff; color: #1e3a8a; font-weight: 600;"
                        )
                    if "Action" in styles.index:
                        act_txt = str(row.get("Action", "")).lower()
                        if (
                            "buy" in act_txt
                            or "breakout" in act_txt
                            or "ready" in act_txt
                        ):
                            styles["Action"] = (
                                "background-color: #dcfce7; color: #15803d; font-weight: 600;"
                            )
                    return styles

                return df_in.style.apply(_row_styler, axis=1)

            edited_live_df = st.data_editor(
                _style_live_stream(display_live_df),
                column_config=live_col_config,
                disabled=live_disabled_cols,
                hide_index=True,
                use_container_width=True,
                height=580,
                key="live_stream_data_editor",
            )

            updated_live_wl = list(st.session_state.watchlist)
            live_wl_changed = False
            for _, erow in edited_live_df.iterrows():
                sym = _extract_symbol_from_link(erow.get("Symbol", ""))
                if not sym:
                    continue
                checked_wl = bool(erow.get("Watchlist", False))
                if checked_wl and sym not in updated_live_wl:
                    updated_live_wl.append(sym)
                    live_wl_changed = True
                elif not checked_wl and sym in updated_live_wl:
                    updated_live_wl.remove(sym)
                    live_wl_changed = True

            if live_wl_changed:
                st.session_state.watchlist = updated_live_wl
                save_json_store(WATCHLIST_FILE, st.session_state.watchlist)
                st.rerun()

            live_view_syms = sorted(
                {s for s in live_sym_names.tolist() if s}
            )
            default_live_sym = (
                st.session_state.editing_note_for
                if st.session_state.editing_note_for in live_view_syms
                else (live_view_syms[0] if live_view_syms else "")
            )

            st.markdown("Select Symbol to view / edit trade plan note:")
            lc_combo, lc_btn = st.columns([5, 1])
            with lc_combo:
                chosen_live_note_sym = st.selectbox(
                    "Select Symbol to view / edit trade plan note:",
                    options=live_view_syms,
                    index=(
                        live_view_syms.index(default_live_sym)
                        if default_live_sym in live_view_syms
                        else 0
                    ),
                    label_visibility="collapsed",
                    key="live_quick_note_symbol_select",
                )
            with lc_btn:
                if st.button(
                    "Edit Note",
                    use_container_width=True,
                    key="btn_live_edit_note_below",
                ):
                    st.session_state.editing_note_for = chosen_live_note_sym

            if chosen_live_note_sym and (
                st.session_state.editing_note_for == chosen_live_note_sym
                or st.session_state.notes.get(chosen_live_note_sym)
            ):
                with st.container(border=True):
                    st.markdown(f"**Trade Plan Note — {chosen_live_note_sym}**")
                    current_live_note = st.session_state.notes.get(
                        chosen_live_note_sym, ""
                    )
                    draft_live_note = st.text_area(
                        f"Trade plan note for {chosen_live_note_sym}",
                        value=current_live_note,
                        height=95,
                        label_visibility="collapsed",
                        key=f"live_inline_note_area_{chosen_live_note_sym}",
                    )
                    lb_save, lb_clear, lb_close = st.columns([1, 1, 1])
                    if lb_save.button(
                        "Save Trade Note",
                        type="primary",
                        key=f"save_live_inline_note_{chosen_live_note_sym}",
                    ):
                        if draft_live_note.strip():
                            st.session_state.notes[chosen_live_note_sym] = (
                                draft_live_note.strip()
                            )
                        else:
                            st.session_state.notes.pop(chosen_live_note_sym, None)
                        save_json_store(NOTES_FILE, st.session_state.notes)
                        st.success(f"Saved note for {chosen_live_note_sym}.")
                        st.rerun()
                    if lb_clear.button(
                        "Clear Note",
                        key=f"clear_live_inline_note_{chosen_live_note_sym}",
                    ):
                        st.session_state.notes.pop(chosen_live_note_sym, None)
                        save_json_store(NOTES_FILE, st.session_state.notes)
                        st.session_state.editing_note_for = None
                        st.rerun()
                    if lb_close.button(
                        "Close Editor",
                        key=f"close_live_inline_note_{chosen_live_note_sym}",
                    ):
                        st.session_state.editing_note_for = None
                        st.rerun()

with tab_cards:
    if df_view.empty:
        st.info("No cards to display for current filter selection.")
    else:
        for _, row in df_view.iterrows():
            sym = row["Symbol"]
            is_watched = sym in st.session_state.watchlist
            with st.container(border=True):
                c_left, c_right = st.columns([4, 1])
                with c_left:
                    st.markdown(
                        f"**{sym}** — `{row['Strategy']}` | **{row['Action']}** | "
                        f"LTP: **₹{row['LTP']:,.2f}** | SL: **₹{row['SL']:,.2f}** ({row['Risk_Pct']:.1f}%) | "
                        f"R²: **{row['R2']:.2f}** | RSI: **{row['RSI']:.0f}**"
                    )
                    st.caption(
                        f"Date: {row['Date']} ({row['Time']}) | Dist to 52WH: {row['Dist_Pct']:.1f}% ({row['52W_Date']}) | "
                        f"Turnover: ₹{row['Turnover']:.1f} Cr | Vol: {row['Vol']:,} vs 1W Avg: {row['1W_Vol']:,} | [TradingView Chart]({row['Chart']})"
                    )
                with c_right:
                    btn_label = "★ Watching" if is_watched else "☆ Shortlist"
                    if st.button(
                        btn_label,
                        key=f"card_wl_{sym}_{row['Strategy']}",
                        use_container_width=True,
                    ):
                        if is_watched:
                            st.session_state.watchlist.remove(sym)
                        else:
                            st.session_state.watchlist.append(sym)
                        save_json_store(WATCHLIST_FILE, st.session_state.watchlist)
                        st.rerun()

with tab_watchlist:
    st.markdown("### ⭐ Shortlisted Watchlist")
    all_syms = sorted(df_all["Symbol"].unique()) if not df_all.empty else []
    add_sym = st.selectbox(
        "Add Symbol to Watchlist", options=[""] + all_syms, index=0
    )
    if st.button("Add to Watchlist") and add_sym:
        if add_sym not in st.session_state.watchlist:
            st.session_state.watchlist.append(add_sym)
            save_json_store(WATCHLIST_FILE, st.session_state.watchlist)
            st.rerun()

    if not st.session_state.watchlist:
        st.info(
            "Your watchlist is empty. Shortlist symbols from Cards, check Watchlist in Signals, or add one above."
        )
    else:
        wl_df = (
            df_all[df_all["Symbol"].isin(st.session_state.watchlist)]
            if not df_all.empty
            else pd.DataFrame()
        )
        if not wl_df.empty:
            st.dataframe(
                style_signal_columns(wl_df[BASE_TABLE_COLUMNS]),
                hide_index=True,
                use_container_width=True,
            )
        for sym in list(st.session_state.watchlist):
            col_s, col_rm = st.columns([5, 1])
            col_s.write(f"**{sym}**")
            if col_rm.button("Remove", key=f"rm_wl_{sym}"):
                st.session_state.watchlist.remove(sym)
                save_json_store(WATCHLIST_FILE, st.session_state.watchlist)
                st.rerun()

with tab_notes:
    st.markdown("### 📝 Trade & Setup Notes")
    note_syms = sorted(
        list(
            dict.fromkeys(
                list(st.session_state.notes.keys())
                + (sorted(df_all["Symbol"].unique()) if not df_all.empty else [])
            )
        )
    )
    selected_note_sym = st.selectbox(
        "Select Symbol for Note",
        options=note_syms if note_syms else ["GENERAL"],
    )
    existing_note = st.session_state.notes.get(selected_note_sym, "")
    note_text = st.text_area(
        f"Notes for {selected_note_sym}",
        value=existing_note,
        height=140,
        placeholder="Enter setup observations, entry triggers, stop-loss notes, or FII/DII context...",
    )
    c_save, c_del = st.columns([1, 1])
    if c_save.button("Save Note", type="primary"):
        if note_text.strip():
            st.session_state.notes[selected_note_sym] = note_text.strip()
        else:
            st.session_state.notes.pop(selected_note_sym, None)
        save_json_store(NOTES_FILE, st.session_state.notes)
        st.success(f"Saved note for {selected_note_sym}.")
        st.rerun()

    if c_del.button("Delete Note"):
        st.session_state.notes.pop(selected_note_sym, None)
        save_json_store(NOTES_FILE, st.session_state.notes)
        st.rerun()

    if st.session_state.notes:
        st.divider()
        for sym, text in st.session_state.notes.items():
            st.markdown(f"**{sym}:** {text}")
