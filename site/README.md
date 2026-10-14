# Site generator

`site/build.py` turns the task ledger, the report configs and any published results into a static site.
It uses the Python standard library only and writes no scripts or remote assets.

```sh
python3 -S site/build.py --out dist [--ledger PATH] [--reports-dir reports] \
  [--results-override SLUG=PATH ...] [--base-url https://example.org/staffbench]
```

- Inputs: `data/task-ledger.json`, every `reports/<slug>/report.json`, and `results/<slug>/leaderboard.json` when present.
- Output: `index.html`, `roles/index.html`, `reports/<slug>/index.html` and `404.html`.
- A report without results renders a "Results pending" page.
- When results carry `preview_notice`, every page shows it as a draft banner.
- Output is byte-identical across builds: the bootstrap seed is fixed and inputs are read in sorted order.
- The build fails, leaving `--out` untouched, on an unresolved link or anchor, a remote asset, or a public-content hit.
  The public-content check is `scripts/check_public_content.py`, plus the patterns in `STAFFBENCH_PRIVATE_TERMS` when set.
- `--base-url` adds canonical and `og:url` links. Set it for any public deploy so `404.html` links resolve from any path.

Tests use the synthetic fixture in `site/tests/fixture/`:

```sh
python3 -S -m unittest discover -s site/tests -v
```

`.github/workflows/site.yml` runs the tests and builds the fixture site on every pull request.
Its Pages deploy job runs only from a manual `workflow_dispatch` and only when the repository is public.
