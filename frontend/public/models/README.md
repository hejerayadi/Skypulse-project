# 3D models

The ground-station view (`/dashboard/nodes`) draws each node as the hardware it
actually carries. The GLBs are **not checked in** — Sketchfab requires an
authenticated download even for CC-licensed models, so fetch them locally:

```powershell
# PowerShell — no inline env-var prefix, set it first
$env:SKETCHFAB_API_TOKEN = "xxxx"; npm run models
```

```bash
# bash / zsh
SKETCHFAB_API_TOKEN=xxxx npm run models
```

Get a token from <https://sketchfab.com/settings/password> (free account). The
script downloads, decimates, compresses textures and quantizes each model, then
writes it here under the name the scene expects.

**A missing file is not a broken page.** `src/components/sensor-model.tsx` falls
back to the old coloured primitive for any model that 404s, so the station
renders with none, some or all of these present.

## What the pipeline produced

3.89 MB for all six, fetched on 2026-08-17. The one that mattered:
`sensor-humidity.glb` came down at 35.46 MB / 2,889,545 triangles and left at
1.49 MB / 57,320 — the raw export is roughly 25x more detail than a prop this
size can show.

Five of the six kept their **original textures**. `sharp`/`libvips` rejects them
with `colourspace: parameter space not set` — their textures declare a colour
space interpretation it does not model, and the failure is in the image data,
not the pipeline (the heartbeat model converted to WebP fine). The script
retries without texture conversion rather than producing nothing, so those files
are larger than they need to be. Converting those textures by hand would
plausibly halve the two 1.4 MB models; nobody has needed to yet.

| File | Model | Author | Licence |
| --- | --- | --- | --- |
| `sensor-temperature.glb` | [DHT22 Temperature Sensor Module](https://sketchfab.com/3d-models/dht22-temperature-sensor-module-c46a658784034c679e4907bbddad0093) | YouniqueĪdeaStudio | CC BY 4.0 |
| `sensor-humidity.glb` | [LOYUNO-L](https://sketchfab.com/3d-models/loyuno-l-8f84a03824d64782858dccc916aeff9a) | LOYTEC | CC BY 4.0 |
| `sensor-pressure.glb` | [Pressure sensor](https://sketchfab.com/3d-models/pressure-sensor-0bbf931dcd084be197be9688685cb942) | AndreiV | CC BY 4.0 |
| `sensor-accelerometer.glb` | [Snowboard GPS Accelerometer logger](https://sketchfab.com/3d-models/snowboard-gps-accelerometer-logger-527e510d5c1449d69f49fe2f50051c94) | bernebeer | CC BY 4.0 |
| `sensor-sound.glb` | [Sound Sensor for Arduino](https://sketchfab.com/3d-models/sound-sensor-for-arduino-a4d302a674f242b3b9f32be18ac463e8) | arieffitrah | CC BY 4.0 |
| `sensor-heartbeat.glb` | [Heartbeat - Version 3](https://sketchfab.com/3d-models/heartbeat-version-3-97f8ba87dadd4ec28d29ff36055735c6) | Apex Trace | CC BY-**NC**-SA 4.0 |
| `antenna.glb` | — | source not recorded | unknown |

## Licence notes

- **CC BY** requires the credit to be visible to the viewer. `Station3D` renders
  it from `src/lib/sensor-models.ts`; if you swap a model, update that table.
- **`sensor-heartbeat.glb` is NonCommercial.** Fine for the internship demo,
  not for a commercial release. ShareAlike also attaches. Replace it before any
  commercial use.
- **`antenna.glb` has no recorded source.** Whoever added it should fill in
  `ANTENNA_CREDIT` in `src/lib/sensor-models.ts`, or it should be replaced —
  right now the page cannot honour a licence it does not know.

## Two models could not be used

The originally chosen pressure and temperature modules by **VIS-All-3D** are
published under the *Sketchfab Standard* licence: viewable and iframe-embeddable
only, with downloads disabled. They cannot be loaded into a three.js scene at
all, at any price short of buying a licence from the author. The closest CC-BY
equivalents are used instead, and both are flagged in the credits panel.
