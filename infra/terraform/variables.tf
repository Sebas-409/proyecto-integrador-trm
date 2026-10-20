variable "project" {
  type    = string
  default = "trm-predict"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "dr_region" {
  description = "Región de recuperación ante desastres (réplica de datos y modelo)."
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

# --- AWS Academy Learner Lab no permite crear roles IAM: se reutiliza LabRole ---
variable "lab_role_name" {
  description = "Si se indica (p. ej. \"LabRole\"), se usa ese rol existente en lugar de crear roles IAM."
  type        = string
  default     = ""
}

variable "lab_instance_profile" {
  description = "Perfil de instancia existente (AWS Academy: \"LabInstanceProfile\")."
  type        = string
  default     = ""
}

# --- Cómputo de la API ---
variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "asg_min" {
  type    = number
  default = 1
}

variable "asg_desired" {
  type    = number
  default = 2
}

variable "asg_max" {
  type    = number
  default = 4
}

variable "cpu_target" {
  description = "Uso de CPU promedio objetivo para el auto-scaling (target tracking)."
  type        = number
  default     = 60
}

variable "api_image_tag" {
  type    = string
  default = "latest"
}

# --- Base de datos ---
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_multi_az" {
  description = "true en producción (alta disponibilidad); false en dev para ahorrar costos."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

# --- Ingesta ---
variable "ingest_schedule" {
  description = "Hora de la ingesta diaria (UTC). 23:30 UTC = 6:30 p. m. Colombia, después de publicarse la TRM."
  type        = string
  default     = "cron(30 23 ? * MON-FRI *)"
}

# --- Resiliencia y costos ---
variable "enable_dr_replication" {
  description = "Replica el bucket de datos/modelos a la región DR (Cross-Region Replication)."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  type    = number
  default = 30
}

variable "enable_budget" {
  description = "AWS Budgets (no disponible en algunas cuentas de AWS Academy)."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Correo para alarmas y presupuesto (opcional)."
  type        = string
  default     = ""
}
