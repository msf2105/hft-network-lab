# DPDK and RDMA Setup Guide

This guide walks through setting up DPDK kernel bypass and RDMA on the Ubuntu-DPDK node in the HFT Network Lab.

---

## Prerequisites

The Ubuntu node needs internet access. Verify connectivity first:

```bash
ping -c 3 8.8.8.8
```

If it fails, configure networking manually:

```bash
# Set IP on the internet-facing interface
ip addr add 192.168.200.10/24 dev eth0 2>/dev/null
ip link set eth0 up
ip route add default via 192.168.200.1 2>/dev/null
echo "nameserver 8.8.8.8" > /etc/resolv.conf

# If still failing, check NAT is set up on EVE-NG host:
# ip addr add 192.168.200.1/24 dev vnet0_5
# echo 1 > /proc/sys/net/ipv4/ip_forward
# iptables -t nat -A POSTROUTING -s 192.168.200.0/24 -j MASQUERADE
```

---

## Part 1: DPDK Setup

### Install packages

```bash
apt update
apt install -y dpdk dpdk-dev python3-pyelftools rdma-core \
               ibverbs-utils perftest iproute2 tcpdump net-tools \
               rdmacm-utils
```

### Configure hugepages

DPDK requires hugepages — large memory pages that reduce TLB misses and
improve packet processing performance. Without them, DPDK fails to allocate
its memory pool.

```bash
# Allocate 256 × 2MB hugepages = 512MB total
echo 256 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Mount the hugepage filesystem
mkdir -p /mnt/huge
mount -t hugetlbfs nodev /mnt/huge

# Verify allocation
grep HugePages_Total /proc/meminfo
# Expected: HugePages_Total: 256
```

**Why hugepages?**
Normal Linux uses 4KB memory pages. A packet buffer pool for a million packets
needs millions of page table lookups. Hugepages (2MB each) reduce that by 512×,
dramatically improving cache efficiency.

### Check DPDK device status

```bash
dpdk-devbind.py --status
```

You'll see your NIC listed under "Network devices using kernel driver".
In a VM, this is typically a `virtio` device — DPDK supports this natively.

### Run testpmd

`testpmd` is DPDK's built-in packet testing application. It demonstrates
the poll-mode driver in action.

```bash
dpdk-testpmd -l 0-1 -n 1 --vdev=net_tap0 -- --interactive --nb-cores=1
```

**Flag explanation:**
- `-l 0-1` — pin DPDK to CPU cores 0 and 1 (dedicated, never preempted)
- `-n 1` — one memory channel
- `--vdev=net_tap0` — use a virtual TAP device (works without a physical DPDK NIC)
- `--nb-cores=1` — one forwarding core

At the `testpmd>` prompt:

```
show port info all     # See port details, driver, MAC address
start                  # Begin poll-mode forwarding
show port stats all    # RX/TX packet counts and rates
stop                   # Stop forwarding
quit                   # Exit testpmd
```

**What you're seeing:**
The forwarding core runs in a tight busy-loop, polling the NIC for packets
instead of waiting for interrupts. CPU usage hits 100% on that core — this
is intentional. Predictable latency requires predictable CPU behavior.

**The kernel bypass in numbers:**
- Normal kernel path: ~20-50 microseconds per packet (interrupts, copies, syscalls)
- DPDK poll mode: ~1-5 microseconds per packet (no interrupts, zero-copy, no syscalls)

### Make hugepages persistent

```bash
# Add to /etc/rc.local or use sysfs at boot
echo "echo 256 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages" \
  >> /etc/rc.local
chmod +x /etc/rc.local
```

---

## Part 2: RDMA via SoftRoCE

RDMA (Remote Direct Memory Access) allows one machine to write directly into
another machine's memory over the network — bypassing the CPU and kernel on
both ends.

SoftRoCE (also called RXE) is a software implementation that provides the
full RDMA API on any standard Ethernet NIC. No special hardware required.

### Load the SoftRoCE kernel module

```bash
modprobe rdma_rxe

# Verify it loaded
lsmod | grep rdma_rxe
# Expected: rdma_rxe  ...  ib_uverbs  ip6_udp_tunnel  udp_tunnel  ib_core
```

### Create an RDMA device on your NIC

```bash
# Add SoftRoCE interface on top of eth0
rdma link add rxe0 type rxe netdev eth0

# Verify it's active
rdma link
# Expected: link rxe0/1 state ACTIVE physical_state LINK_UP netdev eth0
```

### Run an RDMA ping test

`rping` is the RDMA equivalent of ping — it verifies the RDMA connection
by writing data directly between two memory regions.

