# TACOS AWS Access

The `aws` CLI is configured for TACOS (us-east-1). Services used: S3 (image storage, DB backups), ECS Fargate (scrape workers), ECR (container registry), SSM Parameter Store (secrets at `/tacos/{environment}/`), CloudWatch Logs. Read `.env.example` and `scripts/` in `/home/ben/Projects/TACOS` for current bucket names, cluster names, and deployment commands.
