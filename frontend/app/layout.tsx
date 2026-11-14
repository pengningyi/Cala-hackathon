import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RoundMe · Your next introduction",
  description: "Turn your lead investor's Cala-backed investment network into considered introduction requests.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
