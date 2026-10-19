# Local Discourse fixture on Apple Silicon

The isolated fixture uses Discourse `v2025.12.0-latest`. Its source, database,
credentials, browser state, screenshots and Trace live under the ignored
`runs/discourse-local/` directory. Do not add that directory to Git.

The official Docker development image currently targets x86, so this fixture
uses native macOS services. The upstream guides are the
[macOS setup guide](https://github.com/discourse/discourse/blob/main/docs/developer-guides/docs/02-development-environments/04-macos-setup.md)
and [Docker setup guide](https://github.com/discourse/discourse/blob/main/docs/developer-guides/docs/02-development-environments/03-docker-setup.md).

## Installed services

- Homebrew Ruby 3.3 with Bundler 2.6.4 and gems installed in the private
  `runs/discourse-local/bundle` directory.
- PostgreSQL 16 on loopback port 5433 with a dedicated
  `webapptester_discourse` database and `pgvector` 0.8.7 built for PostgreSQL
  16.
- Redis on loopback port 6381.
- Rails application on `http://127.0.0.1:3103/` and Ember frontend on
  `http://127.0.0.1:4203/`.

Run backend commands from `runs/discourse-local/app` with these environment
variables:

```sh
export PATH="$PWD/../tools/node_modules/.bin:/opt/homebrew/opt/ruby@3.3/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
export BUNDLE_PATH="$PWD/../bundle"
export PGHOST=127.0.0.1 PGPORT=5433
export DISCOURSE_DEV_DB=webapptester_discourse
export DISCOURSE_REDIS_PORT=6381 DISCOURSE_MESSAGE_BUS_REDIS_PORT=6381
export DISCOURSE_HOSTNAME=127.0.0.1 RAILS_ENV=development
```

The backend starts with `bundle exec rails server -b 127.0.0.1 -p 3103`.
The frontend starts in a separate terminal with
`UNICORN_PORT=3103 bin/ember-cli --host 127.0.0.1 --port 4203` using the same
`PATH`. Start PostgreSQL and Redis before either application process.

The isolated administrator was created locally. Its credentials are in the
private `runs/discourse-local/admin.env` file. Refresh the browser login state
when needed:

```sh
.venv/bin/python scripts/bootstrap_discourse_auth.py
```

This writes `runs/discourse-local/auth.json` with mode `0600`. Pass it through
`--storage-state` for authenticated scenarios. The public topic acceptance
report is at
`runs/discourse-local/topic-heading-acceptance/20261019T085002912699Z/report.json`:
local Ollama `qwen3.5:9b` opened `/t/welcome-to-discourse/5`, found the visible
page title in `heading[0]`, and returned PASS in two browser actions. The
screenshots and Trace are next to the report.

The B0–B3 smoke comparison for the same public scenario is under
`runs/discourse-local/ablation-smoke/`. It exercises the harness but is not a
course benchmark: the formal scenario set has not been released in this
workspace.
