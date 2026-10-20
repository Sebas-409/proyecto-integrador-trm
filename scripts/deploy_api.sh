#!/usr/bin/env bash
# Construye la imagen de la API, la sube a ECR, publica el modelo en S3 y refresca el Auto Scaling Group.
# Requiere: terraform apply previo, AWS CLI configurado y Docker.
set -euo pipefail
cd "$(dirname "$0")/.."

TF="terraform -chdir=infra/terraform"
REGION=$($TF output -raw ecr_repository_url | cut -d. -f4)
REPO=$($TF output -raw ecr_repository_url)
BUCKET=$($TF output -raw data_bucket)
ASG=$($TF output -raw asg_name)
TAG=${1:-latest}

aws s3 cp models/model.joblib "s3://$BUCKET/models/model.joblib"
aws s3 cp models/metadata.json "s3://$BUCKET/models/metadata.json"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -f api/Dockerfile -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

# Reemplazo gradual de instancias (mantiene >= 50 % sanas): despliegue sin caída.
aws autoscaling start-instance-refresh --region "$REGION" --auto-scaling-group-name "$ASG" \
  --preferences '{"MinHealthyPercentage": 50}'

echo "API: $($TF output -raw api_url)"
