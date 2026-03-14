
def probe_esphome_rpc(ip, port=80):
    """Probes for the ESPHome JSON-RPC signature."""
    print(f"Probing {ip}:{port} for ESPHome RPC...")
    try:
        # ESPHome usually responds to JRPC requests on port 80 (web) or serial.
        # However, for a networked device, we check if it identifies as a Transit Tracker.
        # We can try a simple HTTP GET to see the title.
        import httpx
        resp = httpx.get(f"http://{ip}/", timeout=2.0)
        if "Transit Tracker" in resp.text:
            return "Transit Tracker (Web Interface)"
        return None
    except Exception as e:
        return f"Probe failed: {e}"

if __name__ == "__main__":
    target_ip = "192.168.5.248"
    result = probe_esphome_rpc(target_ip)
    print(f"Result: {result}")
