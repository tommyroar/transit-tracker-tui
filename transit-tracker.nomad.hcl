# =============================================================================
# Transit Tracker — Nomad Job (RETIRED)
#
# The transit-tracker container is now managed directly by Docker with
# --restart=always, which auto-starts with OrbStack on login.
#
# This file is kept for reference and ad-hoc use:
#   nomad job run transit-tracker.nomad.hcl
#
# For normal operation, use:
#   scripts/start_container.sh --detach
#   scripts/stop_container.sh
# =============================================================================

job "transit-tracker" {
  datacenters = ["dc1"]
  type        = "service"

  group "proxy" {
    count = 1

    network {
      port "ws" {
        static = 8000
      }
      port "web" {
        static = 8081
      }
    }

    restart {
      attempts = 10
      interval = "10m"
      delay    = "15s"
      mode     = "delay"
    }

    task "transit-tracker" {
      driver = "raw_exec"

      env {
        PATH          = "/Users/tommydoerr/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        DOCKER_HOST   = "unix:///Users/tommydoerr/.orbstack/run/docker.sock"
      }

      config {
        command = "/bin/bash"
        args = [
          "-c",
          <<-EOF
          # Wait for OrbStack Docker socket (up to 60s)
          for i in $(seq 1 60); do
            docker info >/dev/null 2>&1 && break
            echo "[NOMAD] Waiting for Docker daemon... ($i/60)"
            sleep 1
          done
          docker info >/dev/null 2>&1 || { echo "[NOMAD] Docker daemon not available"; exit 1; }

          # Stop and remove any existing container
          docker rm -f transit-tracker 2>/dev/null || true

          # Run the container in foreground (Nomad manages lifecycle)
          exec docker run --rm \
            --name transit-tracker \
            -p 8000:8000 \
            -p 8081:8080 \
            -v /Users/tommydoerr/dev/transit_tracker/.local/home.yaml:/config/config.yaml:ro \
            transit-tracker:latest
          EOF
        ]
      }

      resources {
        cpu    = 300
        memory = 384
      }
    }
  }
}
