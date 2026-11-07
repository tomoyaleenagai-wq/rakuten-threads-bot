"""Decides, for each scheduled time slot, whether the bot posts in it today.

GitHub runs the workflow at every slot below. Each day a different plan is drawn
(2-5 posts, mostly at lunch and after work, more daytime posts on weekends), so
the number of posts and their times change every day. The plan is seeded by the
date, so every slot of the same day agrees on it. No extra packages needed.

    python3 -m bot.schedule "20 3 * * *"   # prints today's plan, says yes/no for this slot
    python3 -m bot.schedule                # just prints today's plan
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# Slot (JST "HH:MM") -> cron in UTC (must match .github/workflows/post.yml),
# weekday weight, weekend weight. Higher weight = picked more often.
SLOTS = {
    "07:30": ("30 22 * * *", 1, 0),   # morning commute
    "10:30": ("30 1 * * *",  0, 2),   # weekend late morning
    "11:40": ("40 2 * * *",  3, 2),   # lunch
    "12:20": ("20 3 * * *",  5, 3),   # lunch (peak)
    "12:55": ("55 3 * * *",  3, 2),   # lunch
    "15:30": ("30 6 * * *",  1, 3),   # afternoon break / weekend
    "18:10": ("10 9 * * *",  2, 2),   # leaving work
    "18:50": ("50 9 * * *",  4, 3),   # commute home
    "19:30": ("30 10 * * *", 5, 4),   # after work (peak)
    "20:15": ("15 11 * * *", 4, 4),   # after dinner
    "21:20": ("20 12 * * *", 2, 3),   # relaxing at home
    "22:30": ("30 13 * * *", 1, 2),   # before bed
}
LUNCH = ["11:40", "12:20", "12:55"]
EVENING = ["18:10", "18:50", "19:30", "20:15"]

# How many posts a day, and how often: about 3 on average.
POSTS_PER_DAY = {2: 25, 3: 40, 4: 25, 5: 10}
MIN_GAP_MINUTES = 80          # never two posts closer than this



def _minutes(slot: str) -> int:
    h, m = slot.split(":")
    return int(h) * 60 + int(m)


def plan_for(day: datetime) -> list[str]:
    rng = random.Random(f"threads-plan-{day:%Y-%m-%d}")
    weekend = day.weekday() >= 5
    weight = {s: (we if weekend else wd) for s, (_, wd, we) in SLOTS.items()}
    n = rng.choices(list(POSTS_PER_DAY), weights=list(POSTS_PER_DAY.values()))[0]

    picked: list[str] = []

    def ok(s: str) -> bool:
        return weight[s] > 0 and all(abs(_minutes(s) - _minutes(p)) >= MIN_GAP_MINUTES for p in picked)

    def pick_from(pool: list[str]) -> None:
        pool = [s for s in pool if ok(s)]
        if pool and len(picked) < n:
            picked.append(rng.choices(pool, weights=[weight[s] for s in pool])[0])

    if rng.random() < 0.9:          # almost always one lunch post
        pick_from(LUNCH)
    if rng.random() < 0.95:         # almost always one after-work post
        pick_from(EVENING)
    for _ in range(20):             # fill the rest from any slot
        if len(picked) >= n:
            break
        pick_from(list(SLOTS))
    return sorted(picked, key=_minutes)


def slot_for_cron(cron: str) -> str | None:
    cron = " ".join(cron.split())
    return next((s for s, (c, _, _) in SLOTS.items() if c == cron), None)


def main() -> int:
    now = datetime.now(JST)
    plan = plan_for(now)
    print(f"Today's plan ({now:%a %Y-%m-%d} JST): {', '.join(plan)}  ({len(plan)} posts)")
    post = True
    if len(sys.argv) > 1:
        slot = slot_for_cron(sys.argv[1])
        post = slot in plan
        print(f"This slot: {slot or 'unknown cron ' + sys.argv[1]} -> {'POST' if post else 'skip'}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"post={'true' if post else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
