/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    domains: ['cdn.twelvelabs.io', 'localhost'],
  },
}
module.exports = nextConfig
