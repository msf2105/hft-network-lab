# HFT Network Lab

A fully functional High-Frequency Trading network simulation built in EVE-NG. Simulates exchange colocation infrastructure with BGP path selection, BFD fast failover, DPDK kernel bypass, and RDMA — the core networking technologies used by firms like Jump Trading, Citadel, and Virtu.

![Topology](docs/topology.png)

---

## What This Lab Teaches

| Technology | What You Learn |
|---|---|
| BGP LOCAL_PREF + MED | Deterministic primary/backup path selection |
| BFD at 150ms intervals | Sub-500ms failure detection vs 9-second hold timers |
| Prefix filtering | Protecting against route leaks from exchanges |
| DPDK + hugepages | Kernel bypass packet processing with testpmd |
| SoftRoCE + rping | RDMA memory transfer without kernel involvement |
| tcpdump / packet capture | BGP UPDATE structure, BFD hello timing, failover events |

---

## Topology

```
  Exchange1 (AS65001)              Exchange2 (AS65003)
  172.16.1.0/24                    172.16.2.0/24
  MED=10 (primary signal)          MED=50 (backup signal)
        |                                |
        | 10.0.1.0/30                    | 10.0.2.0/30
        | LOCAL_PREF=200                 | LOCAL_PREF=100
        |                                |
        +---------- ColoPeer -----------+
                    (AS65002)
               192.168.100.0/24
               (Jump colo simulation)
                       |
                  Ubuntu-DPDK
               192.168.200.10/24
               (DPDK + RDMA node)
```

### Traffic flow
- ColoPeer prefers Exchange1 (LOCAL_PREF 200) as the primary path
- Exchange1 signals primary status via MED=10 (lower = preferred)
- BFD detects Exchange1 failure in ~450ms and BGP reconverges to Exchange2
- Ubuntu node runs DPDK in userspace and RDMA via SoftRoCE

---

## Requirements

### EVE-NG host
- EVE-NG Community or Pro (tested on Community Edition)
- 8GB+ RAM on the EVE-NG host
- Images required (see Image Setup below):
  - `vios-adventerprisek9-m.SPA.159-3.M6` (or any IOSv version)
  - `linux-linux-ubuntu-server-20.04`
  - `linux-linux-netem`

### Your machine
- EVE-NG web UI access
- SSH access to EVE-NG host
- PuTTY or telnet client for console access

---

## Image Setup

EVE-NG requires specific image naming conventions. If your IOSv images are prefixed with `iosv-` instead of `vios-`, create a symlink:

```bash
sudo ln -s /opt/unetlab/addons/qemu/iosv-vios-adventerprisek9-m.SPA.159-3.M6 \
           /opt/unetlab/addons/qemu/vios-adventerprisek9-m.SPA.159-3.M6
```

If your images are stored in Google Cloud Storage (GCS-backed EVE-NG), extract them first:

```bash
# IOSv
sudo mkdir -p /tmp/iosv-extract
sudo tar -xzf "/mnt/eve-gcs/Cisco IOSv/vios-adventerprisek9-m.SPA.159-3.M6.tgz" \
  -C /tmp/iosv-extract/

# Move the qcow2 to the correct location
sudo find /tmp/iosv-extract -name "virtioa.qcow2" -exec mv {} \
  /opt/unetlab/addons/qemu/iosv-vios-adventerprisek9-m.SPA.159-3.M6/ \;

# Ubuntu
sudo tar -xzf "/mnt/eve-gcs/Linux/linux-ubuntu-server-20.04.tgz" \
  -C /opt/unetlab/addons/qemu/linux-linux-ubuntu-server-20.04/

sudo find /opt/unetlab/addons/qemu/linux-linux-ubuntu-server-20.04 \
  -name "virtioa.qcow2" -mindepth 2 -exec mv {} \
  /opt/unetlab/addons/qemu/linux-linux-ubuntu-server-20.04/ \;
```

---

## Quick Start

### 1. Import the lab

```
EVE-NG web UI → your folder → import icon (cloud + arrow) → upload lab.unl
```

> **Note:** The lab file uses `vios-adventerprisek9-m.SPA.159-3.M6` as the image name.
> Right-click each IOSv node → Edit → Image → select your installed version if different.

### 2. Run the setup script

SSH into your EVE-NG host and run:

