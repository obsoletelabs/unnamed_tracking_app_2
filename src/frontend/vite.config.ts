import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "vendor",
              test: /[\\/]node_modules[\\/]/,
              priority: 2,
            },
            {
              // Bundle only modules that are statically required to mount the
              // authenticated shell. Route views remain separate dynamic
              // chunks and keep their own CSS.
              name: "app-shell",
              test: /[\\/]src[\\/](?:main\.ts|App\.vue|router[\\/]index\.ts|state[\\/](?:auth|preferences|shortcuts|dialog|pluginExtensions|uiPalette)\.ts|components[\\/](?:PageHeader|AppIcon|UiModal|ProfileAvatar|AccountChip)\.vue|services[\\/](?:apiError|providerEvents|notifications)\.ts)$/,
              priority: 1,
            },
            {
              // Consolidate small data helpers used together by library routes.
              name: "library-utils",
              test: /[\\/]src[\\/](?:utils[\\/](?:priority|dates|platforms|scoring|genres|libraryLayout|useKeptAlive|gameOwnership|gameCopies)\.ts|state[\\/]libraryNav\.ts|services[\\/](?:identityReview|gameDuplicates|gameIdentity|librarySync|gameCache|settings)\.ts)$/,
              priority: 0,
            },
            {
              // Keep the library card and top-bar controls/styles together.
              // Route views and occasional forms retain their own assets.
              name: "library-ui",
              test: /[\\/]src[\\/]components[\\/](?:SegmentedTabs|CheckIcon|HeartIcon|CompletionBadge|GameArtwork|GameCard|AppTopBar|GameTopBar|game[\\/]AutomaticMatchBanner)\.vue$/,
              priority: 0,
            },
          ],
        },
        // The previous static-asset experiment could cache asset 404s as
        // immutable. Change the asset URL epoch once so browsers discard
        // those poisoned entries; content hashes still cache unchanged files.
        entryFileNames: "assets/[name]-[hash]-rev2.js",
        chunkFileNames: "assets/[name]-[hash]-rev2.js",
        assetFileNames: "assets/[name]-[hash]-rev2[extname]",
      },
    },
  },
  server: {
    host: true, // listen on 0.0.0.0 so the container's port mapping works
    port: 80,
    allowedHosts: true,
    watch: {
      // Docker Desktop on Windows doesn't forward native filesystem
      // change events across the bind mount, so Vite's watcher never
      // fires without polling — HMR silently stops working otherwise.
      usePolling: true,
    },
    proxy: {
      "/service-worker.js": { target: "http://backend:8000" },
      "/manifest.webmanifest": { target: "http://backend:8000" },
      "/pwa/": { target: "http://backend:8000" },
      "/api": {
        target: "http://backend:8000",
      },
    },
  },
});
