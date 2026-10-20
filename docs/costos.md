# Análisis de costos (R5)

Precios on-demand de AWS en us-east-1 (Virginia), 730 h/mes. Son una **estimación**: antes de entregar,
confirmarlos en la [AWS Pricing Calculator](https://calculator.aws/) y reemplazar la sección "Costos reales"
con la factura de Cost Explorer filtrada por el tag `Project = trm-predict`.

## 1. Estimado mensual (operando 24/7)

| Servicio | Configuración | Cálculo | USD/mes |
|---|---|---|---:|
| Application Load Balancer | 1 ALB, tráfico bajo (< 1 LCU) | 0,0225 × 730 + LCU ≈ 0,008 × 730 × 0,1 | 17,0 |
| EC2 (API) | 2 × t3.micro (ASG deseado = 2) | 2 × 0,0104 × 730 | 15,2 |
| EBS | 2 × 8 GB gp3 | 16 × 0,08 | 1,3 |
| IPv4 públicas | 2 del ALB + 2 de las instancias | 4 × 0,005 × 730 | 14,6 |
| RDS PostgreSQL | db.t4g.micro Single-AZ | 0,016 × 730 | 11,7 |
| Almacenamiento RDS | 20 GB gp3 (backups ≤ tamaño de BD sin costo) | 20 × 0,115 | 2,3 |
| S3 + réplica DR | < 1 GB estándar + < 1 GB Standard-IA + transferencia CRR | | 0,1 |
| Lambda (ingesta + ETL) | ~44 invocaciones/mes, < 60 s c/u | dentro del nivel gratuito | 0,0 |
| EventBridge, SSM (estándar) | regla programada y parámetro | sin costo | 0,0 |
| ECR | ~0,5 GB de imágenes (se guardan 5) | 0,5 × 0,10 | 0,1 |
| CloudWatch | 1 tablero (3 gratis), 3 alarmas, logs con 14 días de retención | 3 × 0,10 + logs | 0,8 |
| Transferencia de datos | < 5 GB de salida | | 0,5 |
| **Total dev (24/7)** | | | **≈ 63,6** |

**Escenario de producción** (`db_multi_az = true`, ASG de 2 a 4): RDS se duplica (+14 USD) y en horas pico
se agregan instancias (+7,6 USD por instancia extra al mes) → **≈ 78 a 95 USD/mes**.

## 2. Estrategia para el semestre (costo real esperado)

| Etapa | Horas encendido | Costo aprox. |
|---|---:|---:|
| Desarrollo e integración (Terraform `apply`/`destroy` por sesión, ~40 h) | 40 | 3,5 |
| Pruebas de auto-scaling y failover Multi-AZ (1 día) | 24 | 2,5 |
| Semana de sustentación (encendido continuo) | 168 | 14,6 |
| **Total del proyecto** | | **≈ 21 USD** |

Cabe dentro de los créditos de AWS Academy Learner Lab (50 a 100 USD) o de los créditos del nivel gratuito
de una cuenta nueva. El presupuesto de AWS Budgets (30 USD) avisa al 50 %, 80 % y 100 %.

## 3. Decisiones costo-beneficio

| Decisión | Alternativa descartada | Ahorro / razón |
|---|---|---|
| VPC Gateway Endpoint de S3 + Lambda de ETL sin Internet | NAT Gateway | ~33 USD/mes (0,045 USD/h + datos) |
| t3.micro / db.t4g.micro (Graviton en RDS) | t3.small / db.t3.small | ~50 % menos; la carga es baja |
| Multi-AZ solo para la demo de alta disponibilidad | Multi-AZ permanente | 14 USD/mes |
| Lambda para la ingesta diaria (FaaS) | EC2 encendida con cron | se paga solo por los segundos de ejecución |
| Ciclo de vida S3 (IA a 30 días, Glacier IR a 180 días) | todo en Standard | relevante cuando crezca la historia |
| EC2 + ALB + ASG para la API | API Gateway + Lambda (≈ 0 USD) | la opción serverless sería más barata, pero el proyecto debe demostrar balanceo de carga y auto-scaling configurables (objetivo de Cloud); queda como trabajo futuro |

## 4. Costos reales (completar en la fase 5)

| Servicio | Estimado | Real (Cost Explorer) | Diferencia |
|---|---:|---:|---:|
| EC2 + EBS + IPv4 | | | |
| ELB | | | |
| RDS | | | |
| S3, Lambda, otros | | | |
| **Total** | | | |
