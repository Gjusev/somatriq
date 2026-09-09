# Somatriq Mobile UI direction

Status: reference target for the separate NOOP-based Android Collector. The working mobile fork already implements the Somatriq identity, sync/status surface and server-backed science views; this concept is not presented as a screenshot of that build.

## Primary jobs

1. Confirm that the band is connected and measurements are arriving.
2. Understand today's recovery inputs without hiding coverage or provenance.
3. Capture a journal event or experiment check-in quickly.
4. Know whether raw evidence and decoded observations are safely synchronized.
5. Diagnose a permanent sync failure without deleting the affected batch.

## Information architecture

| Destination | Contents |
|---|---|
| Today | Recovery, sleep, HRV, RHR, main insight, coverage and freshness |
| Live | Current HR, band battery, BLE state and latest raw-frame time |
| Journal | Quick event logging, intervention check-ins and recent entries |
| More | Pairing, sync queue, raw retention, source/device provenance and settings |

The Collector must stay useful without a server connection. Remote analysis enriches the experience but never becomes a prerequisite for capture.

## Required states

- collector connected / reconnecting / unavailable;
- local data current / partial / stale;
- sync pending / uploading / acknowledged / retryable failure / permanent failure;
- recovery complete / preliminary / missing inputs;
- experiment baseline / intervention / check-in due.

Use text labels for these states. Color can support hierarchy but must never carry the meaning alone.
