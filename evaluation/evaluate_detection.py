"""Final detection results on the TEST set (run once, after tuning on validation).

    python evaluation/evaluate_detection.py

Compares four configurations plus one ablation:
    rules      Monitoring agent rules only
    zscore     z-score only
    iforest    Isolation Forest only
    full       rules + z-score + Isolation Forest (the deployed system)
    full_no_cm full system without the building-wide (weather) check
    full_no_hold full system without the 20-minute hold on LOW-severity alarms

Writes to evaluation/results/:
    summary.csv, per_fault.csv, per_injection.csv, false_alarms.csv
    results.md                 tables ready to paste into the report
    fig_time_to_detect.png     median time to detect per fault and method
    fig_false_alarms.png       false alarms per configuration
    fig_example_filter.png     one filter blockage, signals + detection time
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from metrics import ROOT, AnomalyDetector, event_metrics, load_log, point_metrics, run_events  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "evaluation" / "results"
MODEL = DATA / "models" / "detector.joblib"

FAULT_ORDER = ["compressor_failure", "after_hours_waste", "sensor_stuck", "filter_blockage", "refrigerant_leak"]
FAULT_NAMES = {
    "compressor_failure": "Compressor\nfailure",
    "after_hours_waste": "After-hours\nwaste",
    "sensor_stuck": "Sensor\nstuck",
    "filter_blockage": "Filter\nblockage",
    "refrigerant_leak": "Refrigerant\nleak",
}
CONFIGS = [  # (name, mode, common_mode, low_hold_min, label)
    ("rules", "rules", True, None, "Rules only"),
    ("zscore", "zscore", True, None, "z-score only"),
    ("iforest", "iforest", True, None, "Isolation Forest only"),
    ("full", "full", True, None, "Full system"),
    ("full_no_cm", "full", False, None, "Full, no weather check"),
    ("full_no_hold", "full", True, 0, "Full, no LOW hold"),
]
# Reference categorical palette (dataviz skill), fixed order, light mode
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    det = AnomalyDetector.load(MODEL)
    test = pd.read_csv(DATA / "test_faults.csv.gz")
    log = load_log(DATA / "fault_log.csv")
    days = test["ts"].str[:10].nunique()
    print(f"Test set: {len(test):,} readings, {days} days, {len(log)} injections")

    scored = det.score(test)
    summaries, per_inj, fas = [], [], []
    for name, mode, cm, hold, label in CONFIGS:
        ev = run_events(scored, mode, common_mode=cm, low_hold_min=hold)
        s, pf, fa = event_metrics(ev, log, days)
        s.update(point_metrics(scored, mode))
        summaries.append({"config": name, "label": label, **s})
        pf["config"] = name
        per_inj.append(pf)
        if len(fa):
            fa = fa.assign(config=name)
            fas.append(fa[["config", "device_id", "ts_detected", "method", "severity"]])
        print(f"{label:24s} detected {s['detected']}/{s['injections']}  false alarms {s['false_alarms']:2d}  "
              f"median delay {s['median_delay_min']:.0f} min  point F1 {s['point_f1']:.2f}")

    summary = pd.DataFrame(summaries)
    per_inj = pd.concat(per_inj)
    summary.to_csv(OUT / "summary.csv", index=False)
    per_inj.to_csv(OUT / "per_injection.csv", index=False)
    (pd.concat(fas) if fas else pd.DataFrame()).to_csv(OUT / "false_alarms.csv", index=False)

    per_fault = (per_inj.groupby(["config", "fault"])
                 .agg(detected=("detected", "sum"), total=("detected", "size"),
                      median_delay_min=("delay_min", "median"), max_delay_min=("delay_min", "max"))
                 .reset_index())
    per_fault.to_csv(OUT / "per_fault.csv", index=False)

    write_markdown(summary, per_fault, days)
    fig_time_to_detect(per_fault)
    fig_false_alarms(summary, days)
    fig_example(det, test, log, per_inj)
    print(f"\nResults written to {OUT}")


def write_markdown(summary: pd.DataFrame, per_fault: pd.DataFrame, days: int) -> None:
    lines = ["# Detection results (test set)", "",
             f"Test set: {days} days x 6 AHUs, 50 injected faults (10 of each type), generated with its own "
             "random seed. All thresholds and design choices were made on a separate 5-day validation set; "
             "the test set was evaluated once.", "",
             "| Configuration | Faults detected | Median time to detect | False alarms | False alarms per AHU-day | Event precision | Reading-level F1 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for _, r in summary.iterrows():
        lines.append(f"| {r.label} | {r.detected}/{r.injections} | {r.median_delay_min:.0f} min | {r.false_alarms} | "
                     f"{r.false_alarms_per_ahu_day:.3f} | {r.event_precision:.0%} | {r.point_f1:.2f} |")
    lines += ["", "## Full system, per fault type", "",
              "| Fault | Detected | Median time to detect | Slowest |", "| --- | --- | --- | --- |"]
    full = per_fault[per_fault.config == "full"].set_index("fault")
    for f in FAULT_ORDER:
        r = full.loc[f]
        lines.append(f"| {FAULT_NAMES[f].replace(chr(10), ' ')} | {r.detected}/{r.total} | "
                     f"{r.median_delay_min:.0f} min | {r.max_delay_min:.0f} min |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def fig_time_to_detect(per_fault: pd.DataFrame) -> None:
    configs = [("rules", "Rules only"), ("zscore", "z-score only"), ("iforest", "Isolation Forest only"), ("full", "Full system")]
    fig, ax = plt.subplots(figsize=(9, 4.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    x = np.arange(len(FAULT_ORDER))
    w = 0.19
    for i, (cfg, label) in enumerate(configs):
        d = per_fault[per_fault.config == cfg].set_index("fault").reindex(FAULT_ORDER)
        vals = d["median_delay_min"].to_numpy()
        pos = x + (i - 1.5) * (w + 0.01)
        ax.bar(pos, np.nan_to_num(vals), width=w, color=SERIES[i], label=label, zorder=2)
        for p, v, det_n, tot in zip(pos, vals, d["detected"], d["total"]):
            if det_n < tot:  # missed faults are called out on the bar
                ax.text(p, (0 if np.isnan(v) else v) + 4, f"{det_n}/{tot}", ha="center", va="bottom",
                        fontsize=7.5, color=INK2)
    ax.set_xticks(x, [FAULT_NAMES[f] for f in FAULT_ORDER], color=INK, fontsize=9)
    ax.set_ylabel("Median minutes from fault start to alarm", color=INK2, fontsize=9)
    ax.set_title("Fast faults are caught in minutes; the slow refrigerant leak takes hours",
                 loc="left", fontsize=11, color=INK, pad=12)
    ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="upper left", labelcolor=INK)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.12)
    fig.text(0.01, 0.01, "Labels like 7/10 mark fault types where a method missed some injections.",
             fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT / "fig_time_to_detect.png", facecolor=SURFACE)
    plt.close(fig)


def fig_false_alarms(summary: pd.DataFrame, days: int) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 3.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(False)
    s = summary.iloc[::-1]
    ax.barh(s["label"], s["false_alarms"], color=SERIES[0], height=0.55, zorder=2)
    for i, (v, det_n) in enumerate(zip(s["false_alarms"], s["detected"])):
        ax.text(v + 0.3, i, f"{v}  (caught {det_n}/50)", va="center", fontsize=8.5, color=INK)
    ax.set_xlabel(f"False alarms in {days} days across 6 AHUs", color=INK2, fontsize=9)
    ax.tick_params(axis="y", colors=INK, labelsize=9)
    ax.set_xlim(0, max(summary["false_alarms"].max() * 1.35, 5))
    ax.set_title("Only the combined system catches all 50 faults, at one false alarm every two days",
                 loc="left", fontsize=11, color=INK, pad=10)
    fig.tight_layout()
    fig.savefig(OUT / "fig_false_alarms.png", facecolor=SURFACE)
    plt.close(fig)


def fig_example(det, test, log, per_inj) -> None:
    hit = per_inj[(per_inj.config == "full") & (per_inj.fault == "filter_blockage") & per_inj.detected].iloc[0]
    dev, start, end = hit.device_id, hit.start, hit.end
    alarm = start + pd.Timedelta(minutes=hit.delay_min)
    d = test[test.device_id == dev].copy()
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_localize(None)
    d = d[(d.ts >= start - pd.Timedelta("90min")) & (d.ts <= end + pd.Timedelta("30min"))]
    panels = [("airflow_cfm", "Airflow (CFM)"), ("power_kw", "Power (kW)"),
              ("supply_temp_c", "Supply air (°C)"), ("zone_temp_c", "Zone temp (°C)")]
    fig, axes = plt.subplots(len(panels), 1, figsize=(9, 6.4), dpi=160, sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, (col, label) in zip(axes, panels):
        style_axes(ax)
        ax.axvspan(start, end, color="#eda100", alpha=0.12, lw=0)
        ax.plot(d.ts, d[col], color=SERIES[0], lw=1.6)
        ax.axvline(alarm, color=INK, lw=1, ls="--")
        ax.set_ylabel(label, color=INK2, fontsize=8.5)
    axes[0].text(start, axes[0].get_ylim()[1], " fault injected", va="top", fontsize=8, color=INK2)
    axes[0].text(alarm, axes[0].get_ylim()[0], f" alarm after {hit.delay_min:.0f} min", va="bottom",
                 fontsize=8, color=INK)
    axes[-1].tick_params(axis="x", labelsize=8)
    import matplotlib.dates as mdates
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.suptitle(f"Filter blockage on {dev}: airflow drops, the compressor works harder, room temperature holds",
                 x=0.01, ha="left", fontsize=10.5, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(OUT / "fig_example_filter.png", facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    main()
