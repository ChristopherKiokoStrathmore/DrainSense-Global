import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/instrument-sans";
import "./globals.css";

export const metadata = {
  title: "DrainSense",
  description:
    "Flood-risk and drainage desk: risk map, drain reports, dispatch plan, sector flags, repair tracker, flood-impact model, and the API behind them.",
};

export const viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
