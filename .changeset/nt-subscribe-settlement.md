---
'@ntcore-ts/mcp': minor
---

`nt_subscribe` now returns a readable settlement for every sampling window instead of only a summary/sample list.

- New `connection` block reports whether the link was up for the whole window (`observable`, `connectedAtStart`, `connectedAtEnd`, `stayedConnected`, `disconnects`, `reconnects`, `disconnectedMs`, `firstDisconnectAtMs`). Link state comes from the client connection listener plus an `isRobotConnected()` poll, so a missed event cannot hide a drop.
- Each topic summary gains `received`, `droppedDuplicate`, `droppedInterval`, `droppedLimit`, and `truncated`. De-duplication (`change_only`), thinning (`sample_interval_ms`), and the `MAX_SAMPLES_PER_TOPIC` cap are counted separately.
- New top-level `complete`, `warnings`, `omittedTopics`, `prefixErrors`, and `samplingErrors` fields make "the value held steady" distinguishable from "nothing was sampled".
- A disconnect mid-window no longer loses data: samples collected before the drop are kept, the window keeps running, and post-reconnect updates accumulate into the same per-topic bucket.

Existing `nt_subscribe` inputs and the `summary` / `samples` response shapes are unchanged; the new fields are additive.
