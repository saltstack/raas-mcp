# Building

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pytest -q
ruff check .
python -m build
python scripts/check_release.py
```

Artifacts are written to `dist/`.

## Docker image

```bash
./scripts/build-image.sh --tag dev
# With a smoke test (starts the container, polls /health/live):
./scripts/build-image.sh --tag dev --verify
```

Defaults to public PyPI inside the build; override with
`PIP_INDEX_URL=https://your-mirror/simple ./scripts/build-image.sh ...` if
your network requires an internal mirror (see `pip.conf.example`).

## Helm chart

```bash
helm lint helm/raas-mcp-server
helm template raas-mcp helm/raas-mcp-server --set config.raasUrl=https://raas.example.com \
  | kubectl apply --dry-run=client -f -
```
