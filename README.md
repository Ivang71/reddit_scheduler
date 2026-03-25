# Reddit Poster

Run everything from `main.py`.

If the profile is missing or logged out, it opens Reddit login first. After that it extracts fresh tokens and schedules posts from `posts.json`.

## Usage

```bash
python3 main.py [profile] [options]
```

## Arguments

- `profile`: profile number, default `0`
- `-c`, `--count`: how many posts to schedule, default `0` = all
- `-i`, `--interval`: minutes between posts, default `15`
- `-s`, `--start-time`: UTC start time in `YYYY-MM-DD HH:MM[:SS]`
- `--login`: force login before extracting tokens and scheduling

## Examples

```bash
python3 main.py 0
python3 main.py 0 -c 10 -i 30
python3 main.py 0 -c 5 -i 60 -s "2026-03-25 12:00:00"
python3 main.py 0 --login
```
