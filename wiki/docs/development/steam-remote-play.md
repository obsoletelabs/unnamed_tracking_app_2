# Steam Remote Play feasibility and delivery plan

Research checked 30 October 2026 (Australia/Perth). This is a proposal, not an implemented streaming feature. Documentation confirms the supported user workflows below; no live Steam client, Steam Link device or browser handoff was exercised during this work.

## Confirmed methods

| Method | Verified evidence | Proposed use |
| --- | --- | --- |
| PC-to-PC streaming through the Steam Library | Valve documents signing into Steam on both computers and starting the stream in the receiving client's library | Supported baseline for desktop users; show setup/help and let Steam select its host |
| Steam Link on a phone, tablet, TV or supported computer | Valve documents installing Steam Link and connecting it to a computer running Steam | Supported baseline for mobile users; pairing, host selection and streaming remain inside Steam Link |
| Steam Link recent-game quick launch | Steam Link developer slouken describes quick-launch buttons on the app's main screen | Optional user guidance after pairing; it does not establish an external launch API |
| Remote Play Together invitations | Steamworks documents ShowRemotePlayTogetherUI, BSendRemotePlayTogetherInvite and the guest-invite URL callback | A real method for a participating game's co-op invitations, but unsuitable as a general library-launch API |

The first two workflows are described on [Valve's Remote Play page](https://store.steampowered.com/remoteplay) and [Steam Support's Remote Play guide](https://help.steampowered.com/en/faqs/view/0689-74B8-92AC-10F2). The host must be running Steam; the receiving device needs Steam or Steam Link and the applicable Steam connection/pairing. UTA sign-in and Steam pairing are independent.

[Valve's ISteamRemotePlay reference](https://partner.steamgames.com/doc/api/ISteamRemotePlay) exposes game-side session information, input handling and Together invitations. The documented interface does not provide a general web-server operation that selects any owned game and computer and starts a personal Remote Play stream. Treat using it for that purpose as unsupported by the reviewed contract.

In the [Extended Launch Options discussion](https://steamcommunity.com/app/353380/discussions/10/571549822592624481/), slouken explicitly declines automatic game streaming through Steam Link command-line arguments and points to the recent-game buttons. The original poster reports --autoconnect and a custom injected workaround; those are not evidence of a supported external game/host launch contract. Do not build the product around that workaround.

## What is not verified

No reviewed source establishes a stable, cross-platform browser URI that selects a specific personal Steam host and streams a particular game. This is a research limit, not proof that no internal or platform-specific mechanism exists. The Valve Developer Community browser-protocol page returned 403 during research and was not used to validate a streaming URI.

Keep the existing local `steam://rungameid/` action labeled as a local launch. Do not relabel it as streaming or manufacture a `steam://stream/` or `steamlink://` URL. Existing Steam account linking, an app ID, a Playnite device label or a store's Remote Play Together flag does not prove that a matching paired streaming host is reachable. A Steam Web API key is not a streaming-pairing credential.

## Recommended first streaming feature

Once [Playnite remote launch](remote-launch.md) works end to end, add a distinct **Steam Remote Play setup** action for Steam-linked games. It should offer the official Steam Link download/setup page and PC-to-PC instructions. Explain that the game runs on the host and the user connects through the receiving Steam/Steam Link app.

A user may first choose **Launch on [PC]** in UTA, then connect using Steam Link. Present those as separate steps: a successful Playnite launch does not confirm a streaming session, and a game launched outside Steam may need separate Steam configuration. Start by supporting native Steam library games; non-Steam shortcuts and other stores need their own verified compatibility work.

Do not silently launch on a different PC, retry into a local launch, collect Steam pairing PINs, expose desktop control, or proxy video/input through the UTA backend. If the receiver application cannot be opened from the browser with a verified platform method, show the manual handoff instructions. Describe the action as setup/help rather than one-click streaming until the complete path is proven.

## Validation before implementation

Record Steam/Steam Link versions, browser/OS, exact action, target computer and observed result for:

- Windows host to another Windows/Linux Steam client and Steam Deck, including two available hosts.
- Windows host to Android and iOS Steam Link, including first pairing, already paired and missing-app cases.
- Fresh launch versus an already-running game; installation missing on the host; host asleep/offline; revoked pairing; LAN and an external network.
- Browser protocol confirmation/cancellation and denied app opening. Verify which machine starts the game and which device receives video, audio and input.
- Native Steam games first; test non-Steam shortcuts separately without assuming their IDs or overlay behavior match Steam app IDs.

Only promote a direct handoff to an implementation task after finding a documented method or recording repeatable evidence with explicit platform/version limits. Keep manual connection as a fallback. If a future method can choose a host, define a verified association between that Steam host and the approved UTA receiver; display names are insufficient.

The next implementation decision is therefore a guided Steam/Steam Link handoff. A fully automatic browser-to-game stream remains a separate feasibility gate. No streaming code, Steam credentials, client automation or network configuration is added in the current host change.
