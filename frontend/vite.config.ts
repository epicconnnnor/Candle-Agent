import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    // Bind mounts (Docker on Windows/macOS, WSL, network shares) do not deliver
    // inotify events, so the default watcher silently misses edits. Polling costs
    // a little CPU in dev and is the only thing that reliably picks changes up.
    watch: { usePolling: true, interval: 300 },
  },
});
