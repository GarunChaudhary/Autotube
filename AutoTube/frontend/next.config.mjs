/** Static export: the FastAPI backend serves frontend/out at http://localhost:8000 */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // In `npm run dev` (port 3000) proxying isn't available with export; the API client uses NEXT_PUBLIC_API.
};
export default nextConfig;
