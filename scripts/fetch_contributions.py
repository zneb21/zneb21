#!/usr/bin/env python3
"""
Fetch GitHub's contribution calendar and write data/contributions.json with
the raw days plus derived stats. When GH_PROFILE_TOKEN is available, the
authenticated GraphQL API includes private-contribution totals; otherwise
the script falls back to public contributions only.
(current streak, longest streak, best day, monthly totals).

Run by .github/workflows/update-profile-art.yml.
"""
import datetime
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

USERNAME = os.environ.get("GH_PROFILE_USER", "zneb21")
URL = f"https://github.com/users/{USERNAME}/contributions"
GRAPHQL_URL = "https://api.github.com/graphql"
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "contributions.json")

GRAPHQL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
"""

LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def date_range():
    end = datetime.datetime.now(datetime.timezone.utc).date()
    start = end - datetime.timedelta(days=364)
    return start, end


def fetch_days_graphql(token):
    start, end = date_range()
    resp = requests.post(
        GRAPHQL_URL,
        json={
            "query": GRAPHQL_QUERY,
            "variables": {
                "login": USERNAME,
                "from": f"{start.isoformat()}T00:00:00Z",
                "to": f"{end.isoformat()}T23:59:59Z",
            },
        },
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": "profile-readme-bot/1.0",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        messages = "; ".join(error.get("message", "GraphQL error") for error in payload["errors"])
        raise RuntimeError(messages)

    calendar = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days.append({
                "date": day["date"],
                "count": int(day["contributionCount"]),
                "level": LEVELS.get(day["contributionLevel"], 0),
            })

    start_text, end_text = start.isoformat(), end.isoformat()
    return [day for day in days if start_text <= day["date"] <= end_text]


def fetch_days():
    token = os.environ.get("GH_PROFILE_TOKEN")
    if token:
        try:
            days = fetch_days_graphql(token)
            if days:
                print("using authenticated GitHub contribution calendar")
                return days
        except (KeyError, RuntimeError, requests.RequestException) as exc:
            print(f"authenticated contribution fetch failed; using public fallback: {exc}", file=sys.stderr)

    resp = requests.get(
        URL,
        params={"cache": str(time.time_ns())},
        headers={
            "User-Agent": "profile-readme-bot/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cells = soup.select("td.ContributionCalendar-day")
    if not cells:
        print("no calendar cells found -- github markup may have changed", file=sys.stderr)
        sys.exit(1)

    days = []
    for td in cells:
        date = td.get("data-date")
        if not date:
            continue
        td_id = td.get("id")
        tooltip_el = soup.find("tool-tip", attrs={"for": td_id}) if td_id else None
        text = tooltip_el.get_text(strip=True) if tooltip_el else ""
        if re.search(r"no contributions", text, re.I):
            count = 0
        else:
            m = re.match(r"(\d+)", text)
            count = int(m.group(1)) if m else 0
        days.append({"date": date, "count": count, "level": int(td.get("data-level") or 0)})

    days.sort(key=lambda d: d["date"])
    return days


def compute_current_streak(days):
    idx = len(days) - 1
    if days[idx]["count"] == 0:
        idx -= 1  # today isn't over yet -- don't break the streak on it
    streak = 0
    end_idx = idx
    while idx >= 0 and days[idx]["count"] > 0:
        streak += 1
        idx -= 1
    start_idx = idx + 1
    if streak == 0:
        return 0, None, None
    return streak, days[start_idx]["date"], days[end_idx]["date"]


def compute_longest_streak(days):
    longest = run = 0
    longest_start = longest_end = None
    run_start_idx = None
    for i, d in enumerate(days):
        if d["count"] > 0:
            if run == 0:
                run_start_idx = i
            run += 1
            if run > longest:
                longest = run
                longest_start = days[run_start_idx]["date"]
                longest_end = days[i]["date"]
        else:
            run = 0
    return longest, longest_start, longest_end


def build_data(days):
    total = sum(d["count"] for d in days)
    active_days = sum(1 for d in days if d["count"] > 0)
    best = max(days, key=lambda d: d["count"])
    cur_len, cur_start, cur_end = compute_current_streak(days)
    long_len, long_start, long_end = compute_longest_streak(days)

    monthly = {}
    for d in days:
        key = d["date"][:7]
        monthly[key] = monthly.get(key, 0) + d["count"]
    monthly_list = [{"month": k, "total": v} for k, v in sorted(monthly.items())]

    return {
        "username": USERNAME,
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "range": {"start": days[0]["date"], "end": days[-1]["date"]},
        "total_contributions": total,
        "active_days": active_days,
        "avg_per_active_day": round(total / active_days, 1) if active_days else 0,
        "current_streak": {"length": cur_len, "start": cur_start, "end": cur_end},
        "longest_streak": {"length": long_len, "start": long_start, "end": long_end},
        "best_day": {"date": best["date"], "count": best["count"]},
        "monthly": monthly_list,
        "days": days,
    }


if __name__ == "__main__":
    days = fetch_days()
    data = build_data(days)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {OUT_PATH}: {data['total_contributions']} contributions, "
          f"current streak {data['current_streak']['length']}, "
          f"longest streak {data['longest_streak']['length']}")
