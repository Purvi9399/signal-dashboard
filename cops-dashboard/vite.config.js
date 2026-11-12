// Standalone COPS dashboard prototype. Separate from the Signal dashboard:
// its own root, data and build output; it shares only node_modules and src/theme.js.
//   npm run cops:dev     npm run cops:build     npm run cops:preview
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

const here = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  root: here,
  base: './',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5174, fs: { allow: ['..'] } },
  preview: { port: 4174 },
})
