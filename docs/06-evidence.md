# 06 — Evidence

All measurements against **a real 45-second capture of a production stream**
(1920×1080, `has_b_frames=2`, real POC wrap state), over **SRT** — the protocol actually in
use. Not a synthetic clip. See [`05-testing-methodology.md`](05-testing-methodology.md) for
why that distinction is load-bearing.

## Does it fix the kill?

| test | reader | packets delivered | `PTS − DTS` distribution |
|---|---|---|---|
| clean feed, **stock** | survived | 609 | `3000×239  9000×181  6000×68  0×61  15000×57` |
| clean feed, **patched** | survived | 601 | **identical shape** |
| +15-AU gap, **stock** | **KILLED** | 181 | — |
| +15-AU gap, **patched** | **SURVIVED** | 598 | same shape, no offset |

Two things this table is chosen to show:

1. **Row 2 is the safety argument.** On a clean stream the patched build produces the same
   timestamp relationship as stock. The changed branch is unreachable, so nothing moves.
2. **Row 4 is the fix.** Same gap that killed stock, reader survives, and — critically — the
   `PTS − DTS` distribution afterwards has the *same shape* and no constant offset. That last
   clause is what distinguishes this from the carry-DTS candidate, which survived but sat
   0.61 s off (see [`04-rejected-approaches.md`](04-rejected-approaches.md)).

## Does the reproduction isolate the variable?

From `tools/drop_frames.py` against MediaMTX v1.19.3:

| case | `pic_order_cnt_type` | gap | reader |
|---|---|---|---|
| A | 0 | none | survives |
| B | 0 | 15 AUs | **KILLED `(11)`** |
| C | **2** | 15 AUs | survives |

B vs A isolates the gap. B vs C isolates a single SPS field.

## Threshold

Measured by bisection on gap size: **12 consecutive lost access units is the smallest gap that
fires**, and the number printed in the error is `G − 1`. Independent of accumulated
`reorderedFrames` — including after forcing a reset on every IDR, which was tested precisely
because it looked like it should help. It does not.

## Upstream tests

`mediacommon`'s own H.264 test suite passes unchanged with the patch applied:

```bash
go test ./pkg/codecs/h264/...
```

## In production

Deployed 2026-07-26. Immediately after deploy, on the live feed:

- `PTS − DTS` values `3000 / 6000 / 0 / 9000 / 15000` — the natural shape for this content
- **0 timestamp errors**
- 6161 kbps, nominal

Before the patch, streams that looked completely clean were carrying **8–10 absorbed
reader-kills each** — invisible until something counted them. That is worth repeating: the
failure was not rare, it was *unattributed*. Downstream reconnects were being read as network
flakiness.

## What is still unproven

- ⬜ **Long-horizon endurance.** The failure took 2h52m to appear in the worst case, so
  "several clean hours" is the bar, and we have not published a number here.
- ⬜ **The gaps themselves remain unexplained.** Both readers die in the same second with the
  same value ⇒ ingest-side, not per-reader. Zero `too slow` messages ⇒ not the reader queue.
  Zero slate events ⇒ `alwaysAvailable` exonerated. The encoder reports zero dropped frames.
  **This patch makes the stream tolerant of gaps; it does not explain or remove them.**
- ⬜ **Sub-threshold DTS drift is untouched.** Loss of fewer than 12 frames still leaves the
  derived DTS permanently offset with no recovery path — a pre-existing issue this patch does
  not address.
