# Gap 6: Read this for me — package licenses, a PDF by name

**Goal:** "check the licenses" tells the owner which packages the last Agent Room session added and flags risky
licenses; "read the lease pdf" reads a PDF chosen by name, not only the newest download.

## Research

- Room session state: `repo: {dir, branch, base (sha), baseBranch}`; sessions live in
  `<room data>/sessions/<id>/state.json`, ids sort by time.
- Manifests at a ref: `git show <ref>:package.json` (dependencies, devDependencies, optionalDependencies),
  `requirements*.txt` (one requirement a line), `pyproject.toml` (`project.dependencies`,
  `project.optional-dependencies`, `tool.poetry.dependencies`); `git ls-tree --name-only <ref>` lists the root.
- Licenses: npm `https://registry.npmjs.org/<name>/latest` → `license`; PyPI
  `https://pypi.org/pypi/<name>/json` → `info.license_expression`, `info.license`, or a `License ::` classifier.
- `assistant/read_for_me.py` `DocumentReader`: `latest_pdf(downloads)` only.

## Design

1. New `assistant/licenses.py`:
   - `manifest_packages(read) -> set[(ecosystem, name)]` from a `read(path) -> str | None` at one ref.
   - `added_packages(repo, base, branch, run_git)`: packages at `branch` not at `base`.
   - `license_of(ecosystem, name, fetch) -> str | None`; `fetch(url) -> dict | None`.
   - `severity(license) -> 'high' | 'medium' | 'low'`: LGPL, MPL, EPL, CDDL, CC-BY-SA and unknown or missing →
     medium; GPL, AGPL, SSPL, BUSL, Commons Clause, non-commercial, UNLICENSED, proprietary → high; MIT, Apache,
     BSD, ISC, 0BSD, Unlicense, CC0, Zlib, PSF/Python → low.
   - `LicenseCheck(room_data, *, run_git, fetch, announce, folder, clock, run)`: phrases "check the licenses",
     "check package licenses", "what licenses did the agents add"; picks the newest non-debate session with a
     repo; works in a thread; speaks `The last Agent Room session, '<title>', added 3 packages. One to watch:
     high, foo uses GPL-3.0.` or `... all with permissive licenses.` or `... added no packages.`; writes
     `readings/<date>-licenses.md`. Package names come from the repo; only names go to npm or PyPI.
2. `read_for_me`: `find_pdf(folders, wanted, now) -> Path | None`: the newest PDF (not partial, not empty) in
   Downloads, Desktop or Documents (top level) whose name holds every word asked for. Phrases: "read the pdf
   called lease", "read lease.pdf", "read the lease pdf", "check the rental agreement pdf". "latest/newest/last"
   keeps the old meaning.
3. Main: `LicenseCheck` beside the reader (same `read_for_me.enabled`), in the intercept chain; the reader gets
   the extra folders.

## Rulings

- On demand only; the check reads the newest Room session — cost: an older session needs its own way in later.
- Package names go to the public npm and PyPI registries, nothing else — cost: needs the internet.
- Severity is a first filter by license name, not legal advice — cost: odd custom licenses read as medium.
- Named PDFs: Downloads, Desktop, Documents, top level only, any age — cost: PDFs deeper in folders are missed.

## Tasks (TDD)

1. `manifest_packages`, `added_packages`, `license_of`, `severity` — tests per manifest and registry shape.
2. `LicenseCheck` flow — tests: picks newest session with a repo; spoken lines (none / all fine / one to watch);
   notes file; busy; no session; registry failure → "no license found" medium.
3. `find_pdf` + phrases — tests: words matched, newest wins, partial skipped, latest phrase unchanged.
4. Wiring, README.
