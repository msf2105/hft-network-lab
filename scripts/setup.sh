#!/bin/bash
# =============================================================================
# HFT Network Lab — EVE-NG Setup Script
# =============================================================================
# Run this on your EVE-NG host after importing lab.unl
# Usage: sudo bash scripts/setup.sh
#
# What this does:
#   1. Finds the active lab UUID
#   2. Creates QEMU overlay disks for all nodes (EVE-NG won't do this on
#      GCS-backed instances due to read-only filesystem mounts)
#   3. Sets up NAT so the Ubuntu node can reach the internet
#   4. Fixes EVE-NG permissions
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Check root ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Please run as root: sudo bash scripts/setup.sh"
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         HFT Network Lab — EVE-NG Setup               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Find lab UUID ─────────────────────────────────────────────────────────────
info "Finding lab UUID..."

LAB_FILE=$(find /opt/unetlab/labs -name "lab.unl" 2>/dev/null | head -1)
if [ -z "$LAB_FILE" ]; then
  error "lab.unl not found. Did you import the lab into EVE-NG first?"
fi

LAB_UUID=$(grep -o 'id="[^"]*"' "$LAB_FILE" | head -1 | sed 's/id="//;s/"//')
if [ -z "$LAB_UUID" ]; then
  error "Could not extract lab UUID from $LAB_FILE"
fi

success "Lab UUID: $LAB_UUID"
LAB_DIR="/opt/unetlab/tmp/0/$LAB_UUID"

# ── Find images ───────────────────────────────────────────────────────────────
info "Finding IOSv image..."

IOSV_IMAGE=$(ls /opt/unetlab/addons/qemu/ | grep -E "^(vios|iosv)-vios" | head -1)
if [ -z "$IOSV_IMAGE" ]; then
  error "No IOSv image found in /opt/unetlab/addons/qemu/
  
  Please ensure you have an IOSv image installed. Expected folder name:
  vios-adventerprisek9-m.SPA.159-3.M6
  
  If your folder starts with 'iosv-' instead of 'vios-', create a symlink:
  sudo ln -s /opt/unetlab/addons/qemu/iosv-vios-adventerprisek9-m.SPA.159-3.M6 \\
             /opt/unetlab/addons/qemu/vios-adventerprisek9-m.SPA.159-3.M6"
fi

IOSV_BASE="/opt/unetlab/addons/qemu/$IOSV_IMAGE/virtioa.qcow2"
if [ ! -f "$IOSV_BASE" ]; then
  error "IOSv qcow2 not found at: $IOSV_BASE
  
  The image folder exists but virtioa.qcow2 is missing.
  This usually means the image wasn't extracted from GCS properly.
  
  If using GCS-backed EVE-NG, extract the image first:
    tar -xzf '/mnt/eve-gcs/Cisco IOSv/vios-adventerprisek9-m.SPA.159-3.M6.tgz' \\
      -C /opt/unetlab/addons/qemu/$IOSV_IMAGE/"
fi

IOSV_SIZE=$(du -sh "$IOSV_BASE" | cut -f1)
success "IOSv image: $IOSV_IMAGE ($IOSV_SIZE)"

info "Finding Ubuntu image..."
UBUNTU_BASE="/opt/unetlab/addons/qemu/linux-linux-ubuntu-server-20.04/virtioa.qcow2"
if [ ! -f "$UBUNTU_BASE" ]; then
  warn "Ubuntu image not found. Skipping Ubuntu node overlay.
  
  To add Ubuntu later:
    tar -xzf '/mnt/eve-gcs/Linux/linux-ubuntu-server-20.04.tgz' \\
      -C /opt/unetlab/addons/qemu/linux-linux-ubuntu-server-20.04/"
  UBUNTU_BASE=""
else
  UBUNTU_SIZE=$(du -sh "$UBUNTU_BASE" | cut -f1)
  success "Ubuntu image found ($UBUNTU_SIZE)"
fi

info "Finding NetEm image..."
NETEM_BASE="/opt/unetlab/addons/qemu/linux-linux-netem/hda.qcow2"
if [ ! -f "$NETEM_BASE" ]; then
  warn "NetEm image not found. Skipping NetEm overlay.
  
  To add NetEm later:
    tar -xzf '/mnt/eve-gcs/Linux/linux-netem.tar.gz' \\
      -C /opt/unetlab/addons/qemu/linux-linux-netem/"
  NETEM_BASE=""
else
  success "NetEm image found"
fi

# ── Create overlays ───────────────────────────────────────────────────────────
echo ""
info "Creating QEMU overlay disks..."

