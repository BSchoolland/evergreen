# TACOS Server Access

SSH into TACOS staging and production servers. Both are Ubuntu EC2 instances running the app via PM2 (`tacos-backend` and `tacos-frontend` processes).

## Servers

**Staging:** `ssh tacos-staging` (ec2-3-84-226-41.compute-1.amazonaws.com, key: ~/.ssh/tacos-staging.pem)
- App directory: `/home/ubuntu/TACOS`
- Database: Supabase (remote Postgres). Connection string in `~/TACOS/.env` as `DATABASE_URL`.
- Daily DB backup at 2am via `scripts/backup-db.sh`, logs to `/var/log/tacos-backup.log`
- Deploy: `ssh tacos-staging "bash ~/TACOS/scripts/deploy-staging.sh"` (pulls master, installs, migrates, reloads PM2)

**Production:** `ssh tacos-prod` (ec2-34-231-178-79.compute-1.amazonaws.com, key: ~/.ssh/tacos-prod.pem)
- Same PM2 layout as staging. Scrape workers run on ECS Fargate, not on this box.

## Common Operations

Query staging DB:
```bash
ssh tacos-staging "cd ~/TACOS && psql \"\$(grep '^DATABASE_URL=' .env | cut -d= -f2-)\" -c 'YOUR QUERY'"
```

Check PM2 status: `ssh tacos-staging "pm2 status"`
View logs: `ssh tacos-staging "pm2 logs tacos-backend --lines 100"`
Restart: `ssh tacos-staging "pm2 reload tacos-backend tacos-frontend"`

Replace `tacos-staging` with `tacos-prod` for production. Always check staging first.
