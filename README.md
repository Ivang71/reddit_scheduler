# Reddit Poster

Schedule posts to Reddit using extracted profile tokens.

## Usage

```bash
python3 schedule_posts.py [profile] [options]
```

### Arguments

- `profile`: Profile number to use (positional argument, default: `0`).
- `-c`, `--count`: Number of posts to schedule from `posts.json`. Default is `0` (schedules all remaining posts).
- `-i`, `--interval`: Time between each post in minutes. Default is `15`.
- `-s`, `--start-time`: Start time in UTC (format: `YYYY-MM-DD HH:MM:SS`). Default is now + interval.

### Examples

Schedule **all** posts, **15 minutes** apart (default behavior):
```bash
python3 schedule_posts.py 0
```

Schedule **10** posts, **30 minutes** apart:
```bash
python3 schedule_posts.py 0 -c 10 -i 30
```

Schedule **5** posts, **60 minutes** apart, starting at a **specific time**:
```bash
python3 schedule_posts.py 0 -c 5 -i 60 -s "2026-03-25 12:00:00"
```
