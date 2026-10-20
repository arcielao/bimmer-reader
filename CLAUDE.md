# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

Bimmer Reader is an INPA/Tool32-style diagnostic GUI for E-series BMWs (set up
for a 2006 E90 330xi). It's a single-file Tkinter app that talks to the car
through EDIABAS (`api32.dll`) via the `pydiabas` package and a K+DCAN cable.
It reads/clears fault memory, shows identification, polls live `STATUS_*` jobs,
runs arbitrary SGBD jobs, registers a new battery, and does profile-driven
coding (backup / restore / bit-flip recipes). It is not NCS Expert (no parsing
of BMW's NCS coding files) and it does **not** flash.

The README is user-facing (setup, wiring, troubleshooting). Keep it in sync
when behaviour, options or files change.

## Files

- `bimmer_reader.py` – the whole app (~1700 lines). Sections, in order:
  helpers → backends (`EdiabasBackend`, `DemoBackend`) → `Worker` thread →
  `App` (Tkinter GUI) → `main()`. Inside `App`, each feature has its own
  `# ---- name ---- #` section (faults, battery registration, coding, ident,
  job lists, live data, settings).
- `coding_e90.json` – coding profiles: per SGBD variant, the read/write jobs,
  args (`hex:` prefix = binary arg, `{data}` = coding bytes) and recipes
  (`byte`, `mask`, `on`, `off`). `demo_only` profiles are hidden unless the
  demo backend is active.
- `coding_backups/` – created at runtime, one JSON per coding read/write.
  Gitignored (it's the user's car data).
- `modules_e90.json` – module list. Each entry has a display `name` and an
  ordered list of `sgbd` names to try (group files like `D_0012` first, then
  older names). The app also rewrites this file when the user adds/removes
  modules in the UI.
- `run.bat` – Windows launcher. Requires 32-bit Python via `py -3-32`,
  installs `pydiabas` on first run, forwards args.
- `bimmer_reader_settings.json` – created at runtime (EDIABAS folder). Per
  machine, not committed.

## Running

```bash
python bimmer_reader.py --demo
```

- `--demo` uses `DemoBackend` (simulated car, works on any OS / 64-bit Python,
  so plain `python` is fine — the dev machine only has 64-bit Python).
  This is the only mode that can be exercised without the car, so test every
  change in demo mode.
- The real backend needs Windows, **32-bit** Python and a `C:\EDIABAS` install
  (`--ediabas <dir>` to override). Claude can't test against the real car —
  say so when a change affects the EDIABAS path, and keep the README's
  "only tested in demo mode" caveat accurate.
- No test suite, no linter config, no dependencies beyond the stdlib and
  `pydiabas`. pydiabas isn't installed on the dev machine; that's fine for
  demo mode.
- To exercise a feature end to end, write a throwaway script in the
  scratchpad (not the repo): import `bimmer_reader`, replace
  `messagebox.showinfo/showerror/showwarning/askyesno` (and `filedialog.*`)
  with stubs that record calls, build `App(root, demo=True)` on a withdrawn
  `tk.Tk()`, call methods like `toggle_connect()` / `read_coding()`, and spin
  `root.update()` for a second or so between steps so the worker callbacks
  run. Check the log text (`app.log_txt`) and the demo backend's state
  (`app.backend.ecus[...]`). Delete any `coding_backups/` a test creates.

## Architecture rules

- **EDIABAS is not thread-safe.** Every backend call must go through the
  single `Worker` thread: `self.run(fn, *args, cb=callback)`. `fn` runs on the
  worker; `cb(result, error)` runs on the Tk thread via `_pump`. Never touch
  Tk widgets from inside `fn`, and never call `self.backend.job(...)` from the
  Tk thread. To push a mid-job UI refresh, put a `(cb, None, None)` tuple on
  `self.out_q` (see `quick_test`).
- Backends share one interface: `connect()`, `disconnect()`, `connected`,
  `label`, and `job(ecu, job, params="", raw=False) -> (system_set, result_sets)`.
  `params` may be `bytes` (one binary argument). Results go through
  `clean_value()` unless `raw=True`. Use raw for anything byte-exact
  (coding), because `clean_value` strips trailing `00` bytes and turns
  printable binary into text. Errors are raised as `DiagError`. When adding a feature that calls a new job, add a
  matching simulated response to `DemoBackend.job` so demo mode keeps working.
- `_resolve(mod)` picks the first SGBD name that exists and caches it in
  `mod["resolved"]`. "Missing file" errors (`SYS-0002`/`SYS-0005`) mean try
  the next name / "Not installed"; any other error means the file exists but
  the ECU didn't answer ("No response").
- Result values pass through `clean_value()` (bytes → text or hex). Use
  `fmt()` for display, `job_status()` for `JOB_STATUS`, `pair_units()` to join
  `*_WERT` with `*_EINH`.
- `DEFAULT_MODULES` in the script is only a fallback if the JSON is missing
  or unreadable; the JSON is the source of truth.

## Domain notes

- SGBD job and result names are German, from BMW's files: `FS_LESEN` (read
  faults), `FS_LOESCHEN` (clear faults), `IDENT`, `_JOBS`, `STATUS_*`,
  `F_ORT_NR` (fault code), `F_ORT_TEXT`, `F_HFK` (count), `*_WERT` (value),
  `*_EINH` (unit). Don't translate these identifiers in code.
- Anything that writes, clears, codes, actuates or resets a module must keep
  a confirmation dialog (see `clear_faults` and the keyword check in
  `run_job`). Don't weaken these safeguards.
- Coding writes all go through `_write_coding`. It checks battery ≥ 12 V,
  re-reads and aborts if the data changed since the user's read, saves a
  backup, writes, then reads back to verify. Keep every one of those steps.
  Restores are refused unless the variant, block name and length match.
- **Never add real-car coding recipes (byte/mask/values) or job names to
  `coding_e90.json` from memory or guesswork.** Wrong values can disable
  modules. Real entries come from the user, citing a source in
  `description`. Demo recipes must stay `demo_only` and clearly labelled
  "(demo)".
- EDIABAS and the ECU `.prg`/`.grp` files are BMW's software — never add them
  to the repo.

## Style

- Match the existing code: `from __future__ import annotations`, type hints on
  signatures, compact one-line Tkinter construction, section banners
  (`# ---- #`), short docstrings, plain-English UI copy written for a home
  mechanic (not an engineer).
- Keep it a single file with stdlib + `pydiabas` only unless there's a strong
  reason to change that.
- Windows is the target platform; keep paths and `run.bat` Windows-friendly.

## Git

- Commit as the repo owner (the configured git user, Arcie). **Do not add
  `Co-Authored-By: Claude` or any other Claude/AI attribution** to commit
  messages or PR descriptions.
- Commit regularly in small, focused commits — one logical change each — so
  the history is easy to read. Short imperative subject line, optional body
  explaining why.
- Don't push unless asked.
