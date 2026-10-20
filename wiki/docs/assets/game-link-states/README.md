# Game-link acceptance captures

Captured from an isolated development deployment with synthetic data. The dark captures use an ordinary account; its own game loads normally and a second ordinary account owns the inaccessible game. No production accounts or data were used.

| Capture | Scenario |
| --- | --- |
| `not-found-light.png` | Missing valid UUID, light palette, app navigation retained. |
| `invalid-link-light.png` | Truncated UUID, safe explanation and library recovery. |
| `other-user-dark.png` | Ordinary user opening another account's game, same not-found state. |
| `invalid-link-mobile-dark.png` | Invalid link at a 390 × 844 CSS-pixel viewport. |
| `not-found-mobile-dark.png` | Missing game at the same responsive width. |
| `connection-failure-dark.png` | Game request with the disposable backend stopped, retry available. |
| `retry-recovered-dark.png` | The same page after restarting the disposable backend and selecting Try again. |

Responsive captures show the actual app inside a same-origin 390 × 844 iframe. This exercises the app's CSS and responsive navigation at that width; it is not a physical-device or mobile-browser compatibility claim. The temporary capture harness was removed from the application source after recording the images.

The HTTP acceptance checked an owner's successful lookup, identical 404 bodies for a foreign game and a nonexistent game, and 422 for a malformed UUID. Frontend regressions check malformed links without data requests, mapped 422/401/server/network failures, retry, stale responses, request deadline, heading focus and the library link. Existing copy-operation tests retain their assertions and use valid UUID fixtures.
