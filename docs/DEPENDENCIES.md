# Dependencies

## Locked

`requirements.txt` is **generated**. Do not hand-edit it.

```bash
uv pip compile requirements.in -o requirements.txt --python-version 3.13
```

`requirements.in` holds the human-maintained top-level requirements; the
compiled file carries exact versions for every transitive dependency.

## Git dependencies are pinned to commits

`steam` and `vdf` come from git, and both originally tracked a **moving
reference** (`@master` and a `v4.0` tag). That means a build could change
without any code change of yours — "worked yesterday, broken today".

They are now pinned:

```
steam @ git+https://github.com/solsticegamestudios/steam.git@1373e885f26bb636225443787f54d4082283e5f3
vdf   @ git+https://github.com/solsticegamestudios/vdf.git@fb88ea7e38a85476743f5601b0c80d5715034623
```

To bump them:

```bash
git ls-remote https://github.com/solsticegamestudios/steam.git HEAD
# edit requirements.in with the new SHA, then:
uv pip compile requirements.in -o requirements.txt --python-version 3.13
```

Note: `steam` declares `vdf` internally via `git+...@v4.0`. uv rejects the same
package coming from two different URLs, so `vdf` is declared with the upstream
ref and uv resolves it to a commit during locking — which is what actually pins
the build.

## Target Python is 3.13

The AppImage bundles **Python 3.13**, and CI uses 3.13. Always compile against
`--python-version 3.13`.

This matters more than it looks: **Python 3.14 evaluates annotations lazily**
(PEP 649). A missing typing import such as `Optional` in a function signature
will import cleanly on 3.14 and crash immediately on 3.13. Developing on 3.14
hides bugs that break the shipped build.

Use `scripts/smoke_import_check.py` after any change — it imports every module
*and* force-resolves annotations, mimicking 3.13's eager behaviour.

```bash
python3 scripts/smoke_import_check.py     # must exit 0 before building
```

## Removed as unused

Verified zero imports anywhere outside `src/deps/` vendored sample scripts:

| Package | Why removed |
|---|---|
| `numpy` | Never imported anywhere in the project |
| `pycryptodomex` | Duplicate fork of `pycryptodome`, which is retained |
| `just-playback`, `tinytag`, `Pillow` | Only referenced by vendored sample scripts under `src/deps/` |

If you later add code that needs one of these, add it back to
`requirements.in` and recompile — do not edit `requirements.txt`.

### Do not remove `urwid`

`urwid` looks unused because only `ui/text_menus.py` imports it — and that file
looks CLI-only. But `managers/cli_manager.py` imports it **at module level,
unconditionally**, so dropping `urwid` breaks CLI mode with a bare
`ModuleNotFoundError` at import. This was tried and reverted.

## The smoke import gate

`scripts/smoke_import_check.py` imports every module under `src/` and resolves
every annotation. It is the gate that catches both of the classes of bug above:

- a missing typing import that only crashes on 3.13 (lazy annotations on 3.14
  hide it), and
- a declared dependency that is absent from the environment.

A missing dependency is reported as a **failure**, not a skip. It used to be
silently skipped, which is exactly what let the missing `urwid` slip through:

```
managers.cli_manager
    missing dependency 'urwid' - it is declared in requirements.txt;
    the build would ship broken.
```

Modules that genuinely cannot be imported headless must be listed explicitly in
`EXPECTED_SKIPS` with a reason — "its dependency happens to be missing" is not
an acceptable reason.

Run it after every change, in the locked environment:

```bash
QT_QPA_PLATFORM=offscreen /path/to/venv313/bin/python scripts/smoke_import_check.py
```

## Optional runtime components

Large binaries (Goldberg, Steamless, SLScheevo) are **not** Python dependencies
and are not in this file. They are downloaded on demand from R2. See
`cloud/README.md`.