# HackerNews Security Monitor

Use `/read-config-evergreen` to get the project name and project path. It also resolves
`EVERGREEN_PROJECT_ID` — the project you're scoped to. When it's set, pass
`--project-id "$EVERGREEN_PROJECT_ID"` to every `record_alert.py add` / `record_alert.py batch`
call so alerts land on the right project; when unset, omit it (defaults to project 1).

Scan HackerNews for security vulnerabilities relevant to the monitored project's stack. Read the project's `package.json` (or equivalent) to understand the dependency tree.

## Steps

1. Run `python3 scripts/hn_security_scan.py 24` to fetch recent HN posts matching security queries. Note which posts are already `[TRACKED]`.

2. Read the titles and identify posts that describe **actual disclosed vulnerabilities, exploits, or active attacks** — not general security news, opinion pieces, policy debates, or company-specific breaches (unless they reveal a flaw in software the project uses). Use your judgment. When in doubt, include it — false positives are cheaper than missed vulns.

3. For each post you flagged, run `python3 scripts/hn_security_scan.py 24 <id1> <id2> ...` to fetch full article text and HN comments. Read the enriched output carefully.

4. For each confirmed vulnerability, assess: does this affect the project or its infrastructure? Check the project's dependency files for overlap, read the codebase as needed to understand exposure, and use `/server-access-evergreen` to check server versions if relevant.

5. Record findings with `python3 scripts/record_alert.py add` or pipe a JSON array to `python3 scripts/record_alert.py batch`. When scoped to a project, pass `--project-id "$EVERGREEN_PROJECT_ID"` so the alert lands on the right project. Set severity based on impact to the project:
   - **critical**: Project is vulnerable and the consequences are high
   - **high**: Project is affected but the consequences are not critical (CVE level high or lower)
   - **low**: Real vulnerability but limited or indirect exposure
   - **info**: Real vulnerability but project is not affected (`NOT_VULNERABLE`)

6. Summarize what you found and what action (if any) is needed.

## Notes

- Skip posts already marked `[TRACKED]` unless you have new information about them.
- The script deduplicates per project on the advisory id in `--cve` (falling back to normalized name + component), so re-running is safe.
- If the NVD or article page doesn't render well, check HN comments — they often have the best technical summary.
- Put the advisory id (CVE, or GHSA when there's no CVE) in `--cve` when one exists. Leave it null when there isn't one — don't invent placeholder identifiers. Use the `--name` field for common names when the vulnerability has one.
