# Deploy on Railway — the simple way (no coding, no server, no SSH)

Goal: **Connect GitHub → enter keys → add a volume → Deploy → done.**
One Railway service runs the dashboard and the 4×/day scans together.

## 1. Create the service
1. Go to **railway.com** and log in with GitHub.
2. Click **New Project** → **Deploy from GitHub repo**.
3. Choose the repository **`justtheboisgang/suzuki.ignis.sport.scanner`**.
4. When asked for the branch, pick **`claude/suzuki-ignis-hunter-q5eti6`**
   (or `main` if it has already been merged there).

Railway detects the `Dockerfile` and `railway.toml` automatically — you don't
configure build commands.

## 2. Enter your keys (Variables tab)
Open the service → **Variables** → add these (only the first two really matter
to start):

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `BRAVE_SEARCH_API_KEY` | your Brave Search API key |
| `SEARCH_PROVIDER_BRAVE_ENABLED` | `true` |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` *(optional)* |
| `SERPAPI_API_KEY` | *(optional)* your SerpApi key |
| `SEARCH_PROVIDER_SERPAPI_ENABLED` | `true` *(only if you added SerpApi)* |
| `BRAVE_MONTHLY_REQUEST_BUDGET` | `2000` *(optional)* |
| `ANTHROPIC_MONTHLY_COST_BUDGET` | `25` *(optional)* |
| `NOTIFY_CHANNELS` | `console,database` |

Do **not** set `PORT` — Railway provides it automatically.

## 3. Add the persistent volume (so your car database survives restarts)
1. In the service, open the **Volumes** (or **Storage / Data**) tab.
2. Click **Add Volume**.
3. **Mount path:** `/app/data`
4. Save.

## 4. Deploy
Click **Deploy** (Railway usually starts the first deploy on its own). Wait for
the build and for the status to go **green / Healthy**.

## 5. Open the dashboard
Open the **Settings** tab → **Networking** → **Generate Domain**. Click the
URL — you'll see the Ignis Sport Hunter overview.

## 6. Start the first real scan
On the dashboard homepage click **“🔎 Run discovery”**, then **“▶ Run scan”**.
Both run in the background — wait a minute and refresh the page. From then on
the four daily scans (00:00, 06:00, 12:00, 18:00) run automatically forever.

## How to tell it's working
- Railway shows the service **Healthy** (health check hits `/healthz`).
- The dashboard opens and shows source/coverage numbers.
- After a scan/discovery, **“Last scan …”** on the overview updates.
- Logs are on Railway’s **Deployments → Logs** (look for `full scan` lines).

That's it. If a deploy restarts, everything comes back automatically and your
database is kept on the volume.
