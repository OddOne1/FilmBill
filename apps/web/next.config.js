/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Lint is its own CI job and its own `pnpm lint`. Failing the build on it
  // too would just make one problem look like two.
  eslint: {
    ignoreDuringBuilds: true,
  },
  images: {
    // Nothing is loaded straight from the object store: brand images and
    // avatars come through the API's own proxy (apps/api/routers/files.py),
    // which is why there is no MinIO/S3 host listed here.
    remotePatterns: [],
  },
  // P1b-4: the screen is called Contacts now. The old addresses keep
  // working so no bookmark breaks; the API and the code still say party.
  // Not permanent (307): a browser caches a 308 for good, and the day the
  // old path means something else again it should not be stuck.
  async redirects() {
    return [
      { source: '/parties', destination: '/contacts', permanent: false },
      { source: '/parties/:id', destination: '/contacts/:id', permanent: false },
    ]
  },
}

module.exports = nextConfig
