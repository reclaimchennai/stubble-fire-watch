# Deploying

The live site is a static folder (`web/`, produced by `scripts/publish.sh`) behind any web server, kept current by
two systemd user timers:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/fire-refresh.* deploy/fire-live.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fire-refresh.timer fire-live.timer
sudo loginctl enable-linger "$USER"      # run without a login session
```

`fire-live.timer` pulls new FIRMS detections every hour; `fire-refresh.timer` refreshes everything every 6 hours
(00:40, 06:40, 12:40, 18:40 UTC). They share a lock, so they never write at the same time. The units expect the
repository at `~/projects/fire`; edit `WorkingDirectory` and `ExecStart` otherwise.

`Caddyfile.example` shows the web-server block: compression for JSON and the binary point files, no-cache for the
HTML, short caches for data that changes hourly. Serve only `web/`: `.env` and `data/raw/` must never be public.
