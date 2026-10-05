# Deploying to Azure (one small server, HTTPS, about 30 minutes)

The whole system runs on one Ubuntu VM with Docker:

```
internet ──443──> Caddy (HTTPS, dashboard) ──/api/*──> backend (FastAPI + agents) <──MQTT── simulator
                                                                │
                                                          Mosquitto (internal only)
```

Only ports 80/443 are public. Viewing is open to anyone with the link; actions that change
something (inject a fault, approve, switch the AI) need the **demo access key** (`DEMO_TOKEN`).

## 1. Create the VM (Azure portal, Azure for Students)

portal.azure.com → **Virtual machines** → **Create** → **Azure virtual machine**

| Field | Value |
| --- | --- |
| Resource group | Create new: `hvac-rg` |
| Virtual machine name | `hvac-vm` |
| Region | **(Asia Pacific) Central India** (if refused, any region your subscription allows) |
| Image | **Ubuntu Server 24.04 LTS - x64 Gen2** |
| Size | **Standard_B2s** (2 vCPU, 4 GB RAM). If not offered: B2ats_v2 or B1ms also work |
| Authentication type | Password; username `azureuser`, a strong password you save |
| Public inbound ports | Allow selected: **SSH (22), HTTP (80), HTTPS (443)** |

Click **Review + create** → **Create**. Wait about 2 minutes → **Go to resource**.

## 2. Give it a name (needed for HTTPS)

On the VM page: **Public IP address** (the blue link) → **Settings → Configuration** →
**DNS name label**: `hvac-ahd` → **Save**. Your address is now
`hvac-ahd.<region>.cloudapp.azure.com` (shown on that page). Copy it.

## 3. Connect and install (one command)

On your laptop (PowerShell):

```powershell
ssh azureuser@hvac-ahd.centralindia.cloudapp.azure.com
```

Type `yes`, then the password. On the server:

```bash
curl -fsSL https://raw.githubusercontent.com/alialtam/hvac-multi-agent/main/deploy/setup.sh -o setup.sh && bash setup.sh
```

It asks for the DNS name (paste it) and the OpenAI key (paste it; it is stored only on the server
in `deploy/.env`, readable only by you). First build: 5-10 minutes. At the end it prints the
**link** and the **demo access key**.

## 4. Check

- Open `https://<your-name>.cloudapp.azure.com`. The padlock shows HTTPS; the top bar says **Live**.
- Click **Unlock controls**, enter the demo key, go to **Simulator**, inject a fault.
- `https://<your-name>.cloudapp.azure.com/api/health` returns `{"ok": true, ...}`.

## 5. Safety nets

- **Budget alert:** portal → **Cost Management** → **Budgets** → **Add**: amount `30` (USD), alerts at
  50 %, 80 %, 100 % to your email. Standard_B2s costs about 1.2 USD per day.
- **Uptime monitor (free):** uptimerobot.com → New monitor → HTTP(s) →
  `https://<your-name>.cloudapp.azure.com/api/health`, every 5 minutes, email alerts.
- After the course is graded: portal → the VM → **Stop**, or delete the resource group `hvac-rg`.

## Updating after new merges

```bash
ssh azureuser@<your-name>.cloudapp.azure.com
bash ~/hvac-multi-agent/deploy/setup.sh
```

## Useful commands (on the server)

```bash
cd ~/hvac-multi-agent
sudo docker compose -f deploy/docker-compose.yml ps                 # what is running
sudo docker compose -f deploy/docker-compose.yml logs -f backend    # backend log (Ctrl+C to leave)
sudo docker compose -f deploy/docker-compose.yml restart simulator  # restart one part
nano deploy/.env    # change settings, then run setup.sh again
```

## Local test of the same setup (no Azure)

`deploy/docker-compose.yml` runs anywhere with Docker. For a laptop, put `SITE_ADDRESS=http://localhost`
in `deploy/.env` (plain HTTP, no certificate).
