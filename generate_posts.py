import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import nodriver as uc
import requests
import websockets


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "gemini_user_data")
APP_URL = "https://gemini.google.com/app"
POST_URL = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"


def status(message: str) -> None:
    print(f"[gemini] {message}", flush=True)
PROMPT = """Act as an expert social media copywriter. Generate a JSON array of 50 high-conversion, organic-looking promotional posts (Reddit/Twitter/TikTok style) for a free streaming website. 

The output MUST be strictly valid JSON containing an array of objects, with each object having a "title" and a "body". 

**Content Strategy & Mix:**
- Create a 50/50 mix of specific trending movies/shows (highly anticipated or currently trending releases) AND generic high-volume search categories (e.g., "where to watch K-dramas," "free movie site for kids," "where to watch true crime," "no credit card required," "iPad compatible").
- Hit hard on real user pain points: high VOD rental fees ($20+), "household" account-sharing blocks, intrusive/unskippable ads, region/geo-blocking, terrible cam rip quality, confusing user interfaces, and subscription fatigue.

**Post Structure:**
1. **"title"**: Short, catchy, and highly searchable. Frequently use the format "Where to watch [Movie/Show/Genre] for free?" or similar simple requests.
2. **"body"**: A two-part structure. 
   - Part 1 (The Vent): A short paragraph authentically complaining about a specific streaming barrier (price, ads, buffering, etc.). Use an organic, frustrated, conversational tone. Do not use salesy or corporate language.
   - Part 2 (The Solve): Always separated by a double line break and starting with "EDIT:". Briefly state that the problem was solved because a mutual/commenter/friend recommended a site. 
   
**Link Rules:**
- You MUST format the link exactly as: https://cine.su
- NEVER put a period, comma, or any punctuation directly after the URL, as it breaks the hyperlink formatting on sites like Reddit. 

**Example JSON format:**
[
  {
    "title": "Where to watch Project Hail Mary for free? ($20 VOD is a joke)",
    "body": "I desperately want to watch Ryan Gosling in Project Hail Mary this weekend, but Amazon is asking for a $20 rental fee on top of my monthly Prime subscription. I'm not dropping that kind of money for a 48-hour rental just to watch a sci-fi movie on my couch. Does anyone have a clean HD link?\n\nEDIT: Found the full 1080p web rip on https://cine.su so I'm watching it there tonight"
  }
]"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Login if needed, extract Gemini tokens, and generate posts.")
    parser.add_argument("profile", nargs="?", default="0", help="profile number")
    parser.add_argument("--login", action="store_true", help="force login before generating")
    return parser.parse_args()


def build_prompt(batch_number: int) -> str:
    return (
        PROMPT
        + f"\n\nGenerate a fresh batch of 50 posts for batch {batch_number}. Avoid reusing the exact same ideas or titles from other batches."
    )


def profile_dir(profile: str) -> str:
    return os.path.join(DATA_DIR, profile)


def token_path(profile: str) -> str:
    return os.path.join(ROOT, f"gemini_tokens_{profile}.json")


def cleanup_profile(profile: str) -> None:
    user_data = profile_dir(profile)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = os.path.join(user_data, name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    subprocess.run(
        ["pkill", "-f", user_data],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def is_post_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, dict) and isinstance(item.get("title"), str) and isinstance(item.get("body"), str)
        for item in value
    )


def parse_posts_json(text: str) -> list[dict]:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1)
    parsed = json.loads(text)
    if not is_post_list(parsed):
        raise ValueError("response was not a posts array")
    return parsed


def find_posts_payload(value):
    if isinstance(value, str):
        try:
            return parse_posts_json(value)
        except Exception:
            pass
        try:
            decoded = json.loads(value)
        except Exception:
            return None
        if decoded != value:
            return find_posts_payload(decoded)
        return None
    if isinstance(value, list):
        if is_post_list(value):
            return value
        for item in value:
            found = find_posts_payload(item)
            if found is not None:
                return found
        return None
    if isinstance(value, dict):
        for item in value.values():
            found = find_posts_payload(item)
            if found is not None:
                return found
    return None


def parse_response(raw: str) -> list[dict]:
    text = raw.lstrip()
    if text.startswith(")]}'"):
        text = text[4:].lstrip()
    candidates = [text]
    candidates.extend(line.strip() for line in text.splitlines() if line.strip().startswith("["))
    for candidate in candidates:
        found = find_posts_payload(candidate)
        if found is not None:
            return found
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        found = find_posts_payload(parsed)
        if found is not None:
            return found
    raise ValueError(f"could not parse Gemini response: {text[:400]}")


async def start_browser(profile: str):
    user_data = profile_dir(profile)
    os.makedirs(user_data, exist_ok=True)
    last_error = None
    status(f"starting browser for profile {profile}")
    for attempt in range(2):
        try:
            browser = await asyncio.wait_for(
                uc.start(user_data_dir=user_data, no_sandbox=True, headless=False),
                timeout=20,
            )
            status("browser started")
            return browser
        except Exception as e:
            last_error = e
            if attempt == 0:
                status("browser start failed, cleaning profile lock files")
                cleanup_profile(profile)
                await asyncio.sleep(1)
    raise last_error


async def get_google_cookies(browser) -> list[str]:
    status("getting browser cookies")
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
                if "google.com" in cookie["domain"] or "gemini.google.com" in cookie["domain"]
            ]


async def get_page_data(page) -> dict:
    status("reading Gemini page state")
    state = await asyncio.wait_for(
        page.evaluate(
            """
            JSON.stringify((() => ({
                url: location.href,
                document_cookie: document.cookie,
                snlm: document.documentElement.innerHTML.match(/[\"']SNlM0e[\"']\\s*:\\s*[\"'](.*?)[\"']/)?.[1] || "",
                bl: document.documentElement.innerHTML.match(/boq_assistant-bard-web-server_[^\"']+/)?.[0] || ""
            }))())
            """,
            return_by_value=True,
        ),
        timeout=15,
    )
    if isinstance(state, list) and state and isinstance(state[0], str):
        state = state[0]
    if isinstance(state, str):
        state = json.loads(state)
    if not isinstance(state, dict):
        state = {}
    return state


def is_logged_in(tokens: dict) -> bool:
    cookie_string = tokens.get("cookie_string", "")
    return "__Secure-1PSID=" in cookie_string and bool(tokens.get("snlm")) and bool(tokens.get("bl"))


async def open_login(profile: str) -> None:
    browser = await start_browser(profile)
    try:
        status("opening Gemini login page")
        page = await browser.get(APP_URL, new_window=True)
        await page.activate()
        print(f"profile: {profile}")
        print("log into Gemini, browser will close automatically")
        status("waiting for login to complete")
        for _ in range(300):
            await asyncio.sleep(1)
            page_data = await get_page_data(page)
            cookie_string = "; ".join(await get_google_cookies(browser))
            if is_logged_in({
                "cookie_string": cookie_string,
                "snlm": page_data.get("snlm", ""),
                "bl": page_data.get("bl", ""),
            }):
                status("login detected")
                return
        raise SystemExit("login missing or expired")
    finally:
        status("closing browser")
        browser.stop()


async def extract_tokens(profile: str) -> dict:
    browser = await start_browser(profile)
    try:
        status("extracting Gemini tokens")
        page = await asyncio.wait_for(browser.get(APP_URL, new_window=True), timeout=20)
        await asyncio.wait_for(page.activate(), timeout=10)
        await asyncio.sleep(5)
        page_data = await get_page_data(page)
        cookie_string = "; ".join(await get_google_cookies(browser))
        status("got page state and cookies")
        _, _, _, user_agent, _ = await asyncio.wait_for(
            browser.connection.send(uc.cdp.browser.get_version()),
            timeout=10,
        )
        tokens = {
            "profile": profile,
            "user_agent": user_agent,
            "cookie_string": cookie_string,
            "document_cookie": page_data.get("document_cookie", ""),
            "snlm": page_data.get("snlm", ""),
            "bl": page_data.get("bl", ""),
            "url": page_data.get("url", ""),
        }
        with open(token_path(profile), "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        status(f"saved tokens to {os.path.basename(token_path(profile))}")
        return tokens
    finally:
        status("closing browser")
        browser.stop()


def generate_posts(tokens: dict, batch_number: int) -> list[dict]:
    headers = {
        "User-Agent": tokens["user_agent"],
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Cookie": tokens["cookie_string"],
        "X-Same-Domain": "1",
    }
    data = {
        "f.req": json.dumps([None, json.dumps([[build_prompt(batch_number)], None, ["", "", ""]], ensure_ascii=False)], ensure_ascii=False),
        "at": tokens["snlm"],
    }
    last_error = None
    for attempt in range(1, 4):
        status(f"building Gemini request for batch {batch_number} attempt {attempt}")
        params = {
            "bl": tokens["bl"],
            "_reqid": str(random.randint(100000, 999999)),
            "rt": "c",
        }
        status(f"sending prompt to Gemini for batch {batch_number} attempt {attempt}")
        response = requests.post(POST_URL, headers=headers, params=params, data=data, timeout=120)
        status(f"Gemini batch {batch_number} attempt {attempt} responded with status {response.status_code}")
        if response.status_code != 200:
            last_error = RuntimeError(response.text[:1000])
        else:
            try:
                status(f"parsing generated posts for batch {batch_number} attempt {attempt}")
                posts = parse_response(response.text)
                status(f"parsed {len(posts)} posts for batch {batch_number} on attempt {attempt}")
                return posts
            except ValueError as e:
                last_error = e
                snippet = response.text[:200].replace("\n", " ")
                status(f"batch {batch_number} attempt {attempt} returned no usable payload: {snippet}")
        if attempt < 3:
            delay = 5 * attempt
            status(f"retrying batch {batch_number} in {delay} seconds")
            time.sleep(delay)
    raise last_error


def generate_posts_parallel(tokens: dict) -> list[dict]:
    status("starting batch 1")
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_1 = executor.submit(generate_posts, tokens, 1)
        status("waiting 2.5 seconds before starting batch 2")
        time.sleep(2.5)
        status("starting batch 2")
        future_2 = executor.submit(generate_posts, tokens, 2)
        posts_1 = future_1.result()
        status(f"batch 1 complete with {len(posts_1)} posts")
        posts_2 = future_2.result()
        status(f"batch 2 complete with {len(posts_2)} posts")
    combined = posts_1 + posts_2
    status(f"combined total: {len(combined)} posts")
    return combined


def run(profile: str = "0", force_login: bool = False) -> list[dict]:
    status("starting post generation")
    needs_login = force_login or not os.path.isdir(os.path.join(profile_dir(profile), "Default"))
    if needs_login:
        status("login required")
        uc.loop().run_until_complete(open_login(profile))
    else:
        status("using existing browser profile")
    tokens = uc.loop().run_until_complete(extract_tokens(profile))
    if not is_logged_in(tokens):
        status("tokens incomplete, reopening login")
        uc.loop().run_until_complete(open_login(profile))
        tokens = uc.loop().run_until_complete(extract_tokens(profile))
    if not is_logged_in(tokens):
        raise SystemExit("login missing or expired")
    posts = generate_posts_parallel(tokens)
    status("writing combined posts.json")
    with open(os.path.join(ROOT, "posts.json"), "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    status(f"saved {len(posts)} posts to posts.json")
    return posts


def main() -> None:
    args = parse_args()
    run(args.profile, args.login)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
