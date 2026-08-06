"""M:AD Job Hunter dashboard.

Run with:  streamlit run app.py
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import db
import description_fetch
import outreach
from config import BRAND, STATUS_VALUES, ANTHROPIC_API_KEY

st.set_page_config(page_title="M:AD Job Hunter", page_icon="🎯", layout="wide")

db.init_db()

MAD_LOGO_URL = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRaiNd0OPnTGGQR1GXeu0GoznOZ5C9UftVA83-9M1ogeQ&s=10"

# Read from the shared DB, not a local file -- last_run needs to reflect
# whichever machine actually ran the pipeline (this PC, GitHub Actions, or
# the button below), not just this dashboard's own local runs.
with db.get_conn() as _conn:
    last_run_info = db.get_last_completed_run(_conn)

_SOURCE_LABELS = {"local": "מהמחשב המקומי", "github_actions": "מהענן (GitHub Actions)", "button": "כפתור ידני"}


def last_run_display(run) -> str:
    if not run:
        return "טרם בוצעה ריצה מלאה"
    try:
        from zoneinfo import ZoneInfo
        finished = datetime.fromisoformat(run["finished_at"]).replace(tzinfo=timezone.utc)
        local_time = finished.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        local_time = run["finished_at"]
    source_label = _SOURCE_LABELS.get(run["source"], run["source"])
    return f"ריצה אחרונה: {local_time} ({source_label})"

# --- Brand styling: clean/minimal RTL, Assistant font, white/black + one accent
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp, button, input, textarea, select,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] {{
  font-family: 'Assistant', sans-serif !important;
}}

:root {{ --mad-accent: {BRAND['color_purple']}; --mad-ink: #1a1a1a; }}

/* Text reads right-to-left, but the sidebar STAYS on the left (per
   explicit requirement) -- so direction:rtl is scoped to content areas
   only, not the outer app/flex layout, which would otherwise flip the
   sidebar to the right too.

   CRITICAL: the data-editor/dataframe grid is a canvas widget
   (glide-data-grid) that does its own pixel-level layout math -- applying
   direction:rtl to it (or any ancestor with `* {{}}`) breaks that math and
   renders cells as single stray characters, which is exactly the bug that
   showed up. It must be explicitly excluded and left in normal direction. */
.stApp {{ background: #ffffff; }}
section[data-testid="stSidebar"] {{ background: #fafafa; border-right: 1px solid #ececec; direction: rtl; }}
[data-testid="stMain"] {{ direction: rtl; }}
[data-testid="stDataFrame"], [data-testid="stDataFrame"] * ,
[data-testid="stDataFrameResizable"], [data-testid="stDataFrameResizable"] * {{
  direction: ltr !important;
}}
h1, h2, h3, p, label, .stMarkdown {{ text-align: right; }}

.mad-header {{
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 22px 4px 18px 4px; margin-bottom: 10px;
  border-bottom: 1px solid #ececec; text-align: center;
}}
.mad-logo-img {{ height: 46px; }}
.mad-subtitle {{ color: #6b6b6b; font-size: 14px; }}
.mad-lastrun {{ color: #9a9a9a; font-size: 12px; margin-top: 2px; }}

div[data-testid="stMetric"] {{
  background: #fafafa; border: 1px solid #ececec; border-radius: 12px; padding: 14px 16px;
}}
div[data-testid="stMetricValue"] {{ color: var(--mad-ink); font-weight: 700; }}

.stButton > button {{
  border-radius: 8px; font-weight: 600; border: 1px solid #e2e2e2;
}}
.stButton > button[kind="primary"] {{
  background: var(--mad-ink); border: none; color: white;
}}

.job-detail-card {{
  border: 1px solid #ececec; border-radius: 12px; padding: 18px 20px;
  background: #fcfcfc; margin-top: 6px;
}}
.bidi {{ direction: rtl; unicode-bidi: plaintext; white-space: pre-wrap; line-height: 1.6; color: #2b2b2b; }}
</style>
<div class="mad-header">
  <img class="mad-logo-img" src="{MAD_LOGO_URL}" alt="M:AD">
  <div style="color:var(--mad-ink); font-weight:700; font-size:17px;">🕵️ Job Hunter &amp; Lead Generation</div>
  <div class="mad-subtitle">{BRAND['tagline']}</div>
  <div class="mad-lastrun">{last_run_display(last_run_info)}</div>
</div>
""", unsafe_allow_html=True)


