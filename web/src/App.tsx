import { useState } from "react";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";
import Science from "./pages/Science";
import DataSources from "./pages/DataSources";

export type Page = "home" | "platform" | "science" | "data";

export default function App() {
  const [page, setPage] = useState<Page>("home");

  return (
    <>
      <nav className="nav">
        <div className="container nav-inner">
          <div className="logo" onClick={() => setPage("home")}>
            OleoCast<span className="dot">●</span>
          </div>
          <div className="nav-links">
            <button
              className={page === "platform" ? "active" : ""}
              onClick={() => setPage("platform")}
            >
              Platform
            </button>
            <button
              className={page === "science" ? "active" : ""}
              onClick={() => setPage("science")}
            >
              Science &amp; Validation
            </button>
            <button
              className={page === "data" ? "active" : ""}
              onClick={() => setPage("data")}
            >
              Data
            </button>
            <button className="btn" onClick={() => setPage("platform")}>
              Launch demo
            </button>
          </div>
        </div>
      </nav>

      {page === "home" && <Landing go={setPage} />}
      {page === "platform" && <Dashboard />}
      {page === "science" && <Science />}
      {page === "data" && <DataSources />}

      <footer className="footer container">
        <span>
          OleoCast — weather-driven soybean oil intelligence. Demo build; not
          agronomic advice.
        </span>
        <span>
          Built on public data: NASA POWER · Open-Meteo · USDA NASS · SSURGO
        </span>
      </footer>
    </>
  );
}
