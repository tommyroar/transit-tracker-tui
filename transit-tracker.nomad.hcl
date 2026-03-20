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
      attempts = 3
      interval = "5m"
      delay    = "10s"
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
