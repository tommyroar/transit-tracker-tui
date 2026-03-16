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

    task "websocket-server" {
      driver = "raw_exec"

      env {
        PATH = "/Users/tommydoerr/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
      }

      config {
        command = "/bin/bash"
        args = [
          "-c",
          "cd /Users/tommydoerr/dev/transit_tracker && exec uv run transit-tracker service",
        ]
      }

      resources {
        cpu    = 200
        memory = 256
      }
    }

    task "web-server" {
      driver = "raw_exec"

      env {
        PATH = "/Users/tommydoerr/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        PORT = "8081"
      }

      config {
        command = "/bin/bash"
        args = [
          "-c",
          "cd /Users/tommydoerr/dev/transit_tracker && exec uv run transit-tracker web",
        ]
      }

      resources {
        cpu    = 100
        memory = 128
      }
    }
  }
}
