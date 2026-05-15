# TACOS AWS Access

TACOS uses AWS (us-east-1) for infrastructure. The `aws` CLI is available and configured. No IaC (Terraform/CDK) — resources were bootstrapped via shell scripts.

## Services

### S3
- **Image bucket** (`S3_IMAGE_BUCKET`): scraped thumbnails and profile pics. Public read, write-only from app.
- **DB backup bucket** (`BACKUP_S3_BUCKET`): daily/weekly Postgres dumps from `scripts/backup-db.sh`.
- **E2E video bucket** (`tacos-e2e-videos`): Playwright test recordings for PR comments.

### ECS Fargate (scrape workers)
- Cluster: `tacos-dev`
- Task family: `tacos-scrape-worker` (2 vCPU / 4 GB RAM per task)
- ECR repo: `tacos-scrape-worker-dev`
- Max 20 concurrent workers enforced in app code
- Secrets injected from SSM at task definition level, not at runtime
- Logs: CloudWatch `/ecs/tacos-scrape-worker` (14-day retention)

### SSM Parameter Store (secrets)
- Path: `/tacos/{environment}/` (staging, prod)
- Stores: `DATABASE_URL`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `APIFY_API_KEY`, `APIFY_PROXY_PASSWORD`, `FIREWORKS_API_KEY`, `IG_SESSION_COOKIES`
- App loads all params at startup via `GetParametersByPathCommand` with decryption

## Useful CLI Commands

List running Fargate tasks:
```bash
aws ecs list-tasks --cluster tacos-dev --desired-status RUNNING
```

Describe a task:
```bash
aws ecs describe-tasks --cluster tacos-dev --tasks TASK_ARN
```

View worker logs:
```bash
aws logs tail /ecs/tacos-scrape-worker --since 1h
```

Check S3 backup status:
```bash
aws s3 ls s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/ --recursive | tail -5
```

Read an SSM secret:
```bash
aws ssm get-parameter --name "/tacos/staging/DATABASE_URL" --with-decryption --query 'Parameter.Value' --output text
```

List all SSM params for an environment:
```bash
aws ssm get-parameters-by-path --path "/tacos/staging/" --with-decryption
```

## Key Scripts in TACOS

- `scripts/bootstrap-fargate.sh` — one-time AWS resource creation (ECR, ECS cluster, IAM roles, CloudWatch)
- `scripts/deploy-worker.sh` — build + push Docker image to ECR, register new task definition
- `scripts/backup-db.sh` — pg_dump to S3 (runs on cron on staging)
