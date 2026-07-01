#!/usr/bin/env python3
"""Push CSR1000v-ported HFT configs over the telnet consoles.

Ports the 3 IOSv configs to IOS-XE: interface remap Gi0/0->Gi1, Gi0/1->Gi2.
Everything else (BGP LOCAL_PREF/MED, prefix-lists, route-maps, BFD 150ms,
fall-over bfd) ports verbatim — IOS-XE honors it identically.

Prompt-aware: waits for boot, declines the setup dialog, then pushes.
Runs 3 routers -> launch in background (exceeds foreground cap).
"""
import socket, time, sys

EVE = "10.10.10.214"

# name -> (console_port, [config lines])
CONFIGS = {
    "Exchange1": (32769, [
        "hostname Exchange1",
        "no ip domain lookup",
        "interface Loopback0",
        " ip address 1.1.1.1 255.255.255.255",
        "exit",
        "interface GigabitEthernet1",
        " description To-ColoPeer",
        " ip address 10.0.1.1 255.255.255.252",
        " bfd interval 150 min_rx 150 multiplier 3",
        " no bfd echo",
        " no shutdown",
        "exit",
        "router bgp 65001",
        " bgp router-id 1.1.1.1",
        " bgp log-neighbor-changes",
        " neighbor 10.0.1.2 remote-as 65002",
        " neighbor 10.0.1.2 fall-over bfd",
        " neighbor 10.0.1.2 timers 3 9",
        " address-family ipv4",
        "  network 172.16.1.0 mask 255.255.255.0",
        "  neighbor 10.0.1.2 activate",
        "  neighbor 10.0.1.2 route-map SET-MED-PRIMARY out",
        " exit-address-family",
        "exit",
        "route-map SET-MED-PRIMARY permit 10",
        " set metric 10",
        "exit",
        "ip route 172.16.1.0 255.255.255.0 Null0",
    ]),
    "ColoPeer": (32770, [
        "hostname ColoPeer",
        "no ip domain lookup",
        "interface Loopback0",
        " ip address 2.2.2.2 255.255.255.255",
        "exit",
        "interface GigabitEthernet1",
        " description To-Exchange1-PRIMARY",
        " ip address 10.0.1.2 255.255.255.252",
        " bfd interval 150 min_rx 150 multiplier 3",
        " no bfd echo",
        " no shutdown",
        "exit",
        "interface GigabitEthernet2",
        " description To-Exchange2-BACKUP",
        " ip address 10.0.2.1 255.255.255.252",
        " bfd interval 150 min_rx 150 multiplier 3",
        " no bfd echo",
        " no shutdown",
        "exit",
        "router bgp 65002",
        " bgp router-id 2.2.2.2",
        " bgp log-neighbor-changes",
        " bgp bestpath compare-routerid",
        " neighbor 10.0.1.1 remote-as 65001",
        " neighbor 10.0.1.1 fall-over bfd",
        " neighbor 10.0.1.1 timers 3 9",
        " neighbor 10.0.2.2 remote-as 65003",
        " neighbor 10.0.2.2 fall-over bfd",
        " neighbor 10.0.2.2 timers 3 9",
        " address-family ipv4",
        "  network 192.168.100.0 mask 255.255.255.0",
        "  neighbor 10.0.1.1 activate",
        "  neighbor 10.0.1.1 soft-reconfiguration inbound",
        "  neighbor 10.0.1.1 route-map PREFER-PRIMARY in",
        "  neighbor 10.0.2.2 activate",
        "  neighbor 10.0.2.2 soft-reconfiguration inbound",
        "  neighbor 10.0.2.2 route-map PREFER-BACKUP in",
        " exit-address-family",
        "exit",
        "ip prefix-list ACCEPT-EXCHANGE1 seq 5 permit 172.16.1.0/24",
        "ip prefix-list ACCEPT-EXCHANGE2 seq 5 permit 172.16.2.0/24",
        "route-map PREFER-PRIMARY permit 10",
        " match ip address prefix-list ACCEPT-EXCHANGE1",
        " set local-preference 200",
        "exit",
        "route-map PREFER-PRIMARY deny 20",
        "exit",
        "route-map PREFER-BACKUP permit 10",
        " match ip address prefix-list ACCEPT-EXCHANGE2",
        " set local-preference 100",
        "exit",
        "route-map PREFER-BACKUP deny 20",
        "exit",
        "ip route 192.168.100.0 255.255.255.0 Null0",
    ]),
    "Exchange2": (32771, [
        "hostname Exchange2",
        "no ip domain lookup",
        "interface Loopback0",
        " ip address 3.3.3.3 255.255.255.255",
        "exit",
        "interface GigabitEthernet1",
        " description To-ColoPeer",
        " ip address 10.0.2.2 255.255.255.252",
        " bfd interval 150 min_rx 150 multiplier 3",
        " no bfd echo",
        " no shutdown",
        "exit",
        "router bgp 65003",
        " bgp router-id 3.3.3.3",
        " bgp log-neighbor-changes",
        " neighbor 10.0.2.1 remote-as 65002",
        " neighbor 10.0.2.1 fall-over bfd",
        " neighbor 10.0.2.1 timers 3 9",
        " address-family ipv4",
        "  network 172.16.2.0 mask 255.255.255.0",
        "  neighbor 10.0.2.1 activate",
        "  neighbor 10.0.2.1 route-map SET-MED-BACKUP out",
        " exit-address-family",
        "exit",
        "route-map SET-MED-BACKUP permit 10",
        " set metric 50",
        "exit",
        "ip route 172.16.2.0 255.255.255.0 Null0",
    ]),
}


