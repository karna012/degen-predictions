# Deploying Degen Predictions to AWS

This is a step-by-step runbook for hosting the app on a single EC2 instance
behind nginx, with HTTPS and a custom domain via Route 53. Steps marked
**(you)** require your AWS login/credit card and can't be done by an
assistant — everything else (scripts, configs) is already prepared in this
`deploy/` folder.

Estimated cost: ~$5-10/month for a `t3.micro`/`t4g.micro` EC2 instance
(free tier eligible for 12 months on a new account) + ~$12-50/year for the
domain depending on TLD, + ~$0.50/month for the Route 53 hosted zone.

---

## 1. Create an IAM user + CLI credentials (you)

Don't use your root AWS account for daily work. In the AWS Console:

1. Go to **IAM → Users → Create user**. Name it e.g. `degen-deploy`.
2. Attach policy `AdministratorAccess` for now (tighten later once things
   work) or, more safely, just `AmazonEC2FullAccess` + `AmazonRoute53FullAccess`.
3. Create an **access key** for this user (IAM → Users → your user →
   Security credentials → Create access key → "Command Line Interface (CLI)").
4. On your own machine, install the AWS CLI, then run:
   ```
   aws configure
   ```
   and paste in the access key ID / secret when prompted. Region: pick
   whichever is closest to your users (e.g. `us-east-1`).

## 2. Register a domain via Route 53 (you)

1. AWS Console → **Route 53 → Domains → Register domain**.
2. Search for your desired name, add to cart, complete the purchase
   (real money, billed to your AWS account).
3. Registration can take a few minutes to a few hours. Route 53
   automatically creates a **hosted zone** for the domain once it's ready —
   note its Hosted Zone ID, you'll need it in step 6.

## 3. Launch the EC2 instance (you)

1. EC2 Console → **Launch instance**.
2. AMI: **Ubuntu Server 24.04 LTS** (or 22.04).
3. Instance type: `t3.micro` (free-tier eligible) is enough for this app.
4. Key pair: create a new one, download the `.pem` file — this is how
   you'll SSH in. Keep it safe, AWS won't let you re-download it.
5. Network settings → **Edit** → Security group: allow inbound
   - SSH (22) from **My IP** only (not 0.0.0.0/0 — don't expose SSH to the
     whole internet)
   - HTTP (80) from anywhere
   - HTTPS (443) from anywhere
6. Launch. Wait for it to reach "Running".

## 4. Allocate an Elastic IP and attach it (you)

Without this, the instance's public IP changes if it's ever stopped/started,
breaking your DNS record.

1. EC2 Console → **Elastic IPs → Allocate Elastic IP address**.
2. Select it → **Actions → Associate Elastic IP address** → pick your
   instance.
3. Note this IP — you'll point DNS at it in step 6.

## 5. SSH in and clone the repo (you, then me if you paste output back)

```
ssh -i /path/to/your-key.pem ubuntu@<elastic-ip>
git clone <this-repo-url> Degen_predictions
cd Degen_predictions
```

If the repo isn't on GitHub/GitLab yet, `scp` it up instead:
```
scp -i your-key.pem -r ./Degen_predictions ubuntu@<elastic-ip>:~/
```

**Important:** the ML pipeline's output (`data/`, `model/`, `output_tables/`)
are large generated artifacts. Either:
- `scp` those three directories up from your local machine alongside the
  repo (fastest), or
- run the full pipeline fresh on the server:
  ```
  source venv/bin/activate   # after step 6 creates the venv
  python scripts/download_data.py
  python scripts/build_features.py
  python scripts/train_models.py
  python scripts/predict_pl.py
  python scripts/backtest_pl.py
  ```
  (slower, and LightGBM issues don't apply on Linux, so no code changes
  needed there.)

## 6. Run the bootstrap script

```
chmod +x deploy/setup_ec2.sh
./deploy/setup_ec2.sh
```

This installs nginx/gunicorn/certbot, creates the venv, installs
dependencies, generates a random `FLASK_SECRET_KEY`, and starts the app as a
systemd service (`degen-predictions`) reverse-proxied through nginx on port
80.

At this point, `http://<elastic-ip>` should already show the site (no
HTTPS/domain yet).

## 7. Point your domain at the instance (you)

In Route 53 → your hosted zone → **Create record**:
- Record name: leave blank (for the root domain) — create a second record
  with name `www` too
- Record type: `A`
- Value: the Elastic IP from step 4
- TTL: default is fine

DNS propagation is usually fast with Route 53 (minutes), but can take up to
48 hours worldwide.

## 8. Enable HTTPS

Once `http://yourdomain.com` resolves and shows the site:

```
sudo nano /etc/nginx/sites-available/degen-predictions   # replace YOUR_DOMAIN
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Certbot will automatically rewrite the nginx config to redirect HTTP →
HTTPS and set up auto-renewal (Let's Encrypt certs expire every 90 days;
certbot installs a renewal timer automatically — verify with
`sudo systemctl status certbot.timer`).

## 9. Set up the daily prediction refresh

```
crontab -e
```
Add:
```
0 6 * * * /home/ubuntu/Degen_predictions/deploy/daily_predict.sh >> /home/ubuntu/Degen_predictions/daily_predict.log 2>&1
```
This regenerates all 5 leagues' predictions every day at 6 AM server time
(same job the Windows Task Scheduler does locally — see main README).

## 10. Verify everything

- `https://yourdomain.com` loads the site with a valid padlock
- `sudo systemctl status degen-predictions` shows `active (running)`
- `sudo systemctl status nginx` shows `active (running)`
- `curl -I https://yourdomain.com` returns `200 OK`

## Updating the app later

```
ssh -i your-key.pem ubuntu@<elastic-ip>
cd Degen_predictions
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart degen-predictions
```

## Rough monthly cost

| Item | Cost |
|---|---|
| EC2 t3.micro | Free for 12mo (new account), then ~$7/mo |
| Elastic IP (attached to a running instance) | Free |
| Route 53 hosted zone | ~$0.50/mo |
| Domain registration | ~$12-50/yr depending on TLD |
| Data transfer | Usually a few cents/mo at this traffic level |
