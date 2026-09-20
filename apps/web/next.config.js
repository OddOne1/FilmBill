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
}

module.exports = nextConfig
