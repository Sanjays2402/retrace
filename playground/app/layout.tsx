import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  icons: { icon: "/mark.svg" },
  title: "Retrace — try crash recovery",
  description:
    "Explore a sample workflow, interrupt it, and resume from durable checkpoints. A hands-on introduction to Retrace for Python.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
