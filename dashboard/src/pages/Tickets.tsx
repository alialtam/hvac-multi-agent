import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { usePoll } from "../lib/live";
import { ErrorNote, Loading, Panel, SeverityTag } from "../components/ui";

export default function Tickets() {
  const { data, error } = usePoll(api.tickets, 5000);
  return (
    <div className="mx-auto max-w-[1100px]">
      <h1 className="font-gauge text-[30px] font-semibold">Maintenance tickets</h1>
      <p className="mt-1 text-[15px] text-ink-2">Tickets are only created after a person approves the agents' recommendation.</p>
      {error && <div className="mt-4"><ErrorNote message={error} /></div>}
      <Panel className="mt-5 overflow-x-auto">
        {!data ? <Loading /> : data.length === 0 ? (
          <p className="px-4 py-6 text-[14px] text-ink-2">No tickets yet. Approve a recommendation on the Incidents page to create one.</p>
        ) : (
          <table className="w-full min-w-[720px] text-[14.5px]">
            <thead>
              <tr className="border-b border-line text-left text-[13px] text-ink-2">
                <th className="px-4 py-2.5 font-medium">Ticket</th>
                <th className="px-3 py-2.5 font-medium">Task</th>
                <th className="px-3 py-2.5 font-medium">Priority</th>
                <th className="px-3 py-2.5 font-medium">Assigned to</th>
                <th className="px-3 py-2.5 font-medium">Created</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.map((t) => (
                <tr key={t.id}>
                  <td className="px-4 py-3 font-gauge font-semibold">{t.id}</td>
                  <td className="px-3 py-3">
                    {t.title}
                    <Link to={`/incidents/${t.incident_id}`} className="block text-[13px] text-chill hover:underline">{t.device_id}, from {t.incident_id}</Link>
                  </td>
                  <td className="px-3 py-3"><SeverityTag severity={t.priority} /></td>
                  <td className="px-3 py-3 text-ink-2">{t.assign_to}</td>
                  <td className="px-3 py-3 text-ink-2">{dateTime(t.created_at)}</td>
                  <td className="px-4 py-3 capitalize">{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
