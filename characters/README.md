# Character assets

`characters/marin/` is retained for compatibility with the installed Live2D
model assets. It is an asset directory, not a character profile. The active
character and all active data are Evelyn.

The Live2D model is optional and license-dependent. `tools/build_release.py`
excludes `characters/` by default; pass `--with-model` only after checking the
model license.

Do not rename the model directory without updating the Live2D configuration
and release tooling. The app falls back to sprite mode when the model is
missing.
