import asyncio
import json
import os
import sys

import nodriver as uc
import websockets


BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reddit_user_data")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_profile_dir(profile: str) -> str:
    user_data = os.path.join(BASE_DIR, profile)
    if not os.path.isdir(os.path.join(user_data, "Default")):
        raise SystemExit(f"profile {profile} not found: {user_data}")
    return user_data


def get_cookie_value(cookie_string: str, name: str) -> str:
    prefix = f"{name}="
    for part in cookie_string.split("; "):
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


async def get_reddit_cookies(browser) -> list[str]:
    async with websockets.connect(browser.connection.websocket_url, max_size=2**28) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            message = json.loads(raw)
            if message.get("id") != 1:
                continue
            cookies = message["result"]["cookies"]
            return [
                f"{cookie['name']}={cookie['value']}"
                for cookie in cookies
                if "reddit.com" in cookie["domain"]
            ]


async def extract(profile: str) -> str:
    user_data = get_profile_dir(profile)

    subreddit = None
    accounts_file = os.path.join(OUT_DIR, "accounts.json")
    if os.path.exists(accounts_file):
        with open(accounts_file) as f:
            accounts = json.load(f)
        idx = int(profile)
        if idx < len(accounts):
            subreddit = accounts[idx].get("subreddit")

    print(f"starting browser for profile {profile}", flush=True)
    browser = await asyncio.wait_for(
        uc.start(user_data_dir=user_data, no_sandbox=True, headless=False),
        timeout=20,
    )
    try:
        print("opening reddit", flush=True)
        page = await asyncio.wait_for(
            browser.get("https://www.reddit.com", new_window=True),
            timeout=20,
        )
        await asyncio.wait_for(page.activate(), timeout=10)
        print("waiting for page settle", flush=True)
        await asyncio.sleep(5)
        js = """
            (() => ({
                user_agent: navigator.userAgent,
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
        print("evaluating page data", flush=True)
        page_data = await asyncio.wait_for(page.evaluate(js), timeout=15)
        if isinstance(page_data, list) and page_data:
            page_data = page_data[0]
        if not isinstance(page_data, dict):
            page_data = {}
        print("reading cookies", flush=True)
        reddit_cookies = await get_reddit_cookies(browser)
        cookie_string = "; ".join(reddit_cookies)

        subreddit_id = ""
        if subreddit:
            print(f"fetching t5_ id for r/{subreddit}", flush=True)
            js_fetch = f"fetch('/r/{subreddit}/about.json').then(r => r.json()).then(d => d.data.name).catch(e => e.toString())"
            subreddit_id = await asyncio.wait_for(page.evaluate(js_fetch, await_promise=True), timeout=10)
            print(f"subreddit_id: {subreddit_id}", flush=True)

        _, _, _, user_agent, _ = await asyncio.wait_for(
            browser.connection.send(uc.cdp.browser.get_version()),
            timeout=10,
        )
        output = {
            "profile": profile,
            "subreddit": subreddit,
            "subreddit_id": subreddit_id,
            "user_agent": user_agent,
            "csrf_token": page_data.get("csrf_token", "") or get_cookie_value(cookie_string, "csrf_token"),
            "client_version": page_data.get("client_version", ""),
            "cookie_string": cookie_string,
            "document_cookie": page_data.get("document_cookie", ""),
        }
        out_file = os.path.join(OUT_DIR, f"tokens_{profile}.json")
        with open(out_file, "w") as f:
            json.dump(output, f, indent=2)
        print(f"saved tokens to {out_file}", flush=True)
        return out_file
    finally:
        browser.stop()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 extract_tokens.py <profile_number>")
    try:
        out_file = uc.loop().run_until_complete(extract(sys.argv[1]))
    except KeyboardInterrupt:
        return
    print(f"saved tokens to {out_file}")


if __name__ == "__main__":
    main()