def days_since(iso_str: str | None) -> int | None:
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


with db.get_conn() as conn:
    rows = [dict(r) for r in db.fetch_jobs_for_dashboard(conn)]

if not rows:
    st.info("אין עדיין משרות במאגר. הרץ `python pipeline.py` כדי לאסוף משרות, ואז רענן דף זה.")
    st.stop()

df = pd.DataFrame(rows)
df["required_tech_stack"] = df["required_tech_stack"].apply(lambda s: json.loads(s) if s else [])
df["requirements_str"] = df["required_tech_stack"].apply(lambda lst: " / ".join(lst))
df["days_live"] = df["posted_date"].apply(days_since)
df["days_live"] = df["days_live"].fillna(df["created_at"].apply(days_since))
df["date_added"] = df["created_at"].apply(lambda s: s[:10] if s else "")
df["outreach_initiated"] = df["outreach_initiated"].astype(bool)
df["best_email"] = df["job_contact_email"].fillna(df["company_email"])

# --- Manual scan trigger -----------------------------------------------------
# Only meaningful when this app is running locally (not the Render/Streamlit
# Cloud copy): scraping needs a real headless browser (Playwright/Chromium)
# installed, which the cloud build deliberately doesn't install (see
# render.yaml -- installing it there too would slow every build down and
# risks the free tier's resource limits, for a button visitors other than
# the owner shouldn't be triggering anyway). Detected via hosting platforms'
# own env markers rather than shown-then-failing, so a CEO clicking around
# the shared link doesn't see a broken-looking error.
_IS_CLOUD_HOST = bool(os.getenv("RENDER") or os.getenv("DYNO") or os.getenv("STREAMLIT_SHARING_MODE"))

with db.get_conn() as _conn:
    _recent_durations = db.get_recent_run_durations(_conn, limit=5)
_avg_duration = sum(_recent_durations) / len(_recent_durations) if _recent_durations else None

if "scan_state" not in st.session_state:
    # A plain dict, mutated in place by the background thread below --
    # deliberately NOT touching st.session_state.<attr> = ... from that
    # thread. Streamlit's session_state proxy needs a ScriptRunContext to
    # know which session a write belongs to; a raw thread doesn't have one
    # (confirmed via repeated "missing ScriptRunContext" warnings in
    # logs/dashboard.log), so those writes silently never reached the
    # browser -- the progress caption stayed frozen on "מתחיל... (0/1)"
    # even though the scan itself was running fine. Reading/mutating the
    # SAME plain dict object from both threads sidesteps that entirely.
    st.session_state.scan_state = {
        "thread": None, "progress": {"stage": "", "current": 0, "total": 0},
        "result": None, "error": None, "start_time": None,
    }

_scan = st.session_state.scan_state


def _run_scan_in_background(shared: dict):
    def _progress_cb(stage: str, current: int, total: int):
        shared["progress"] = {"stage": stage, "current": current, "total": total}
    try:
        import pipeline
        stats = pipeline.main(progress_cb=_progress_cb, source="button")
        shared["result"] = stats
    except Exception as e:
        shared["error"] = str(e)
    finally:
        shared["thread"] = None


if _IS_CLOUD_HOST:
    st.sidebar.caption("🔄 הסריקה האוטומטית רצה מהמחשב המקומי / GitHub Actions -- לא ניתנת להפעלה ידנית מכאן.")
