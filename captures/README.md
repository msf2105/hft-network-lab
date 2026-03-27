# Packet Captures

Sample captures from a live lab session. Open with Wireshark.

---

## Files

| File | Description |
|---|---|
| `bgp_keepalives.pcap` | BGP keepalives every 3 seconds between Exchange1 and ColoPeer |
| `bgp_update.pcap` | BGP UPDATE with MED=10 prefix advertisement triggered by `clear ip bgp soft out` |
| `bfd_hellos.pcap` | BFD hellos at ~150ms intervals, TTL=255, UDP port 3784 |
| `failover_event.pcap` | Complete failover sequence: BFD detection → BGP teardown → session rebuild |

---

## Wireshark Filters

```
bgp                     BGP messages only
udp.port == 3784        BFD hellos only
tcp.port == 179         All BGP TCP traffic
ip.addr == 10.0.1.1     Traffic from/to Exchange1
bgp.type == 2           BGP UPDATE messages only
bgp.type == 4           BGP KEEPALIVE messages only
```

---

## What to look for

### In bgp_keepalives.pcap
- Keepalives exactly 3 seconds apart (matches `timers 3 9` config)
- 19-byte payload — minimum BGP message size, just a header
- TCP ACK pattern: KEEPALIVE → KEEPALIVE reply → bare ACK

### In bgp_update.pcap
At the bottom of the capture, look for `Update Message (2)`:
```
Origin: IGP
AS Path: 65001
Next Hop: 10.0.1.1
Multi Exit Discriminator: 10    ← MED=10 visible on the wire
Updated routes: 172.16.1.0/24
```
This is the route-map `SET-LOW-MED` in action, visible at the packet level.

### In bfd_hellos.pcap
- UDP source port 49152, destination port 3784
- TTL=255 — single-hop enforcement, cannot be forwarded
- Payload ~24 bytes — intentionally minimal for high-rate sending
- Both directions firing independently every ~150ms

### In failover_event.pcap
Look for these events in sequence:

1. **Normal BFD** — both directions, ~150ms intervals
2. **ICMP unreachable** — `10.0.1.1 udp port 3784 unreachable`
   This is the detection event. Three consecutive unreachables = 450ms detection
3. **TCP FIN** on port 179 — BGP session torn down by BFD signal
4. **TCP SYN** on port 179 — BGP reconnecting
5. **BGP OPEN** exchange — session negotiation
6. **BGP UPDATE** — routes re-advertised
7. **BFD hellos resume** — both directions, session healthy again

Total time from step 2 to step 7: approximately 1.5-2 seconds.

---

## Generating Your Own Captures

On the EVE-NG host:

```bash
# Capture everything on the Exchange1-ColoPeer segment
sudo tcpdump -i vnet0_1 -n -w /tmp/my_capture.pcap

# Trigger a BGP UPDATE
# (on Exchange1): clear ip bgp 10.0.1.2 soft out

# Trigger a failover
# (on Exchange1): conf t → interface Gi0/0 → shutdown

# Stop capture
# Ctrl+C

# Copy to your machine via SCP:
scp user@eve-ng-host:/tmp/my_capture.pcap ./captures/
```
