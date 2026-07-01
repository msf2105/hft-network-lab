#!/usr/bin/env python3
"""Port the HFT Network Lab (3x IOSv) to CSR1000v and build it in EVE-NG via API.

Original lab (msf2105/hft-network-lab) uses vios/IOSv (not installed on this box).
CSR1000v (IOS-XE) is substituted — it honors BGP LOCAL_PREF/MED/prefix-lists and
sub-second BFD faithfully (the lab's headline features).

Topology:
  Exchange1(AS65001) --10.0.1.0/30-- ColoPeer(AS65002) --10.0.2.0/30-- Exchange2(AS65003)
  Interface remap: IOSv Gi0/0->CSR Gi1, Gi0/1->Gi2.

Usage:
  build_hft_csr.py <EVE_IP> create   # lab + 3 CSR nodes + 2 links + wiring
  build_hft_csr.py <EVE_IP> start
  build_hft_csr.py <EVE_IP> status
  build_hft_csr.py <EVE_IP> ifmap
  build_hft_csr.py <EVE_IP> wipe
"""
import sys, json, urllib.request, urllib.error, http.cookiejar

EVE = "http://" + (sys.argv[1] if len(sys.argv) > 1 else "10.10.10.214")
USER, PWD = "admin", "eve"
LAB = "HFT-CSR.unl"
TEMPLATE = "csr1000vng"
IMG = "csr1000vng-universalk9.17.03.03"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(EVE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"code": e.code, "status": "error", "message": str(e)}


def login():
    r = api("POST", "/api/auth/login", {"username": USER, "password": PWD, "html5": "-1"})
    assert r.get("status") == "success", r


def create():
    print(api("POST", "/api/labs", {"path": "/", "name": LAB.replace(".unl", ""),
              "version": "1", "author": "auto"}).get("message"))
    coords = {"Exchange1": (150, 180), "ColoPeer": (420, 180), "Exchange2": (690, 180)}
    nodes = {}
    for name, (l, t) in coords.items():
        r = api("POST", f"/api/labs/{LAB}/nodes",
                {"type": "qemu", "template": TEMPLATE, "image": IMG, "name": name,
                 "icon": "Router-2D-Gen-White-S.svg", "ram": 3072, "cpu": 1,
                 "ethernet": 4, "left": l, "top": t})
        nodes[name] = r.get("data", {}).get("id")
        print(f"node {name}: id={nodes[name]} ({r.get('message')})")
    # two point-to-point bridges
    nets = {}
    for nm, (l, t) in {"A_Ex1_Colo": (285, 160), "B_Colo_Ex2": (555, 160)}.items():
        r = api("POST", f"/api/labs/{LAB}/networks",
                {"type": "bridge", "name": nm, "left": l, "top": t, "visibility": "1"})
        nets[nm] = r.get("data", {}).get("id")
        print(f"net {nm}: id={nets[nm]} ({r.get('message')})")
    # CSR Gi1=idx0, Gi2=idx1
    wiring = {
        "Exchange1": {"0": nets["A_Ex1_Colo"]},                       # Gi1 -> link A
        "ColoPeer":  {"0": nets["A_Ex1_Colo"], "1": nets["B_Colo_Ex2"]},  # Gi1->A, Gi2->B
        "Exchange2": {"0": nets["B_Colo_Ex2"]},                       # Gi1 -> link B
    }
    for name, ifmap in wiring.items():
        r = api("PUT", f"/api/labs/{LAB}/nodes/{nodes[name]}/interfaces",
                {k: str(v) for k, v in ifmap.items()})
        print(f"wire {name}: {r.get('message')}")
    print("CREATE COMPLETE")


def start():
    for nid in api("GET", f"/api/labs/{LAB}/nodes").get("data", {}):
        print(nid, api("GET", f"/api/labs/{LAB}/nodes/{nid}/start").get("message"))


def status():
    for nid, n in api("GET", f"/api/labs/{LAB}/nodes").get("data", {}).items():
        print(f"  {n['name']:11} id={nid} status={n.get('status')} (2=run) console={n.get('url')}")


def ifmap():
    for nid, n in api("GET", f"/api/labs/{LAB}/nodes").get("data", {}).items():
        ifs = api("GET", f"/api/labs/{LAB}/nodes/{nid}/interfaces").get("data", {})
        parts = []
        for i, e in enumerate(ifs.get("ethernet", [])):
            if isinstance(e, dict):
                parts.append(f"{i}={e.get('name')}(net={e.get('network_id')})")
        print(f"  {n['name']:11} (id={nid}): {', '.join(parts)}")


def wipe():
    print(api("DELETE", f"/api/labs/{LAB}").get("message"))


if __name__ == "__main__":
    phase = sys.argv[2] if len(sys.argv) > 2 else "status"
    login()
    {"create": create, "start": start, "status": status,
     "ifmap": ifmap, "wipe": wipe}[phase]()
