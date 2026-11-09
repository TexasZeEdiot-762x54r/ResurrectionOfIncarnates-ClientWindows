# Friend acceptance checklist

Use a disposable **exported** server on a private VPN first. The current live
revival domain is a convenient Steam internet preset, but has not been upgraded
by this export and does not provide the new Lamina roster endpoint. Do not use it
to test multiplayer Lamina.

1. Use a fresh client folder per account. Confirm Steam registration, main menu,
   player name and selected character; no existing game save is imported.
2. On an unlocked server check 0 LP, all cosmetics/characters, five fully open
   grids, 618 capped non-diagnostic cubes, all 15 Lamina with three abilities each.
   Finish a battle: XP/LP must still be credited and equipped cubes must not wear.
3. On a separate normal server check no initial items/LP, locked skill system
   before level 5, first grid at 5, the configured expansion schedule, and the
   Lamina menu becoming available exactly at level 10.
4. With **two distinct Steam accounts**, select different Lamina/abilities (e.g.
   Golem beta and Yazata gamma). Join one Custom room together and enter the same
   battle. After briefing, return to the menu. Each player keeps their own
   `tools/logs/play.log`. To compare the two logs locally:

   ```text
   python tools/compare_peers.py first-play.log second-play.log
   ```

   Only the `LAMINA_PEERS` lines are needed; don't send complete logs or credentials.
   The server freezes the authorized human loadouts for that round. A matching
   trace establishes that both clients received the same assignments; it does
   **not** establish that both clients execute one summon on the same simulation
   frame. The Vs. Bot-only native summon guard remains in place pending this test.
5. Test a Linux friend on GE-Proton11-11: both character-selection video and a
   complete match with results matter. Reaching a lobby is insufficient.
6. On the isolated LAN server, test wrong-password rejection, successful login
   and a second login on the same account. The Git export contains **no LAN
   adapter DLL**. Do not disable endpoint protection to test a third-party adapter.
