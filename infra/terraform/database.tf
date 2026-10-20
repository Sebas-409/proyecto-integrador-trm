# PostgreSQL administrado (RDS, PaaS) en subredes privadas.
# Alta disponibilidad: Multi-AZ activable con var.db_multi_az (réplica síncrona en otra AZ y failover automático).
# Recuperación: backups automáticos diarios con point-in-time recovery (var.db_backup_retention_days).

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-pg"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 50 # autoescalado de almacenamiento
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "trm"
  username = "trm_admin"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "06:00-06:30"
  maintenance_window      = "sun:07:00-sun:08:00"
  copy_tags_to_snapshot   = true

  skip_final_snapshot = var.environment != "prod"
  deletion_protection = var.environment == "prod"
  apply_immediately   = true
}

# La contraseña queda en SSM Parameter Store (cifrada); las instancias la leen al arrancar.
resource "aws_ssm_parameter" "db_password" {
  name  = "/${var.project}/${var.environment}/db_password"
  type  = "SecureString"
  value = random_password.db.result
}