create_overlay() {
  local NODE_ID=$1
  local BASE_IMAGE=$2
  local DISK_NAME=$3

  local NODE_DIR="$LAB_DIR/$NODE_ID"
  local OVERLAY="$NODE_DIR/$DISK_NAME"

  mkdir -p "$NODE_DIR"

  if [ -f "$OVERLAY" ]; then
    local OSIZE=$(du -sh "$OVERLAY" | cut -f1)
    warn "Node $NODE_ID overlay already exists ($OSIZE) — skipping"
    return
  fi

  qemu-img create -f qcow2 -b "$BASE_IMAGE" -F qcow2 "$OVERLAY" > /dev/null 2>&1
  chown root:unl "$OVERLAY"
  chmod 664 "$OVERLAY"
  success "Node $NODE_ID overlay created"
}

# IOSv nodes (1, 2, 3)
for NODE in 1 2 3; do
  create_overlay "$NODE" "$IOSV_BASE" "virtioa.qcow2"
done

# NetEm node (4)
if [ -n "$NETEM_BASE" ]; then
  create_overlay 4 "$NETEM_BASE" "hda.qcow2"
fi

# Ubuntu node (5)
if [ -n "$UBUNTU_BASE" ]; then
  create_overlay 5 "$UBUNTU_BASE" "virtioa.qcow2"
fi

# ── Fix permissions ───────────────────────────────────────────────────────────
echo ""
info "Fixing EVE-NG permissions..."
/opt/unetlab/wrappers/unl_wrapper -a fixpermissions > /dev/null 2>&1
success "Permissions fixed"

# ── Set up NAT for Ubuntu node ────────────────────────────────────────────────
echo ""
info "Setting up NAT for Ubuntu-DPDK node..."

# Find the bridge for network 5 (Ubuntu's internet segment)
DPDK_BRIDGE=$(brctl show 2>/dev/null | grep "vnet0_5" | awk '{print $1}')
if [ -z "$DPDK_BRIDGE" ]; then
  warn "DPDK bridge (vnet0_5) not found — nodes may not be running yet.
  
  After starting nodes, run manually:
    ip addr add 192.168.200.1/24 dev vnet0_5
    ip link set vnet0_5 up
    echo 1 > /proc/sys/net/ipv4/ip_forward
    iptables -t nat -A POSTROUTING -s 192.168.200.0/24 -j MASQUERADE"
else
  # Check if IP already set
  if ip addr show vnet0_5 | grep -q "192.168.200.1"; then
    warn "NAT gateway IP already set on vnet0_5"
  else
    ip addr add 192.168.200.1/24 dev vnet0_5 2>/dev/null || true
    ip link set vnet0_5 up
    success "Bridge IP set: 192.168.200.1/24 on vnet0_5"
  fi

  echo 1 > /proc/sys/net/ipv4/ip_forward

  # Remove duplicate rules before adding
  iptables -t nat -D POSTROUTING -s 192.168.200.0/24 -j MASQUERADE 2>/dev/null || true
  iptables -t nat -A POSTROUTING -s 192.168.200.0/24 -j MASQUERADE
  success "NAT rule added for 192.168.200.0/24"
  success "IP forwarding enabled"
fi

# ── Persist NAT across reboots ────────────────────────────────────────────────
info "Making NAT persistent..."

RC_LOCAL="/etc/rc.local"
NAT_MARKER="# HFT-LAB-NAT"

if grep -q "$NAT_MARKER" "$RC_LOCAL" 2>/dev/null; then
  warn "NAT startup script already in $RC_LOCAL — skipping"
else
  cat >> "$RC_LOCAL" << 'RCEOF'

# HFT-LAB-NAT — added by hft-network-lab setup.sh
ip addr add 192.168.200.1/24 dev vnet0_5 2>/dev/null || true
ip link set vnet0_5 up 2>/dev/null || true
echo 1 > /proc/sys/net/ipv4/ip_forward
iptables -t nat -A POSTROUTING -s 192.168.200.0/24 -j MASQUERADE 2>/dev/null || true
RCEOF
  chmod +x "$RC_LOCAL"
  success "NAT startup script added to $RC_LOCAL"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║                   Setup Complete                     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Hard refresh EVE-NG browser tab (Ctrl+Shift+R)"
echo "  2. Right-click canvas → Start all nodes"
echo "  3. Wait 2-3 minutes for IOSv to boot"
echo "  4. Telnet into each router and paste configs:"
echo "     telnet 127.0.0.1 32769   # Exchange1"
echo "     telnet 127.0.0.1 32770   # ColoPeer"
echo "     telnet 127.0.0.1 32771   # Exchange2"
echo "  5. On ColoPeer: show bgp ipv4 unicast summary"
echo ""
echo "Ubuntu-DPDK console: telnet 127.0.0.1 32773"
echo "  Login: root / root (or ubuntu / ubuntu)"
echo ""
echo "See README.md for full exercise guide."
echo ""
