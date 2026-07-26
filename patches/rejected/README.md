# Rejected candidates — do NOT deploy

Kept as a record, because both of these **passed their tests** and then broke a live stream in
a way the tests could not see. The full write-up is in
[`../../docs/04-rejected-approaches.md`](../../docs/04-rejected-approaches.md).

- **`mediamtx-pr5714-dts-recover.diff`** — nils the DTS extractor and re-primes on the next
  IDR. The reader survives (2 recoveries, 0 kills in 10 minutes live) **but the re-primed
  timeline comes back wrong**: video and audio pushed in opposite directions, a sustained
  ~230 timestamp errors per 15 s, and **viewers saw a black screen** while 6.2 Mbps of
  structurally valid video kept arriving.

- **carry-DTS** (not included here; described in the docs) — carries the container's DTS
  instead of deriving it. Bit-for-bit exact on a synthetic clip; on a live feed the masked
  delta landed on a constant ~0.61 s offset ⇒ judder. ⚠️ The *idea* is sound and worth
  revisiting — the container really does carry the right DTS — but the wrap handling must be
  validated against a real capture, never `testsrc2`.
