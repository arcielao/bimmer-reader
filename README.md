# Bimmer Reader

An INPA-style diagnostic tool for E-series BMWs, set up for a **2006 E90 330xi**.
It talks to the car through EDIABAS (the same engine INPA uses) and a K+DCAN cable.

What it does:

- **Quick test** – asks every module for its ID and fault count, like INPA's
  quick test. Modules light up green (OK), orange (faults stored) or red
  (no response).
- **Fault memory** – reads stored codes with description, present/stored
  status, count and P-code. Shows every raw field (mileage, environment data).
  Clears codes after a confirmation. Exports to CSV.
- **Identification** – part numbers, software and hardware versions.
- **Live data** – polls any `STATUS_*` jobs the module offers (RPM, temps,
  voltage...). Optional CSV logging.
- **Job runner** – lists every job in a module and runs any of them with
  arguments, like Tool32. It warns before jobs that look like they write,
  clear, code or actuate.
- **Full report** – one text file with every module's faults.
- **Demo mode** – a simulated car, so you can learn the screens first.

It does **not** code (NCS Expert) or flash (WinKFP).

---

## What you need

| | |
|---|---|
| Laptop | Windows 10/11 |
| Cable | K+DCAN USB with the K-line / D-CAN switch (FTDI chip) |
| Python | **32-bit** Python 3.10 or newer (EDIABAS's `api32.dll` is 32-bit) |
| EDIABAS | A working `C:\EDIABAS` with the ECU files (`Ecu\*.prg`, `Ecu\*.grp`) |

**About EDIABAS.** EDIABAS and its ECU description files are BMW's software.
This app doesn't include them, and I can't supply them. If you already have an
INPA/EDIABAS install, use that `C:\EDIABAS`. There is also an open-source drop-in
`api32.dll` from **EdiabasLib** (github.com/uholeschak/ediabaslib). It still needs
the `.prg`/`.grp` ECU files from a BMW install.

---

## Setup (once)

1. **Install 32-bit Python.** Go to python.org → Downloads → Windows and pick
   *"Windows installer (32-bit)"*. Keep **"py launcher"** ticked.
2. **Plug in the cable** (USB only for now). The FTDI driver usually installs
   itself.
3. **Set the COM port.** Open Device Manager → Ports → *USB Serial Port* →
   Properties → Port Settings → Advanced:
   - COM Port Number: **COM1**
   - Latency Timer: **1 ms**
4. **Check the EDIABAS config files**, both in `C:\EDIABAS\bin`:
   - `EDIABAS.INI` → `Interface = STD:OBD`
   - `OBD.INI` → under `[OBD]`: `Port=Com1`
5. **Set the cable switch.** E90s built **before 03/2007** use K-line, so a
   2006 car should go in the K-line / "pre-2007" position. Check the build date
   on the driver's door-jamb sticker. Switch labels differ between cables, so
   if every module says *No response*, flip the switch and try again.
6. **Try demo mode.** Double-click `run.bat` once. The first run installs
   `pydiabas`. To use demo mode, run `run.bat --demo` from a command prompt, or
   tick **Demo mode** in the app.

## Connecting to the car

This order works best:

1. Cable into the OBD port (under the dash, driver's side).
2. Ignition **ON**, engine off. Press start without the brake, or turn the key
   to position 2.
3. USB into the laptop.
4. Run `run.bat` → **Connect** → **Quick test**.

Put a battery charger on for long sessions. Modules start dropping out below
about 12 V, and the header shows the battery voltage.

---

## Using it

- **Pick a module on the left**, then use the tabs on the right.
- **Fault memory → Read faults.** Click a code to see every field below it.
  Codes are BMW's hex fault codes, like INPA shows. The P-code column shows
  the generic OBD code when the module provides one (DME only).
- **Clear faults** asks first and re-reads straight after. If a code comes
  straight back, it's a live fault.
- **Live data → Load STATUS jobs**, select one or more (Ctrl-click), then
  **Start**. Job and result names come from BMW's files, so they're German:
  - `MOTORDREHZAHL` = RPM
  - `MOTORTEMPERATUR` = coolant temperature
  - `UBATT` = battery voltage
  - `ANSAUGLUFT` = intake air
  
  Some `STATUS_*` jobs need arguments. Use the Job runner for those.
- **Job runner → List jobs in module.** Filter, pick a job, add arguments
  (separate several with `;`), then **Run**. Tick *Show system set* to see
  which SGBD variant answered.

## The module list (`modules_e90.json`)

Each module has one or more EDIABAS names to try, in order. `D_0012` etc. are
the E-series group files by address. The app uses the first one that exists in
your `C:\EDIABAS\Ecu` folder.

If a module shows **Not installed**, that file isn't in your Ecu folder. Look
there for the right `.grp`/`.prg` name and add it, either in the JSON or with
**Add module...**. You can also type a variant directly, such as `MSV70` for the
N52 DME.

**No response** means the file exists but the module didn't answer. That's
normal for options your car doesn't have, like PDC or nav.

---

## Troubleshooting

| Message | What it usually means |
|---|---|
| `This is 64-bit Python` | Run with `run.bat` (it uses `py -3-32`), or install 32-bit Python |
| `api32.dll not found` | Wrong EDIABAS folder. Use **Settings** to point at the folder that holds `bin\api32.dll` |
| `IFH-0002` / `IFH-0003` | EDIABAS can't open the cable. Check the COM1 setting and OBD.INI, then re-plug the USB |
| `IFH-0009 ECU doesn't respond` | Ignition off, wrong switch position, or the module isn't fitted |
| `SYS-0002 ... not found` | That SGBD/group file isn't in `C:\EDIABAS\Ecu` |
| Battery shown in yellow | Under 12 V. Charge it before reading or clearing |

Command-line options:

- `--demo` runs the simulated car.
- `--ediabas D:\EDIABAS` sets the EDIABAS folder.
- `bimmer_reader_settings.json` remembers the folder you set in Settings.

## Files

- `bimmer_reader.py` – the app.
- `modules_e90.json` – module list. Edit it freely.
- `run.bat` – launcher. It checks for 32-bit Python and installs `pydiabas`.

Built on [pydiabas](https://github.com/BembelBytes/pydiabas) (MIT licence).
It has only been tested in demo mode so far. Report anything odd you see on
the real car, together with the log text at the bottom of the window.
