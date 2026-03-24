import asyncio
import os
import sys

import nodriver as uc


BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reddit_user_data")


def get_args() -> tuple[str, str]:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ./run.sh <profile_number|--login>")
    arg = sys.argv[1]
    if arg == "--login":
        os.makedirs(BASE_DIR, exist_ok=True)
        nums = [int(name) for name in os.listdir(BASE_DIR) if name.isdigit()]
        return "login", str(max(nums, default=-1) + 1)
    if arg.isdigit():
        return "open", arg
    raise SystemExit("usage: ./run.sh <profile_number|--login>")


async def main() -> None:
    mode, profile = get_args()
    user_data = os.path.join(BASE_DIR, profile)
    if mode == "open" and not os.path.isdir(os.path.join(user_data, "Default")):
        raise SystemExit(f"profile {profile} not found: {user_data}")

    browser = await uc.start(user_data_dir=user_data, no_sandbox=True)
    try:
        url = "https://www.reddit.com/login" if mode == "login" else "https://www.reddit.com"
        await browser.get(url)
        print(f"profile: {profile}")
        print(f"path: {user_data}")
        while not browser.stopped:
            await asyncio.sleep(1)
    finally:
        browser.stop()


if __name__ == "__main__":
    try:
        uc.loop().run_until_complete(main())
    except KeyboardInterrupt:
        pass
