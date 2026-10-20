"""Follow random active developers and unfollow the ones who don't follow back.

    python scripts/grow.py follow     # follow up to DAILY_LIMIT new people
    python scripts/grow.py unfollow   # unfollow non-followers after GRACE_DAYS

Everyone ever followed is kept in follow-log.json and is never followed again.
Needs GH_USERNAME and GH_TOKEN (classic token with the user:follow scope).
Set DRY_RUN=true to print what would happen without following anyone.
"""
import base64
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "20"))
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "3"))
ACTIVE_DAYS = int(os.environ.get("ACTIVE_DAYS", "30"))
LOCATION = os.environ.get("LOCATION", "")  # e.g. "India" to only pick people there
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
LOG = Path(__file__).resolve().parent.parent / "follow-log.json"

LANGUAGES = ["javascript", "typescript", "python", "java", "go", "rust", "c++", "c#",
             "php", "kotlin", "swift", "dart", "ruby", "html", "c"]
TODAY = dt.datetime.now(dt.timezone.utc).date()


def api(method, path, **params):
    url = f"https://api.github.com{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": ME,
    })
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, None


def logins(path):
    out, page = set(), 1
    while True:
        _, batch = api("GET", path, per_page=100, page=page)
        if not batch:
            return out
        out |= {u["login"].lower() for u in batch}
        page += 1


def load_log():
    return json.loads(LOG.read_text()) if LOG.exists() else []


def save_log(log):
    if not DRY_RUN:
        LOG.write_text(json.dumps(log, indent=2) + "\n")


SEARCH = """
query($q: String!, $after: String, $since: DateTime!) {
  search(query: $q, type: USER, first: 100, after: $after) {
    nodes {
      ... on User {
        login
        followers { totalCount }
        following { totalCount }
        contributionsCollection(from: $since) { contributionCalendar { totalContributions } }
      }
    }
  }
}"""


def graphql(query, **variables):
    req = urllib.request.Request("https://api.github.com/graphql", method="POST",
                                 data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": ME})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()).get("data")
    except urllib.error.HTTPError:
        return None


def looks_good(u):
    """Under 10 followers, follows at least as many as follow them, and active lately."""
    followers, following = u["followers"]["totalCount"], u["following"]["totalCount"]
    active = u["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    return followers < 10 and following >= max(3, followers) and active > 0


def candidates():
    """Yield small, active developer accounts from random slices of user search."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ACTIVE_DAYS)).isoformat()
    for _ in range(40):
        lang = random.choice(LANGUAGES)
        offset = random.randrange(0, 1000, 100)
        sort = random.choice(["joined-desc", "joined-asc", "followers-desc"])
        q = f"language:{lang} followers:<10 repos:3..100 sort:{sort}"
        if LOCATION:
            q += f' location:"{LOCATION}"'
        after = base64.b64encode(f"cursor:{offset}".encode()).decode() if offset else None
        data = graphql(SEARCH, q=q, after=after, since=since)
        if not data:
            time.sleep(5)
            continue
        hits = [u for u in data["search"]["nodes"] if u and looks_good(u)]
        random.shuffle(hits)
        print(f"search: {lang}, {sort}, from #{offset}: {len(hits)} good")
        yield from hits
        time.sleep(2)


def follow():
    log = load_log()
    seen = {u.lower() for entry in log for u in entry.get("users", [])}
    skip = seen | logins(f"/users/{ME}/following") | logins(f"/users/{ME}/followers") | {ME.lower()}

    followed = []
    for hit in candidates():
        login = hit["login"]
        if login.lower() in skip:
            continue
        skip.add(login.lower())
        if DRY_RUN:
            print(f"Would follow {login}")
        else:
            status, _ = api("PUT", f"/user/following/{login}")
            if status != 204:
                print(f"Could not follow {login} (HTTP {status})")
                continue
            print(f"Followed {login}")
            time.sleep(random.uniform(4, 10))
        followed.append(login)
        if len(followed) >= DAILY_LIMIT:
            break

    if followed:
        log.append({"date": TODAY.isoformat(), "users": followed})
        save_log(log)
    print(f"{len(followed)} followed today")


def unfollow():
    log = load_log()
    for entry in log:
        if entry.get("processed"):
            continue
        if (TODAY - dt.date.fromisoformat(entry["date"])).days < GRACE_DAYS:
            continue
        back, dropped = [], []
        for login in entry["users"]:
            status, _ = api("GET", f"/users/{login}/following/{ME}")
            if status == 204:
                back.append(login)
                continue
            if DRY_RUN:
                print(f"Would unfollow {login}")
            else:
                api("DELETE", f"/user/following/{login}")
                print(f"Unfollowed {login}")
                time.sleep(random.uniform(2, 5))
            dropped.append(login)
        print(f"{entry['date']}: {len(back)} followed back, {len(dropped)} unfollowed")
        entry.update(processed=True, followed_back=back, unfollowed=dropped)
    save_log(log)


if __name__ == "__main__":
    {"follow": follow, "unfollow": unfollow}[sys.argv[1]]()
