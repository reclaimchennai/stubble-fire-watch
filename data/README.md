# data/

Working directory of the pipeline and of the dashboard. Not tracked in git.

- **To run the dashboard:** download `dashboard-data.zip` from the latest release and unzip it at the repository
  root; it fills `data/fires`, `data/smoke`, `data/geo`, `data/creams`, `data/db` and `data/burnscar`.
- **To rebuild from source:** run `sh scripts/update.sh`. Raw downloads go to `data/raw/` (about 4.5 GB: FIRMS yearly
  files, CREAMS bulletin PDFs, government documents, API caches); built files go to the folders above.

The open, documented versions of these data are in [`../datasets/`](../datasets/) and the release assets.
