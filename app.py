import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import pytz
import streamlit as st
from dateutil.relativedelta import relativedelta
from googleapiclient.discovery import build, Resource
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DEFAULT_CALENDAR = "Fitness"
DEFAULT_TIMEZONE = "Asia/Singapore"
DEFAULT_RANGE = "All available"

CREDENTIALS_PATH = os.path.join("secrets", "acrodashboardv2_cred.json")

CATEGORY_KEYWORDS = [
    ("standing acrobatics", ["standing acrobatics", "standing acro", "acrobatics"]),
    ("contemporary dance", ["contemporary", "contemp", "contemporary dance"]),
    ("partner lifts", ["partner lift", "partner lifts", "partnering", "lifts"]),
    ("acroyoga", ["acroyoga", "acro"]),
    ("calisthenics", ["calisthenics", "calisthenic", "calis"]),
    ("strength and stretch", ["strength", "stretch", "mobility", "conditioning", "strength & stretch"]),
    ("jams", ["jam", "jams"]),
]


def normalize_timezone(tz_input: str) -> str:
    cleaned = (tz_input or "").strip()
    if cleaned.lower() in {"singapore", "sg", "s"}:
        return "Asia/Singapore"
    return cleaned or "UTC"


def get_service() -> "Resource":
    from google.oauth2 import service_account
    
    # Check if running on Streamlit Cloud
    if "credentials" in st.secrets:
        # Running on Streamlit Cloud - use secrets
        credentials_dict = dict(st.secrets["credentials"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=SCOPES
        )
    else:
        # Running locally - use local service account file
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_PATH, scopes=SCOPES  # This already points to secrets/acrodashboardv2_cred.json
        )
    
    return build("calendar", "v3", credentials=creds)


def resolve_calendar_id(service, calendar_name: str) -> str:
    if calendar_name.lower() == "primary":
        return "primary"

    page_token = None
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for entry in calendar_list.get("items", []):
            if entry.get("summary", "").strip().lower() == calendar_name.lower():
                return entry["id"]
        page_token = calendar_list.get("nextPageToken")
        if not page_token:
            break

    available = [c.get("summary", "") for c in calendar_list.get("items", [])]
    raise ValueError(f"Calendar '{calendar_name}' not found. Available: {available}")


def fetch_events(service, calendar_id: str, time_min: str, time_max: str) -> list[dict]:
    events = []
    page_token = None
    while True:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return events


def categorize_event(text: str) -> str:
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in lowered:
                return category
    return "other"


def parse_event_datetime(value: dict, tz_name: str) -> tuple[pd.Timestamp, bool]:
    tz = pytz.timezone(tz_name)
    if "dateTime" in value:
        dt = pd.to_datetime(value["dateTime"])
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        return dt.tz_convert(tz), False

    dt = pd.to_datetime(value.get("date"))
    return dt.tz_localize(tz), True


def build_dataframe(events: list[dict], tz_name: str) -> pd.DataFrame:
    rows = []
    for event in events:
        summary = event.get("summary", "(no title)")
        description = event.get("description", "")
        location = event.get("location", "")
        text = " ".join([summary, description, location])

        start_dt, start_all_day = parse_event_datetime(event.get("start", {}), tz_name)
        end_dt, end_all_day = parse_event_datetime(event.get("end", {}), tz_name)

        duration_hours = max((end_dt - start_dt).total_seconds() / 3600, 0)
        all_day = start_all_day or end_all_day

        rows.append(
            {
                "summary": summary,
                "start": start_dt,
                "end": end_dt,
                "duration_hours": duration_hours,
                "category": categorize_event(text),
                "all_day": all_day,
            }
        )

    return pd.DataFrame(rows)


def aggregate_by_month(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df["month"] = df["start"].dt.to_period("M").dt.to_timestamp()
    hours = (
        df.groupby(["month", "category"], as_index=False)["duration_hours"]
        .sum()
        .sort_values("month")
    )
    counts = (
        df.groupby(["month", "category"], as_index=False)["summary"]
        .count()
        .rename(columns={"summary": "class_count"})
        .sort_values("month")
    )
    return hours, counts


st.set_page_config(page_title="Acro Schedule Dashboard", layout="wide")
st.title("Acro Schedule Dashboard")
st.caption("Pulls Google Calendar events and visualizes class time and frequency.")

with st.sidebar:
    st.header("Settings")
    calendar_name = st.text_input("Calendar name", value=DEFAULT_CALENDAR)
    timezone_input = st.text_input("Timezone", value=DEFAULT_TIMEZONE)
    tz_name = normalize_timezone(timezone_input)
    try:
        pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        st.error(f"Unknown timezone '{tz_name}'. Falling back to UTC.")
        tz_name = "UTC"

    range_choice = st.selectbox(
        "Date range",
        options=["Last 12 months", "Last 24 months", "All available"],
        index=["Last 12 months", "Last 24 months", "All available"].index(DEFAULT_RANGE),
    )
    include_all_day = st.checkbox("Include all-day events", value=False)

    with st.expander("Category keywords"):
        st.write("Edit these in the code if your class titles differ.")
        st.write({category: keywords for category, keywords in CATEGORY_KEYWORDS})

if not os.path.exists(CREDENTIALS_PATH):
    st.error(f"Credentials file not found: {CREDENTIALS_PATH}")
    st.stop()

if range_choice == "Last 12 months":
    start_dt = datetime.now(pytz.timezone(tz_name)) - relativedelta(months=12)
elif range_choice == "Last 24 months":
    start_dt = datetime.now(pytz.timezone(tz_name)) - relativedelta(months=24)
else:
    start_dt = datetime(2000, 1, 1, tzinfo=pytz.timezone(tz_name))

end_dt = datetime.now(pytz.timezone(tz_name)) + relativedelta(days=1)

st.info("If this is your first run, a browser window will open for Google approval.")

try:
    service = get_service()
    calendar_id = resolve_calendar_id(service, calendar_name)
    events = fetch_events(
        service,
        calendar_id=calendar_id,
        time_min=start_dt.isoformat(),
        time_max=end_dt.isoformat(),
    )
except Exception as exc:
    st.error(f"Failed to load calendar data: {exc}")
    st.stop()

if not events:
    st.warning("No events found for the selected range.")
    st.stop()

df = build_dataframe(events, tz_name)
if not include_all_day:
    df = df[~df["all_day"]]

if df.empty:
    st.warning("No events left after filtering. Try including all-day events.")
    st.stop()

hours_df, counts_df = aggregate_by_month(df)

col1, col2 = st.columns(2)
with col1:
    fig_hours = px.bar(
        hours_df,
        x="month",
        y="duration_hours",
        color="category",
        title="Hours per Month by Category",
        labels={"duration_hours": "Hours", "month": "Month"},
    )
    st.plotly_chart(fig_hours, use_container_width=True)

with col2:
    fig_counts = px.line(
        counts_df,
        x="month",
        y="class_count",
        color="category",
        title="Class Frequency per Month by Category",
        labels={"class_count": "Classes", "month": "Month"},
        markers=True,
    )
    st.plotly_chart(fig_counts, use_container_width=True)

st.subheader("Raw data")
st.dataframe(df.sort_values("start"), use_container_width=True)