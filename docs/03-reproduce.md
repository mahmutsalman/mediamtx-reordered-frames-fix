# 03 — Reproducing it

**Self-contained: one generated file, four commands. No private stream, no packet capture,
no network loss required.** This is the part that turns "it happens randomly in production"
into a deterministic test.

## The idea

Frame loss, from the receiver's point of view, is not "corrupted data" — it is **missing
access units whose neighbours kept their original Picture Order Counts.** `tools/drop_frames.py`
excises a run of access units from an MPEG-TS at the transport layer, leaving every surviving
frame's POC untouched. That is exactly the shape upstream loss has.

## Steps

**1. Encode 20 s with `pic_order_cnt_type = 0`.** libx264 emits type 0 whenever B-frames are on.

```bash
ffmpeg -y -f lavfi -i "testsrc2=size=640x360:rate=30:duration=20" \
  -c:v libx264 -preset veryfast -profile:v high -g 60 -keyint_min 60 -sc_threshold 0 \
  -x264-params "bframes=2" -f mpegts src.ts
```

**2. Find the video PID, then excise 15 access units.**

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=id -of csv=p=0 src.ts   # e.g. 0x100
python3 tools/drop_frames.py src.ts gap.ts 0x100 200 15
```

**3. Publish `gap.ts` into MediaMTX and read it back.**

```bash
mediamtx tools/mtxtest.yml &

ffmpeg -nostdin -loglevel error -re -i gap.ts -c copy \
  -f mpegts "srt://127.0.0.1:18890?streamid=publish:live" &

ffmpeg -nostdin -loglevel error \
  -i "srt://127.0.0.1:18890?streamid=read:live&latency=200000" \
  -t 20 -c copy -f mpegts -y out.ts
```

**4. Read the server log.**

```
[SRT] [conn 127.0.0.1:xxxxx] closed: too many reordered frames (11)
```

Stock MediaMTX kills the reader. The patched build does not.

## The control that makes it conclusive

Re-encode the *same* content with `pic_order_cnt_type = 2` (`bframes=0`) and apply the *same*
15-frame gap:

```bash
ffmpeg -y -f lavfi -i "testsrc2=size=640x360:rate=30:duration=20" \
  -c:v libx264 -preset veryfast -g 60 -keyint_min 60 -sc_threshold 0 \
  -x264-params "bframes=0" -f mpegts src_t2.ts
python3 tools/drop_frames.py src_t2.ts gap_t2.ts 0x100 200 15
```

| case | `pic_order_cnt_type` | gap | reader |
|---|---|---|---|
| A | 0 | none | survives |
| B | 0 | 15 AUs | **KILLED `(11)`** |
| C | 2 | 15 AUs | survives |

B vs C isolates the variable to a single SPS field. B vs A isolates it to the gap.
Note `(11)` = `G − 1` = 15 − ... — see the threshold algebra in [`01-the-bug.md`](01-the-bug.md);
the printed value tracks the gap, and 12 is the smallest gap that fires at all.

## Verifying `pic_order_cnt_type` in your own stream

`ffprobe` does not expose this field. Use `trace_headers`, and note it needs `-c copy` —
re-decoding discards the SPS you care about:

```bash
ffmpeg -loglevel error -y -i "<source>" -t 3 -c copy -f mpegts /tmp/v.ts
ffmpeg -loglevel info -i /tmp/v.ts -c copy -bsf:v trace_headers -f null - 2>&1 \
  | grep pic_order_cnt_type | head -3
```

## Testing a candidate build with zero production impact

`tools/mtxtest.yml` runs a **second MediaMTX** beside your production one, on spare ports.

⭐ **`moq: no` is the unlock.** Without it the second instance clashes on `:8892` and dies at
startup. It works from **v1.19.3**; it did **not** in v1.19.1, which is why older notes claim a
second instance cannot coexist.

```bash
/tmp/mtx-candidate tools/mtxtest.yml >/tmp/t.log 2>&1 &  M=$!
sleep 2
ffmpeg -nostdin -loglevel error -re -i real-capture.ts -c copy \
  -f mpegts "srt://127.0.0.1:18890?streamid=publish:live" >/dev/null 2>&1 &  P=$!
sleep 3
ffmpeg -nostdin -loglevel error -i "srt://127.0.0.1:18890?streamid=read:live&latency=200000" \
  -t 20 -c copy -f mpegts -y /tmp/out.ts >/dev/null 2>&1
kill $P $M

# THE assertion: the pts-dts gap must stay in 0..9000, matching the source
ffprobe -v error -select_streams v:0 -show_entries packet=pts,dts -of csv=p=0 /tmp/out.ts \
 | awk -F, '$2!="N/A"{g=$1-$2; if(g<0||g>9000) bad++} END{print "out-of-range gaps:", bad+0}'
```

🔴 **Kill by PID, never `pkill -f <pattern>`.** On a remote host that pattern matches its own
ssh/bash command line and kills your session. We did it three times in one night.

🔴 **When grepping a patched build's log, match `closed: too many reordered`, not
`too many reordered`** — patched builds may print that phrase inside their own diagnostics,
and the loose pattern will report a kill that never happened.

## Two traps that will give you a false result

- ⚠️ **Test over the protocol you actually run.** Our first test of one candidate ran over
  RTMP, which that patch does not touch — so stock and patched were identical and we nearly
  shipped on a result that meant nothing.
- ⚠️ **Test with a real capture, not a synthetic clip.** `testsrc2` is fine for *this*
  reproduction, because the variable under test is the SPS field. It is **not** fine for
  validating a candidate fix: synthetic clips have well-behaved timestamps and no wrap state,
  and they hid a real failure completely. See
  [`05-testing-methodology.md`](05-testing-methodology.md).
