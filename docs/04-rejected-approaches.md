# 04 — Rejected approaches

Everything here was tried and abandoned **with a measurement**, not on reasoning alone. Two of
them were deployed to a live system before being rolled back.

## 🔴 Two patches that shipped and were rolled back

Both passed their tests. Both broke the stream in a way their tests could not see.

### PR #5714 — nil the extractor, re-prime on the next IDR

Nils the DTS extractor on failure and re-initialises from the next IDR.

- ✅ **The reader survives.** Two recoveries, zero kills, over ten minutes live.
- 🔴 **The re-primed timeline comes back wrong.** Video and audio were pushed in *opposite*
  directions, producing a sustained **230 timestamp errors per 15 seconds**. The downstream
  decoder gave up: **viewers saw a black screen** while 6.2 Mbps of structurally valid video
  kept arriving.

**The lesson:** "does the reader survive?" is a narrower question than "is the stream correct?"
Surviving with a corrupt timeline is worse than dying, because nothing alarms.

### carry-DTS — use the container's DTS instead of deriving it

Adds a `DTS *int64` to `unit.Unit`, populates it from the demuxer, and uses it when present.

- ✅ **Bit-for-bit exact on a synthetic clip.**
- 🔴 **Judder on the live feed.** The masked delta `(pts - dts) & 0x1FFFFFFFF` landed on a
  constant **~54,775 tick (0.61 s)** offset instead of the true 1–2 frames.

⚠️ **The idea is still sound** — the container really does carry the correct DTS, and stock
really does invent wrong ones (measured: it emitted `126090`, `126180` — not even frame
aligned). What is broken is the wrap/offset handling. If you want to pursue this route, the
diff is a starting point, but it **must** be validated against a real capture with real wrap
state, not `testsrc2`.

**The lesson:** a synthetic clip has no wrap state. It cannot exercise the code path where
this failed.

## ⚠️ The encoder switch — correct, and still not worth it for us

Switching the encoder from Apple VideoToolbox to libx264 with `bframes=0` yields
`pic_order_cnt_type = 2`, which makes the bug **unreachable**. That is real, and if it is free
for you, take it.

It was not free for us:

- At 1080p30, libx264 `veryfast` exceeded the machine's real-time budget: **23% of frames
  skipped at the encoder** (11,646 of 50,644), effective fps 28.06, render time 35.57 ms
  against a 33.3 ms budget.
- Audio kept full rate, so the result was **audio/video desync** — viewers reported the sound
  arriving before the picture.
- Dropping to 720p fixed the lag (0.00%) but cost resolution on a stream whose product is
  readable text.

⭐ **An encoder switch is a two-part change — encoder *and* resolution, in the same edit.**
Shipping half of it is a new failure, not a partial win.

We reverted to hardware encoding and fixed the relay instead. **The patch is the fix; the
encoder is then free to be whatever looks and sounds best.**

🔴 A measurement trap found on the way: `get_stats()`-style encoder counters
(`output_skipped` / `output_total`) **go stale after a stream restart** — they belong to the
previous encoder instance and read `+0`. Sample a per-session status API instead, twice, and
take the delta.

## ⛔ Measured dead ends

| approach | why it does not work |
|---|---|
| `net.core.rmem_max` / socket buffer tuning | the SRT implementation never calls `setsockopt`; the socket stayed at `rb212992` regardless |
| `udpReadBufferSize` | not wired to the SRT path |
| RTSP transport (any) | RTP has no DTS at all — that is why it is immune, and also why it desyncs. Not a fix, a different problem |
| `alwaysAvailable` / slate | exonerated by measurement: zero slate events across the failures |
| resetting `reorderedFrames` on IDR | measured: smallest fatal gap stays 12. `R` cancels algebraically — see [`01-the-bug.md`](01-the-bug.md) |
| switching relay software | GStreamer, SRS and OvenMediaEngine all carry the container DTS and have no POC code, so the bug is MediaMTX-specific — **but** SRS has no slate at all and closes forwarders on unpublish, OvenMediaEngine's slate transcodes by default, and GStreamer's `fallbackswitch` never sets DTS while `livesync` refuses H.264. Replacing the relay means rebuilding the failover design, which was the larger part of the system |

## Upstream status

- **#5808** — closed and locked, asking for a dedicated report with reproduction instructions.
- **#4892** — proposed carrying the container's DTS through, with working patches. Declined in
  44 minutes: *"propagating DTS is something we want to avoid since it's not supported by many
  protocols and therefore it wouldn't solve much."* An argument about coverage, not correctness.
- **PR #5714** — open and unreviewed since 2026-04-30. See above for why we would not want it
  merged as-is.

⭐ **Why the upstream reports contradicted each other:** several users reported "disabling
B-frames fixed it" and several reported it did not. Both are true. `pic_order_cnt_type` is the
actual variable; B-frames-off is a *proxy* that happens to produce type 2 **on libx264**
(`sps->i_poc_type = param->i_bframe || interlaced || avcintra ? 0 : 2`) and does **not** on
Apple VideoToolbox, which emits type 0 regardless. Measured across 18 encoder configurations
including Baseline profile and Apple's software encoder: none produced type 2. Nobody was
wrong; everybody was describing their own encoder.
