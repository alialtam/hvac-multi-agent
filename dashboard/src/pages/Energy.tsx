import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { usePoll } from "../lib/live";
import { ErrorNote, Loading, Panel } from "../components/ui";

export default function Energy() {
  const { data: e, error } = usePoll(api.energy, 15000);
  if (error) return <ErrorNote message={error} />;
  if (!e) return <Loading label="Loading energy data" />;
  const worst = [...e.devices].sort((a, b) => b.waste_kwh - a.waste_kwh)[0];
  const hourly = e.hourly.map((h) => ({ ...h, label: `${String(h.hour).padStart(2, "0")}:00` }));

  return (
    <div className="mx-auto max-w-[1200px]">
      <h1 className="font-gauge text-[30px] font-semibold">
        {money(e.waste_cost_today, e.currency)} of electricity wasted today
      </h1>
      <p className="mt-1 text-[15px] text-ink-2">
        The building used {num(e.total_kwh_today)} kWh against an expected {num(e.baseline_kwh_today)} kWh
        ({num(e.waste_kwh_today)} kWh extra at {money(e.tariff_per_kwh, e.currency, 2)} per kWh).
        {worst && worst.waste_kwh > 0 && <> Most of it came from {worst.device_id}.</>}
      </p>

      <Panel title="Hourly use against what is normal for this building" className="mt-6">
        <div className="h-[300px] px-2 py-4">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={hourly} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barCategoryGap={3}>
              <CartesianGrid stroke="#e3e9e7" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 12, fill: "#4c5f5c" }} interval={2} stroke="#d5dedb" />
              <YAxis width={48} tick={{ fontSize: 12, fill: "#4c5f5c" }} stroke="#d5dedb" unit=" kWh" />
              <Tooltip formatter={(v, n) => [`${num(Number(v))} kWh`, n]} contentStyle={{ borderRadius: 6, border: "1px solid #d5dedb", fontSize: 13 }} />
              <Legend wrapperStyle={{ fontSize: 13 }} />
              <Bar dataKey="kwh" name="Used" fill="#2a78d6" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Line dataKey="baseline_kwh" name="Normal" stroke="#1d2e2c" strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="By air handling unit" className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[560px] text-[14.5px]">
          <thead>
            <tr className="border-b border-line text-left text-[13px] text-ink-2">
              <th className="px-4 py-2.5 font-medium">Unit</th>
              <th className="px-3 py-2.5 text-right font-medium">Used today</th>
              <th className="px-3 py-2.5 text-right font-medium">Normal</th>
              <th className="px-3 py-2.5 text-right font-medium">Extra</th>
              <th className="px-4 py-2.5 text-right font-medium">Cost of the extra</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {e.devices.map((d) => (
              <tr key={d.device_id}>
                <td className="px-4 py-2.5 font-gauge font-semibold">{d.device_id}</td>
                <td className="px-3 py-2.5 text-right">{num(d.kwh_today)} kWh</td>
                <td className="px-3 py-2.5 text-right text-ink-2">{num(d.baseline_kwh)} kWh</td>
                <td className={`px-3 py-2.5 text-right ${d.waste_kwh >= 5 ? "font-semibold text-alarm" : ""}`}>{num(d.waste_kwh)} kWh</td>
                <td className="px-4 py-2.5 text-right">{money(d.waste_kwh * e.tariff_per_kwh, e.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
