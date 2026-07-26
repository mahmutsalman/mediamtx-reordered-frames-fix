# 05 — How to test a candidate

> Written after three patches were deployed to a live system in one night, and three different
> ways of breaking it were discovered — by a human saying "it looks wrong", never by a metric.

| # | candidate | what the test proved | what it missed | what viewers got |
|---|---|---|---|---|
| 1 | PR #5714 | "the reader survives" ✅ | DTS accuracy after recovery | **black screen** |
| 2 | carry-DTS | "DTS matches the source" ✅ *(synthetic clip)* | the live stream's wrap state | **judder** |
| 3 | — | every health metric green | metrics cannot see either failure | — |

🔴 **The pattern: every health check we owned stayed GREEN through both failures.** Socket
`ESTABLISHED`, process alive, bitrate nominal, 30 fps, 0% encoding lag, 0% render lag, DTS
monotonic, zero timestamp errors. All true. All useless.

## The rules

### 1. Test with a real capture of the live feed, never a synthetic clip

Keep a 45–60 s capture of your actual production stream and test against that. Synthetic
`testsrc2` clips have well-behaved timestamps and no accumulated wrap state, and they hid
failure #2 completely — it was *bit-for-bit exact* on synthetic input and produced a constant
0.61 s offset on the real thing.

```bash
ffmpeg -loglevel error -y -i "srt://127.0.0.1:8890?streamid=read:live" -t 60 -c copy \
  -f mpegts samples/live-real-60s.ts
```

### 2. Assert on the PTS↔DTS *relationship*, not DTS alone

Failure #2 had DTS incrementing perfectly by 3000 per frame and never going backwards — while
sitting a constant 0.61 s behind PTS. Every "is DTS sane?" check passed.

The correct gap for 1–2 B-frames of reordering is **3000–9000 ticks** at 90 kHz. Check the gap:

```bash
ffprobe -v error -select_streams v:0 -show_entries packet=pts,dts -of csv=p=0 out.ts \
 | awk -F, '$2!="N/A"{g=$1-$2; if(g<0||g>9000) bad++} END{print "out-of-range gaps:", bad+0}'
```

Better still, compare the **distribution** against the same measurement on a stock build with a
clean input. Ours is in [`06-evidence.md`](06-evidence.md) — the shape should be identical.

### 3. A metric that cannot go red for the failure you fear is not a test

Before trusting any check, ask: **"what would this read if the thing I am afraid of were
happening?"** If the answer is "the same as now", it is not a test, it is decoration.

"Is the process alive" cannot detect a corrupt timeline. "Is the socket established" cannot
detect a black screen. Both were green through failure #1.

### 4. Test over the protocol you actually run

Failure #2's first test ran over RTMP. The patch does not touch the RTMP path at all, so stock
and patched were necessarily identical — a result that meant nothing, and nearly shipped.

### 5. Deploy off-air

Restarting the server throws a large timestamp discontinuity into any live downstream session.
Twice we saw that wedge a broadcast platform's decoder even though our own pusher survived.

### 6. Test on an isolated instance first

`tools/mtxtest.yml` runs a second server on spare ports with zero production impact
(`moq: no` is what makes it possible — v1.19.3+). There is no reason for a first look at a
candidate to touch production at all.

## Operational traps that cost us real time

- 🔴 **`pkill -f <pattern>` on a remote host matches its own ssh/bash command line and kills
  your session.** Three times in one night, and it was already documented from a previous
  incident. **Kill by PID.**
- 🔴 **`cp` over a running binary → `Text file busy`.** Use `install` to a temp name, then `mv`.
- 🔴 **Grep for `closed: too many reordered`, not `too many reordered`.** A patched build may
  print the phrase in its own diagnostics; the loose pattern reports a kill that did not happen.
  This produced a false "the patch failed" verdict on a build that was working.
- ⚠️ **A test that sends commands to a running ffmpeg needs `-re`.** Without pacing, ffmpeg
  encodes 20 s of frames in about a second and exits before your test's `sleep` finishes —
  indistinguishable from "the feature does not work".
- ⚠️ **`md5` is meaningless downstream of a lossy encoder.** Even a *static* source yields
  different hashes per frame, because quantisation varies with GOP position. Compare the
  picture, not the bytes.
- ⚠️ **`testsrc2` is a moving pattern.** "The output frames differ" proves nothing about the
  change you made.

## The general lesson

🔴 **Three fixes were prescribed from reading and all three were wrong:** bigger socket
buffers, RTSP-over-TCP, disable B-frames. Each was refuted by a measurement taking under five
minutes.

The failure was not insufficient research. It was **asserting a mechanism without measuring its
precondition on this system.** Four upstream reports agreed that disabling B-frames worked —
and that was true about *their* encoders and said nothing about ours.

⭐ **Before prescribing a fix that depends on a property, measure that property here.**