```bash
sudo bash scripts/setup.sh
```

This script:
- Creates per-node QEMU overlay disks (required — EVE-NG won't do this automatically on GCS-backed instances)
- Sets up NAT so the Ubuntu node can reach the internet
- Adds the bridge IP for the DPDK network segment
- Fixes EVE-NG permissions

### 3. Start all nodes

```
Right-click canvas → Start all nodes
```

Wait 2-3 minutes for IOSv to boot.

### 4. Apply router configs

Telnet into each router from the EVE-NG host:

```bash
telnet 127.0.0.1 32769   # Exchange1
telnet 127.0.0.1 32770   # ColoPeer
telnet 127.0.0.1 32771   # Exchange2
```

Paste the configs from the `configs/` directory into each node.

### 5. Verify BGP

On ColoPeer:
```
show bgp ipv4 unicast summary
show bgp ipv4 unicast
show bfd neighbors
```

Expected output:
```
     Network          Next Hop       Metric LocPrf  Path
 *>  172.16.1.0/24   10.0.1.1           10    200   65001 i
 *>  172.16.2.0/24   10.0.2.2           50    100   65003 i
 *>  192.168.100.0   0.0.0.0             0          i
```

---

## Lab Exercises

### Exercise 1 — Verify BGP path preference
On ColoPeer, confirm Exchange1 is the preferred primary path:
```
show bgp ipv4 unicast 172.16.1.0/24
```
Look for `localpref 200` and `metric 10`.

### Exercise 2 — BFD failover test
Open two terminal windows.

**Terminal 1 — watch the failover:**
```bash
sudo tcpdump -i vnet0_1 -n -tttt 2>/dev/null | grep -E "3784|179"
```

**Terminal 2 — trigger failure:**
```bash
telnet 127.0.0.1 32769
```
```
enable
conf t
interface GigabitEthernet0/0
 shutdown
```

Watch Terminal 1. BFD detects the failure in ~450ms. BGP rebuilds in ~400ms.

Restore:
```
no shutdown
```

### Exercise 3 — Route filtering (prefix list)
Test that the prefix filter blocks unauthorized routes:
```
# On Exchange1 — advertise a bogus prefix
conf t
ip route 10.99.99.0 255.255.255.0 Null0
router bgp 65001
 address-family ipv4
  network 10.99.99.0 mask 255.255.255.0
```

On ColoPeer — verify it's blocked:
```
show bgp ipv4 unicast neighbors 10.0.1.1 received-routes
show bgp ipv4 unicast
```
The bogus prefix should appear in `received-routes` but NOT in the main BGP table.

### Exercise 4 — DPDK kernel bypass
On the Ubuntu node (telnet 127.0.0.1 32773):
```bash
# Set up networking first
ip addr add 192.168.200.10/24 dev eth0 2>/dev/null
ip route add default via 192.168.200.1 2>/dev/null
echo "nameserver 8.8.8.8" > /etc/resolv.conf

# Run testpmd
dpdk-testpmd -l 0-1 -n 1 --vdev=net_tap0 -- --interactive --nb-cores=1
```
At the `testpmd>` prompt:
```
show port info all
start
show port stats all
stop
quit
```

### Exercise 5 — RDMA via SoftRoCE
```bash
# Load module and create RoCE device
modprobe rdma_rxe
rdma link add rxe0 type rxe netdev eth0
rdma link

# Run RDMA ping test
rping -s -v -C 5 -a 192.168.200.10 &
sleep 1 && rping -c -a 192.168.200.10 -v -C 5
```

You'll see RDMA completions firing — memory transferred directly between processes with no kernel TCP stack.

---

## Packet Captures

The `captures/` directory contains sample pcap files:

| File | Contents |
|---|---|
| `bgp_keepalives.pcap` | BGP keepalive pattern — one every 3 seconds |
| `bgp_update.pcap` | BGP UPDATE message with MED=10 and prefix advertisement |
| `bfd_hellos.pcap` | BFD hellos at 150ms intervals |
| `failover_event.pcap` | Complete failover: BFD detection → BGP teardown → session rebuild |

Open with Wireshark. Filter by:
- `bgp` — BGP messages only
- `udp.port == 3784` — BFD hellos only
- `tcp.port == 179` — all BGP TCP traffic

---

## Key Configuration Values

| Parameter | Value | Rationale |
|---|---|---|
| BGP timers | 3s keepalive / 9s hold | Aggressive but VM-stable |
| BFD interval | 150ms tx/rx | Fastest reliable for QEMU VMs |
| BFD multiplier | 3 | 450ms detection time |
| LOCAL_PREF primary | 200 | Deterministic path selection |
| LOCAL_PREF backup | 100 | Clear preference hierarchy |
| MED Exchange1 | 10 | Signal primary preference |
| MED Exchange2 | 50 | Signal backup preference |
| DPDK hugepages | 256 × 2MB | 512MB total for testpmd |

---

## How BFD Compares to Hold Timer

| Method | Detection Time | How |
|---|---|---|
| BGP hold timer (default) | 180 seconds | 3 missed keepalives × 60s |
| BGP hold timer (tuned) | 9 seconds | 3 missed keepalives × 3s |
| BFD (this lab) | ~450ms | 3 missed hellos × 150ms |
| BFD (production bare metal) | ~150ms | 3 missed hellos × 50ms |
| BFD (max performance) | ~30ms | 3 missed hellos × 10ms |

The log message changes from `hold time expired` to `Interface flap` when BFD is working — the latter means sub-second detection.

---

## IP Reference

| Node | Interface | IP | Purpose |
|---|---|---|---|
| Exchange1 | Gi0/0 | 10.0.1.1/30 | BGP to ColoPeer |
| Exchange1 | Lo0 | 1.1.1.1/32 | Router ID |
| ColoPeer | Gi0/0 | 10.0.1.2/30 | BGP to Exchange1 |
| ColoPeer | Gi0/1 | 10.0.2.1/30 | BGP to Exchange2 |
| ColoPeer | Lo0 | 2.2.2.2/32 | Router ID |
| Exchange2 | Gi0/0 | 10.0.2.2/30 | BGP to ColoPeer |
| Exchange2 | Lo0 | 3.3.3.3/32 | Router ID |
| Ubuntu-DPDK | eth0 | 192.168.200.10/24 | Internet/NAT |
| Ubuntu-DPDK | eth1 | 10.0.1.10/32 | Router segment |
| EVE-NG host | vnet0_5 | 192.168.200.1/24 | NAT gateway |

---

## Troubleshooting

**Nodes won't start (error 12)**
EVE-NG failed to create the QEMU overlay disk. Run `scripts/setup.sh` — it creates overlays manually.

**Image not found error**
Your image folder name doesn't match the template. Check:
```bash
ls /opt/unetlab/addons/qemu/ | grep vios
```
Right-click each IOSv node → Edit → Image → select the correct version.

**BGP sessions stuck in Idle/Active**
Router configs weren't applied. Telnet into each node and paste configs from `configs/`.

**BFD not forming (RD=0)**
BFD interval not applied to interfaces. Run on each router:
```
conf t
interface GigabitEthernet0/0
 bfd interval 150 min_rx 150 multiplier 3
 no bfd echo
```

**Ubuntu can't reach internet**
NAT not set up. Run on EVE-NG host:
```bash
ip addr add 192.168.200.1/24 dev vnet0_5 2>/dev/null
echo 1 > /proc/sys/net/ipv4/ip_forward
iptables -t nat -A POSTROUTING -s 192.168.200.0/24 -j MASQUERADE
```

**DPDK testpmd fails (Cannot allocate memory)**
Increase hugepages:
```bash
echo 256 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

---

## Background

This lab was built as interview preparation for a Network Engineer role at a High-Frequency Trading firm. The topology simulates the actual infrastructure pattern used at HFT firms — servers co-located inside exchange data centers with redundant BGP sessions, aggressive BFD timers, and kernel bypass networking for microsecond-level latency.

The full build process including troubleshooting GCS image extraction, QEMU overlay issues, EVE-NG template mismatches, and network namespace configuration is documented in the commit history.

---

## Contributing

PRs welcome. Ideas for extensions:
- Juniper vMX version of the router configs (Junos syntax)
- Multicast market data feed simulation (PIM-SM)
- iperf3 latency measurement between Ubuntu and routers
- Ansible playbook to automate config deployment
- Second Ubuntu node for two-machine RDMA benchmarking with `ib_read_lat`

---

## License

MIT — use freely, including for interview prep.
