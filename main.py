import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import nodriver as uc
import requests
import websockets


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "reddit_user_data")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Login if needed, extract tokens, and schedule Reddit posts.")
    parser.add_argument("profile", nargs="?", default="0", help="profile number")
    parser.add_argument("-c", "--count", type=int, default=0, help="posts to schedule, 0 means all")
    parser.add_argument("-i", "--interval", type=int, default=15, help="minutes between posts")
    parser.add_argument("-s", "--start-time", help="UTC start time: YYYY-MM-DD HH:MM[:SS] or YYYY-MM-DDTHH:MM:SS")
    parser.add_argument("--login", action="store_true", help="force login before scheduling")
    return parser.parse_args()


def profile_dir(profile: str) -> str:
    return os.path.join(DATA_DIR, profile)


def token_path(profile: str) -> str:
    return os.path.join(ROOT, f"tokens_{profile}.json")


def parse_start_time(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise SystemExit("invalid start time, use YYYY-MM-DD HH:MM[:SS]")


def text_to_richtext(text: str) -> str:
    document = []
    for part in text.split("\n"):
        if part.strip():
            document.append({"e": "par", "c": [{"e": "text", "t": part}]})
    return json.dumps({"document": document})


def cookie_value(cookie_string: str, name: str) -> str:
    prefix = f"{name}="
    for part in cookie_string.split("; "):
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


def load_account(profile: str) -> dict:
    path = os.path.join(ROOT, "accounts.json")
    with open(path) as f:
        accounts = json.load(f)
    idx = int(profile)
    if idx >= len(accounts):
        raise SystemExit(f"no account entry for profile {profile} in accounts.json")
    return accounts[idx]


def load_posts() -> list[dict]:
    with open(os.path.join(ROOT, "posts.json")) as f:
        return json.load(f)


def save_posts(posts: list[dict]) -> None:
    with open(os.path.join(ROOT, "posts.json"), "w") as f:
        json.dump(posts, f, indent=2)


def is_logged_in(tokens: dict) -> bool:
    cookie_string = tokens.get("cookie_string", "")
    return "reddit_session=" in cookie_string and "token_v2=" in cookie_string


async def open_login(profile: str) -> None:
    user_data = profile_dir(profile)
    os.makedirs(user_data, exist_ok=True)
    browser = await uc.start(user_data_dir=user_data, no_sandbox=True, headless=False)
    try:
        page = await browser.get("https://www.reddit.com/login", new_window=True)
        await page.activate()
        print(f"profile: {profile}")
        print("log in, then close the browser window")
        while not browser.stopped:
            await asyncio.sleep(1)
    finally:
        browser.stop()


async def get_reddit_cookies(browser) -> list[str]:
    async with websockets.connect(browser.connection.websocket_url, max_size=2**28) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            message = json.loads(raw)
            if message.get("id") != 1:
                continue
            return [
                f"{cookie['name']}={cookie['value']}"
                for cookie in message["result"]["cookies"]
                if "reddit.com" in cookie["domain"]
            ]


async def extract_tokens(profile: str, subreddit: str) -> dict:
    browser = await asyncio.wait_for(
        uc.start(user_data_dir=profile_dir(profile), no_sandbox=True, headless=False),
        timeout=20,
    )
    try:
        print("opening reddit", flush=True)
        page = await asyncio.wait_for(
            browser.get("https://www.reddit.com", new_window=True),
            timeout=20,
        )
        await asyncio.wait_for(page.activate(), timeout=10)
        await asyncio.sleep(5)
        page_data = await asyncio.wait_for(
            page.evaluate(
                """
                (() => ({
                    document_cookie: document.cookie,
                    csrf_token: document.cookie
                        .split("; ")
                        .find(v => v.startsWith("csrf_token="))
                        ?.split("=")[1] || "",
                    client_version:
                        window.___r?.config?.clientVersion ||
                        window.___r?.config?.version ||
                        ""
                }))()
                """
            ),
            timeout=15,
        )
        if isinstance(page_data, list) and page_data:
            page_data = page_data[0]
        if not isinstance(page_data, dict):
            page_data = {}
        cookie_string = "; ".join(await get_reddit_cookies(browser))
        subreddit_id = await asyncio.wait_for(
            page.evaluate(
                f"fetch('/r/{subreddit}/about.json').then(r => r.json()).then(d => d.data.name)",
                await_promise=True,
            ),
            timeout=10,
        )
        _, _, _, user_agent, _ = await asyncio.wait_for(
            browser.connection.send(uc.cdp.browser.get_version()),
            timeout=10,
        )
        tokens = {
            "profile": profile,
            "subreddit": subreddit,
            "subreddit_id": subreddit_id,
            "user_agent": user_agent,
            "csrf_token": page_data.get("csrf_token") or cookie_value(cookie_string, "csrf_token"),
            "client_version": page_data.get("client_version", ""),
            "cookie_string": cookie_string,
            "document_cookie": page_data.get("document_cookie", ""),
        }
        with open(token_path(profile), "w") as f:
            json.dump(tokens, f, indent=2)
        return tokens
    finally:
        browser.stop()


def schedule_posts(profile: str, tokens: dict, count: int, interval: int, start_time: str | None) -> None:
    posts = load_posts()
    if not posts:
        print("no posts left in posts.json")
        return
    if count < 0:
        raise SystemExit("count must be 0 or greater")
    if interval <= 0:
        raise SystemExit("interval must be greater than 0")
    selected = posts[:] if count == 0 else posts[:count]
    if not selected:
        print("nothing to schedule")
        return
    start_dt = parse_start_time(start_time) if start_time else datetime.now(timezone.utc) + timedelta(minutes=interval)
    headers = {
        "User-Agent": tokens["user_agent"],
        "Accept": "text/vnd.reddit.partial+html, application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.reddit.com",
        "Referer": f"https://www.reddit.com/r/{tokens['subreddit']}/submit/",
        "Cookie": tokens["cookie_string"],
        "x-reddit-client-version": tokens.get("client_version") or "2026-03-24T12:00Z~1223a43b",
    }
    url = "https://www.reddit.com/svc/shreddit/graphql"
    print(f"found {len(posts)} posts, scheduling {len(selected)}")
    for idx, post in enumerate(selected):
        publish_at = (start_dt + timedelta(minutes=interval * idx)).strftime("%Y-%m-%dT%H:%M:%S")
        payload = {
            "operation": "CreateScheduledPost",
            "variables": {
                "input": {
                    "isNsfw": False,
                    "isSpoiler": False,
                    "content": {"richText": text_to_richtext(post["body"])},
                    "title": post["title"],
                    "subredditId": tokens["subreddit_id"],
                    "sticky": "NONE",
                    "isContestMode": False,
                    "isPostAsMetaMod": False,
                    "suggestedCommentSort": "BLANK",
                    "scheduling": {
                        "publishAt": publish_at,
                        "clientTimezone": "UTC",
                    },
                    "assetIds": [],
                    "creationToken": str(uuid.uuid4()),
                }
            },
            "csrf_token": tokens["csrf_token"],
        }
        print(f"[{idx + 1}/{len(selected)}] {post['title']}")
        response = requests.post(url, headers=headers, json=payload)
        ok = False
        if response.status_code == 200:
            try:
                ok = bool(response.json().get("data", {}).get("createScheduledPost", {}).get("ok"))
            except json.JSONDecodeError:
                ok = False
        if not ok:
            print(response.text)
            print("stopping on failure")
            return
        posts.remove(post)
        save_posts(posts)
        print(f"scheduled for {publish_at} UTC")
        if idx < len(selected) - 1:
            time.sleep(2.5)
    print(f"done, {len(posts)} posts remaining")


def main() -> None:
    args = parse_args()
    account = load_account(args.profile)
    needs_login = args.login or not os.path.isdir(os.path.join(profile_dir(args.profile), "Default"))
    if needs_login:
        uc.loop().run_until_complete(open_login(args.profile))
    tokens = uc.loop().run_until_complete(extract_tokens(args.profile, account["subreddit"]))
    if not is_logged_in(tokens):
        uc.loop().run_until_complete(open_login(args.profile))
        tokens = uc.loop().run_until_complete(extract_tokens(args.profile, account["subreddit"]))
    if not is_logged_in(tokens):
        raise SystemExit("login missing or expired")
    schedule_posts(args.profile, tokens, args.count, args.interval, args.start_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
