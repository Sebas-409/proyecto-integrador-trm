"""Experimento de escalabilidad: carga sostenida sobre /api/scenario y seguimiento del Auto Scaling Group.

Cada 30 s registra: peticiones por segundo, latencia p50/p95, errores, capacidad deseada e instancias sanas.
Uso:  AWS_PROFILE=trm python scripts/experiments/load_test.py <api_url> <asg_name> <target_group_name> [minutos] [hilos]
Salida: reports/experiments/load_timeline.csv y reports/experiments/load_summary.json
"""
import json
import random
import sys
import threading
import time
import urllib.request
from pathlib import Path

import boto3

URL, ASG, TG = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
MINUTES = float(sys.argv[4]) if len(sys.argv) > 4 else 14
THREADS = int(sys.argv[5]) if len(sys.argv) > 5 else 16
OUT = Path(__file__).resolve().parents[2] / "reports" / "experiments"
OUT.mkdir(parents=True, exist_ok=True)

elb, asg = boto3.client("elbv2"), boto3.client("autoscaling")
tg_arn = elb.describe_target_groups(Names=[TG])["TargetGroups"][0]["TargetGroupArn"]
lock, window, stop = threading.Lock(), [], threading.Event()


def worker():
    while not stop.is_set():
        # Escenarios aleatorios: obliga a calcular las 3 predicciones (sin caché) en cada petición.
        body = json.dumps({"shocks": {"brent_ret_1": random.uniform(-0.05, 0.05),
                                      "dxy_ret_1": random.uniform(-0.01, 0.01)}}).encode()
        req = urllib.request.Request(f"{URL}/api/scenario", data=body, headers={"Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
                ok = r.status == 200
        except Exception:
            ok = False
        with lock:
            window.append((ok, (time.time() - t) * 1000))


threads = [threading.Thread(target=worker, daemon=True) for _ in range(THREADS)]
for th in threads:
    th.start()

rows, t0 = [], time.time()
while time.time() - t0 < MINUTES * 60:
    time.sleep(30)
    with lock:
        batch, window[:] = list(window), []
    g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG])["AutoScalingGroups"][0]
    healthy = sum(t["TargetHealth"]["State"] == "healthy"
                  for t in elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"])
    lat = sorted(l for ok, l in batch if ok)
    row = {"t_min": round((time.time() - t0) / 60, 2), "rps": round(len(batch) / 30, 2),
           "errors": sum(not ok for ok, _ in batch),
           "p50_ms": round(lat[len(lat) // 2]) if lat else None,
           "p95_ms": round(lat[int(len(lat) * 0.95)]) if lat else None,
           "desired": g["DesiredCapacity"], "healthy": healthy}
    rows.append(row)
    print(row, flush=True)
stop.set()

with open(OUT / "load_timeline.csv", "w", encoding="utf-8") as f:
    f.write(",".join(rows[0]) + "\n")
    f.writelines(",".join(str(v) for v in r.values()) + "\n" for r in rows)
acts = asg.describe_scaling_activities(AutoScalingGroupName=ASG, MaxRecords=10)["Activities"]
summary = {"threads": THREADS, "minutes": MINUTES, "total_requests": round(sum(r["rps"] * 30 for r in rows)),
           "total_errors": sum(r["errors"] for r in rows), "max_desired": max(r["desired"] for r in rows),
           "max_healthy": max(r["healthy"] for r in rows),
           "scaling_activities": [{"time": a["StartTime"].isoformat(), "description": a["Description"]} for a in acts]}
(OUT / "load_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
