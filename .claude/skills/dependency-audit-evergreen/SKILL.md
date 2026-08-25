# Dependency Audit

Applies to `code_db` projects (a checked-out repo to audit). Use `/read-config-evergreen` to get
the project path and project name; when `EVERGREEN_PROJECT_ID` is set, pass
`--project-id "$EVERGREEN_PROJECT_ID"` to `record_alert.py`.

Run a dependency audit on the monitored project and assess real risk.

## Steps

1. `cd` to the project path and run the appropriate audit command (`npm audit`, `bun audit`, `pip audit`, etc. based on what the project uses).

2. For each critical or high vulnerability, determine whether it's in a production dependency or only in devDependencies. Check the dependency chain — a vuln in a transitive dep of a test framework is very different from one in a web server.

3. For anything that looks plausibly risky, read the project codebase to understand whether the vulnerable code path is actually reachable. Use `/server-access-evergreen` to check server state if relevant.

4. Record findings with `python3 scripts/record_alert.py add` or pipe a JSON array to `python3 scripts/record_alert.py batch`. Use `--source npm-audit`. Set severity based on impact:
   - **critical**: Project is vulnerable and the consequences are high
   - **high**: Project is affected but the consequences are not critical
   - **low**: Real vulnerability but limited or indirect exposure
   - **info**: Real vulnerability but project is not affected (`NOT_VULNERABLE`)

5. Summarize what you found and what action (if any) is needed.

## Notes

- The script deduplicates per project on the advisory id in `--cve` (falling back to normalized name + component), so re-running is safe.
- Always put the advisory id in `--cve`, exactly as the audit tool reports it (GHSA ids for npm/bun audit) — a consistent id per source is what makes dedup fire. Only leave it null when the advisory truly has no id. Use `--name` for common names when the vulnerability has one.
- Moderate and low vulns in devDependencies can usually be recorded as info and moved on.
