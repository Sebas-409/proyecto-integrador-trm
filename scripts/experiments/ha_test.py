"""Experimento de alta disponibilidad: termina una instancia del ASG mientras se consulta la API.

Mide peticiones fallidas, latencia y el tiempo hasta que el ASG vuelve a tener 2 instancias sanas.
Uso:  AWS_PROFILE=trm python scripts/experiments/ha_test.py <api_url> <asg_name> <target_group_name>
Salida: reports/experiments/ha_probes.csv y reports/experiments/ha_summary.json
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import boto3

URL, ASG, TG = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
OUT = Path(__file__).resolve().parents[2] / "reports" / "experiments"
OUT.mkdir(parents=True, exist_ok=True)
DURATION, KILL_AT = 480, 30

elb = boto3.client("elbv2")
asg = boto3.client("autoscaling")
tg_arn = elb.describe_target_groups(Names=[TG])["TargetGroups"][0]["TargetGroupArn"]
probes, events, stop = [], [], threading.Event()
t0 = time.time()


def healthy_targets():
    th = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
    return sorted(t["Target"]["Id"] for t in th if t["TargetHealth"]["State"] == "healthy")


def prober():
    while not stop.is_set():
        start = time.time()
        try:
            with urllib.request.urlopen(f"{URL}/health", timeout=5) as r:
                body = json.loads(r.read())
                probes.append((round(start - t0, 2), r.status, round((time.time() - start) * 1000, 1), body["instance"]))
        except Exception as err:
            probes.append((round(start - t0, 2), 0, round((time.time() - start) * 1000, 1), type(err).__name__))
        time.sleep(max(0, 0.5 - (time.time() - start)))


th = threading.Thread(target=prober, daemon=True)
th.start()
initial = healthy_targets()
events.append({"t": 0, "event": "start", "healthy": initial})
victim, killed_at, recovered_at = initial[0], None, None
while time.time() - t0 < DURATION:
    now = round(time.time() - t0, 1)
    if killed_at is None and now >= KILL_AT:
        asg.terminate_instance_in_auto_scaling_group(InstanceId=victim, ShouldDecrementDesiredCapacity=False)
        killed_at = now
        events.append({"t": now, "event": "terminate", "instance": victim})
    if killed_at is not None:
        h = healthy_targets()
        if events[-1].get("healthy") != h:
            events.append({"t": now, "event": "healthy_changed", "healthy": h})
        if recovered_at is None and len(h) >= 2 and victim not in h:
            recovered_at = now
            events.append({"t": now, "event": "recovered", "healthy": h})
            if now > killed_at + 60:
                time.sleep(20)
                break
    time.sleep(5)
stop.set()
th.join()

with open(OUT / "ha_probes.csv", "w", encoding="utf-8") as f:
    f.write("t_s,status,latency_ms,instance\n")
    f.writelines(f"{a},{b},{c},{d}\n" for a, b, c, d in probes)
ok = [p for p in probes if p[1] == 200]
lat = sorted(p[2] for p in ok)
summary = {
    "probes": len(probes), "ok": len(ok), "failed": len(probes) - len(ok),
    "availability_pct": round(100 * len(ok) / len(probes), 3),
    "latency_ms_p50": lat[len(lat) // 2], "latency_ms_p95": lat[int(len(lat) * 0.95)],
    "terminated_at_s": killed_at, "recovered_at_s": recovered_at,
    "time_to_full_capacity_s": round(recovered_at - killed_at, 1) if recovered_at else None,
    "instances_seen": sorted({p[3] for p in ok}), "events": events,
}
(OUT / "ha_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