else:
    if _avg_duration:
        st.sidebar.caption(f"ריצות קודמות ארכו בממוצע כ-{int(_avg_duration // 60)} דקות")

    _scan_running = _scan["thread"] is not None and _scan["thread"].is_alive()

    if st.sidebar.button("🔄 הרץ סריקה עכשיו", use_container_width=True, disabled=_scan_running):
        _scan["result"] = None
        _scan["error"] = None
        _scan["progress"] = {"stage": "מתחיל...", "current": 0, "total": 1}
        _scan["start_time"] = time.time()
        t = threading.Thread(target=_run_scan_in_background, args=(_scan,), daemon=True)
        _scan["thread"] = t
        t.start()
        st.rerun()

    if _scan_running:
        p = _scan["progress"]
        frac = (p["current"] / p["total"]) if p["total"] else 0.0
        st.sidebar.progress(min(frac, 1.0))
        elapsed = time.time() - _scan["start_time"]
        eta_note = ""
        if frac > 0.05:  # need at least a little progress for a sane estimate
            estimated_total = elapsed / frac
            remaining = max(estimated_total - elapsed, 0)
            eta_note = f" -- נותרו כ-{int(remaining // 60)}:{int(remaining % 60):02d} דקות"
        elif _avg_duration:
            remaining = max(_avg_duration - elapsed, 0)
            eta_note = f" -- הערכה לפי ריצות קודמות: כ-{int(remaining // 60)}:{int(remaining % 60):02d} דקות"
        st.sidebar.caption(f"{p['stage']} ({p['current']}/{p['total']}){eta_note}")
        time.sleep(1)
        st.rerun()
    elif _scan["result"] is not None:
        st.sidebar.success(f"הסתיים ({int(time.time()-_scan['start_time'])} שניות): {_scan['result']}")
        _scan["result"] = None
    elif _scan["error"] is not None:
        st.sidebar.error(f"הסריקה נכשלה: {_scan['error']}")
        _scan["error"] = None

# --- Sidebar filters ---------------------------------------------------------
st.sidebar.header("סינון")
min_score = st.sidebar.slider("ציון התאמה מינימלי (fit score) — משרות שטרם דורגו תמיד יוצגו", 0, 100, 0)
sources = st.sidebar.multiselect("מקור", sorted(df["source"].unique()), default=list(sorted(df["source"].unique())))
statuses = st.sidebar.multiselect("סטטוס", STATUS_VALUES, default=[s for s in STATUS_VALUES if s != "ARCHIVED"])
hide_contacted = st.sidebar.checkbox("הצג רק חברות שלא פנינו אליהן עדיין", value=False)

filtered = df[
    (df["fit_score"].isna() | (df["fit_score"] >= min_score))
    & (df["source"].isin(sources))
    & (df["status"].isin(statuses))
]
if hide_contacted:
    filtered = filtered[~filtered["outreach_initiated"]]

scored = filtered["fit_score"].dropna()
pending_count = int(filtered["fit_score"].isna().sum())

col1, col2, col3, col4 = st.columns(4)
col1.metric("סה\"כ משרות רלוונטיות", len(filtered))
col2.metric("חברות ייחודיות", filtered["company_name"].nunique())
col3.metric("פנינו כבר", int(filtered["outreach_initiated"].sum()))
col4.metric("ציון ממוצע (מדורגות)", round(scored.mean(), 1) if len(scored) else "—")

if pending_count:
    st.warning(
        f"{pending_count} משרות עדיין לא דורגו על ידי Claude (fit_score) -- "
        f"יש להוסיף ANTHROPIC_API_KEY בקובץ .env ולהריץ `python pipeline.py` כדי לדרג אותן."
    )

st.subheader("משרות")
# company_name/job_title are listed FIRST so they're the default-visible
# columns without scrolling -- the data-grid widget is a canvas Streamlit
# draws internally, and reversing the Python order to simulate "RTL" (a
# prior attempt) actually made company_name scroll further away, not closer.
# Natural/first = guaranteed visible on open, regardless of how the grid
# handles direction internally.
display_cols = {
    "company_name": "חברה",
    "job_title": "תפקיד",
    "fit_score": "ציון התאמה",
    "requirements_str": "דרישות",
    "seniority_level": "רמת בכירות",
    "source": "מקור",
    "days_live": "ימים באוויר",
    "date_added": "נוסף בתאריך",
    "status": "סטטוס",
    "outreach_initiated": "פנינו יזום?",
    "best_email": "מייל ליצירת קשר",
    "job_url": "קישור",
}
table_df = filtered[list(display_cols.keys())].rename(columns=display_cols)

