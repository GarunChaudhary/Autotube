import "./globals.css";
import Shell from "../components/Shell";

export const metadata = {
  title: "AutoTube AI",
  description: "Local zero-budget YouTube automation studio",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/favicon.png", apple: "/icons/apple-touch-icon.png" },
  // iPhone: "Add to Home Screen" opens full-screen like an app
  appleWebApp: { capable: true, title: "AutoTube", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#0e1018" },
               { media: "(prefers-color-scheme: light)", color: "#f6f7fb" }],
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
