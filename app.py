import os
from datetime import datetime
from googleapiclient.discovery import build

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import pytz
import streamlit as st
from dateutil.relativedelta import relativedelta


SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DEFAULT_CALENDAR = "Acro dashboard"
DEFAULT_TIMEZONE = "Asia/Singapore"
DEFAULT_RANGE = "All available"
API_KEY = st.secrets["GOOGLE_API_KEY"]  # Use secrets in production
CALENDAR_ID = "c9818e9ca3bed4795137692d62986c957dab16f568697938a04832f19a4ea4b8@group.calendar.google.com"  # Replace with your calendar ID

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


def get_service():
    """Build Google Calendar service with API key (no authentication needed)"""
    return build("calendar", "v3", developerKey=API_KEY)

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
                fields="nextPageToken,items(summary,start,end,description)",  #test just adding nextPageToken, then maxResults=2500
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
        summary = event.get("summary", "(no title)") #Calendar events should be set to see all event details and not just Free/Busy
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


st.set_page_config(page_title="Cal", layout="wide")
st.title("Cal")
st.caption("Pulls Google Calendar events and visualizes class time and frequency.")

tz_name = DEFAULT_TIMEZONE  # "Asia/Singapore"
range_choice = DEFAULT_RANGE  # "All available"
include_all_day = False

# with st.sidebar:
#     st.header("Settings")
#     timezone_input = st.text_input("Timezone", value=DEFAULT_TIMEZONE)
#     tz_name = normalize_timezone(timezone_input)
#     try:
#         pytz.timezone(tz_name)
#     except pytz.UnknownTimeZoneError:
#         st.error(f"Unknown timezone '{tz_name}'. Falling back to UTC.")
#         tz_name = "UTC"

#     range_choice = st.selectbox(
#         "Date range",
#         options=["Last 12 months", "Last 24 months", "All available"],
#         index=["Last 12 months", "Last 24 months", "All available"].index(DEFAULT_RANGE),
#     )
#     include_all_day = st.checkbox("Include all-day events", value=False)

#     with st.expander("Category keywords"):
#         st.write("Edit these in the code if your class titles differ.")
#         st.write({category: keywords for category, keywords in CATEGORY_KEYWORDS})

if range_choice == "Last 12 months":
    start_dt = datetime.now(pytz.timezone(tz_name)) - relativedelta(months=12)
elif range_choice == "Last 24 months":
    start_dt = datetime.now(pytz.timezone(tz_name)) - relativedelta(months=24)
else:
    start_dt = datetime(2000, 1, 1, tzinfo=pytz.timezone(tz_name))

end_dt = datetime.now(pytz.timezone(tz_name)) + relativedelta(days=1)


# tz_name = normalize_timezone(DEFAULT_TIMEZONE)
# include_all_day = False
# start_dt = datetime(2000, 1, 1, tzinfo=pytz.timezone(tz_name))
# end_dt = datetime.now(pytz.timezone(tz_name)) + relativedelta(days=1)

try:
    service = get_service()
    calendar_id = CALENDAR_ID 

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

df = df.copy()
df["month_label"] = df["start"].dt.to_period("M").astype(str)
selected_months = st.multiselect("Select months", options=sorted(df["month_label"].unique()), default=sorted(df["month_label"].unique())[-3:]) #most recent month
df_view = df[df["month_label"].isin(selected_months)]
if df_view.empty:
    st.warning("No data for selected month(s)")
    st.stop()

hours_df, counts_df = aggregate_by_month(df_view)

k1, k2, k3 = hours_df["duration_hours"].sum(), counts_df["class_count"].sum(), hours_df["duration_hours"].mean()
cols = st.columns(3)
cols[0].metric("Total Hours", f"{k1:.1f}")
cols[1].metric("Total Classes", f"{k2}")
cols[2].metric("Avg Hours/Month", f"{k3:.1f}")

fig_pie = px.pie(hours_df, values="duration_hours", names="category", title="Hours by Category")
st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("### Classes and Hours by Month and Category")
col1, col2 = st.columns(2)
fig_bar = px.bar(counts_df, x="month", y="class_count", color="category", title="Classes per Month by Category")
col1.plotly_chart(fig_bar, use_container_width=True)
fig_line = px.line(hours_df, x="month", y="duration_hours", color="category", title="Hours per Month by Category")
col2.plotly_chart(fig_line, use_container_width=True)

st.markdown("### Intensity Heatmap")
intensity_mode = st.selectbox("Intensity view", [
    "Weekday x Hour (Class count)",
    "Weekday x Hour (Hours)",
    "Month x Weekday (Class count)"
])

tmp = df_view.copy()
tmp["weekday"] = pd.Categorical(tmp["start"].dt.day_name(), categories=[
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
], ordered=True)
tmp["hour"] = tmp["start"].dt.hour

if intensity_mode == "Weekday x Hour (Class count)":
    pivot = tmp.groupby(["weekday", "hour"]).size().unstack(fill_value=0)
    title = "Class Count by Weekday and Hour"
elif intensity_mode == "Weekday x Hour (Hours)":
    pivot = tmp.groupby(["weekday", "hour"])["duration_hours"].sum().unstack(fill_value=0)
    title = "Hours by Weekday and Hour"
else:  # Month x Weekday (Class count)
    tmp["month"] = tmp["start"].dt.to_period("M").astype(str)
    pivot = tmp.groupby(["month", "weekday"]).size().unstack(fill_value=0)
    title = "Class Count by Month and Weekday"

fig_heat = px.imshow(pivot,
                    labels=dict(x="Hour" if intensity_mode != "Month x Weekday (Class count)" else "Weekday",
                                y="Weekday" if intensity_mode != "Month x Weekday (Class count)" else "Month",
                                color="Count"),
                    x=pivot.columns if intensity_mode != "Month x Weekday (Class count)" else pivot.columns,
                    y=pivot.index,
                    aspect="auto",
                    color_continuous_scale="YlOrRd",
                    title=title)
st.plotly_chart(fig_heat, use_container_width=True)

# st.dataframe(df_view.sort_values("start"))

# st.subheader("Raw data")
# st.dataframe(df.sort_values("start"), use_container_width=True)