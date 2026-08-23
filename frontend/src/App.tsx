import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { PipelineView } from "./PipelineView";
import { LedgerView } from "./LedgerView";
import { RunDrilldown } from "./RunDrilldown";
import { QueueView } from "./QueueView";

// HashRouter, not BrowserRouter: the bundle is served as plain static files with no
// server-side catch-all route, so every path must resolve to the one `index.html`
// (ADR-0008). Hash routes never leave that document.
export function App() {
  return (
    <HashRouter>
      <div className="shell">
        <nav className="nav">
          <div className="nav__brand">Invoice Automation</div>
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Pipeline
          </NavLink>
          <NavLink to="/ledger" className={({ isActive }) => (isActive ? "active" : "")}>
            Ledger
          </NavLink>
          <NavLink to="/queue" className={({ isActive }) => (isActive ? "active" : "")}>
            Review queue
          </NavLink>
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={<PipelineView />} />
            <Route path="/ledger" element={<LedgerView />} />
            <Route path="/ledger/:documentName" element={<RunDrilldown />} />
            <Route path="/queue" element={<QueueView />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
