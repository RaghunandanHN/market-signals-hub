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


# -----------------------------------------------------------------------------
# 1. PERSISTENT WATCHLIST & NOTES HELPERS
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
  with open(filepath, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, ensure_ascii=False)


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

  # Fallback calculation if Dist_Pct is 0 but 52WH and LTP exist in source sheet
  if "52WH" in df.columns:
    w52 = _clean_num(df["52WH"], default=0.0)
    missing_dist = (df["Dist_Pct"] == 0.0) & (w52 > 0.0) & (df["LTP"] > 0.0)
    df.loc[missing_dist, "Dist_Pct"] = (
        (df.loc[missing_dist, "LTP"] - w52[missing_dist]) / w52[missing_dist]
    ) * 100.0

  # Always show distance to 52W high as a non-negative percentage
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

# -----------------------------------------------------------------------------
# 4. SIDEBAR: SIGNAL FILTERS (CHECKBOXES)
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
    selected_strategies = []
    for strat in available_strats:
      if st.checkbox(strat, value=True, key=f"chk_strat_{strat}"):
        selected_strategies.append(strat)

    st.markdown("### Action Selection")
    available_actions = sorted(
        df_all["Action"].replace("", pd.NA).dropna().unique()
    )
    selected_actions = []
    for act in available_actions:
      if st.checkbox(act, value=True, key=f"chk_act_{act}"):
        selected_actions.append(act)

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

tab_signals, tab_cards, tab_watchlist, tab_notes = st.tabs([
    f"Signals ({len(df_view)})",
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
