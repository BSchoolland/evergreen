# TACOS Dependency Audit

Run `cd ../TACOS && bun audit` to check for known vulnerabilities in TACOS dependencies, then assess real risk and record findings.

## Stack context

TACOS is a Node.js/Express/Next.js app on AWS EC2 (Ubuntu). Key dependencies: Prisma, Playwright, bcryptjs, dotenv, TypeScript. Scraper runs on Fargate via apify-client. Database is Postgres via Supabase.

## Steps

1. Run `cd ../TACOS && bun audit` and review the output.

2. For each critical or high vulnerability, determine whether it's in a production dependency or only in devDependencies. Check the dependency chain — a vuln in a transitive dep of `vitest` is very different from one in `express`.

3. For anything that looks plausibly risky, read the TACOS codebase to understand whether the vulnerable code path is actually reachable. For example, a path traversal in `basic-ftp` only matters if TACOS actually calls `downloadToDir()`. Use `/server-access-evergreen` to check server state if relevant.

4. Record findings with `python3 scripts/record_alert.py add` or pipe a JSON array to `python3 scripts/record_alert.py batch`. Use `--source npm-audit`. Set severity based on impact to TACOS:
   - **critical**: TACOS is vulnerable and the consequences are high
   - **high**: TACOS is affected but the consequences are not critical
   - **low**: real vulnerability but limited or indirect exposure
   - **info**: real vulnerability but TACOS is not affected (`NOT_VULNERABLE`)

5. Summarize what you found and what action (if any) is needed.

## Notes

- The script deduplicates on `(source, cve, source_url)` so re-running is safe.
- Use real CVE IDs when they exist. Leave the CVE field null when there isn't one. Use `--name` for common names when the vulnerability has one.
- Moderate and low vulns in devDependencies can usually be recorded as info and moved on.
