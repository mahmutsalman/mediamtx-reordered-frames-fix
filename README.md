# MediaMTX — `too many reordered frames` reader kill, root cause and fix

**A ~12-line patch that stops MediaMTX from disconnecting every reader of a path when frames
go missing upstream.**

If you run MediaMTX as a relay and your downstream recordings/broadcasts break into pieces at
random — while your uplink is provably fine — this is very likely why.

```
[SRT] [conn 10.0.0.1:51000] closed: too many reordered frames (11)
```

- The stream is **healthy**. The encoder reports zero dropped frames.
- It happens on **loopback**, where there is no network to blame.
- **Every reader of the path dies in the same second, with the same number.**

---

## The one-paragraph version

MediaMTX **discards the DTS the container already carries** and re-derives it from H.264
Picture Order Count. When a run of access units is lost anywhere upstream, the surviving
frames' POCs jump, the derived `PTS − DTS` gap overshoots the hard limit
`maxReorderedFrames = 10`, and the extractor returns an error. That error propagates up and
**tears down every reader of the path** — not just the one that saw the bad frame.

The threshold is exactly **12 consecutive lost access units**, deterministic, and reachable
only when the SPS says `pic_order_cnt_type = 0`.

**The fix reinterprets that jump as what it actually is — a discontinuity, not reordering.**
It re-syncs the expected POC to the one actually observed, resets the counters, and continues.
A clean stream never reaches the changed branch.

---

## Contents

| | |
|---|---|
| [`docs/01-the-bug.md`](docs/01-the-bug.md) | mechanism, the threshold algebra, why `pic_order_cnt_type` decides it |
| [`docs/02-the-fix.md`](docs/02-the-fix.md) | what the patch does, why the blast radius is tiny, how to build it |
| [`docs/03-reproduce.md`](docs/03-reproduce.md) | self-contained repro: one file, four commands, no private stream |
| [`docs/04-rejected-approaches.md`](docs/04-rejected-approaches.md) | two patches that shipped and were rolled back, and why. Plus measured dead ends |
| [`docs/05-testing-methodology.md`](docs/05-testing-methodology.md) | how to test a candidate. Written after three failed deploys in one night |
| [`docs/06-evidence.md`](docs/06-evidence.md) | the measurements, against a real capture rather than a synthetic clip |
| [`patches/mediacommon-poc-resync.diff`](patches/mediacommon-poc-resync.diff) | **the patch** |
| [`tools/drop_frames.py`](tools/drop_frames.py) | excises N access units from an MPEG-TS, leaving POCs untouched |
| [`tools/mtxtest.yml`](tools/mtxtest.yml) | isolated second instance on spare ports, for zero-impact testing |

---

## Am I affected?

Two conditions, both required:

**1. Your H.264 stream has `pic_order_cnt_type = 0` in its SPS.**

`ffprobe` cannot show this field. You need `trace_headers`, and it only works on a copied
(not re-decoded) stream:

```bash
ffmpeg -loglevel error -y -i "<your source>" -t 3 -c copy -f mpegts /tmp/v.ts
ffmpeg -loglevel info -i /tmp/v.ts -c copy -bsf:v trace_headers -f null - 2>&1 \
  | grep pic_order_cnt_type | head -3
```

**2. Something upstream loses runs of ~12+ consecutive frames.**

⚠️ Note the asymmetry: **this patch makes the stream tolerant of gaps. It does not stop the
gaps.** If you can find and remove the source of your frame loss, do that as well — but a
relay should not detonate because its input had a hole in it.

## The escape that is not always available

At `pic_order_cnt_type = 2` the extractor short-circuits (`DTS = PTS`, no reorder arithmetic),
so **the error is unreachable**. libx264 emits type 2 when B-frames are off:

```c
/* x264 encoder/set.c */
sps->i_poc_type = param->i_bframe || param->b_interlaced || param->b_avcintra ? 0 : 2;
```

🔴 **But "just disable B-frames" is a proxy, not the rule, and it fails on some encoders.**
Apple VideoToolbox emits `pic_order_cnt_type = 0` **even with B-frames disabled** — measured
across 18 encoder configurations, including Baseline profile and Apple's software encoder;
none produced type 2. That single fact reconciles the contradictory reports upstream: everyone
was right about their own encoder.

So the encoder-side workaround exists, but only if your encoder can be changed *and* can emit
type 2 *and* the switch does not cost you something else. In our case it did — see
[`docs/04-rejected-approaches.md`](docs/04-rejected-approaches.md).

---

## Status

- **Running in production since 2026-07-26** on a MediaMTX SRT relay. Patched binary reports
  `v1.19.3-dirty`.
- **2026-07-27 — a full day of real broadcasting, `too many reordered frames` count: 0.**
  Worth stating precisely, because that day was *not* quiet: the same relay had two upstream
  broadcast disconnects and several restarts of its downstream publisher, all from unrelated
  causes. Through all of it the extractor never tore down a reader. Before the patch, streams
  that looked completely clean were each absorbing 8-10 reader-kills.
- `mediacommon`'s own H.264 test suite passes unchanged.
- **Not upstreamed.** Issue #5808 was closed and locked; a related proposal (#4892, carrying
  the container's DTS through) was declined. See
  [`docs/04-rejected-approaches.md`](docs/04-rejected-approaches.md#upstream-status).

🔴 **If you deploy this, record that you did.** A routine "upgrade MediaMTX" silently restores
the stock binary — no error, no symptom, until the next loss burst. Check
`mediamtx --version` for the `-dirty` suffix.

## Licence

The patch modifies [`bluenviron/mediacommon`](https://github.com/bluenviron/mediacommon),
which is MIT licensed; the patched lines remain under that licence and its copyright.
The documentation and tools in this repository are MIT ([`LICENSE`](LICENSE)).
