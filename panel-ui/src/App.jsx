import { useCallback, useEffect, useState } from "react";
import "./App.css";

const POLL_MS = 5000;

function formatSeen(seconds) {
  if (seconds === null) return "Never connected";
  if (seconds < 60) return `Seen ${seconds}s ago`;
  if (seconds < 3600) return `Seen ${Math.floor(seconds / 60)}m ago`;
  return `Seen ${Math.floor(seconds / 3600)}h ago`;
}

function KeyGate({ onSubmit, error }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="gate"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <h1>Machine panel</h1>
      <label htmlFor="key">Admin key</label>
      <input
        id="key"
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" className="primary">Unlock</button>
    </form>
  );
}

function MachineRow({ machine, onShutdown }) {
  const [confirming, setConfirming] = useState(false);
  const queued = machine.pending_cmd === "shutdown";

  return (
    <li className="row">
      <span
        className={`dot ${machine.online ? "on" : "off"}`}
        role="img"
        aria-label={machine.online ? "Online" : "Offline"}
      />
      <div className="info">
        <span className="name">{machine.name}</span>
        <span className="meta">
          {machine.online ? "Online" : "Offline"}, {formatSeen(machine.seconds_since_seen).toLowerCase()}
        </span>
      </div>

      <div className="actions">
        {queued ? (
          <span className="queued">Shutdown queued</span>
        ) : confirming ? (
          <>
            <button
              className="danger"
              onClick={() => {
                setConfirming(false);
                onShutdown(machine.name);
              }}
            >
              Shut down {machine.name}
            </button>
            <button onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button disabled={!machine.online} onClick={() => setConfirming(true)}>
            Shut down
          </button>
        )}
      </div>
    </li>
  );
}

export default function App() {
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem("adminKey") || "");
  const [machines, setMachines] = useState(null); // null = not loaded yet
  const [error, setError] = useState("");

  const signOut = useCallback((message = "") => {
    sessionStorage.removeItem("adminKey");
    setAdminKey("");
    setMachines(null);
    setError(message);
  }, []);

  const api = useCallback(
    async (path, options = {}) => {
      const res = await fetch(path, {
        ...options,
        headers: { ...options.headers, "X-Admin-Key": adminKey },
      });
      if (res.status === 401) {
        signOut("That admin key was rejected. Check it and try again.");
        throw new Error("unauthorized");
      }
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      return res.json();
    },
    [adminKey, signOut]
  );

  const loadMachines = useCallback(async () => {
    try {
      setMachines(await api("/api/machines"));
      setError("");
    } catch (e) {
      if (e.message !== "unauthorized") setError("Can't reach the server. Retrying...");
    }
  }, [api]);

  useEffect(() => {
    if (!adminKey) return;
    loadMachines();
    const id = setInterval(loadMachines, POLL_MS);
    return () => clearInterval(id);
  }, [adminKey, loadMachines]);

  async function shutdown(name) {
    try {
      await api(`/api/machines/${encodeURIComponent(name)}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: "shutdown" }),
      });
      loadMachines();
    } catch (e) {
      if (e.message !== "unauthorized") setError(`Couldn't queue shutdown for ${name}.`);
    }
  }

  if (!adminKey) {
    return (
      <KeyGate
        error={error}
        onSubmit={(key) => {
          sessionStorage.setItem("adminKey", key);
          setError("");
          setAdminKey(key);
        }}
      />
    );
  }

  const onlineCount = machines ? machines.filter((m) => m.online).length : 0;

  return (
    <main className="panel">
      <header>
        <h1>Machines</h1>
        {machines && (
          <p className="summary">
            {onlineCount} of {machines.length} online
          </p>
        )}
        <button className="link" onClick={() => signOut()}>Lock</button>
      </header>

      {error && <p className="error" role="alert">{error}</p>}

      {machines === null ? (
        <p className="empty">Loading machines...</p>
      ) : machines.length === 0 ? (
        <p className="empty">
          No machines yet. Register one with POST /api/machines, then start its agent.
        </p>
      ) : (
        <ul className="list">
          {machines.map((m) => (
            <MachineRow key={m.name} machine={m} onShutdown={shutdown} />
          ))}
        </ul>
      )}
    </main>
  );
}