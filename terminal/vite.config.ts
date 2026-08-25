import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5174,
    // bind mounts don't deliver inotify events; poll so edits are picked up
    watch: { usePolling: true, interval: 300 },
  },
});
