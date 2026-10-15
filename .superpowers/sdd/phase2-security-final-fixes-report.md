# Phase 2 security final fixes

## Changes

- Removed the Streamlit dashboard's raw `trace.zip` download for every run,
  including after evidence reveal. The dashboard retains a validated local
  trace path and warns that Playwright traces may contain session cookies.
- Kept report and JSONL downloads restricted to their existing safe field
  subsets, which drop arbitrary cookie fields and action input values.
- Restricted the authentication bootstrap login URL to the exact local
  `http://127.0.0.1:3001/login` endpoint, with an optional trailing slash.
  Validation runs before the env file is read or Playwright starts.
- Checked the page URL immediately after navigation and immediately before
  each credential fill. A redirect away from the local login page fails closed.
- Tightened dashboard confirmation to a local non-login URL with a visible
  `Add Project` button or link; generic page text containing “dashboard” is no
  longer sufficient.

## Verification

Focused tests:

```text
tests/test_dashboard.py
tests/test_4gaboards_auth.py
```

The focused results and full-suite results are recorded by the parent agent
after this commit.
