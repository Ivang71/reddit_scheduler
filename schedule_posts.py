import argparse
import json
import uuid
import requests
import time
import sys
from datetime import datetime, timedelta, timezone

def text_to_richtext(text):
    paragraphs = text.split('\n')
    document = []
    for p in paragraphs:
        if p.strip() == '':
            continue
        document.append({
            "e": "par",
            "c": [{"e": "text", "t": p}]
        })
    return json.dumps({"document": document})

def parse_start_time(time_str):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"Invalid time format: {time_str}. Use YYYY-MM-DD HH:MM:SS")

def main():
    parser = argparse.ArgumentParser(description="Schedule Reddit posts.")
    parser.add_argument("profile", nargs="?", default="0", help="Profile number (default: 0)")
    parser.add_argument("-c", "--count", type=int, default=0, help="Number of posts to schedule (default: 0, meaning all remaining)")
    parser.add_argument("-i", "--interval", type=int, default=15, help="Interval between posts in minutes (default: 15)")
    parser.add_argument("-s", "--start-time", type=str, help="Start time in YYYY-MM-DD HH:MM:SS format (UTC). Default is now + interval.")
    args = parser.parse_args()

    profile_num = args.profile

    # Load tokens
    try:
        with open(f"tokens_{profile_num}.json") as f:
            tokens = json.load(f)
    except FileNotFoundError:
        print(f"Error: tokens_{profile_num}.json not found. Run extract_tokens.py first.")
        return

    subreddit_id = tokens.get("subreddit_id")
    if not subreddit_id:
        print("Error: subreddit_id not found in tokens. Please make sure accounts.json is set up and extract_tokens.py was run.")
        return

    # Load posts
    try:
        with open("posts.json", "r") as f:
            posts = json.load(f)
    except FileNotFoundError:
        print("Error: posts.json not found.")
        return

    if not posts:
        print("No posts left in posts.json!")
        return

    url = "https://www.reddit.com/svc/shreddit/graphql"
    
    headers = {
        "User-Agent": tokens["user_agent"],
        "Accept": "text/vnd.reddit.partial+html, application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.reddit.com",
        "Referer": f"https://www.reddit.com/r/{tokens.get('subreddit', '')}/submit/",
        "Cookie": tokens["cookie_string"],
        "x-reddit-client-version": tokens.get("client_version", "2026-03-24T12:00Z~1223a43b")
    }

    if args.count > 0:
        posts_to_schedule = posts[:args.count]
    else:
        posts_to_schedule = posts

    if args.start_time:
        try:
            start_dt = parse_start_time(args.start_time)
        except ValueError as e:
            print(e)
            return
    else:
        # Default to starting after the first interval
        start_dt = datetime.now(timezone.utc) + timedelta(minutes=args.interval)
    
    print(f"Found {len(posts)} posts. Scheduling {len(posts_to_schedule)} posts...")

    for i, post in enumerate(posts_to_schedule):
        title = post["title"]
        body = post["body"]
        
        creation_token = str(uuid.uuid4())
        publish_dt = start_dt + timedelta(minutes=args.interval * i)
        publish_at = publish_dt.strftime("%Y-%m-%dT%H:%M:%S")
        
        payload = {
            "operation": "CreateScheduledPost",
            "variables": {
                "input": {
                    "isNsfw": False,
                    "isSpoiler": False,
                    "content": {
                        "richText": text_to_richtext(body)
                    },
                    "title": title,
                    "subredditId": subreddit_id,
                    "sticky": "NONE",
                    "isContestMode": False,
                    "isPostAsMetaMod": False,
                    "suggestedCommentSort": "BLANK",
                    "scheduling": {
                        "publishAt": publish_at,
                        "clientTimezone": "UTC"
                    },
                    "assetIds": [],
                    "creationToken": creation_token
                }
            },
            "csrf_token": tokens["csrf_token"]
        }
        
        print(f"[{i+1}/{len(posts_to_schedule)}] Scheduling: '{title}' for {publish_at} UTC...")
        response = requests.post(url, headers=headers, json=payload)
        
        success = False
        if response.status_code == 200:
            try:
                resp_data = response.json()
                if resp_data.get("data", {}).get("createScheduledPost", {}).get("ok"):
                    success = True
                    print("  -> Success!")
                else:
                    print(f"  -> Failed: {response.text}")
            except json.JSONDecodeError:
                print(f"  -> Failed to parse response: {response.text}")
        else:
            print(f"  -> HTTP Error {response.status_code}: {response.text}")
            
        if success:
            # Remove the post from the main list
            posts.remove(post)
            # Save immediately so we don't lose progress if the script crashes
            with open("posts.json", "w") as f:
                json.dump(posts, f, indent=2)
        else:
            print("  -> Stopping due to error.")
            break
            
        # Delay 2.5 seconds between requests as requested
        if i < len(posts_to_schedule) - 1:
            time.sleep(2.5)

    print(f"\nDone! {len(posts)} posts remaining in posts.json.")

if __name__ == "__main__":
    main()