def recv_until(s, tokens, timeout=25):
    s.setblocking(False)
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        try:
            d = s.recv(4096)
            if d:
                buf += d.decode(errors="ignore")
                if any(t in buf for t in tokens):
                    return buf
            else:
                time.sleep(0.2)
        except BlockingIOError:
            time.sleep(0.3)
    return buf


def send(s, line):
    s.sendall((line + "\r").encode())


def get_to_enable(s):
    send(s, "")
    t = recv_until(s, ["yes/no", "#", ">", "terminate autoinstall"], 20)
    if "yes/no" in t or "initial configuration dialog" in t:
        send(s, "no")
        t = recv_until(s, ["terminate autoinstall", "#", ">", "Press RETURN"], 25)
    if "terminate autoinstall" in t:
        send(s, "yes")
        recv_until(s, ["Press RETURN", "#", ">"], 25)
    for _ in range(3):
        send(s, "")
        t = recv_until(s, ["#", ">"], 8)
        if "#" in t or ">" in t:
            break
    if ">" in t and "#" not in t:
        send(s, "enable")
        recv_until(s, ["#"], 10)


def push(name, port, lines):
    print(f"\n===== {name} (console {port}) =====", flush=True)
    s = socket.create_connection((EVE, port), timeout=10)
    time.sleep(0.5)
    get_to_enable(s)
    send(s, "terminal length 0"); recv_until(s, ["#"], 8)
    send(s, "configure terminal"); recv_until(s, ["(config)#", "#"], 10)
    for ln in lines:
        send(s, ln)
        recv_until(s, ["#"], 6)
    send(s, "end"); recv_until(s, ["#"], 6)
    send(s, "write memory"); t = recv_until(s, ["[OK]", "#"], 20)
    send(s, ""); t2 = recv_until(s, [f"{name}#"], 6)
    print(f"  {name}: prompt_ok={f'{name}#' in t2} saved={'[OK]' in t}", flush=True)
    s.close()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for name, (port, lines) in CONFIGS.items():
        if only and name != only:
            continue
        try:
            push(name, port, lines)
        except Exception as e:
            print(f"{name} ERROR: {e}", flush=True)
    print("\nALL HFT CONFIG PUSHES COMPLETE", flush=True)
