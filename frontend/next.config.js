/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'cdn.twelvelabs.io' },
      { protocol: 'http', hostname: 'localhost' },
    ],
  },
}
module.exports = nextConfig