```bash
# Start server in background
rping -s -v -C 5 -a 192.168.200.10 &

# Connect client (after 1 second)
sleep 1 && rping -c -a 192.168.200.10 -v -C 5
```

**Expected output:**
```
ping data: rdma-ping-0: ABCDEFGHIJKLMNOPQRSTUVWXYZ...
ping data: rdma-ping-1: BCDEFGHIJKLMNOPQRSTUVWXYZ...
ping data: rdma-ping-2: CDEFGHIJKLMNOPQRSTUVWXYZ...
ping data: rdma-ping-3: DEFGHIJKLMNOPQRSTUVWXYZ...
ping data: rdma-ping-4: EFGHIJKLMNOPQRSTUVWXYZ...
```

Each line is one RDMA completion — the server wrote data directly into the
client's registered memory region. No TCP stack, no kernel copies, no
intermediate buffers.

### Run RDMA bandwidth benchmarks

```bash
# Latency test (loopback)
ib_read_lat -d rxe0 &
sleep 1
ib_read_lat -d rxe0 127.0.0.1

# Bandwidth test (loopback)
ib_send_bw -d rxe0 &
sleep 1
ib_send_bw -d rxe0 127.0.0.1
```

These are the same tools used in real InfiniBand deployments. On SoftRoCE
latency will be higher than hardware RDMA (~100μs vs ~1μs) but the API
and behavior are identical.

---

## Part 3: Connect Ubuntu to the Router Segment

The Ubuntu node has a second interface (eth1) connected to the
Exchange1-ColoPeer network segment (10.0.1.0/30).

```bash
# Configure eth1
ip addr add 10.0.1.10/32 dev eth1
ip link set eth1 up
ip route add 10.0.1.0/30 dev eth1

# Add host routes on Exchange1 and ColoPeer so they know about Ubuntu
# On Exchange1:
#   ip route 10.0.1.10 255.255.255.255 GigabitEthernet0/0
# On ColoPeer:
#   ip route 10.0.1.10 255.255.255.255 10.0.1.1

# Test reachability
ping -c 3 10.0.1.1    # Exchange1
ping -c 3 10.0.1.2    # ColoPeer
```

### Capture BGP traffic from Ubuntu

```bash
# Capture BGP keepalives (port 179)
tcpdump -i eth1 -n port 179 -v

# Capture BFD hellos (UDP port 3784)
# Note: BFD uses TTL=255 - capture from EVE-NG host bridge instead:
# sudo tcpdump -i vnet0_1 -n udp port 3784 -v
```

---

## Understanding the Concepts

### Why DPDK matters to HFT

In a standard Linux system, every received packet goes through:
1. NIC hardware interrupt → CPU context switch
2. Kernel network stack (TCP/IP processing)
3. Memory copy: kernel buffer → user buffer
4. Application syscall (recv/read)

Total overhead: 20-50 microseconds per packet.

With DPDK:
1. NIC writes packet directly to user-space buffer via DMA
2. Application polls the buffer in a tight loop
3. No interrupts, no copies, no syscalls

Total overhead: 1-5 microseconds per packet.

For HFT, this 10-50x improvement means the difference between reacting to
a market event in 1 microsecond vs 50 microseconds. At a typical exchange
where prices move faster than 10 microseconds, that latency gap is money.

### Why RDMA matters to HFT

Within a trading firm's data center, servers need to share data extremely
quickly — risk systems need to see positions, pricing engines need market
data, order management systems need acknowledgements.

Normal TCP between two machines in the same rack: ~10-50 microseconds.

With RDMA: ~1-5 microseconds, using almost no CPU on either end.

This is why HFT firms invest heavily in Mellanox/NVIDIA ConnectX NICs,
InfiniBand fabrics, and now RoCEv2 networks — the difference is measurable
in profitability per trade.

---

## Troubleshooting

**testpmd: Cannot allocate memory**
```bash
echo 512 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

**rping: rdma_bind_addr: Cannot assign requested address**
Use the actual interface IP, not 127.0.0.1:
```bash
rping -s -v -C 5 -a 192.168.200.10 &
rping -c -a 192.168.200.10 -v -C 5
```

**rdma link add: File exists**
The device is already created — check with `rdma link` to confirm it's active.

**apt update fails (DNS error)**
```bash
echo "nameserver 8.8.8.8" > /etc/resolv.conf
```

**Wrong apt mirror (Chinese mirror timeout)**
```bash
cat > /etc/apt/sources.list << 'EOF'
deb http://archive.ubuntu.com/ubuntu focal main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu focal-security main restricted universe multiverse
EOF
apt update
```
