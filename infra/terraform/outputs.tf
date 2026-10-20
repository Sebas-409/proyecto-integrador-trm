output "api_url" {
  description = "URL pública de la API y el tablero (ALB)."
  value       = "http://${aws_lb.api.dns_name}"
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "dr_bucket" {
  value = var.enable_dr_replication ? aws_s3_bucket.dr[0].bucket : null
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "ingest_lambda" {
  value = aws_lambda_function.ingest.function_name
}

output "asg_name" {
  value = aws_autoscaling_group.api.name
}
