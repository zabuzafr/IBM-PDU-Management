import { defineConfig } from "vite";

// host 0.0.0.0 est indispensable dans Docker : sinon Vite n'écoute que sur
// localhost *dans le conteneur* et l'interface est inaccessible depuis l'hôte.
export default defineConfig({
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
});