edited = st.data_editor(
    table_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "סטטוס": st.column_config.SelectboxColumn(options=STATUS_VALUES),
        "פנינו יזום?": st.column_config.CheckboxColumn(),
        "קישור": st.column_config.LinkColumn(),
        "ציון התאמה": st.column_config.ProgressColumn(min_value=0, max_value=100),
    },
    disabled=[c for c in table_df.columns if c not in ("סטטוס", "פנינו יזום?")],
    key="jobs_editor",
)

# Persist edits back to SQLite (status / outreach-initiated toggles).
if not edited.equals(table_df):
    with db.get_conn() as conn:
        for (idx, orig), (_, new) in zip(table_df.iterrows(), edited.iterrows()):
            job_id = int(filtered.loc[idx, "id"])
            if orig["סטטוס"] != new["סטטוס"]:
                db.set_job_status(conn, job_id, new["סטטוס"])
            if bool(orig["פנינו יזום?"]) != bool(new["פנינו יזום?"]):
                db.set_outreach_initiated(conn, job_id, bool(new["פנינו יזום?"]))
    st.rerun()

# --- Job detail + outreach draft ---------------------------------------------
st.subheader("פרטי משרה + טיוטת פנייה")
if len(filtered):
    options = {f"{r.company_name} — {r.job_title} (#{r.id})": r.id for r in filtered.itertuples()}
    picked_label = st.selectbox("בחר משרה", list(options.keys()))
    picked_id = options[picked_label]
    job = filtered[filtered["id"] == picked_id].iloc[0]
    description = job["raw_description"] or ""

    # Lazy full-description fetch: the bulk pipeline only stores the
    # search-result snippet (fetching full text for every job during a
    # batch run was slow and could hang -- see description_fetch.py). Here
    # we fetch once, only for the single job being viewed, and cache it.
    if len(description) < 400:
        with st.spinner("טוען תיאור מלא..."):
            fuller = description_fetch.fetch_full_description(job["source"], job["job_url"])
        if fuller and len(fuller) > len(description):
            description = fuller
            with db.get_conn() as conn:
                db.update_raw_description(conn, int(job["id"]), description)

    st.markdown(
        f'<div class="job-detail-card"><div class="bidi">{description or "(אין תיאור מלא שמור עבור משרה זו)"}</div></div>',
        unsafe_allow_html=True,
    )
    st.caption(f"מקור: {job['source']} · [פתח את המודעה המקורית]({job['job_url']})")

    email_lang = st.radio("שפת הטיוטה", ["אוטומטי (לפי שפת המודעה)", "עברית", "English"], horizontal=True)
    if st.button("צור טיוטת פנייה", type="primary"):
        lang_map = {"אוטומטי (לפי שפת המודעה)": job["language"] or "he", "עברית": "he", "English": "en"}
        if not ANTHROPIC_API_KEY:
            st.error("לא הוגדר מפתח ANTHROPIC_API_KEY -- לא ניתן להפיק טיוטת פנייה. יש להגדיר אותו בהגדרות ה-Secrets של האפליקציה.")
        else:
            try:
                with st.spinner("Claude מנסח את הפנייה..."):
                    draft = outreach.draft_email(
                        company_name=job["company_name"],
                        job_title=job["job_title"],
                        tech_stack=job["required_tech_stack"],
                        language=lang_map[email_lang],
                    )
                st.session_state["last_draft"] = draft
                with db.get_conn() as conn:
                    db.insert_outreach_log(
                        conn, job_id=int(job["id"]), contact_person=job["contact_name"],
                        contact_email=job["best_email"], language=lang_map[email_lang],
                        email_subject=draft["subject"], email_body=draft["body"],
                    )
            except Exception as e:
                import traceback
                print(f"[outreach draft error] {traceback.format_exc()}", flush=True)
                st.error(f"שגיאה ביצירת טיוטת הפנייה: {type(e).__name__}: {e}")

    if st.session_state.get("last_draft"):
        draft = st.session_state["last_draft"]
        st.text_input("נושא", value=draft["subject"], key="draft_subject")
        st.markdown(f'<div class="job-detail-card bidi">{draft["body"]}</div>', unsafe_allow_html=True)
        st.caption(f"נשלח אל: {job['best_email'] or '— לא נמצא מייל, יש להשלים ידנית —'}")
else:
    st.info("אין משרות תואמות לסינון הנוכחי.")
