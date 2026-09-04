/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static-export CSR (ADR 0014): an auth-gated SPA; Traefik serves it at `/`
  // on the single origin. The API lives at `/api` on the same host, so fetches
  // are same-origin with no CORS.
  output: "export",
  trailingSlash: true,
};

export default nextConfig;
