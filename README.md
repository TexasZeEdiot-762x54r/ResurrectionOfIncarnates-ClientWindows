# Rise of Incarnates experimental patch: Windows

This checkout is a **patch**, not the retail game. Use 64-bit Windows, 64-bit
Python 3.10+ and a separate, user-owned folder. Do not install over Steam, a
working revival install or a save. This is a friend-test candidate, not a
multiplayer acceptance report.

1. Sign into Steam and open `steam://install/258160`. Accept the installation.
   A new-account claim was reported working for this free title. Confirm that
   Rise of Incarnates appears in your library; local game files do not grant Steam
   access. Properties > Installed Files > Browse gives you the Steam path.
2. Open `steam://open/console`, enter
   `download_depot 258160 258162 1099989411347492672`, and wait for completion.
   Steam prints the depot folder, normally under `steamapps/content/app_258160/depot_258162`.
   Alternatively an operator can recover a clean client from the offline base
   backup; in that case replace **both** `--steam` and `--depot` and their paths
   with `--base "O:\Git Staging Files\Full\BaseGame\Original"` (adjust for your disk).
   This replaces the asset download, not the Steam login for VPN or Internet play.
3. From PowerShell in this checkout:

```powershell
py -3 tools/verify_release.py
py -3 setup_client.py --mode internet --steam 'D:\SteamLibrary\steamapps\common\Rise_of_Incarnates' --depot 'D:\SteamLibrary\steamapps\content\app_258160\depot_258162'
.\Play.cmd
```

The public preset uses **riseofincarnates.artificerreviver.com:52714** and its
pinned game TLS certificate. Steam must be signed in. The first launch enrolls
with a Steam ticket; `tools/account-auth.json` is private. Never commit it or
copy it between users.

For a private Steam server, replace `--mode internet` with
`--mode vpn --host PRIVATE_GATEWAY --pin OPERATOR_CERTIFICATE_SHA256`.
Connect your VPN first; WireGuard is recommended and **not** installed by this
patch. Both Steam profiles use TLS. Never disable pin verification.

**LAN status:** operator-managed username/password enrollment is a separate
server mode. Choose `--mode lan --host PRIVATE_IPV4 --pin OPERATOR_PIN` and
run `py -3 tools/lan_login.py` with the operator-issued credentials. The original client uses Steam P2P for peer battles; a working
Steam-free transport is not implied by a working LAN login. Do not use a Steam
profile as a fallback when offline operation is required.

**Tests still required:** registration on a clean PC, full post-battle results,
Lamina unlocks on an enrolled account, and two peers observing identical
summons/effects in the same battle. Joining a lobby alone proves none of these.

## Client updates

The launcher checks the signed development feed before play in public Internet mode.
Accept the prompt to install or decline to play the current build. LAN and private
VPN configurations make no public update request. To check manually run
`python roi_update.py --check` (Linux: `python3`); use `--apply` to install.
On Linux install OpenSSH client tools and zenity or kdialog for a desktop prompt.
On Windows use the Windows OpenSSH Client optional feature. Updates do not
download Steam's original game or change account credentials.
