import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/instrument-sans";
import Nav from "../components/Nav";
import "./globals.css";

export const metadata = {
  title: "DrainSense",
  description:
    "Flood-risk and drainage desk: risk map, drainage ledger, dispatch plan, sector flags, flood-impact model, and the API behind them.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <header className="mast">
          <a className="mark" href="/#overview">
            <Grate />
            <span>
              <strong>DrainSense</strong>
              <em>Global flood desk</em>
            </span>
          </a>
          <Nav />
          <p className="mast-note">5° cells · scores 2014–2024 · not a warning</p>
        </header>
        <div className="frame">
          <div className="stage">{children}</div>
        </div>
      </body>
    </html>
  );
}

function Grate() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="grate">
      <rect x="3" y="3" width="42" height="42" rx="3" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M12 10v28M24 10v28M36 10v28M8 16h32M8 24h32M8 32h32" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="36" cy="36" r="3.2" fill="#ff6a3d" stroke="none" />
    </svg>
  );
}