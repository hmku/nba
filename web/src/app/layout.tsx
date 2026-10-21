import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Nav } from "@/components/Nav";
import { ThemeAuto } from "@/components/client";
import { ServiceWorkerRegister } from "@/components/ServiceWorker";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Fantasy Desk", template: "%s · Fantasy Desk" },
  description: "Fantasy basketball agent dashboard: draft room, agents, approvals, models.",
  applicationName: "Fantasy Desk",
  // manifest.ts is linked automatically; iOS "Add to Home Screen" reads these instead.
  appleWebApp: { capable: true, title: "Fantasy Desk", statusBarStyle: "black-translucent" },
  icons: { apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }] },
  formatDetection: { telephone: false },
  // Next emits only the standard `mobile-web-app-capable` for appleWebApp.capable; older iOS needs the Apple name.
  other: { "apple-mobile-web-app-capable": "yes" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover", // lets env(safe-area-inset-*) report the notch / home-indicator insets (used in Nav + main)
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0c0f" },
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
  ],
};

// Theme: "auto" (default) = light 07:00–19:00 local time, dark otherwise; or a fixed "light"/"dark" from localStorage.
// Runs before paint to avoid a flash of the wrong theme; ThemeAuto re-evaluates every 10 minutes.
const themeScript = `try{var t=localStorage.getItem('theme');var h=new Date().getHours();var d=t==='dark'||(t!=='light'&&(h<7||h>=19));document.documentElement.classList.toggle('dark',d)}catch(e){document.documentElement.classList.add('dark')}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="antialiased">
        <ThemeAuto />
        <ServiceWorkerRegister />
        <Nav />
        <main className="mx-auto min-h-screen min-h-[100dvh] w-full max-w-5xl min-w-0 px-4 pt-4 pb-[calc(6rem+env(safe-area-inset-bottom))] md:pb-10">{children}</main>
      </body>
    </html>
  );
}
