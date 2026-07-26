# 01 — The bug

## Symptom

```
[SRT] [conn 127.0.0.1:44627] closed: too many reordered frames (11)
```

Every reader of the path is disconnected. Not the one that saw a bad frame — **all of them,
in the same second, with the same number.** Downstream consumers reconnect, which produces a
splice, and if the downstream is a broadcast platform that splice can end the broadcast and
start a new recording.

What makes it disorienting:

- It happens on **loopback**. There is no network between server and reader to blame.
- The **encoder reports zero dropped frames**, so nothing looks wrong at the source.
- The relay is `-c copy` end to end, so "the transcode is struggling" cannot be it.
- Every health metric stays green: sockets `ESTABLISHED`, bitrate nominal, fps steady.

## Where it comes from

`mediacommon/pkg/codecs/h264/dts_extractor.go`.

MPEG-TS (and RTMP, and most containers) carry an explicit DTS per access unit. MediaMTX does
not use it. `internal/unit/unit.go` has no DTS field at all —

```go
type Unit struct {
    PTS int64        // relative time
    NTP time.Time    // absolute time
    RTPPackets []*rtp.Packet
    Payload  any
}
```

— so DTS is structurally discarded at ingest, on every protocol, and re-derived downstream
from the H.264 **Picture Order Count** in each slice header.

On a clean stream this reproduces the container's DTS bit-for-bit. The trouble starts when the
input has a hole in it.

## The mechanism

The extractor keeps `expectedPOC` and advances it by `pocIncrement` per access unit. It then
measures how far the *observed* POC is from the expectation, converts that into a
`ptsDTSDiff` in frames, and treats the result as **reordering depth**:

```go
case ptsDTSDiff > (2*d.reorderedFrames + 1):
    increase := ptsDTSDiff - (2*d.reorderedFrames + 1)
    if (d.reorderedFrames + increase) > maxReorderedFrames {
        return 0, false, fmt.Errorf("too many reordered frames (%d)", d.reorderedFrames+increase)
    }
```

When a run of access units is **lost upstream**, the surviving frames keep their original POC
values — they were assigned by the encoder, and loss does not renumber them. So `expectedPOC`
falls behind the real stream by exactly the size of the gap, and the extractor reads that
offset as "this stream suddenly has enormous reordering depth."

It is not reordering. It is a **discontinuity**. The code has no way to express that, so it
errors — and the error tears down the path.

## The threshold is exactly 12, and it does not depend on history

Let `G` = number of consecutive access units lost, `R` = `reorderedFrames` at the time.

After the gap, the observed POC is `G` increments ahead of `expectedPOC`, so
`ptsDTSDiff = G`. The branch condition is `ptsDTSDiff > 2R + 1`, giving
`increase = G − (2R + 1)`, and the failure test is:

```
R + increase        > 10
R + G − 2R − 1      > 10
G − R − 1           > 10
```

⚠️ Careful here — this is where an earlier analysis of ours went wrong. It looks like larger
`R` makes failure *less* likely, which would suggest accumulated reordering protects you.
But `R` only reaches a value by having survived a `ptsDTSDiff` that large, and the extractor
raises `R` toward `ptsDTSDiff` as it goes. In the failing case `R` is at its plateau and the
algebra collapses to:

> **`G ≥ 12`.** Always. Independent of accumulated state.

And the number printed in the error is literally **`G − 1`**.

🔴 **Consequence worth knowing:** resetting `reorderedFrames` on every IDR — a natural-looking
mitigation — **does not help.** We measured it: the smallest fatal gap stays 12.

Past the POC wrap the reported value appears as `32 − N` instead; same mechanism, other side
of the modulus.

## Why `pic_order_cnt_type` decides everything

At `pic_order_cnt_type = 2`, POC is defined to increase monotonically with decode order —
there is no reordering to express — so the extractor short-circuits: `DTS = PTS`, no
arithmetic, **the error branch is unreachable.**

At type 0, POC is a wrapping counter with reordering semantics, and all of the above applies.

| `pic_order_cnt_type` | 15-AU gap | outcome |
|---|---|---|
| 0 | yes | **reader killed** |
| 0 | no | fine |
| 2 | yes | fine |

That table is reproducible from a file in four commands — see
[`03-reproduce.md`](03-reproduce.md).

## The sharpest part

The container was carrying the right answer the whole time.

Apple VideoToolbox emits `PTS_DTS_flags = '10'` on every PES packet, which explicitly asserts
`DTS == PTS` — *this stream has no reordering.* MediaMTX drops that on the floor
(`to_stream.go` binds the DTS callback argument to `_`), recomputes DTS from POC, and then
disconnects the reader claiming there is too much reordering.

There is also a quieter failure below the kill threshold: after any loss smaller than 12
frames, the derived DTS is **permanently wrong and never re-syncs** — up to 10 frames of
silent error that nothing reports.

Carrying the container DTS through was proposed upstream as issue **#4892** with working
patches, and declined in 44 minutes:

> *"propagating DTS is something we want to avoid since it's not supported by many protocols
> and therefore it wouldn't solve much."*

That is an argument about breadth of coverage, not about correctness. It is also why this
patch takes the other route — leave the architecture alone, and just stop treating a
discontinuity as an error.
