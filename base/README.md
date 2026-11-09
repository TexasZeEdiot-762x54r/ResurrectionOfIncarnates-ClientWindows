# Rise of Incarnates: unpatched base

`Original/` is the actual offline recovery copy, **not** a launcher or a revival patch.
It contains the verified 64-bit Windows client and full historical assets. Proton uses
the same original binaries. No Steam account, entitlement, save or revival credentials
are contained in it.

**Recover without Steam's depot server:** make a separate copy of this entire folder,
including `Original/`, on another disk. A normal Git clone does **not** include
Git-ignored files. To check a restored backup from this directory:

```text
python base_game.py --steam Original --depot Original --check
```

To produce a fresh verified copy in a new directory named `CleanGame`:

```text
python base_game.py --steam Original --depot Original --output CleanGame
```

Use Python 3.10 or later (`python3` on Linux). Keep the recovery copy read-only;
apply patches only to a separate working copy. Verification checks every listed file's
SHA-256, not just the executable.

**Acquire from Steam:** while signed into your own account, open
`steam://install/258160` and accept Steam's installation prompt. A tester reported
that claiming the free game on a previously unentitled account worked; this is not
a guarantee for every account or region. Check that Rise of Incarnates appears in
your Steam library. A local copy of the files cannot grant a Steam license.
Install it and use Properties > Installed Files > Browse to locate its folder.
Then open `steam://open/console` and enter:

```text
download_depot 258160 258162 1099989411347492672
```

Wait for the Steam console's completion message. Use the directory printed there,
normally ending in `steamapps/content/app_258160/depot_258162`, for `--depot`
and the installed game folder for `--steam`. Run the builder with `--check` first.
The shutdown install supplies the executable/browser; the historical depot supplies
the full assets. If Steam refuses the license or depot, don't substitute a different
manifest or an unverified executable.
