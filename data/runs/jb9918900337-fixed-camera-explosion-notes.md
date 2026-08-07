# jb9918900337 fixed-camera explosion notes

- Formal camera directions remain locked: `fixed_123` and its exact centre-opposite `fixed_456` only.
- This trial used `fixed_456` with `ZOOM=1.5` and native `PAN=(-0.15,-0.40)` to compensate for Creo zoom anchoring; these values affect composition only and do not alter the camera matrix.
- 30.1.1 studs (`51/5025/79;51/5025/82;51/5025/83`) passed visual rendering with root translation `[0,0,600]` mm.
- 30.1.2 studs (`51/5050/73;51/5050/76;51/5050/77`) rendered blank at `[0,0,600]` mm but passed at `[0,0,320]` mm.
- Explosion acceptance requires a nonblank native image, visible receiver area, visible moving set, and unchanged per-occurrence rotation audit. Do not promote an uninspected render.
