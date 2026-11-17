import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "K8s Troubleshooting Agent",
  description: "Claude-powered troubleshooting for Huawei Cloud CCE",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
