# 02 — The fix

**`patches/mediacommon-poc-resync.diff` — ~12 lines in one file**,
`mediacommon/pkg/codecs/h264/dts_extractor.go`.

## What it does

Where the extractor used to return `too many reordered frames`, it now recognises the jump for
what it is — **access units were lost upstream** — re-syncs `expectedPOC` to the POC it
actually observed, resets the counters, and continues with `ptsDTSDiff = 0` (so `DTS = PTS`
for that one frame).

```go
var observedPOC uint32
var observedPOCValid bool

// ... in the non-IDR reference-picture case, where poc is known:
observedPOC = poc
observedPOCValid = true

// ... at BOTH fatal sites (positive and negative direction):
if (d.reorderedFrames + increase) > maxReorderedFrames {
    if observedPOCValid {
        d.expectedPOC = observedPOC
    }
    d.reorderedFrames = 0
    d.pause = 0
    ptsDTSDiff = 0
    break
}
```

Both error sites are patched. The negative branch is the same discontinuity seen from the
other side of the POC wrap.

## Why the blast radius is small

⭐ **A clean stream never reaches the changed branch.** The condition guarding it is the one
that used to `return` an error — so on a stream that never lost 12 consecutive frames, patched
and stock builds are byte-identical in behaviour. We verified that rather than assuming it:
the `PTS − DTS` distribution over a clean 45-second real capture is *identical* between the
two builds (see [`06-evidence.md`](06-evidence.md)).

What changes is only what happens *instead of dying*:

| | stock | patched |
|---|---|---|
| clean stream | correct DTS | **identical** |
| < 12 frames lost | DTS permanently offset, silently | identical (pre-existing issue, untouched) |
| ≥ 12 frames lost | **every reader disconnected** | one frame at `DTS = PTS`, then correct |

## What it does not do

- ⚠️ **It does not stop frame loss.** It makes the relay tolerant of it. If you have an
  upstream loss problem you still have it — you just no longer lose all your readers over it.
- ⚠️ **It does not fix the sub-threshold DTS drift** (loss < 12 frames leaves the derived DTS
  offset with no recovery path). Fixing that properly means carrying the container's DTS, which
  upstream declined. See [`01-the-bug.md`](01-the-bug.md).
- ⚠️ One frame per discontinuity is emitted with `DTS = PTS`. On a stream with real B-frame
  reordering that frame's DTS is approximate. It is one frame, at a point where frames were
  already lost.

## Building it

⚠️ **Two repositories.** The patch is in **mediacommon**, which MediaMTX consumes as a module.
You patch mediacommon, then build MediaMTX against your patched copy with a `replace`
directive.

```bash
# 1. patch mediacommon
git clone --depth 1 https://github.com/bluenviron/mediacommon.git mc
cd mc
git apply /path/to/patches/mediacommon-poc-resync.diff
go test ./pkg/codecs/h264/...        # upstream's own tests must still pass
cd ..

# 2. build mediamtx against it
git clone --depth 1 --branch <TAG> https://github.com/bluenviron/mediamtx.git mtx
cd mtx
go mod edit -replace github.com/bluenviron/mediacommon/v2=../mc
go mod tidy
go generate ./internal/core/... ./internal/servers/hls/...   # VERSION + hls.min.js, else the build FAILS
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o mediamtx-linux-amd64 .
```

🔴 **Do not skip `go generate`.** MediaMTX generates its version string and the embedded
HLS player; without it the build fails in a way that does not obviously point back here.

⚠️ Check the mediacommon major version in MediaMTX's `go.mod` (`/v2` above) and match the
`replace` path to it.

## Installing over a running instance

```bash
cp -a /opt/mediamtx/mediamtx /opt/mediamtx/mediamtx.<VER>-stock.bak   # keep the stock binary
install -m 755 ./mediamtx-linux-amd64 /opt/mediamtx/mediamtx.new
mv /opt/mediamtx/mediamtx.new /opt/mediamtx/mediamtx
systemctl restart mediamtx
```

- 🔴 **`cp` directly over the running binary fails with `Text file busy`.** Use `install` to a
  temporary name then `mv` — `mv` within a filesystem is atomic and works on a busy file.
- 🔴 **Restart off-air if you can.** Restarting MediaMTX throws a large timestamp discontinuity
  into any live downstream session. We twice saw that wedge a broadcast platform's decoder even
  though our pusher process itself survived. Follow with a fresh downstream session.

## Confirming it is installed

```bash
mediamtx --version      # expect a -dirty suffix, e.g. v1.19.3-dirty
```

The `-dirty` suffix is Go's marker for "built from a modified working tree". It is the only
outward sign the patch is present.

🔴 **This is the failure mode to guard against: a routine upgrade silently reinstates the stock
binary.** No error, no log line, no symptom — until the next loss burst takes your readers
down again. If you have deployment automation, make it assert on the version string.

## Rollback

```bash
cp /opt/mediamtx/mediamtx.<VER>-stock.bak /opt/mediamtx/mediamtx.r
mv /opt/mediamtx/mediamtx.r /opt/mediamtx/mediamtx
systemctl restart mediamtx
```
