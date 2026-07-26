#!/usr/bin/env python3
"""Excise a run of video access units from an MPEG-TS, leaving everything else intact.

Simulates upstream frame loss: the POC of surviving frames is UNCHANGED, so the
receiver sees a Picture Order Count gap -- exactly what network loss produces.
Usage: drop_frames.py in.ts out.ts VIDEO_PID SKIP_AUS DROP_AUS
"""
import sys
inp, outp, pid, skip, drop = sys.argv[1], sys.argv[2], int(sys.argv[3],0), int(sys.argv[4]), int(sys.argv[5])
data = open(inp,'rb').read()
TS = 188
au = -1           # access units seen on the video PID (counted by PUSI)
kept = bytearray(); dropped = 0
for o in range(0, len(data)-TS+1, TS):
    p = data[o:o+TS]
    if p[0] != 0x47:
        continue
    p_pid = ((p[1] & 0x1F) << 8) | p[2]
    pusi  = bool(p[1] & 0x40)
    if p_pid == pid:
        if pusi:
            au += 1
        if skip <= au < skip + drop:
            dropped += 1
            continue
    kept += p
open(outp,'wb').write(kept)
print(f"  video AUs seen: {au+1}   TS packets dropped: {dropped}   ({len(data)-len(kept)} bytes)")
