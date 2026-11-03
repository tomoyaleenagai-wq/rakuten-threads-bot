# Rakuten Travel → Threads auto-poster

Five times a day this bot:

1. **Finds a listing:** searches Rakuten Travel with a random keyword from `config.yaml`, or pulls from Rakuten's onsen and premium rankings. It keeps only places rated **4.2★ or higher with at least 50 reviews** that it hasn't posted in the last 120 days.
2. **Writes the post with Claude, in your format:**
   - **Main post:** a one-line hook (e.g. `伊勢志摩に行くなら、ここは絶対泊まらないで、、`) plus the listing photos as a carousel.
   - **Your own reply:** `宿名(県・エリア)`, then 2–3 sentences on why it's good, then access, then `口コミ4.22(307件)。` and your affiliate link with `#PR`.
   Claude is told to use **only facts from Rakuten's data**. The review score and link are filled in by code, not by the model.
3. **Posts to Threads** through the official API, then replies to itself.

It runs free on GitHub Actions. You don't need a server or a computer left on.

---

## One-time setup (about 30–40 min)

### 1. Rakuten Web Service (finds hotels and gives you the affiliate link)
1. Sign in at <https://webservice.rakuten.co.jp/> → **アプリID発行** (register an app).
2. Copy your **アプリID (applicationId)** and **アクセスキー (accessKey)**.
3. Copy your **アフィリエイトID** (shown on the same site, top right / affiliate page).
4. When registering the app:
   - **Application name:** anything, e.g. `Threads Travel Bot`.
   - **Application URL:** your Threads profile, e.g. `https://www.threads.com/@otokutabihanta`.
   - **Application type:** choose the server / backend option if there is one. If there's only a website option, choose that.
   - **Allowed websites:** your GitHub repo URL, e.g. `https://github.com/YOURNAME/rakuten-threads-bot`. Then in GitHub go to **Settings → Secrets and variables → Actions → Variables** and add a variable `RAKUTEN_ALLOWED_SITE` with that **same URL**. The bot sends it with every request so Rakuten accepts them.
   - **Allowed IP addresses (if asked):** leave it blank, or enter `0.0.0.0/0`. GitHub's servers don't have a fixed IP.

Links come back as `hb.afl.rakuten.co.jp/...` affiliate URLs. They earn the same commission as your `a.r10.to` short links.

### 2. Threads API (lets the bot post as you)
1. Go to <https://developers.facebook.com/apps> → **Create app** → choose the use case **"Access the Threads API"**.
2. Under **Use cases → Threads → Permissions**, add `threads_basic`, `threads_content_publish` and `threads_manage_replies`.
3. Under **App roles → Roles → Add people → Threads Tester**, add your account (`otokutabihanta`). In the Threads app, accept the invite: **Settings → Account → Website permissions → Invites**.
   *You don't need App Review, because the app only posts to your own account.*
4. In **Use cases → Threads → Settings**, use the **User Token Generator** to create a token for your account. Then turn it into a 60-day token by opening this URL in your browser:
   `https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=APP_SECRET&access_token=SHORT_TOKEN`
5. Get your user ID: `https://graph.threads.net/v1.0/me?fields=id,username&access_token=LONG_TOKEN`

### 3. Anthropic API key (writes the text)
Create a key at <https://console.anthropic.com/>. Each post is one short request, so 150 posts a month costs very little.

### 4. GitHub
1. Create a new repository and upload everything in this folder, including the hidden `.github` folder.
   **Make it public.** Public repos get unlimited Actions minutes, and if a Rakuten photo has to be resized the bot serves it from the repo. Your keys go in Secrets, which are never public.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
|---|---|
| `RAKUTEN_APP_ID` | アプリID |
| `RAKUTEN_ACCESS_KEY` | アクセスキー |
| `RAKUTEN_AFFILIATE_ID` | アフィリエイトID |
| `ANTHROPIC_API_KEY` | from step 3 |
| `THREADS_ACCESS_TOKEN` | the 60-day token |
| `THREADS_USER_ID` | the id from step 2.5 |
| `GH_PAT` | a [fine-grained token](https://github.com/settings/personal-access-tokens/new) for this repo with **Secrets: Read and write**. It lets the weekly job renew your Threads token so it never expires. |

3. Do a test run: **Actions → Post to Threads → Run workflow**, leaving **"Preview only"** ticked. Open the run and you'll see exactly what it would post. Untick the box and run again to post for real.

After that it posts on its own at about **7:40, 12:10, 17:50, 20:20 and 22:40 JST**. Each post lands 0–15 minutes after the listed time so it doesn't look robotic.

---

## Day-to-day
- **Change destinations or themes:** edit `search_keywords` in `config.yaml`.
- **Change posting times:** edit the `cron` lines in `.github/workflows/post.yml`. They're in UTC, which is JST minus 9 hours.
- **Pause:** **Actions → Post to Threads → ⋯ → Disable workflow**.
- **See what was posted:** `data/posted.json`, or each run's summary page.
- If a run fails, GitHub emails you. If the main post went up but the reply failed, the summary tells you to add the link by hand.

## Limits to know
- Rakuten's API gives **2 photos per hotel** (main photo + room photo), so carousels have 2 images instead of 3.
- Threads limits text length. If the reply is too long, the bot drops the last sentence and tries again.
- Threads allows 250 posts a day; 5 is nowhere near that.

## Files
```
bot/rakuten.py    Rakuten Travel search (KeywordHotelSearch / SimpleHotelSearch 2026-07-31, ranking)
bot/writer.py     Claude prompt + your post format
bot/images.py     photo checks / resizing for Threads
bot/threads.py    Threads carousel + reply
bot/main.py       one run, start to finish
config.yaml       keywords, rating filters, model
tests/            offline tests (pytest)
```
