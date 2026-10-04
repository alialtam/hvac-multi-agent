import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import "./index.css";
import { LiveProvider } from "./lib/live";
import Shell from "./components/Shell";
import Overview from "./pages/Overview";
import DevicePage from "./pages/DevicePage";
import Incidents from "./pages/Incidents";
import Activity from "./pages/Activity";
import Tickets from "./pages/Tickets";
import Energy from "./pages/Energy";
import Simulator from "./pages/Simulator";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Overview />} />
            <Route path="devices/:id" element={<DevicePage />} />
            <Route path="incidents" element={<Incidents />} />
            <Route path="incidents/:id" element={<Incidents />} />
            <Route path="activity" element={<Activity />} />
            <Route path="tickets" element={<Tickets />} />
            <Route path="energy" element={<Energy />} />
            <Route path="simulator" element={<Simulator />} />
            <Route path="*" element={<p className="text-ink-2">This page does not exist. <Link className="text-chill underline" to="/">Go to the building overview</Link>.</p>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LiveProvider>
  </StrictMode>,
);
