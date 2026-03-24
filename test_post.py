import json
import uuid
import requests
from datetime import datetime, timedelta, timezone

def test_post(profile_num="0", subreddit="u_abq"):
    with open(f"tokens_{profile_num}.json") as f:
        tokens = json.load(f)
        
    url = "https://www.reddit.com/svc/shreddit/graphql"
    
    headers = {
        "User-Agent": tokens["user_agent"],
        "Accept": "text/vnd.reddit.partial+html, application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.reddit.com",
        "Referer": f"https://www.reddit.com/user/abq/submit/",
        "Cookie": tokens["cookie_string"],
        "x-reddit-client-version": tokens.get("client_version", "2026-03-24T12:00Z~1223a43b")
    }
    
    # We need a unique creation token for each post
    creation_token = str(uuid.uuid4())
    
    # Schedule for 1 hour from now
    publish_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    
    payload = {
        "operation": "CreateScheduledPost",
        "variables": {
            "input": {
                "isNsfw": False,
                "isSpoiler": False,
                "content": {
                    "richText": json.dumps({
                        "document": [{
                            "e": "par",
                            "c": [{
                                "e": "text",
                                "t": "Just testing the automated scheduler for my nail polish salon!"
                            }]
                        }]
                    })
                },
                "title": "Nail Salon Test Post",
                "subredditId": tokens.get("subreddit_id", "t5_2qgta"),
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
    
    print("Sending request...")
    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

if __name__ == "__main__":
    test_post()
