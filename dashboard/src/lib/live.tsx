import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { DEMO, wsUrl } from "./api";

type Connection = "demo" | "live" | "polling" | "offline";

interface LiveState {
  /** increases whenever the server pushes something; pollers refetch at once */
  version: number;
  connection: Connection;
  setReachable: (ok: boolean) => void;
}

const LiveCtx = createContext<LiveState>({ version: 0, connection: "demo", setReachable: () => {} });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  const [socketOpen, setSocketOpen] = useState(false);
  const [reachable, setReachable] = useState(true);

  useEffect(() => {
    const url = wsUrl();
    if (!url) return;
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    let pending: number | undefined;
    const connect = () => {
      ws = new WebSocket(url);
      ws.onopen = () => setSocketOpen(true);
      ws.onmessage = () => {
        // telemetry arrives 6 times per step; batch the refreshes
        if (pending === undefined) {
          pending = window.setTimeout(() => { pending = undefined; setVersion((v) => v + 1); }, 400);
        }
      };
      ws.onclose = () => {
        setSocketOpen(false);
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => { closed = true; window.clearTimeout(retry); ws?.close(); };
  }, []);

  const connection: Connection = DEMO ? "demo" : !reachable ? "offline" : socketOpen ? "live" : "polling";
  return <LiveCtx.Provider value={{ version, connection, setReachable }}>{children}</LiveCtx.Provider>;
}

export const useLive = () => useContext(LiveCtx);

/** Fetch now, again every `everyMs`, and whenever the server pushes an update. */
export function usePoll<T>(fetcher: () => Promise<T>, everyMs = 5000, key = "") {
  const { version, setReachable } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const refresh = useCallback(async () => {
    try {
      const d = await fetchRef.current();
      setData(d);
      setError(null);
      setReachable(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setReachable(false);
    }
  }, [setReachable]);

  useEffect(() => { setData(null); }, [key]);
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, everyMs);
    return () => window.clearInterval(t);
  }, [refresh, everyMs, key, version]);

  return { data, error, refresh };
}
